package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	pi "github.com/ejumper/aunic/harness/pi"
	"github.com/ejumper/aunic/llm"
	"github.com/ejumper/aunic/markers"
	"github.com/ejumper/aunic/prompts"
	"github.com/ejumper/aunic/transcript"
)

// piEventMsg delivers one raw JSON event line from Pi's stdout.
type piEventMsg struct{ data []byte }

// piDeadMsg is emitted when the Pi subprocess exits unexpectedly.
type piDeadMsg struct{}

// knownHarnesses is the set of harness keys with a working Aunic-side
// dispatch implementation. aunic.json can list a harness Aunic doesn't
// (yet) know how to run — selecting one of its models is a configuration
// error surfaced via the indicator, not a crash.
var knownHarnesses = map[string]bool{
	"pi":     true,
	"claude": true,
}

// switchToModel applies cfg as the active model: updates the label/indicator,
// persists the note, and respawns the harness subprocess. Returns an error
// string (empty on success) for the caller to surface if cfg's harness isn't
// one Aunic knows how to run.
func (m appModel) switchToModel(cfg llm.Config) (appModel, tea.Cmd, string) {
	if !knownHarnesses[cfg.Harness] {
		return m, nil, fmt.Sprintf("unsupported harness: %q", cfg.Harness)
	}
	m.ag.SetModelLabel(cfg.ModelName)
	m.ag.Indicator.Set("Model: " + cfg.ModelName)
	m.saveNote()
	m.llmCfg = cfg
	m, cmd := m.respawnActiveHarness()
	return m, cmd, ""
}

// startRun dispatches a submitted prompt to whichever harness is currently
// configured.
func (m appModel) startRun(prompt string) (appModel, tea.Cmd) {
	switch m.llmCfg.Harness {
	case "claude":
		return m.startClaudeRun(prompt)
	default:
		return m.startPiRun(prompt)
	}
}

// respawnActiveHarness respawns whichever harness subprocess is currently
// configured, after an agent-mode or model change requiring a fresh process.
// Preserves the existing indicator-refresh behavior even when no respawn is
// needed (e.g. no harness running yet).
func (m appModel) respawnActiveHarness() (appModel, tea.Cmd) {
	switch m.llmCfg.Harness {
	case "pi":
		if m.piProc != nil {
			return m.respawnPiOpts()
		}
	case "claude":
		if m.claudeProc != nil {
			return m.respawnClaudeOpts()
		}
	}
	return m, m.ag.Indicator.StaleCmd()
}

// waitForPiOutput returns a tea.Cmd that blocks until the next JSON event
// arrives on the Pi output channel. Must be re-queued after every event.
func (m appModel) waitForPiOutput() tea.Cmd {
	return func() tea.Msg {
		data, ok := <-m.piProc.Output()
		if !ok {
			return piDeadMsg{}
		}
		return piEventMsg{data: data}
	}
}

// piStateCheckCmd fires a get_state request to Pi as a one-shot tea.Cmd.
// The response arrives asynchronously via waitForPiOutput.
func (m appModel) piStateCheckCmd() tea.Cmd {
	return func() tea.Msg {
		_ = m.piProc.GetState()
		return nil
	}
}

