package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/ejumper/aunic/agent"
	"github.com/ejumper/aunic/editor"
	claude "github.com/ejumper/aunic/harness/claude"
	pi "github.com/ejumper/aunic/harness/pi"
	"github.com/ejumper/aunic/llm"
	"github.com/ejumper/aunic/markers"
	"github.com/ejumper/aunic/todos"
	"github.com/ejumper/aunic/transcript"
	"github.com/ejumper/aunic/voice"
	"github.com/ejumper/aunic/web"

	tea "github.com/charmbracelet/bubbletea"
)

type appModel struct {
	editor   editor.Model
	ag       agent.Pane
	filepath string
	width    int
	height   int
	ready    bool

	savedValue string

	// promptFocus is true when the prompt box (not the file editor) has
	// keyboard focus.
	promptFocus bool

	// agentH tracks the last computed agent pane height so we can detect when
	// it changes and resize the editor accordingly.
	agentH int

	// editorH is the editor's current allocated height, used for mouse Y routing.
	editorH int

	// Exit-confirmation dialog state.
	showExitDialog bool
	dialogFocus    int // 0=save, 1=exit, 2=cancel

	// findMode is true when the find bar is active in the agent pane.
	findMode bool

	// gotoMode is true when the goto bar is active in the agent pane.
	gotoMode bool

	// webMode is true when the @web bar has keyboard focus. The bar can be
	// open without being focused (user clicked the editor while the web
	// pager was open — the bar stays rendered, but keys go to the editor).
	webMode bool

	// webOpen is true while the @web bar is rendered. It only flips to false
	// when the bar is fully closed (WebClosedMsg or fetch error).
	webOpen bool

	// webResizing is true while the user is dragging the agent pane's top
	// border to resize the web pager.
	webResizing bool

	llmCfg llm.Config

	// modelMode is true when the model picker bar is open.
	modelMode bool

	// cmdMode is true when the command picker bar is open.
	cmdMode bool

	// webQueryMode is true when the web search query input bar is open.
	webQueryMode bool

	// todoMode is true when the /todo authoring modal is open.
	todoMode bool

	// transcriptBar is the top-of-screen area showing parsed transcript rows
	// from the note file. transcriptRows is the canonical in-memory list.
	transcriptBar  agent.TranscriptBar
	transcriptRows []transcript.Row
	transcriptH    int
	nextToolID     int // monotonic counter for new tool_id values

	// transcriptFocus is true when keyboard events route to the transcript bar.
	// prevFocusWasPrompt records which focus state to restore on exit.
	transcriptFocus    bool
	prevFocusWasPrompt bool

	// lastWebQuery is the query string of the in-flight @web search. Used to
	// record the search in the transcript when WebSearchDoneMsg arrives.
	lastWebQuery string

	// mode is "note" (default) or "chat". In chat mode the model ends runs with
	// a plain-text reply (recorded as a transcript message row) instead of a
	// note_edit/note_write tool call.
	mode string

	// agentMode is "off" (default), "read", or "work". Controls which filesystem
	// tools are included in the model's tool list for the run.
	agentMode string

	// pendingImages holds raw image bytes (PNG or JPEG) pasted from the clipboard
	// via ctrl+v. They are cleared when a run starts and sent as multimodal
	// content parts in the user message.
	pendingImages [][]byte

	// todos is the persistent todo list parsed from the "## Todos" section of
	// the note file. Source of truth is the file; this slice is rewritten via
	// writeNote() after every todo_write / todo_done.
	todos []todos.Todo

	// homeDir and cwd are cached at startup so renderTitleBar/formatTitlePath
	// don't issue os.UserHomeDir / os.Getwd syscalls on every render frame.
	// Empty string means the lookup failed at startup; callers fall back to the
	// absolute path in that case (same behavior as before caching).
	homeDir string
	cwd     string

	// Marker-scan cache. refreshMarkerHighlight is wired into every keystroke
	// path; markers.Scan is O(buffer-size). markerHashLen + markerHash form a
	// content fingerprint — when both match the previous call, the scan is
	// skipped. Any textual change perturbs at least one of these, so staleness
	// is not possible.
	markerHashLen int
	markerHash    uint64
	markerCached  bool

	// Voice I/O state.
	voiceEnabled     bool          // true when 🔈 is active
	voicePipeCh      <-chan string // receives lines from the STT pipe; nil when off
	voicePipeRelease func()        // closes the pipe + clears the symlink; nil when off

	// Pi harness state.
	piProc           *pi.Process    // nil when harness is not Pi or Pi failed to start
	piRunActive      bool           // true while Pi is processing a prompt
	noteSnapshotHash string         // fingerprint of the last snapshot injected into Pi
	noteEditedInRun  bool           // true if Pi touched the note file during this run
	piFollowUpSent   bool           // true if a follow-up was already sent this agent cycle
	pendingWebCtx    string         // formatted web search results; prepended on next run (shared across harnesses)
	inProgressRow    int            // index into transcriptRows for the streaming assistant row; -1 if none
	activeToolRows   map[string]int // toolCallId → transcriptRows index

	// Claude harness state.
	claudeProc             *claude.Process            // nil when harness is not Claude or Claude failed to start
	claudeRunActive        bool                       // true while Claude is processing a prompt
	claudeNoteSnapshotHash string                     // fingerprint of the last snapshot injected into Claude
	claudeNoteEditedInRun  bool                       // true if Claude touched the note file during this run
	claudeFollowUpSent     bool                       // true if a follow-up was already sent this agent cycle
	claudeInProgressRow    int                        // index into transcriptRows for the streaming assistant row; -1 if none
	claudeActiveToolRows   map[string]int             // toolUseId → transcriptRows index
	claudeHistoryInjected  bool                       // true once the cold-start transcript recap has been sent for this process instance
	claudeToolCallBufs     map[int]*claudeToolCallBuf // content-block index → in-progress tool_use argument buffer

	// Task overlay state.
	taskOverlay taskOverlayState
}

