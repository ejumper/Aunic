package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	claude "github.com/ejumper/aunic/harness/claude"
	"github.com/ejumper/aunic/markers"
	"github.com/ejumper/aunic/prompts"
	"github.com/ejumper/aunic/transcript"
)

// claudeEventMsg delivers one raw JSON event line from Claude's stdout.
type claudeEventMsg struct{ data []byte }

// claudeDeadMsg is emitted when the Claude subprocess exits unexpectedly.
type claudeDeadMsg struct{}

// claudeToolCallBuf accumulates a tool_use content block's streamed
// arguments (delivered as input_json_delta/partial_json chunks) until its
// content_block_stop arrives.
type claudeToolCallBuf struct {
	id, name string
	argsJSON strings.Builder
}

// waitForClaudeOutput returns a tea.Cmd that blocks until the next JSON event
// arrives on Claude's output channel. Must be re-queued after every event.
func (m appModel) waitForClaudeOutput() tea.Cmd {
	return func() tea.Msg {
		data, ok := <-m.claudeProc.Output()
		if !ok {
			return claudeDeadMsg{}
		}
		return claudeEventMsg{data: data}
	}
}

// startClaudeRun validates the note, records the user message, builds the
// XML-tag-delineated prompt (note context + web context + the user's
// request), and dispatches it to Claude.
func (m appModel) startClaudeRun(prompt string) (appModel, tea.Cmd) {
	if m.claudeRunActive {
		return m, nil
	}
	if m.claudeProc == nil {
		m.ag.Indicator.SetError("no harness configured")
		return m, m.ag.Indicator.StaleCmd()
	}

	// 1. Validate markers in the note body.
	noteBody, _ := transcript.Split(m.editor.Value())
	parsed := markers.Scan(noteBody)
	if vErr := parsed.Validate(); vErr != nil {
		m.ag.Indicator.SetError(vErr.Message)
		return m, m.ag.Indicator.StaleCmd()
	}

	// Snapshot rows that existed before this turn's user row is appended,
	// so the cold-start history recap never includes the prompt itself.
	priorRows := m.transcriptRows

	// 2. Record user message in transcript before the run.
	m.nextToolID++
	m.transcriptRows = append(m.transcriptRows, transcript.Row{
		Num:     m.nextToolID,
		Role:    transcript.RoleUser,
		Type:    transcript.TypeMessage,
		Content: transcript.EncodeMessage(prompt),
	})
	m.transcriptBar.SetRows(m.transcriptRows)

	// 3. Auto-save so the file is current before Claude opens it.
	if err := m.writeNote(); err != nil {
		m.ag.Indicator.SetError("save failed: " + err.Error())
		return m, m.ag.Indicator.StaleCmd()
	}

	// 4. Build note context (only when the visible snapshot changed since
	// last injection).
	absPath, _ := filepath.Abs(m.filepath)
	noteCtx, newHash := m.injectClaudeSnapshotIfStale(parsed, absPath)
	m.claudeNoteSnapshotHash = newHash

	// 5. Cold start: prepend a plain-text recap of prior transcript rows the
	// first time this Claude process handles a run, if Aunic already has
	// history for this note. Known minor inefficiency: since Claude has no
	// get_state-equivalent query (unlike Pi), Aunic can't tell whether the
	// resumed --session-id already carries this same history natively, so a
	// respawn (e.g. after a model switch) may redundantly re-inject it once.
	if !m.claudeHistoryInjected && len(priorRows) > 0 {
		summary := claude.SummarizePriorTranscript(priorRows)
		if noteCtx != "" {
			noteCtx = summary + "\n\n" + noteCtx
		} else {
			noteCtx = summary
		}
		m.claudeHistoryInjected = true
	}

	// 6. Pending web search results, then clear the shared buffer.
	webCtx := m.pendingWebCtx
	m.pendingWebCtx = ""

	fullPrompt := claude.BuildPrompt(prompt, noteCtx, webCtx)

	// 7. Reset per-run state.
	m.claudeRunActive = true
	m.claudeNoteEditedInRun = false
	m.claudeFollowUpSent = false
	m.claudeInProgressRow = -1
	m.claudeActiveToolRows = make(map[string]int)
	m.claudeToolCallBufs = nil

	// 8. Dispatch prompt to Claude.
	_ = m.claudeProc.SendPrompt(fullPrompt)
	m.ag.Indicator.Set("running…")
	return m, m.ag.Indicator.StaleCmd()
}