// startPiRun validates the note, records the user message, injects the
// note snapshot if stale, prepends any pending web context, and dispatches
// the prompt to Pi.
func (m appModel) startPiRun(prompt string) (appModel, tea.Cmd) {
	if m.piRunActive {
		return m, nil
	}
	if m.piProc == nil {
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

	// 2. Record user message in transcript before the run.
	m.nextToolID++
	m.transcriptRows = append(m.transcriptRows, transcript.Row{
		Num:     m.nextToolID,
		Role:    transcript.RoleUser,
		Type:    transcript.TypeMessage,
		Content: transcript.EncodeMessage(prompt),
	})
	m.transcriptBar.SetRows(m.transcriptRows)

	// 3. Auto-save so the file is current before Pi opens it.
	if err := m.writeNote(); err != nil {
		m.ag.Indicator.SetError("save failed: " + err.Error())
		return m, m.ag.Indicator.StaleCmd()
	}

	// 4. Inject the note snapshot into Pi's context if the content changed.
	absPath, _ := filepath.Abs(m.filepath)
	newHash, injectErr := m.injectSnapshotIfStale(parsed, absPath)
	if injectErr != nil {
		m.ag.Indicator.SetError("snapshot: " + injectErr.Error())
		return m, m.ag.Indicator.StaleCmd()
	}
	m.piNoteSnapshotHash = newHash

	// 5. Prepend pending web search results, then clear the buffer.
	fullPrompt := prompt
	if m.pendingWebCtx != "" {
		fullPrompt = m.pendingWebCtx + "\n\n" + prompt
		m.pendingWebCtx = ""
	}

	// 6. Reset per-run state.
	m.piRunActive = true
	m.piNoteEditedInRun = false
	m.piFollowUpSent = false
	m.piInProgressRow = -1
	m.piActiveToolRows = make(map[string]int)

	// 7. Dispatch prompt to Pi.
	_ = m.piProc.SendPrompt(fullPrompt)
	m.ag.Indicator.Set("running…")
	return m, m.ag.Indicator.StaleCmd()
}

// handlePiEvent dispatches a raw JSON event line from Pi's stdout.
// Callers must re-queue waitForPiOutput after this returns.
func (m appModel) handlePiEvent(data []byte) (appModel, tea.Cmd) {
	var base struct {
		Type string `json:"type"`
	}
	if err := json.Unmarshal(data, &base); err != nil {
		return m, nil
	}

	switch base.Type {
	case "agent_start":
		m.piRunActive = true
		m.piNoteEditedInRun = false
		m.piInProgressRow = -1
		if m.piActiveToolRows == nil {
			m.piActiveToolRows = make(map[string]int)
		}
		m.ag.Indicator.Set("running…")
		return m, m.ag.Indicator.StaleCmd()

	case "message_update":
		return m.handleMessageUpdate(data)

	case "tool_execution_start":
		var ev pi.ToolExecStartEvent
		_ = json.Unmarshal(data, &ev)
		m.ag.Indicator.Set(ev.ToolName + ": running…")
		return m, m.ag.Indicator.StaleCmd()

	case "tool_execution_update":
		return m, nil

	case "tool_execution_end":
		return m.handleToolExecEnd(data)

	case "agent_end":
		return m.handleAgentEnd(data)

	case "compaction_start":
		m.ag.Indicator.Set("compacting context…")
		return m, m.ag.Indicator.StaleCmd()

	case "compaction_end":
		m.piNoteSnapshotHash = "" // force re-injection after compaction
		m.ag.Indicator.Set("compaction done")
		return m, m.ag.Indicator.StaleCmd()

	case "auto_retry_start":
		var ev pi.AutoRetryStartEvent
		_ = json.Unmarshal(data, &ev)
		m.ag.Indicator.Set(fmt.Sprintf("retry %d/%d…", ev.Attempt, ev.MaxAttempts))
		return m, m.ag.Indicator.StaleCmd()

	case "extension_ui_request":
		var ev pi.ExtensionUIRequestEvent
		_ = json.Unmarshal(data, &ev)
		if ev.Method == "setStatus" {
			if ev.StatusText != "" {
				m.ag.Indicator.Set(ev.StatusText)
				return m, m.ag.Indicator.StaleCmd()
			}
			return m, nil
		}
		// Auto-cancel dialog requests that require TUI interaction.
		if ev.Method == "select" || ev.Method == "confirm" || ev.Method == "input" || ev.Method == "editor" {
			_ = m.piProc.SendUICancel(ev.ID)
		}
		return m, nil

	case "response":
		var ev pi.RpcResponse
		_ = json.Unmarshal(data, &ev)
		if !ev.Success {
			m.ag.Indicator.SetError("pi: " + ev.Error)
			return m, m.ag.Indicator.StaleCmd()
		}
		if ev.Command == "get_state" {
			return m.handleStateResponse(data)
		}
		return m, nil

	case "extension_error":
		var ev pi.ExtensionErrorEvent
		_ = json.Unmarshal(data, &ev)
		m.ag.Indicator.SetError("pi: " + ev.Error)
		return m, m.ag.Indicator.StaleCmd()
	}

	return m, nil
}

// handleMessageUpdate processes streaming assistant message events.
func (m appModel) handleMessageUpdate(data []byte) (appModel, tea.Cmd) {
	var ev pi.MessageUpdateEvent
	if err := json.Unmarshal(data, &ev); err != nil {
		return m, nil
	}
	ae := ev.AssistantMessageEvent

	switch ae.Type {
	case "text_start":
		m.nextToolID++
		m.transcriptRows = append(m.transcriptRows, transcript.Row{
			Num:     m.nextToolID,
			Role:    transcript.RoleAssistant,
			Type:    transcript.TypeMessage,
			Content: transcript.EncodeMessage(""),
		})
		m.piInProgressRow = len(m.transcriptRows) - 1
		m.transcriptBar.SetRows(m.transcriptRows)

	case "text_delta":
		if m.piInProgressRow >= 0 && m.piInProgressRow < len(m.transcriptRows) {
			c, _ := transcript.DecodeMessage(m.transcriptRows[m.piInProgressRow].Content)
			c.Text += ae.Delta
			m.transcriptRows[m.piInProgressRow].Content = transcript.EncodeMessage(c.Text)
			m.transcriptBar.SetRows(m.transcriptRows)
		}

	case "thinking_start":
		m.ag.Indicator.Set("thinking…")
		return m, m.ag.Indicator.StaleCmd()

	case "toolcall_end":
		m.piInProgressRow = -1
		if ae.ToolCall != nil {
			m = m.appendToolCallRow(*ae.ToolCall)
		}
	}

	return m, nil
}

// appendToolCallRow maps a Pi tool call to a transcript tool_call row and
// stores the row index in piActiveToolRows for later lookup by tool result.
func (m appModel) appendToolCallRow(tc pi.ToolCallObj) appModel {
	var content json.RawMessage
	switch tc.Name {
	case "edit":
		filePath, _ := tc.Arguments["file_path"].(string)
		oldStr, _ := tc.Arguments["old_string"].(string)
		newStr, _ := tc.Arguments["new_string"].(string)
		content = transcript.EncodeAgentFileCall(filePath, oldStr, newStr)
	case "write":
		filePath, _ := tc.Arguments["file_path"].(string)
		fileContent, _ := tc.Arguments["content"].(string)
		content = transcript.EncodeAgentFileCall(filePath, "", fileContent)
	case "read":
		filePath, _ := tc.Arguments["file_path"].(string)
		content = transcript.EncodeAgentFileCall(filePath, "", "")
	case "bash":
		cmd, _ := tc.Arguments["command"].(string)
		content = transcript.EncodeAgentCmdCall(cmd)
	case "grep":
		pattern, _ := tc.Arguments["pattern"].(string)
		content = transcript.EncodeAgentPatternCall(pattern)
	case "glob":
		pattern, _ := tc.Arguments["pattern"].(string)
		content = transcript.EncodeAgentPatternCall(pattern)
	default:
		content = transcript.EncodeAgentCmdCall(fmt.Sprintf("%s(%v)", tc.Name, tc.Arguments))
	}

	m.nextToolID++
	row := transcript.Row{
		Num:     m.nextToolID,
		Role:    transcript.RoleAssistant,
		Type:    transcript.TypeToolCall,
		Tool:    mapToolName(tc.Name),
		ToolID:  tc.ID,
		Content: content,
	}
	m.transcriptRows = append(m.transcriptRows, row)
	m.piActiveToolRows[tc.ID] = len(m.transcriptRows) - 1
	m.transcriptBar.SetRows(m.transcriptRows)
	return m
}

// handleToolExecEnd records the tool result, reloads the editor if the note
// was touched, and persists the transcript to disk.
func (m appModel) handleToolExecEnd(data []byte) (appModel, tea.Cmd) {
	var ev pi.ToolExecEndEvent
	if err := json.Unmarshal(data, &ev); err != nil {
		return m, nil
	}

	output := extractToolText(ev.Result.Content)

	var content json.RawMessage
	switch ev.ToolName {
	case "read", "grep", "glob":
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
		Tool:    mapToolName(ev.ToolName),
		ToolID:  ev.ToolCallID,
		Content: content,
	})
	m.transcriptBar.SetRows(m.transcriptRows)

	// If Pi edited or wrote the note file, reload the editor from disk so the
	// editor reflects Pi's changes. writeNote() below restores the transcript
	// section if Pi's write inadvertently wiped it.
	if (ev.ToolName == "edit" || ev.ToolName == "write") && m.toolTouchedNoteFile(ev) {
		m.piNoteEditedInRun = true
		m.piNoteSnapshotHash = "" // force re-injection on next run
		m.reloadNoteFromDisk()
	}

	// Persist transcript after every tool pair. Also restores transcript section
	// if Pi's write overwrote it (writeNote re-appends from m.transcriptRows).
	m.saveNote()
	return m, m.ag.Indicator.StaleCmd()
}