func newApp(fp, content string, cfg llm.Config) appModel {
	// Strip any per-file state line (backward compat — old files embed state).
	content, perFileState, hadFileState := transcript.ExtractState(content)
	noteBody, txArea := transcript.Split(content)
	tableArea, todosArea := transcript.SplitArea(txArea)
	rows, _ := transcript.Parse(tableArea)
	todoList := todos.Parse(todosArea)

	// Load global state. When the global file doesn't exist yet, seed it from
	// the per-file state line (one-time migration) so settings carry over.
	globalState, _ := transcript.LoadGlobalState()
	var savedState transcript.State
	if globalState == (transcript.State{}) {
		if hadFileState {
			savedState = perFileState
			_ = transcript.SaveGlobalState(savedState)
		}
	} else {
		savedState = globalState
	}

	// Apply persisted state with validation. Unknown values fall back to
	// defaults silently.
	mode := "note"
	if savedState.Mode == "chat" || savedState.Mode == "note" {
		mode = savedState.Mode
	}
	agentMode := "off"
	if savedState.Agent == "read" || savedState.Agent == "work" || savedState.Agent == "off" {
		agentMode = savedState.Agent
	}
	appliedCfg := cfg
	if savedState.Model != "" {
		if slash := strings.Index(savedState.Model, "/"); slash > 0 {
			pk, mk := savedState.Model[:slash], savedState.Model[slash+1:]
			if c, err := llm.ConfigForModel(pk, mk); err == nil {
				appliedCfg = c
			}
		}
	}

	home, _ := os.UserHomeDir()
	wd, _ := os.Getwd()
	m := appModel{
		editor:               editor.New(fp, noteBody),
		filepath:             fp,
		savedValue:           noteBody,
		llmCfg:               appliedCfg,
		transcriptRows:       rows,
		todos:                todoList,
		mode:                 mode,
		agentMode:            agentMode,
		homeDir:              home,
		cwd:                  wd,
		inProgressRow:        -1,
		activeToolRows:       make(map[string]int),
		claudeInProgressRow:  -1,
		claudeActiveToolRows: make(map[string]int),
	}
	// nextToolID seeded one past the highest existing row number so newly
	// appended rows keep ordering monotonic.
	for _, r := range rows {
		if r.Num >= m.nextToolID {
			m.nextToolID = r.Num + 1
		}
	}
	m.transcriptBar = agent.NewTranscriptBar()
	m.transcriptBar.SetRows(rows)
	m.transcriptBar.SetTodos(todoList)
	switch savedState.Transcript {
	case "open:partial":
		m.transcriptBar.SetCollapsed(false)
		m.transcriptBar.SetFullHeight(false)
	case "open:full":
		m.transcriptBar.SetCollapsed(false)
		m.transcriptBar.SetFullHeight(true)
	default: // "closed", "", or anything unrecognized
		m.transcriptBar.SetCollapsed(true)
		m.transcriptBar.SetFullHeight(false)
	}
	m.transcriptH = m.transcriptBar.Height()
	// ag is sized in the first WindowSizeMsg; start with a zero-width pane so
	// Height() still returns a valid value before the terminal size is known.
	m.ag = agent.NewPane(80)
	m.agentH = m.ag.Height()
	switch {
	case appliedCfg.Err() != "":
		m.ag.Indicator.SetError("config error: " + appliedCfg.Err())
	case appliedCfg.ModelName != "":
		m.ag.Indicator.Set(filepath.Base(fp) + " loaded · " + appliedCfg.ModelName)
	default:
		m.ag.Indicator.Set(filepath.Base(fp) + " loaded")
	}

	// Populate model button label and valid-names map from the config file.
	if appliedCfg.ModelName != "" {
		m.ag.SetModelLabel(appliedCfg.ModelName)
	}
	m.ag.SetAgentLabel("agent: " + m.agentMode)
	m.ag.SetModeLabel("mode: " + m.mode)
	names := make(map[string]bool)
	for _, e := range llm.AllModels() {
		names[strings.ToLower(e.ModelName)] = true
	}
	m.ag.SetModelNames(names)
	m.refreshMarkerHighlight()

	// Voice: restore persisted on/off state and open the pipe if enabled.
	if savedState.Voice == "on" {
		ch, release, err := voice.OpenPipe(os.Getpid())
		if err == nil {
			m.voiceEnabled = true
			m.voicePipeCh = ch
			m.voicePipeRelease = release
			m.ag.Buttons.VoiceLabel = "🔈"
		}
	}

	// Pi harness: spawn the subprocess if the configured provider is "pi".
	if appliedCfg.Harness == "pi" {
		proc, err := pi.Open(m.piOpts())
		if err != nil {
			m.ag.Indicator.SetError("harness: " + err.Error())
		} else {
			m.piProc = proc
		}
	}

	// Claude harness: spawn the subprocess if the configured provider is "claude".
	if appliedCfg.Harness == "claude" {
		proc, err := claude.Open(m.claudeOpts())
		if err != nil {
			m.ag.Indicator.SetError("harness: " + err.Error())
		} else {
			m.claudeProc = proc
		}
	}

	return m
}