// handleClaudeEvent dispatches a raw JSON event line from Claude's stdout.
// Callers must re-queue waitForClaudeOutput after this returns.
func (m appModel) handleClaudeEvent(data []byte) (appModel, tea.Cmd) {
	var base struct {
		Type string `json:"type"`
	}
	if err := json.Unmarshal(data, &base); err != nil {
		return m, nil
	}

	switch base.Type {
	case "system", "rate_limit_event":
		// "system" carries init/status telemetry; "rate_limit_event" carries
		// five-hour-window usage telemetry. Neither needs action.
		return m, nil

	case "stream_event":
		var env claude.StreamEventEnvelope
		if err := json.Unmarshal(data, &env); err != nil {
			return m, nil
		}
		return m.handleClaudeStreamEvent(env.Event)

	case "assistant":
		// A complete, non-streaming duplicate of the assistant message
		// already built incrementally from stream_event deltas below —
		// ignored to avoid double-recording.
		return m, nil

	case "user":
		return m.handleClaudeToolResult(data)

	case "result":
		return m.handleClaudeResult(data)
	}

	return m, nil
}

// handleClaudeStreamEvent unwraps one inner Messages-API-style event and
// dispatches on its own type.
func (m appModel) handleClaudeStreamEvent(eventRaw json.RawMessage) (appModel, tea.Cmd) {
	var base claude.InnerEventBase
	if err := json.Unmarshal(eventRaw, &base); err != nil {
		return m, nil
	}

	switch base.Type {
	case "content_block_start":
		var ev claude.ContentBlockStartEvent
		if err := json.Unmarshal(eventRaw, &ev); err != nil {
			return m, nil
		}
		return m.handleClaudeBlockStart(ev), nil

	case "content_block_delta":
		var ev claude.ContentBlockDeltaEvent
		if err := json.Unmarshal(eventRaw, &ev); err != nil {
			return m, nil
		}
		return m.handleClaudeBlockDelta(ev)

	case "content_block_stop":
		var ev claude.ContentBlockStopEvent
		if err := json.Unmarshal(eventRaw, &ev); err != nil {
			return m, nil
		}
		return m.handleClaudeBlockStop(ev), nil
	}

	return m, nil
}

// handleClaudeBlockStart starts a new transcript row for a text block, or
// begins buffering a tool_use block's streamed arguments.
func (m appModel) handleClaudeBlockStart(ev claude.ContentBlockStartEvent) appModel {
	switch ev.ContentBlock.Type {
	case "text":
		m.nextToolID++
		m.transcriptRows = append(m.transcriptRows, transcript.Row{
			Num:     m.nextToolID,
			Role:    transcript.RoleAssistant,
			Type:    transcript.TypeMessage,
			Content: transcript.EncodeMessage(""),
		})
		m.claudeInProgressRow = len(m.transcriptRows) - 1
		m.transcriptBar.SetRows(m.transcriptRows)

	case "tool_use":
		if m.claudeToolCallBufs == nil {
			m.claudeToolCallBufs = make(map[int]*claudeToolCallBuf)
		}
		m.claudeToolCallBufs[ev.Index] = &claudeToolCallBuf{
			id:   ev.ContentBlock.ID,
			name: ev.ContentBlock.Name,
		}
	}
	return m
}