// handleAgentEnd processes the agent_end event, sends a follow-up in note
// mode if the model didn't touch the note, and persists the transcript.
func (m appModel) handleAgentEnd(_ []byte) (appModel, tea.Cmd) {
	m.piRunActive = false
	m.ag.Indicator.Set("")

	if m.mode == modeNote && !m.piNoteEditedInRun && !m.piFollowUpSent && m.piProc != nil {
		followUp := fmt.Sprintf(
			"Before finishing: does the note at %s need to be updated with anything "+
				"important from this conversation? If so, use your edit tool to update it. "+
				"If nothing needs recording, reply with only \"Nothing to record.\"",
			m.filepath)
		_ = m.piProc.SendFollowUp(followUp)
		m.piFollowUpSent = true
	}

	m.saveNote()
	return m, m.ag.Indicator.StaleCmd()
}

// handleStateResponse processes the get_state response. If Pi has no history
// but Aunic has transcript rows, it synthesizes a session from those rows.
func (m appModel) handleStateResponse(data []byte) (appModel, tea.Cmd) {
	var resp struct {
		Data pi.StateData `json:"data"`
	}
	if err := json.Unmarshal(data, &resp); err != nil {
		return m, nil
	}
	if resp.Data.MessageCount == 0 && len(m.transcriptRows) > 0 {
		return m.synthesizeSessionFromTranscript()
	}
	return m, nil
}