// voiceInputMsg arrives when voice-input.sh writes a line to the named pipe.
type voiceInputMsg struct{ text string }

// waitForVoiceInput returns a tea.Cmd that blocks until the next STT line
// arrives. When the pipe channel is closed (voice disabled) it returns nil.
func (m appModel) waitForVoiceInput() tea.Cmd {
	return func() tea.Msg {
		text, ok := <-m.voicePipeCh
		if !ok {
			return nil
		}
		return voiceInputMsg{text: text}
	}
}

func (m appModel) Init() tea.Cmd {
	cmds := []tea.Cmd{m.editor.Init(), m.ag.Indicator.StaleCmd()}
	if m.voicePipeCh != nil {
		cmds = append(cmds, m.waitForVoiceInput())
	}
	if m.piProc != nil {
		cmds = append(cmds, m.waitForPiOutput(), m.piStateCheckCmd())
	}
	if m.claudeProc != nil {
		cmds = append(cmds, m.waitForClaudeOutput())
	}
	return tea.Batch(cmds...)
}

// clearInsertHighlight removes any active insert highlight.
func (m *appModel) clearInsertHighlight() {
	m.editor.SetInsertHighlight(nil)
}

// refreshMarkerHighlight recomputes the marker syntax-highlight spans from the
// current editor content and pushes them to the editor overlay. Called after
// any operation that may change the note body.
//
// The scan is skipped when the buffer's (length, fnv64-hash) pair matches the
// previous call. Any single-byte change perturbs the hash, so this only avoids
// the redundant scan when the buffer is byte-identical — staleness can't slip
// through. Worth caching because this runs on every keystroke.
func (m *appModel) refreshMarkerHighlight() {
	v := m.editor.Value()
	h := fnv64(v)
	if m.markerCached && m.markerHashLen == len(v) && m.markerHash == h {
		return
	}
	m.markerHashLen = len(v)
	m.markerHash = h
	m.markerCached = true

	p := markers.Scan(v)
	bg, ul := p.HighlightRanges()
	bgSpans := make([]editor.MarkerSpan, len(bg))
	for i, r := range bg {
		bgSpans[i] = editor.MarkerSpan{Start: r.Start, End: r.End, Color: r.Color}
	}
	ulSpans := make([]editor.MarkerSpan, len(ul))
	for i, r := range ul {
		ulSpans[i] = editor.MarkerSpan{Start: r.Start, End: r.End, Color: r.Color}
	}
	m.editor.SetMarkerHighlight(bgSpans, ulSpans)
}