// handleClaudeBlockDelta appends a text delta to the in-progress transcript
// row, or accumulates a tool_use argument chunk.
func (m appModel) handleClaudeBlockDelta(ev claude.ContentBlockDeltaEvent) (appModel, tea.Cmd) {
	switch ev.Delta.Type {
	case "text_delta":
		if m.claudeInProgressRow >= 0 && m.claudeInProgressRow < len(m.transcriptRows) {
			c, _ := transcript.DecodeMessage(m.transcriptRows[m.claudeInProgressRow].Content)
			c.Text += ev.Delta.Text
			m.transcriptRows[m.claudeInProgressRow].Content = transcript.EncodeMessage(c.Text)
			m.transcriptBar.SetRows(m.transcriptRows)
		}
	case "input_json_delta":
		if buf, ok := m.claudeToolCallBufs[ev.Index]; ok {
			buf.argsJSON.WriteString(ev.Delta.PartialJSON)
		}
	}
	return m, nil
}

// handleClaudeBlockStop finalizes a tool_use block (parses its accumulated
// argument JSON and appends the transcript tool_call row) or clears the
// in-progress text row.
func (m appModel) handleClaudeBlockStop(ev claude.ContentBlockStopEvent) appModel {
	if buf, ok := m.claudeToolCallBufs[ev.Index]; ok {
		var args map[string]any
		if s := buf.argsJSON.String(); s != "" {
			_ = json.Unmarshal([]byte(s), &args)
		}
		if args == nil {
			args = map[string]any{}
		}
		delete(m.claudeToolCallBufs, ev.Index)
		return m.appendClaudeToolCallRow(buf.id, buf.name, args)
	}
	m.claudeInProgressRow = -1
	return m
}

// appendClaudeToolCallRow maps a Claude tool call to a transcript tool_call
// row and stores the row index in claudeActiveToolRows for later lookup by
// tool result. Claude's tool names (Read/Write/Edit/Bash/Grep/Glob) already
// equal the transcript.Tool* constants, so no name-mapping is needed.
func (m appModel) appendClaudeToolCallRow(id, name string, args map[string]any) appModel {
	var content json.RawMessage
	switch name {
	case transcript.ToolEdit:
		filePath, _ := args["file_path"].(string)
		oldStr, _ := args["old_string"].(string)
		newStr, _ := args["new_string"].(string)
		content = transcript.EncodeAgentFileCall(filePath, oldStr, newStr)
	case transcript.ToolWrite:
		filePath, _ := args["file_path"].(string)
		fileContent, _ := args["content"].(string)
		content = transcript.EncodeAgentFileCall(filePath, "", fileContent)
	case transcript.ToolRead:
		filePath, _ := args["file_path"].(string)
		content = transcript.EncodeAgentFileCall(filePath, "", "")
	case transcript.ToolBash:
		cmd, _ := args["command"].(string)
		content = transcript.EncodeAgentCmdCall(cmd)
	case transcript.ToolGrep, transcript.ToolGlob:
		pattern, _ := args["pattern"].(string)
		content = transcript.EncodeAgentPatternCall(pattern)
	default:
		content = transcript.EncodeAgentCmdCall(fmt.Sprintf("%s(%v)", name, args))
	}

	m.nextToolID++
	row := transcript.Row{
		Num:     m.nextToolID,
		Role:    transcript.RoleAssistant,
		Type:    transcript.TypeToolCall,
		Tool:    name,
		ToolID:  id,
		Content: content,
	}
	m.transcriptRows = append(m.transcriptRows, row)
	m.claudeActiveToolRows[id] = len(m.transcriptRows) - 1
	m.transcriptBar.SetRows(m.transcriptRows)
	return m
}