// synthesizeSessionFromTranscript builds a Pi v3 session JSONL from the
// Aunic transcript rows and respawns Pi with the synthesized session file.
// The outer piEventMsg handler will re-queue waitForPiOutput for the new proc.
func (m appModel) synthesizeSessionFromTranscript() (appModel, tea.Cmd) {
	absPath, _ := filepath.Abs(m.filepath)
	sid := sessionIDForPath(absPath)
	dir := aunicSessionDir()
	cwd := filepath.Dir(absPath)

	backupPath, err := pi.BuildSessionFile(m.transcriptRows, cwd, dir, sid)
	if err != nil {
		m.ag.Indicator.SetError("session restore: " + err.Error())
		return m, m.ag.Indicator.StaleCmd()
	}

	if m.piProc != nil {
		_ = m.piProc.Close()
		m.piProc = nil
	}

	opts := m.piOpts()
	opts.SessionPath = backupPath
	opts.SessionID = ""
	opts.SessionDir = ""

	proc, err := pi.Open(opts)
	if err != nil {
		m.ag.Indicator.SetError("harness: " + err.Error())
		return m, m.ag.Indicator.StaleCmd()
	}
	m.piProc = proc
	m.ag.Indicator.Set("session restored")
	// Note: waitForPiOutput is added by the piEventMsg case in Update, not here.
	return m, m.ag.Indicator.StaleCmd()
}

// respawnPiOpts closes the current Pi process and opens a new one using the
// current model state (llmCfg + agentMode). Used after agent mode changes.
func (m appModel) respawnPiOpts() (appModel, tea.Cmd) {
	if m.piProc != nil {
		_ = m.piProc.Close()
		m.piProc = nil
	}
	m.piRunActive = false
	m.piNoteSnapshotHash = ""

	opts := m.piOpts()
	proc, err := pi.Open(opts)
	if err != nil {
		m.ag.Indicator.SetError("harness: " + err.Error())
		return m, m.ag.Indicator.StaleCmd()
	}
	m.piProc = proc
	return m, tea.Batch(m.waitForPiOutput(), m.ag.Indicator.StaleCmd())
}