// fnv64 is the standard FNV-1a 64-bit hash. Used as a buffer fingerprint by
// refreshMarkerHighlight; inlined to avoid a hash/fnv import + allocation.
func fnv64(s string) uint64 {
	const (
		offset64 = 14695981039346656037
		prime64  = 1099511628211
	)
	h := uint64(offset64)
	for i := 0; i < len(s); i++ {
		h ^= uint64(s[i])
		h *= prime64
	}
	return h
}

// writeNote serializes the editor body + transcript rows to disk. State is
// no longer embedded in the file; it is written to the global state file
// alongside every save so the settings are shared across all open files.
func (m *appModel) writeNote() error {
	full := transcript.Join(m.editor.Value(), m.transcriptRows, todos.Render(m.todos))
	if err := os.WriteFile(m.filepath, []byte(full), 0644); err != nil {
		return err
	}
	_ = transcript.SaveGlobalState(m.currentState())
	if m.voiceEnabled {
		_ = voice.ClaimSymlink(os.Getpid())
	}
	return nil
}

// currentState gathers the persistent UI state for serialization. Transcript
// visibility collapses (collapsed=true, fullHeight=*) into "closed" since
// IsFullHeight is observable-false while collapsed.
func (m *appModel) currentState() transcript.State {
	transcriptVis := "closed"
	if !m.transcriptBar.IsCollapsed() {
		if m.transcriptBar.IsFullHeight() {
			transcriptVis = "open:full"
		} else {
			transcriptVis = "open:partial"
		}
	}
	model := ""
	if m.llmCfg.Harness != "" && m.llmCfg.ModelKey != "" {
		model = m.llmCfg.Harness + "/" + m.llmCfg.ModelKey
	}
	voice := "off"
	if m.voiceEnabled {
		voice = "on"
	}
	return transcript.State{
		Mode:       m.mode,
		Agent:      m.agentMode,
		Model:      model,
		Transcript: transcriptVis,
		Voice:      voice,
	}
}

// appendTranscriptPair adds a (tool_call, tool_result) pair, refreshes the bar,
// writes the file, and triggers a layout resize. Returns any combined tea.Cmd.
func (m *appModel) appendTranscriptPair(tool string, callContent, resultContent []byte) tea.Cmd {
	toolID := fmt.Sprintf("call_%d", m.nextToolID)
	m.nextToolID++
	m.transcriptRows = append(m.transcriptRows,
		transcript.Row{Num: m.nextToolID, Role: transcript.RoleAssistant, Type: transcript.TypeToolCall, Tool: tool, ToolID: toolID, Content: callContent},
	)
	m.nextToolID++
	m.transcriptRows = append(m.transcriptRows,
		transcript.Row{Num: m.nextToolID, Role: transcript.RoleTool, Type: transcript.TypeToolResult, Tool: tool, ToolID: toolID, Content: resultContent},
	)
	m.transcriptBar.SetRows(m.transcriptRows)
	if err := m.writeNote(); err != nil {
		m.ag.Indicator.SetError("Transcript save failed: " + err.Error())
		return m.ag.Indicator.StaleCmd()
	}
	return m.maybeResizeEditorCmd()
}