// handleClaudeToolResult records each tool_result block in the "user"-role
// message, reloads the editor if the note was touched, and persists the
// transcript to disk.
func (m appModel) handleClaudeToolResult(data []byte) (appModel, tea.Cmd) {
	var msg claude.ToolResultMessage
	if err := json.Unmarshal(data, &msg); err != nil {
		return m, nil
	}

	for _, block := range msg.Message.Content {
		if block.Type != "tool_result" {
			continue
		}

		toolName := ""
		if idx, ok := m.claudeActiveToolRows[block.ToolUseID]; ok {
			toolName = m.transcriptRows[idx].Tool
		}

		output := extractClaudeToolText(block.Content)
		if output == "" && msg.ToolUseResult.Stdout != "" {
			output = msg.ToolUseResult.Stdout
		}

		var content json.RawMessage
		switch toolName {
		case transcript.ToolRead, transcript.ToolGrep, transcript.ToolGlob:
			lines := strings.Split(output, "\n")
			if len(lines) == 1 && lines[0] == "" {
				lines = []string{}
			}
			if len(lines) > 20 {
				lines = lines[:20]
			}
			content = transcript.EncodeAgentPreviewResult(lines)
		default:
			if len(output) > 500 {
				output = output[:500] + "…"
			}
			content = transcript.EncodeAgentOutputResult(output)
		}

		m.nextToolID++
		m.transcriptRows = append(m.transcriptRows, transcript.Row{
			Num:     m.nextToolID,
			Role:    transcript.RoleTool,
			Type:    transcript.TypeToolResult,
			Tool:    toolName,
			ToolID:  block.ToolUseID,
			Content: content,
		})
		m.transcriptBar.SetRows(m.transcriptRows)

		// If Claude edited or wrote the note file, reload the editor from
		// disk so it reflects Claude's changes. writeNote() below restores
		// the transcript section if Claude's write inadvertently wiped it.
		if (toolName == transcript.ToolEdit || toolName == transcript.ToolWrite) && m.claudeToolTouchedNoteFile(block.ToolUseID) {
			m.claudeNoteEditedInRun = true
			m.claudeNoteSnapshotHash = "" // force re-injection on next run
			m.reloadNoteFromDisk()
		}
	}

	_ = m.writeNote()
	return m, m.ag.Indicator.StaleCmd()
}

// handleClaudeResult processes the result event (end of turn), sends a
// follow-up in note mode if the model didn't touch the note, and persists
// the transcript.
func (m appModel) handleClaudeResult(_ []byte) (appModel, tea.Cmd) {
	m.claudeRunActive = false
	m.ag.Indicator.Set("")

	if m.mode == modeNote && !m.claudeNoteEditedInRun && !m.claudeFollowUpSent && m.claudeProc != nil {
		followUp := fmt.Sprintf(
			"Before finishing: does the note at %s need to be updated with anything "+
				"important from this conversation? If so, use your Edit tool to update it. "+
				"If nothing needs recording, reply with only \"Nothing to record.\"",
			m.filepath)
		_ = m.claudeProc.SendPrompt(claude.BuildPrompt(followUp, "", ""))
		m.claudeFollowUpSent = true
		m.claudeRunActive = true // the follow-up is itself a new run
	}

	_ = m.writeNote()
	return m, m.ag.Indicator.StaleCmd()
}

// respawnClaudeOpts closes the current Claude process and opens a new one
// using the current model state (llmCfg + agentMode). Used after agent mode
// changes, model switches, and as the ESC-abort fallback (Claude has no
// documented raw-JSON interrupt message, so aborting means closing and
// reopening with the same deterministic --session-id, which resumes the
// conversation).
func (m appModel) respawnClaudeOpts() (appModel, tea.Cmd) {
	if m.claudeProc != nil {
		_ = m.claudeProc.Close()
		m.claudeProc = nil
	}
	m.claudeRunActive = false
	m.claudeNoteSnapshotHash = ""
	m.claudeHistoryInjected = false

	opts := m.claudeOpts()
	proc, err := claude.Open(opts)
	if err != nil {
		m.ag.Indicator.SetError("harness: " + err.Error())
		return m, m.ag.Indicator.StaleCmd()
	}
	m.claudeProc = proc
	return m, tea.Batch(m.waitForClaudeOutput(), m.ag.Indicator.StaleCmd())
}

// abortClaudeRun stops an in-flight Claude turn. See respawnClaudeOpts for
// why this is a full respawn rather than an in-place interrupt.
func (m appModel) abortClaudeRun() (appModel, tea.Cmd) {
	return m.respawnClaudeOpts()
}