// piOpts builds the Pi subprocess options from the current model state.
// If a backup session file exists for this note, it is preferred over the
// session-id lookup so Pi resumes the synthesized session on restart.
func (m appModel) piOpts() pi.Opts {
	absPath, _ := filepath.Abs(m.filepath)
	sid := sessionIDForPath(absPath)
	sessionDir := aunicSessionDir()

	opts := pi.Opts{
		Binary:       "pi",
		ModelID:      m.llmCfg.Model,
		Tools:        toolsForAgentMode(m.agentMode),
		Cwd:          filepath.Dir(absPath),
		SystemPrompt: buildSystemPrompt(m.filepath),
	}

	backupPath := filepath.Join(sessionDir, sid+"-backup.jsonl")
	if _, err := os.Stat(backupPath); err == nil {
		opts.SessionPath = backupPath
	} else {
		opts.SessionID = sid
		opts.SessionDir = sessionDir
	}

	return opts
}

// injectSnapshotIfStale writes a temp file containing the visible note
// snapshot (marker-filtered) and sends a bash cat command to Pi to inject
// it into the model's context. Skips the write when the fingerprint matches
// the last injection. Returns the new fingerprint string (always valid).
func (m appModel) injectSnapshotIfStale(parsed markers.Parse, absNotePath string) (newHash string, err error) {
	snap := parsed.BuildSnapshot()
	key := snapshotFingerprint(snap)
	if m.piNoteSnapshotHash == key {
		return key, nil
	}

	tmpPath := snapshotTempPath(absNotePath)
	if err := os.WriteFile(tmpPath, []byte(snap.Visible), 0600); err != nil {
		return m.piNoteSnapshotHash, err
	}
	if err := m.piProc.SendBash("cat " + tmpPath); err != nil {
		return m.piNoteSnapshotHash, err
	}
	return key, nil
}

// toolTouchedNoteFile returns true when the tool call associated with ev
// targeted the note file. Used to detect when Pi has edited the note.
func (m appModel) toolTouchedNoteFile(ev pi.ToolExecEndEvent) bool {
	idx, ok := m.piActiveToolRows[ev.ToolCallID]
	if !ok {
		return false
	}
	return m.rowTargetsNoteFile(idx)
}

// extractToolText joins all text content blocks from a Pi ToolResult.
func extractToolText(blocks []pi.ContentBlock) string {
	var sb strings.Builder
	for i, b := range blocks {
		if b.Type != "text" {
			continue
		}
		if i > 0 {
			sb.WriteByte('\n')
		}
		sb.WriteString(b.Text)
	}
	return sb.String()
}

// mapToolName converts Pi's lowercase tool name to the Aunic transcript constant.
func mapToolName(name string) string {
	switch name {
	case "edit":
		return transcript.ToolEdit
	case "write":
		return transcript.ToolWrite
	case "read":
		return transcript.ToolRead
	case "bash":
		return transcript.ToolBash
	case "grep":
		return transcript.ToolGrep
	case "glob":
		return transcript.ToolGlob
	}
	return name
}

// sessionIDForPath returns a stable 16-hex-char session ID derived from the
// note's absolute path via sha256.
func sessionIDForPath(absPath string) string {
	h := sha256.Sum256([]byte(absPath))
	return hex.EncodeToString(h[:])[:16]
}

// toolsForAgentMode returns the tool list for the given agent mode.
// "off" → empty slice (--no-tools); "read" → read-only subset; "work" → nil (all tools).
func toolsForAgentMode(mode string) []string {
	switch mode {
	case agentModeOff:
		return []string{}
	case agentModeRead:
		return []string{"read", "grep", "glob", "find"}
	default:
		return nil
	}
}

// buildSystemPrompt assembles the system prompt from the embedded template,
// substituting the note path and cwd placeholders.
func buildSystemPrompt(notePath string) string {
	absPath, _ := filepath.Abs(notePath)
	cwd := filepath.Dir(absPath)
	base := prompts.PiSystem
	base = strings.ReplaceAll(base, "{{NOTE_PATH}}", absPath)
	base = strings.ReplaceAll(base, "{{CWD}}", cwd)
	return base
}

// snapshotTempPath returns a stable temp file path for the note snapshot,
// keyed by the note's absolute path so multiple open files don't collide.