func (m *appModel) maybeResizeEditorCmd() tea.Cmd {
	newAgentH := m.ag.Height()
	// Tell the transcript bar how tall it may grow in full-height mode:
	// terminal minus title row and separator row, minus the agent pane.
	avail := m.height - 2 - newAgentH
	if avail < 1 {
		avail = 1
	}
	m.transcriptBar.SetAvailableHeight(avail)
	newTxH := m.transcriptBar.Height()
	if newAgentH == m.agentH && newTxH == m.transcriptH {
		return nil
	}
	m.agentH = newAgentH
	m.transcriptH = newTxH
	if m.transcriptBar.IsFullHeight() {
		// Editor is hidden; the separator absorbs row 1 (the otherwise-blank row).
		m.editorH = 0
	} else {
		m.editorH = m.height - 2 - 1 - newTxH - newAgentH
		if m.editorH < 1 {
			m.editorH = 1
		}
	}
	em, cmd := m.editor.Update(tea.WindowSizeMsg{Width: m.width, Height: m.editorH})
	m.editor = em.(editor.Model)
	return cmd
}

func (m appModel) executeWrapMarker(open, close string) (tea.Model, tea.Cmd) {
	if !m.editor.WrapSelection(open, close) {
		m.ag.Indicator.SetError("Error: text must be selected in file")
		return m, m.ag.Indicator.StaleCmd()
	}
	m.refreshMarkerHighlight()
	m.clearInsertHighlight()
	if err := m.writeNote(); err != nil {
		m.ag.Indicator.SetError("write failed: " + err.Error())
		return m, m.ag.Indicator.StaleCmd()
	}
	m.savedValue = m.editor.Value()
	m.promptFocus = false
	m.ag = m.ag.SetPromptFocus(false)
	m.editor.SetFocused(true)
	return m, m.maybeResizeEditorCmd()
}

// executeClear dispatches /clear to either transcript-row clearing
// (trans/chat/tool/search) or marker-token clearing (markers / @!$% subset).
// An empty target (bare "/clear") is an intentional no-op that only shows a
// usage hint.
func (m appModel) executeClear(target string) (tea.Model, tea.Cmd) {
	if target == "" {
		m.ag.Indicator.Set("Usage: /clear <trans|chat|tool|search|markers|@!$%>")
		return m, m.ag.Indicator.StaleCmd()
	}

	if target == "markers" || isMarkerCharTarget(target) {
		return m.executeClearMarkers(target)
	}

	// /clear trans clears todos as well — they are part of the transcript area.
	if target == "trans" {
		m.todos = nil
		m.transcriptBar.SetTodos(nil)
	}

	before := len(m.transcriptRows)
	m.transcriptRows = clearTranscriptRows(m.transcriptRows, target)
	removed := before - len(m.transcriptRows)

	m.transcriptBar.SetRows(m.transcriptRows)
	if err := m.writeNote(); err != nil {
		m.ag.Indicator.SetError("clear failed: " + err.Error())
		return m, m.ag.Indicator.StaleCmd()
	}
	m.ag.Indicator.Set(fmt.Sprintf("Cleared %d transcript row(s)", removed))
	return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())
}

// isMarkerCharTarget mirrors agent.isMarkerCharSet for the app side. Kept
// local to avoid exporting an internal helper from agent.
func isMarkerCharTarget(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		switch r {
		case '@', '!', '$', '%':
		default:
			return false
		}
	}
	return true
}

// executeClearMarkers strips marker wrappers (the bracket tokens) from the
// editor body for the requested kinds. Body content is preserved. "markers"
// targets all four kinds; otherwise target is a char-subset like "@!".
func (m appModel) executeClearMarkers(target string) (tea.Model, tea.Cmd) {
	var kinds []markers.Kind
	if target == "markers" {
		kinds = []markers.Kind{markers.KindWriteScope, markers.KindExclude, markers.KindIncludeOnly, markers.KindReadOnly}
	} else {
		seen := make(map[markers.Kind]bool)
		for _, r := range target {
			var k markers.Kind
			switch r {
			case '@':
				k = markers.KindWriteScope
			case '%':
				k = markers.KindExclude
			case '!':
				k = markers.KindIncludeOnly
			case '$':
				k = markers.KindReadOnly
			default:
				continue
			}
			if !seen[k] {
				seen[k] = true
				kinds = append(kinds, k)
			}
		}
	}
	if len(kinds) == 0 {
		m.ag.Indicator.Set("Usage: /clear <trans|chat|tool|search|markers|@!$%>")
		return m, m.ag.Indicator.StaleCmd()
	}

	before := m.editor.Value()
	after := markers.StripMarkers(before, kinds...)
	if after == before {
		m.ag.Indicator.Set("No matching markers to clear")
		return m, m.ag.Indicator.StaleCmd()
	}
	m.editor.SetContent(after)
	m.refreshMarkerHighlight()
	m.clearInsertHighlight()
	if err := m.writeNote(); err != nil {
		m.ag.Indicator.SetError("clear failed: " + err.Error())
		return m, m.ag.Indicator.StaleCmd()
	}
	m.savedValue = after
	m.ag.Indicator.Set(fmt.Sprintf("Cleared %s", markerKindsLabel(kinds)))
	return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())
}