// claudeOpts builds the Claude subprocess options from the current model
// state. SessionID is deterministic per note path, so --session-id creates
// or resumes the same conversation across respawns and across Aunic
// relaunches, matching Pi's session-continuity contract.
func (m appModel) claudeOpts() claude.Opts {
	absPath, _ := filepath.Abs(m.filepath)
	return claude.Opts{
		Binary:       "claude",
		ModelID:      m.llmCfg.Model,
		Tools:        claudeToolsForAgentMode(m.agentMode),
		SessionID:    claudeSessionIDForPath(absPath),
		Cwd:          filepath.Dir(absPath),
		SystemPrompt: prompts.ClaudeSystem,
	}
}

// injectClaudeSnapshotIfStale returns a <note-context>-tagged block for the
// current marker-filtered note snapshot, or an empty string if the snapshot
// hasn't changed since the last injection (same fingerprinting scheme as
// Pi's injectSnapshotIfStale). newHash is always valid and should be stored
// on the model regardless of whether noteCtx is empty.
func (m appModel) injectClaudeSnapshotIfStale(parsed markers.Parse, absNotePath string) (noteCtx, newHash string) {
	snap := parsed.BuildSnapshot()
	key := snapshotFingerprint(snap)
	if m.claudeNoteSnapshotHash == key {
		return "", key
	}
	return claude.BuildNoteContext(absNotePath, snap), key
}

// claudeToolTouchedNoteFile returns true when the tool call associated with
// toolUseID targeted the note file. Used to detect when Claude has edited
// the note.
func (m appModel) claudeToolTouchedNoteFile(toolUseID string) bool {
	idx, ok := m.claudeActiveToolRows[toolUseID]
	if !ok {
		return false
	}
	return m.rowTargetsNoteFile(idx)
}

// extractClaudeToolText extracts the text of a tool_result's content field,
// which Claude Code emits as either a plain string or an array of typed
// content blocks.
func extractClaudeToolText(content any) string {
	switch v := content.(type) {
	case string:
		return v
	case []any:
		var sb strings.Builder
		wrote := false
		for _, item := range v {
			blockMap, ok := item.(map[string]any)
			if !ok {
				continue
			}
			if t, _ := blockMap["type"].(string); t != "text" {
				continue
			}
			text, _ := blockMap["text"].(string)
			if wrote {
				sb.WriteByte('\n')
			}
			sb.WriteString(text)
			wrote = true
		}
		return sb.String()
	}
	return ""
}

// claudeSessionIDForPath returns a stable, deterministic UUID-shaped session
// ID derived from the note's absolute path via sha256 — Claude requires
// --session-id to be a valid UUID, unlike Pi's bare 16-hex-char ID. The
// version/variant bits are set to look like a standard v4 UUID in case
// Claude's validation checks RFC 4122 structure rather than just the
// dashed-hex shape (unconfirmed live; see plan Step 0.5).
func claudeSessionIDForPath(absPath string) string {
	h := sha256.Sum256([]byte(absPath))
	b := h[:16]
	b[6] = (b[6] & 0x0f) | 0x40 // version 4
	b[8] = (b[8] & 0x3f) | 0x80 // variant 10xx
	hexStr := hex.EncodeToString(b)
	return fmt.Sprintf("%s-%s-%s-%s-%s", hexStr[0:8], hexStr[8:12], hexStr[12:16], hexStr[16:20], hexStr[20:32])
}

// claudeToolsForAgentMode returns the tool list for the given agent mode.
// "off" → empty slice (--tools ""); "read" → read-only subset (Glob covers
// what Pi calls "find" — Claude has no separate find tool); "work" → nil
// (no --tools flag, Claude's own default built-in set).
func claudeToolsForAgentMode(mode string) []string {
	switch mode {
	case agentModeOff:
		return []string{}
	case agentModeRead:
		return []string{"Read", "Grep", "Glob"}
	default:
		return nil
	}
}