// markerKindsLabel formats a slice of marker kinds as "@>> <<@, %>> <<%, …"
// for the indicator message.
func markerKindsLabel(kinds []markers.Kind) string {
	parts := make([]string, len(kinds))
	for i, k := range kinds {
		parts[i] = k.String()
	}
	return strings.Join(parts, ", ")
}

// clearTranscriptRows returns a new slice with rows matching target removed.
// tool_call and tool_result rows are paired by ToolID — when a call is
// dropped, its result is dropped too.
func clearTranscriptRows(rows []transcript.Row, target string) []transcript.Row {
	if target == "trans" {
		return nil
	}
	dropPairID := map[string]bool{}
	out := make([]transcript.Row, 0, len(rows))
	for _, r := range rows {
		if shouldClearRow(r, target, dropPairID) {
			continue
		}
		out = append(out, r)
	}
	return out
}

func shouldClearRow(r transcript.Row, target string, dropPairID map[string]bool) bool {
	switch target {
	case "chat":
		return r.Type == transcript.TypeMessage
	case "tool":
		if r.Type == transcript.TypeToolCall &&
			r.Tool != transcript.ToolWebSearch && r.Tool != transcript.ToolWebFetch {
			dropPairID[r.ToolID] = true
			return true
		}
		if r.Type == transcript.TypeToolResult && dropPairID[r.ToolID] {
			return true
		}
	case "search":
		if r.Type == transcript.TypeToolCall &&
			(r.Tool == transcript.ToolWebSearch || r.Tool == transcript.ToolWebFetch) {
			dropPairID[r.ToolID] = true
			return true
		}
		if r.Type == transcript.TypeToolResult && dropPairID[r.ToolID] {
			return true
		}
	}
	return false
}

func (m appModel) openWebQueryBar() (tea.Model, tea.Cmd) {
	m.webQueryMode = true
	m.promptFocus = true
	m.ag = m.ag.OpenWebQueryBar()
	m.editor.SetFocused(false)
	return m, m.maybeResizeEditorCmd()
}

func (m appModel) executeWebSearch(query string, n int) (tea.Model, tea.Cmd) {
	m.promptFocus = true
	m.webMode = true
	m.webOpen = true
	m.lastWebQuery = query
	var searchCmd tea.Cmd
	m.ag, searchCmd = m.ag.OpenWeb(query, n)
	m.editor.SetFocused(false)
	m.ag.Indicator.Set("Searching…")
	return m, tea.Batch(searchCmd, m.maybeResizeEditorCmd())
}

// resizeWebTo handles a drag-motion event on the agent pane's top border,
// moving the border to the row indicated by mouseY (absolute terminal Y).
// Adjusts m.editorH and the webBar's userMaxRows so the pane re-renders with
// the new height. Clamps so the pane is at least minWebContent rows of
// content and at most ~3/4 of the terminal height.
func (m appModel) resizeWebTo(mouseY int) (tea.Model, tea.Cmd) {
	const (
		minWebContent = 4 // min webBar content rows (incl. hint)
		paneOverhead  = 3 // indicator + top border + bottom border
		minEditorH    = 5 // never shrink editor below this
	)
	// rowsNotEditor counts every row of the screen that isn't the editor
	// itself (above OR below it): title + blank above, transcript bar below.
	// Editor occupies (mouseY-1) - rowsNotEditor rows.
	rowsNotEditor := 2 + 1 + m.transcriptH // 2=title+blank, 1=separator
	newEditorH := mouseY - 1 - rowsNotEditor

	maxEditorH := m.height - rowsNotEditor - (paneOverhead + minWebContent)
	maxWebContent := 3*m.height/4 - paneOverhead
	if maxWebContent < minWebContent {
		maxWebContent = minWebContent
	}
	minEditorClamp := m.height - rowsNotEditor - (paneOverhead + maxWebContent)
	if minEditorClamp < minEditorH {
		minEditorClamp = minEditorH
	}

	if newEditorH < minEditorClamp {
		newEditorH = minEditorClamp
	}
	if newEditorH > maxEditorH {
		newEditorH = maxEditorH
	}
	if newEditorH == m.editorH {
		return m, nil
	}

	m.editorH = newEditorH
	newWebContent := m.height - rowsNotEditor - newEditorH - paneOverhead
	m.ag.SetWebUserMaxRows(newWebContent)

	em, cmd := m.editor.Update(tea.WindowSizeMsg{Width: m.width, Height: m.editorH})
	m.editor = em.(editor.Model)
	return m, cmd
}

// renderSeparator returns a full-width thin horizontal rule in ANSI color 8
// (bright black), used as the divider between the editor and transcript bar.
func renderSeparator(width int) string {
	return "\x1b[90m" + strings.Repeat("─", width) + "\x1b[0m"
}

func pluralS(n int) string {
	if n == 1 {
		return ""
	}
	return "s"
}

func (m appModel) maybeQuit() (tea.Model, tea.Cmd) {
	if m.editor.Value() != m.savedValue {
		m.showExitDialog = true
		m.dialogFocus = 0
		return m, nil
	}
	return m, tea.Quit
}

func (m appModel) handleDialogKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "left":
		if m.dialogFocus > 0 {
			m.dialogFocus--
		}
	case "right":
		if m.dialogFocus < 2 {
			m.dialogFocus++
		}
	case "enter":
		return m.executeDialog()
	case "ctrl+q":
		m.showExitDialog = false
	}
	return m, nil
}

// dialogOptionCols returns the visual [start, end) column ranges of each
// dialog option within the title bar.
func (m appModel) handleDialogClick(x int) (tea.Model, tea.Cmd) {
	_, starts, ends := dialogOptionCols(m.width)
	for i := range starts {
		if x >= starts[i] && x < ends[i] {
			m.dialogFocus = i
			return m.executeDialog()
		}
	}
	return m, nil
}

func (m appModel) handleTitleBarClick(x int) (tea.Model, tea.Cmd) {
	saveEnd, minStart, minEnd, closeStart := titleBarLayout(m.width)
	switch {
	case x >= closeStart:
		return m.maybeQuit()
	case x >= minStart && x < minEnd:
		return m, func() tea.Msg { return tea.Suspend() }
	case x < saveEnd:
		content := m.editor.Value()
		if err := m.writeNote(); err == nil {
			m.savedValue = content
			m.ag.Indicator.Set("Saved")
			return m, m.ag.Indicator.StaleCmd()
		}
	}
	return m, nil
}

func (m appModel) executeDialog() (tea.Model, tea.Cmd) {
	switch m.dialogFocus {
	case 0: // save
		_ = m.writeNote()
		return m, tea.Quit
	case 1: // exit without saving
		return m, tea.Quit
	case 2: // cancel
		m.showExitDialog = false
	}
	return m, nil
}

func (m appModel) View() string {
	if !m.ready {
		return m.editor.View()
	}

	unsaved := m.editor.Value() != m.savedValue
	parts := []string{
		renderTitleBar(m.width, m.filepath, m.homeDir, m.cwd, unsaved, m.showExitDialog, m.dialogFocus),
	}
	if m.transcriptBar.IsFullHeight() {
		// Full-height mode: editor is hidden. The separator sits in the row
		// directly below the title bar (taking the place of the usual blank
		// row), and the transcript fills everything down to the agent pane.
		parts = append(parts, renderSeparator(m.width))
	} else {
		parts = append(parts, "")
		parts = append(parts, m.editor.View())
		parts = append(parts, renderSeparator(m.width))
	}
	if m.taskOverlay.open {
		parts = append(parts, m.viewTaskOverlay(m.width, m.transcriptH)...)
	} else {
		parts = append(parts, m.transcriptBar.View(m.width)...)
	}
	parts = append(parts, m.ag.View())
	return strings.Join(parts, "\n")
}

// ── Model runs ────────────────────────────────────────────────────────────────

// buildModelItems converts llm.AllModels() into the agent.ModelItem slice that
// the model picker consumes.
func buildModelItems() []agent.ModelItem {
	entries := llm.AllModels()
	items := make([]agent.ModelItem, len(entries))
	for i, e := range entries {
		items[i] = agent.ModelItem{
			HarnessKey: e.HarnessKey,
			ModelKey:   e.ModelKey,
			Name:       e.ModelName,
		}
	}
	return items
}

// attachFileMsg is delivered by openFilePickerCmd when the user selects a file.
type attachFileMsg struct{ Path string }

// openFilePickerCmd runs zenity --file-selection in a goroutine and delivers
// attachFileMsg with the selected path, or nil if cancelled.
func openFilePickerCmd() tea.Cmd {
	return func() tea.Msg {
		out, err := exec.Command("zenity", "--file-selection").Output()
		if err != nil {
			return nil
		}
		path := strings.TrimSpace(string(out))
		if path == "" {
			return nil
		}
		return attachFileMsg{Path: path}
	}
}

// recordSearchInTranscript appends a tool_call/tool_result pair for a web
// search to the transcript and persists the note.
func (m *appModel) recordSearchInTranscript(query string, results []web.Result) tea.Cmd {
	if query == "" {
		return nil
	}
	hits := make([]transcript.SearchResultHit, 0, len(results))
	for _, r := range results {
		hits = append(hits, transcript.SearchResultHit{
			Title:   r.Title,
			URL:     r.URL,
			Domain:  r.Domain,
			Snippet: r.Abstract,
		})
	}
	return m.appendTranscriptPair(
		transcript.ToolWebSearch,
		transcript.EncodeSearchCall(query),
		transcript.EncodeSearchResult(hits),
	)
}

// recordRunnerToolInTranscript handles model-side web_search / web_fetch tool
// calls emitted by the runner. It decodes the runner's raw JSON back into
// web.Result / web.Page values and routes through the same record helpers used
// by the user-driven @web path. Returns nil for tools we don't persist
// (note_edit / note_write are applied to the editor buffer instead).
// the full page body is intentionally dropped to keep the transcript compact.
func (m *appModel) recordFetchInTranscript(page web.Page) tea.Cmd {
	if page.URL == "" {
		return nil
	}
	snippet := transcript.Snippet(page.Markdown)
	return m.appendTranscriptPair(
		transcript.ToolWebFetch,
		transcript.EncodeFetchCall(page.URL),
		transcript.EncodeFetchResult(page.Title, page.URL, snippet),
	)
}

// removeTranscriptEntry drops a transcript entry by row number. When hitIdx
// is < 0 the entire (tool_call, tool_result) pair is removed. When hitIdx >= 0
// and the row's tool_result is a web_search, only that single hit is dropped
// from the search results content. Chat message rows (Type=message) delete only
// themselves — they are not paired.
func removeTranscriptEntry(rows []transcript.Row, rowNum, hitIdx int) []transcript.Row {
	// Chat messages: drop just the single row.
	for i, r := range rows {
		if r.Num == rowNum && r.Type == transcript.TypeMessage {
			out := make([]transcript.Row, 0, len(rows)-1)
			out = append(out, rows[:i]...)
			out = append(out, rows[i+1:]...)
			return out
		}
	}
	// Find the target tool_call and its paired tool_result.
	var callIdx, resultIdx int = -1, -1
	var toolID, tool string
	for i, r := range rows {
		if r.Num == rowNum && r.Type == transcript.TypeToolCall {
			callIdx = i
			toolID = r.ToolID
			tool = r.Tool
			break
		}
	}
	if callIdx < 0 {
		return rows
	}
	for i, r := range rows {
		if r.ToolID == toolID && r.Type == transcript.TypeToolResult {
			resultIdx = i
			break
		}
	}
	if hitIdx >= 0 && tool == transcript.ToolWebSearch && resultIdx >= 0 {
		hits, err := transcript.DecodeSearchResult(rows[resultIdx].Content)
		if err == nil && hitIdx < len(hits) {
			hits = append(hits[:hitIdx], hits[hitIdx+1:]...)
			rows[resultIdx].Content = transcript.EncodeSearchResult(hits)
			return rows
		}
	}
	// Delete the whole pair. Remove resultIdx first so callIdx stays valid.
	out := make([]transcript.Row, 0, len(rows))
	for i, r := range rows {
		if i == callIdx || i == resultIdx {
			continue
		}
		out = append(out, r)
	}
	return out
}
