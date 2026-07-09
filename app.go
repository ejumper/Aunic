package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/atotto/clipboard"
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

func (m appModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.ready = true

		m.ag.SetWidth(msg.Width)
		m.ag.SetHeight(msg.Height)
		m.agentH = m.ag.Height()
		m.transcriptBar.SetWidth(msg.Width)
		m.transcriptBar.SetTermHeight(msg.Height)
		// availableHeight = termHeight - title (2 rows) - agent pane.
		// Caps the transcript so it never pushes the agent above the title bar.
		avail := msg.Height - 2 - m.agentH
		if avail < 1 {
			avail = 1
		}
		m.transcriptBar.SetAvailableHeight(avail)
		m.transcriptH = m.transcriptBar.Height()
		m.editorH = msg.Height - 2 - 1 - m.transcriptH - m.agentH
		if m.editorH < 1 {
			m.editorH = 1
		}

		em, cmd := m.editor.Update(tea.WindowSizeMsg{Width: msg.Width, Height: m.editorH})
		m.editor = em.(editor.Model)
		return m, cmd

	case tea.MouseMsg:
		// Title bar click (row 0).
		if msg.Y == 0 && msg.Action == tea.MouseActionPress && msg.Button == tea.MouseButtonLeft {
			if m.showExitDialog {
				return m.handleDialogClick(msg.X)
			}
			return m.handleTitleBarClick(msg.X)
		}

		// Layout bands (top → bottom):
		//   row 0:                                           title bar
		//   row 1:                                           blank (partial) OR separator (full)
		//   rows [2, 2+editorH):                             editor (partial only)
		//   row 2+editorH:                                   separator (partial)
		//   rows [txTop, txTop+transcriptH):                 transcript bar
		//   rows below:                                      agent pane
		editorTop := 2
		var txTop int
		if m.transcriptBar.IsFullHeight() {
			// Row 1 = separator; transcript begins at row 2.
			txTop = 2
		} else {
			txTop = editorTop + m.editorH + 1 // +1 for separator row
		}
		paneTop := txTop + m.transcriptH

		// Agent pane top-border resize drag (intercepts before normal routing).
		if m.webOpen {
			topBorderY := paneTop + 1
			if msg.Action == tea.MouseActionPress && msg.Button == tea.MouseButtonLeft && msg.Y == topBorderY {
				m.webResizing = true
				return m, nil
			}
			if m.webResizing {
				switch msg.Action {
				case tea.MouseActionMotion:
					return m.resizeWebTo(msg.Y)
				case tea.MouseActionRelease:
					if msg.Button == tea.MouseButtonLeft {
						m.webResizing = false
					}
					return m, nil
				}
			}
		}

		// Agent pane area.
		if msg.Y >= paneTop {
			if msg.Action == tea.MouseActionPress && msg.Button == tea.MouseButtonLeft {
				m.transcriptFocus = false
				m.transcriptBar.SetFocused(false)
				switch {
				case m.webOpen && !m.webMode:
					// Web pager visible but unfocused — refocus it.
					m.webMode = true
					m.editor.SetFocused(false)
				case !m.promptFocus && !m.findMode && !m.gotoMode && !m.webOpen:
					m.promptFocus = true
					m.ag = m.ag.SetPromptFocus(true)
					m.editor.SetFocused(false)
				}
			}
			msg.Y -= paneTop
			pane, cmd := m.ag.Update(msg)
			m.ag = pane
			return m, cmd
		}

		// Transcript bar area (between editor and pane).
		if msg.Y >= txTop && msg.Y < paneTop {
			msg.Y -= txTop
			wasFull := m.transcriptBar.IsFullHeight()
			wasCollapsed := m.transcriptBar.IsCollapsed()
			bar, cmd := m.transcriptBar.Update(msg)
			m.transcriptBar = bar
			// If [+] just promoted the bar to full-height and the editor was
			// holding focus, move focus to the now-only-visible transcript.
			if !wasFull && m.transcriptBar.IsFullHeight() && m.currentFocus() == focusEditor {
				m = m.setFocus(focusTranscript)
			}
			if wasFull != m.transcriptBar.IsFullHeight() || wasCollapsed != m.transcriptBar.IsCollapsed() {
				_ = m.writeNote()
			}
			return m, tea.Batch(cmd, m.maybeResizeEditorCmd())
		}

		// Title bar / blank line area.
		if msg.Y < 2 {
			return m, nil
		}

		// Editor area — clicking editor while a bar is open closes it.
		if msg.Action == tea.MouseActionPress && msg.Button == tea.MouseButtonLeft {
			m.transcriptFocus = false
			m.transcriptBar.SetFocused(false)
			if m.findMode {
				m.findMode = false
				m.ag = m.ag.CloseFind()
				m.editor.ClearSearch()
				m.editor.SetFocused(true)
				return m, m.maybeResizeEditorCmd()
			}
			if m.webMode {
				// Unfocus the web pager but keep it open and rendered.
				m.webMode = false
				m.editor.SetFocused(true)
				// fall through so the click positions the editor cursor.
			}
			if m.gotoMode {
				m.gotoMode = false
				m.ag = m.ag.CloseGoto()
				m.editor.SetFocused(true)
				return m, m.maybeResizeEditorCmd()
			}
			if m.promptFocus {
				m.promptFocus = false
				m.ag = m.ag.SetPromptFocus(false)
				m.editor.SetFocused(true)
			}
		}
		msg.Y -= editorTop
		prev := m.editor.Value()
		em, cmd := m.editor.Update(msg)
		m.editor = em.(editor.Model)
		if m.editor.Value() != prev {
			m.refreshMarkerHighlight()
			m.clearInsertHighlight()
		}
		return m, cmd

	case tea.KeyMsg:
		return m.handleAppKey(msg)

	case editor.IndicatorMsg:
		m.ag.Indicator.Set(string(msg))
		return m, m.ag.Indicator.StaleCmd()

	case agent.IndicatorStaleMsg:
		m.ag.Indicator.MarkStale(msg.Seq)
		return m, nil

	case agent.FindQueryMsg:
		result := m.editor.SetSearch(msg.Query, msg.CaseSensitive)
		m.ag.Indicator.Set(agent.FormatMatchCount(result.Count, result.Current))
		return m, m.ag.Indicator.StaleCmd()

	case agent.FindNavMsg:
		var result editor.SearchResultMsg
		if msg.Direction > 0 {
			result = m.editor.SearchNext()
		} else {
			result = m.editor.SearchPrev()
		}
		m.ag.Indicator.Set(agent.FormatMatchCount(result.Count, result.Current))
		return m, m.ag.Indicator.StaleCmd()

	case agent.FindReplaceMsg:
		var result editor.SearchResultMsg
		if msg.All {
			result = m.editor.ReplaceAllMatches(msg.Replacement)
		} else {
			result = m.editor.ReplaceCurrentMatch(msg.Replacement)
		}
		m.refreshMarkerHighlight()
		m.clearInsertHighlight()
		m.ag.Indicator.Set(agent.FormatMatchCount(result.Count, result.Current))
		return m, m.ag.Indicator.StaleCmd()

	case agent.FindModeMsg:
		query := m.ag.FindQuery()
		m.findMode = true
		m.ag = m.ag.OpenFind(msg.ReplaceMode, query)
		return m, m.maybeResizeEditorCmd()

	case agent.FindClosedMsg:
		m.findMode = false
		m.ag = m.ag.CloseFind()
		m.editor.ClearSearch()
		m.editor.SetFocused(true)
		m.ag.Indicator.Set(filepath.Base(m.filepath) + " loaded")
		return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())

	case agent.GotoLineMsg:
		if m.editor.GotoLine(msg.Line) {
			m.gotoMode = false
			m.ag = m.ag.CloseGoto()
			m.editor.SetFocused(true)
			m.ag.Indicator.Set(fmt.Sprintf("Jumped to line %d", msg.Line))
			return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())
		}
		m.ag.Indicator.SetError("Line doesn't exist")
		return m, nil

	case agent.GotoClosedMsg:
		m.gotoMode = false
		m.ag = m.ag.CloseGoto()
		m.editor.SetFocused(true)
		m.ag.Indicator.Set(filepath.Base(m.filepath) + " loaded")
		return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())

	case agent.WebSearchDoneMsg:
		if msg.Err != nil {
			m.webMode = false
			m.webOpen = false
			m.ag = m.ag.CloseWeb()
			m.editor.SetFocused(true)
			if errors.Is(msg.Err, web.ErrDdgrNotFound) {
				m.ag.Indicator.SetError("ddgr not found — install: github.com/jarun/ddgr")
			} else {
				m.ag.Indicator.SetError("Search error: " + msg.Err.Error())
			}
			return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())
		}
		m.ag.ApplyWebResults(msg.Results)
		count := len(msg.Results)
		m.ag.Indicator.Set(fmt.Sprintf("%d result%s — ↑↓ nav  space select  enter fetch", count, pluralS(count)))
		// Record the search in the transcript using the query we stashed when
		// the bar was opened.
		txCmd := m.recordSearchInTranscript(m.lastWebQuery, msg.Results)
		// Build a pending web context block for the next harness run.
		if m.piProc != nil || m.claudeProc != nil {
			var sb strings.Builder
			sb.WriteString(fmt.Sprintf("[Web search: %q]\n", m.lastWebQuery))
			for i, r := range msg.Results {
				if i >= 10 {
					break
				}
				fmt.Fprintf(&sb, "%d. %s <%s>\n   %s\n", i+1, r.Title, r.URL, r.Abstract)
			}
			m.pendingWebCtx = sb.String()
		}
		return m, tea.Batch(m.ag.Indicator.StaleCmd(), txCmd, m.maybeResizeEditorCmd())

	case agent.WebFetchDoneMsg:
		if msg.Err != nil {
			m.ag.ApplyWebFetchError()
			m.ag.Indicator.SetError("Fetch error: " + msg.Err.Error())
			return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())
		}
		m.ag.ApplyWebPage(msg.Page)
		m.ag.Indicator.Set(msg.Page.Title)
		if msg.SkipRecord {
			return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())
		}
		txCmd := m.recordFetchInTranscript(msg.Page)
		return m, tea.Batch(m.ag.Indicator.StaleCmd(), txCmd, m.maybeResizeEditorCmd())

	case agent.TranscriptOpenInPagerMsg:
		m.promptFocus = true
		m.webMode = true
		m.webOpen = true
		var fetchCmd tea.Cmd
		m.ag, fetchCmd = m.ag.OpenWebForURL(msg.URL)
		m.editor.SetFocused(false)
		m.ag.Indicator.Set("Fetching…")
		return m, tea.Batch(fetchCmd, m.maybeResizeEditorCmd())

	case agent.TranscriptOpenFileMsg:
		m.promptFocus = true
		m.webMode = true
		m.webOpen = true
		m.ag = m.ag.OpenWebForFile()
		m.editor.SetFocused(false)
		var markdown string
		if msg.Path != "" {
			data, err := os.ReadFile(msg.Path)
			if err != nil {
				m.webMode = false
				m.webOpen = false
				m.ag = m.ag.CloseWeb()
				m.editor.SetFocused(true)
				m.ag.Indicator.SetError("Cannot read file: " + err.Error())
				return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())
			}
			markdown = string(data)
		} else {
			markdown = msg.Content
		}
		title := msg.Title
		if title == "" {
			title = "output"
		}
		m.ag.ApplyWebPage(web.Page{Title: title, URL: msg.Path, Markdown: markdown})
		m.ag.Indicator.Set(title)
		return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())

	case agent.WebOpenBrowserMsg:
		if err := web.Open(msg.URL); err != nil {
			m.ag.Indicator.SetError("Open URL failed: " + err.Error())
			return m, m.ag.Indicator.StaleCmd()
		}
		m.ag.Indicator.Set("Opening: " + msg.URL)
		return m, m.ag.Indicator.StaleCmd()

	case agent.TranscriptOpenURLMsg:
		if err := web.Open(msg.URL); err != nil {
			m.ag.Indicator.SetError("Open URL failed: " + err.Error())
			return m, m.ag.Indicator.StaleCmd()
		}
		m.ag.Indicator.Set("Opening: " + msg.URL)
		return m, m.ag.Indicator.StaleCmd()

	case agent.TranscriptCopyMsg:
		clipboard.WriteAll(msg.Text)
		m.ag.Indicator.Set("Copied")
		return m, m.ag.Indicator.StaleCmd()

	case agent.TranscriptDeleteMsg:
		m.transcriptRows = removeTranscriptEntry(m.transcriptRows, msg.RowNum, msg.HitIdx)
		m.transcriptBar.SetRows(m.transcriptRows)
		if err := m.writeNote(); err != nil {
			m.ag.Indicator.SetError("Save failed: " + err.Error())
			return m, m.ag.Indicator.StaleCmd()
		}
		return m, m.maybeResizeEditorCmd()

	case agent.WebClosedMsg:
		m.webMode = false
		m.webOpen = false
		m.ag = m.ag.CloseWeb()
		m.editor.SetFocused(true)
		m.ag.Indicator.Set(filepath.Base(m.filepath) + " loaded")
		return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())

	case agent.WebCopiedMsg:
		m.ag.Indicator.Set("Copied")
		return m, m.ag.Indicator.StaleCmd()

	case piEventMsg:
		m2, cmd := m.handlePiEvent(msg.data)
		if m2.piProc != nil {
			return m2, tea.Batch(cmd, m2.waitForPiOutput())
		}
		return m2, cmd

	case piDeadMsg:
		m.piRunActive = false
		m.piProc = nil
		m.ag.Indicator.SetError("pi: process exited")
		return m, m.ag.Indicator.StaleCmd()

	case claudeEventMsg:
		m2, cmd := m.handleClaudeEvent(msg.data)
		if m2.claudeProc != nil {
			return m2, tea.Batch(cmd, m2.waitForClaudeOutput())
		}
		return m2, cmd

	case claudeDeadMsg:
		m.claudeRunActive = false
		m.claudeProc = nil
		m.ag.Indicator.SetError("claude: process exited")
		return m, m.ag.Indicator.StaleCmd()

	case agent.PromptSubmitMsg:
		if cmd := agent.ParseSlashCmd(msg.Content); cmd != nil {
			return m.executeSlashCmd(cmd)
		}
		if cmd := agent.FindInlineCmd(msg.Content); cmd != nil {
			return m.executeSlashCmd(cmd)
		}
		return m.startRun(msg.Content)

	case agent.CmdPickerOpenMsg:
		m.cmdMode = true
		m.promptFocus = true
		m.ag = m.ag.OpenCmdBar()
		m.editor.SetFocused(false)
		if text := m.ag.CmdBarInitialIndicator(); text != "" {
			m.ag.Indicator.Set(text)
		}
		return m, m.maybeResizeEditorCmd()

	case agent.CmdBarClosedMsg:
		m.cmdMode = false
		m.ag = m.ag.CloseCmdBar()
		m.promptFocus = true
		m.ag = m.ag.SetPromptFocus(true)
		m.editor.SetFocused(false)
		m.ag.Indicator.Set("")
		return m, m.maybeResizeEditorCmd()

	case agent.CmdBarSelectMsg:
		m.cmdMode = false
		m.ag = m.ag.CloseCmdBar()
		m.ag.Indicator.Set("")
		switch msg.ExecKind {
		case agent.CmdExecSlash:
			return m.executeSlashCmd(&agent.SlashCmdResult{Kind: msg.SlashKind})
		case agent.CmdExecPrompt:
			m.ag.PromptBox.SetValue(msg.PromptText)
			m.promptFocus = true
			m.ag = m.ag.SetPromptFocus(true)
			m.editor.SetFocused(false)
			return m, m.maybeResizeEditorCmd()
		}
		return m, nil

	case agent.WebQuerySubmitMsg:
		m.webQueryMode = false
		m.ag = m.ag.CloseWebQueryBar()
		return m.executeWebSearch(msg.Query, 10)

	case agent.WebQueryClosedMsg:
		m.webQueryMode = false
		m.ag = m.ag.CloseWebQueryBar()
		m.promptFocus = true
		m.ag = m.ag.SetPromptFocus(true)
		m.editor.SetFocused(false)
		return m, m.maybeResizeEditorCmd()

	case agent.TodoSubmitMsg:
		// Mirror normal prompt editor: an empty prompt does not send.
		if msg.Prompt == "" {
			return m, nil
		}
		m.todos = todos.AssignIDs(msg.Todos)
		m.transcriptBar.SetTodos(m.todos)
		if err := m.writeNote(); err != nil {
			m.ag.Indicator.SetError("todo write failed: " + err.Error())
		}
		m.todoMode = false
		m.ag = m.ag.CloseTodoBar()
		m.promptFocus = true
		m.ag = m.ag.SetPromptFocus(true)
		m.editor.SetFocused(false)
		fullPrompt := msg.Prompt + "\n\nTodos:\n" + strings.Join(msg.Todos, "\n")
		return m.startRun(fullPrompt)

	case agent.TodoBarClosedMsg:
		m.todoMode = false
		m.ag = m.ag.CloseTodoBar()
		m.promptFocus = true
		m.ag = m.ag.SetPromptFocus(true)
		m.editor.SetFocused(false)
		return m, m.maybeResizeEditorCmd()

	case agent.CmdBarIndicatorMsg:
		m.ag.Indicator.Set(msg.Text)
		return m, nil

	case agent.ModelOpenMsg:
		m.modelMode = true
		m.promptFocus = true
		m.ag = m.ag.OpenModel(buildModelItems())
		return m, m.maybeResizeEditorCmd()

	case agent.AgentModeCyclePressMsg:
		switch m.agentMode {
		case "off":
			m.agentMode = "read"
		case "read":
			m.agentMode = "work"
		default:
			m.agentMode = "off"
		}
		m.ag.SetAgentLabel("agent: " + m.agentMode)
		m.ag.Indicator.Set("Agent mode: " + m.agentMode)
		_ = m.writeNote()
		return m.respawnActiveHarness()

	case agent.ModeTogglePressMsg:
		if m.mode == "chat" {
			m.mode = "note"
		} else {
			m.mode = "chat"
		}
		m.ag.SetModeLabel("mode: " + m.mode)
		m.ag.Indicator.Set("Switched to " + m.mode + " mode")
		_ = m.writeNote()
		return m, m.ag.Indicator.StaleCmd()

	case agent.VoiceTogglePressMsg:
		if m.voiceEnabled {
			// Disable: close pipe+symlink.
			if m.voicePipeRelease != nil {
				m.voicePipeRelease()
				m.voicePipeRelease = nil
				m.voicePipeCh = nil
			}
			m.voiceEnabled = false
			m.ag.Buttons.VoiceLabel = "🔇"
		} else {
			// Enable: open pipe and start watcher.
			ch, release, err := voice.OpenPipe(os.Getpid())
			if err != nil {
				m.ag.Indicator.SetError("voice pipe: " + err.Error())
				return m, m.ag.Indicator.StaleCmd()
			}
			m.voiceEnabled = true
			m.voicePipeCh = ch
			m.voicePipeRelease = release
			m.ag.Buttons.VoiceLabel = "🔈"
		}
		_ = m.writeNote()
		return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.waitForVoiceInput())

	case voiceInputMsg:
		// STT text arrived from voice-input.sh via the named pipe.
		// Inject into the prompt box and hand focus to the agent pane.
		m.ag.PromptBox.SetValue(msg.text)
		m.promptFocus = true
		m.ag = m.ag.SetPromptFocus(true)
		m.editor.SetFocused(false)
		return m, tea.Batch(m.maybeResizeEditorCmd(), m.waitForVoiceInput())

	case agent.AttachPickerPressMsg:
		return m, openFilePickerCmd()

	case attachFileMsg:
		token := "@" + msg.Path + " "
		m.ag.PromptBox.SetValue(token + m.ag.PromptBox.Value())
		m.promptFocus = true
		m.ag = m.ag.SetPromptFocus(true)
		m.editor.SetFocused(false)
		return m, m.maybeResizeEditorCmd()

	case agent.PasteImageMsg:
		n := len(m.pendingImages) + 1
		m.pendingImages = append(m.pendingImages, msg.Data)
		token := fmt.Sprintf("[image #%d]", n)
		if m.ag.PromptBox.Value() == "" {
			m.ag.PromptBox.SetValue(token)
		} else {
			m.ag.PromptBox.InsertString(token)
		}
		return m, nil

	case agent.FocusTranscriptMsg:
		// Up-arrow at the top of the prompt box transfers focus to the
		// transcript. Works even when collapsed — landing on the [^] toggle
		// is the keyboard way to open the bar.
		return m.setFocus(focusTranscript), nil

	case agent.FocusEditorMsg:
		// Up-arrow at the top of the transcript bar hands focus to the
		// editor. Suppressed in full-height mode — the editor is hidden.
		if m.transcriptBar.IsFullHeight() {
			return m, nil
		}
		return m.setFocus(focusEditor), nil

	case agent.FocusPromptMsg:
		// Down-arrow at the bottom of the transcript bar (or on [^] when
		// collapsed) hands focus to the prompt box.
		return m.setFocus(focusPrompt), nil

	case agent.ModelSelectedMsg:
		m.modelMode = false
		m.ag = m.ag.CloseModel()
		m.editor.SetFocused(true)
		cfg, err := llm.ConfigForModel(msg.HarnessKey, msg.ModelKey)
		if err != nil {
			m.ag.Indicator.SetError("model error: " + err.Error())
			return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())
		}
		m2, switchCmd, switchErr := m.switchToModel(cfg)
		if switchErr != "" {
			m2.ag.Indicator.SetError(switchErr)
		}
		return m2, tea.Batch(switchCmd, m2.ag.Indicator.StaleCmd(), m2.maybeResizeEditorCmd())

	case agent.ModelBarClosedMsg:
		m.modelMode = false
		m.ag = m.ag.CloseModel()
		m.editor.SetFocused(true)
		return m, m.maybeResizeEditorCmd()

	case agent.TasksOpenMsg:
		m = m.openTaskOverlay()
		return m, nil

	case agent.TodoSummaryClearAllMsg:
		m.todos = nil
		m.transcriptBar.SetTodos(nil)
		if err := m.writeNote(); err != nil {
			m.ag.Indicator.SetError("todo clear failed: " + err.Error())
		}
		return m, m.maybeResizeEditorCmd()

	case agent.TodoItemToggleMsg:
		for i := range m.todos {
			if m.todos[i].ID == msg.ID {
				m.todos[i].Done = !m.todos[i].Done
				break
			}
		}
		m.transcriptBar.SetTodos(m.todos)
		if err := m.writeNote(); err != nil {
			m.ag.Indicator.SetError("todo update failed: " + err.Error())
		}
		return m, nil

	case agent.TodoItemDeleteMsg:
		out := m.todos[:0]
		for _, t := range m.todos {
			if t.ID != msg.ID {
				out = append(out, t)
			}
		}
		m.todos = out
		m.transcriptBar.SetTodos(m.todos)
		if err := m.writeNote(); err != nil {
			m.ag.Indicator.SetError("todo update failed: " + err.Error())
		}
		return m, m.maybeResizeEditorCmd()
	}

	em, cmd := m.editor.Update(msg)
	m.editor = em.(editor.Model)
	return m, cmd
}

func (m appModel) handleAppKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	// Global keys — work from any focus state.
	if msg.String() == "ctrl+alt+z" {
		return m, func() tea.Msg { return tea.Suspend() }
	}
	if msg.String() == "ctrl+q" {
		return m.maybeQuit()
	}

	// ESC while a harness is running aborts the agent run.
	if msg.String() == "esc" {
		switch {
		case m.piRunActive && m.piProc != nil:
			_ = m.piProc.SendAbort()
			return m, nil
		case m.claudeRunActive && m.claudeProc != nil:
			return m.abortClaudeRun()
		}
	}

	// Task overlay is open: route all keys to the overlay handler.
	if m.taskOverlay.open {
		m2, cmd, handled := m.handleTaskOverlayKey(msg)
		if handled {
			return m2, cmd
		}
	}

	// ctrl+l always cycles focus regardless of which modal UI is active,
	// except during the exit confirmation dialog.
	if msg.String() == "ctrl+l" && !m.showExitDialog {
		return m.cycleFocusNext(), nil
	}

	// Model picker is open and owns the prompt slot: route keys to the agent pane.
	if m.modelMode && m.promptFocus {
		pane, cmd := m.ag.Update(msg)
		m.ag = pane
		return m, tea.Batch(cmd, m.maybeResizeEditorCmd())
	}

	// Command picker is open and owns the prompt slot: route keys to the agent pane.
	if m.cmdMode && m.promptFocus {
		pane, cmd := m.ag.Update(msg)
		m.ag = pane
		return m, tea.Batch(cmd, m.maybeResizeEditorCmd())
	}

	// Web query bar is open and owns the prompt slot: route keys to the agent pane.
	if m.webQueryMode && m.promptFocus {
		pane, cmd := m.ag.Update(msg)
		m.ag = pane
		return m, tea.Batch(cmd, m.maybeResizeEditorCmd())
	}

	// /todo bar is open and owns the prompt slot: route keys to the agent pane.
	if m.todoMode && m.promptFocus {
		pane, cmd := m.ag.Update(msg)
		m.ag = pane
		return m, tea.Batch(cmd, m.maybeResizeEditorCmd())
	}

	if m.showExitDialog {
		return m.handleDialogKey(msg)
	}

	// Web bar has focus: route all keys to the agent pane.
	if m.webMode {
		pane, cmd := m.ag.Update(msg)
		m.ag = pane
		return m, tea.Batch(cmd, m.maybeResizeEditorCmd())
	}

	// Find bar is open and owns the prompt slot: route keys to the agent pane.
	if m.findMode && m.promptFocus {
		if msg.String() == "ctrl+g" {
			m.findMode = false
			m.gotoMode = true
			m.ag = m.ag.OpenGoto()
			m.editor.ClearSearch()
			return m, m.maybeResizeEditorCmd()
		}
		pane, cmd := m.ag.Update(msg)
		m.ag = pane
		return m, tea.Batch(cmd, m.maybeResizeEditorCmd())
	}

	// Goto bar is open and owns the prompt slot: route keys to the agent pane.
	if m.gotoMode && m.promptFocus {
		pane, cmd := m.ag.Update(msg)
		m.ag = pane
		return m, tea.Batch(cmd, m.maybeResizeEditorCmd())
	}

	// Transcript bar has keyboard focus.
	if m.transcriptFocus {
		switch msg.String() {
		case "esc":
			// Return to where the user came from. If that location is now
			// hidden (editor with full-height transcript), fall back to prompt.
			target := focusEditor
			if m.prevFocusWasPrompt || m.transcriptBar.IsFullHeight() {
				target = focusPrompt
			}
			return m.setFocus(target), nil
		}
		wasFull := m.transcriptBar.IsFullHeight()
		wasCollapsed := m.transcriptBar.IsCollapsed()
		bar, cmd := m.transcriptBar.Update(msg)
		m.transcriptBar = bar
		if wasFull != m.transcriptBar.IsFullHeight() || wasCollapsed != m.transcriptBar.IsCollapsed() {
			_ = m.writeNote()
		}
		return m, tea.Batch(cmd, m.maybeResizeEditorCmd())
	}

	// Prompt box has focus: route keys there.
	if m.promptFocus {
		switch msg.String() {
		case "esc":
			// Exit prompt to editor; if editor is hidden, go to transcript.
			target := focusEditor
			if m.transcriptBar.IsFullHeight() {
				target = focusTranscript
			}
			return m.setFocus(target), nil
		case "ctrl+f":
			m.promptFocus = true
			m.findMode = true
			m.ag = m.ag.OpenFind(false, "")
			m.editor.SetFocused(false)
			return m, m.maybeResizeEditorCmd()
		case "ctrl+h":
			m.promptFocus = true
			m.findMode = true
			m.ag = m.ag.OpenFind(true, "")
			m.editor.SetFocused(false)
			return m, m.maybeResizeEditorCmd()
		case "ctrl+g":
			m.promptFocus = true
			m.gotoMode = true
			m.ag = m.ag.OpenGoto()
			m.editor.SetFocused(false)
			return m, m.maybeResizeEditorCmd()
		case "ctrl+alt+c":
			text := m.ag.PromptBox.Value()
			clipboard.WriteAll(text)
			m.ag.PromptBox.Clear()
			m.ag.Indicator.Set("Copied to clipboard")
			return m, m.ag.Indicator.StaleCmd()
		}
		pane, agentCmd := m.ag.Update(msg)
		m.ag = pane

		// Resize editor if the prompt box grew or shrank.
		var resizeCmd tea.Cmd
		if newH := m.ag.Height(); newH != m.agentH {
			m.agentH = newH
			m.editorH = m.height - 2 - 1 - newH
			if m.editorH < 1 {
				m.editorH = 1
			}
			em, rc := m.editor.Update(tea.WindowSizeMsg{Width: m.width, Height: m.editorH})
			m.editor = em.(editor.Model)
			resizeCmd = rc
		}
		return m, tea.Batch(agentCmd, resizeCmd)
	}

	// Editor has focus.
	if msg.String() == "down" && m.editor.IsAtLastVisualLine() {
		return m.setFocus(focusTranscript), nil
	}
	switch msg.String() {
	case "ctrl+f":
		m.promptFocus = true
		m.findMode = true
		m.ag = m.ag.OpenFind(false, "")
		m.editor.SetFocused(false)
		return m, m.maybeResizeEditorCmd()
	case "ctrl+h":
		m.promptFocus = true
		m.findMode = true
		m.ag = m.ag.OpenFind(true, "")
		m.editor.SetFocused(false)
		return m, m.maybeResizeEditorCmd()
	case "ctrl+g":
		m.promptFocus = true
		m.gotoMode = true
		m.ag = m.ag.OpenGoto()
		m.editor.SetFocused(false)
		return m, m.maybeResizeEditorCmd()
	case "ctrl+s":
		if m.piRunActive || m.claudeRunActive {
			return m, nil // harness may be editing the file; skip manual save during run
		}
		content := m.editor.Value()
		if err := m.writeNote(); err != nil {
			m.ag.Indicator.SetError("Save failed: " + err.Error())
			return m, m.ag.Indicator.StaleCmd()
		}
		m.savedValue = content
		m.ag.Indicator.Set("Saved")
		return m, m.ag.Indicator.StaleCmd()
	}

	prev := m.editor.Value()
	em, cmd := m.editor.Update(msg)
	m.editor = em.(editor.Model)
	if m.editor.Value() != prev {
		m.refreshMarkerHighlight()
		m.clearInsertHighlight()
	}
	return m, cmd
}

// maybeResizeEditorCmd checks if the agent pane height changed and, if so,
// sends a WindowSizeMsg to the editor to resize it. Returns the editor's cmd.
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

func (m appModel) executeSlashCmd(cmd *agent.SlashCmdResult) (tea.Model, tea.Cmd) {
	switch cmd.Kind {
	case agent.SlashFind, agent.SlashFindReplaceOpen, agent.SlashFindReplace:
		m.promptFocus = false
		m.findMode = true
		m.ag = m.ag.OpenFindCmd(cmd)
		m.editor.SetFocused(false)
		// Trigger initial match highlights if a query was provided.
		if cmd.FindQuery != "" {
			result := m.editor.SetSearch(cmd.FindQuery, false)
			m.ag.Indicator.Set(agent.FormatMatchCount(result.Count, result.Current))
			return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())
		}
		return m, m.maybeResizeEditorCmd()

	case agent.SlashGotoOpen:
		m.promptFocus = false
		m.gotoMode = true
		m.ag = m.ag.OpenGoto()
		m.editor.SetFocused(false)
		return m, m.maybeResizeEditorCmd()

	case agent.SlashGoto:
		m.promptFocus = false
		m.ag = m.ag.SetPromptFocus(false)
		m.editor.SetFocused(true)
		if m.editor.GotoLine(cmd.Line) {
			m.ag.Indicator.Set(fmt.Sprintf("Jumped to line %d", cmd.Line))
		} else {
			m.ag.Indicator.SetError("Line doesn't exist")
		}
		return m, m.ag.Indicator.StaleCmd()

	case agent.SlashCopy:
		clipboard.WriteAll(cmd.CopyText)
		m.ag.PromptBox.Clear()
		m.ag.Indicator.Set("Copied to clipboard")
		return m, m.ag.Indicator.StaleCmd()

	case agent.SlashBg:
		if cmd.CopyText != "" {
			m.ag.PromptBox.SetValue(cmd.CopyText)
		}
		return m, func() tea.Msg { return tea.Suspend() }

	case agent.SlashModel:
		if cmd.CopyText != "" {
			m.ag.PromptBox.SetValue(cmd.CopyText)
		}
		m.promptFocus = false
		m.ag = m.ag.SetPromptFocus(false)
		m.editor.SetFocused(true)
		// If a model name was given, try to switch directly.
		if cmd.ModelName != "" {
			for _, e := range llm.AllModels() {
				if strings.EqualFold(e.ModelName, cmd.ModelName) {
					cfg, err := llm.ConfigForModel(e.HarnessKey, e.ModelKey)
					if err == nil {
						m2, switchCmd, switchErr := m.switchToModel(cfg)
						if switchErr != "" {
							m2.ag.Indicator.SetError(switchErr)
							return m2, m2.ag.Indicator.StaleCmd()
						}
						return m2, switchCmd
					}
				}
			}
		}
		// Name didn't match or was empty: open the picker.
		m.modelMode = true
		m.ag = m.ag.OpenModel(buildModelItems())
		return m, m.maybeResizeEditorCmd()

	case agent.SlashFixTables:
		var content string
		if startRow, endRow, hasSel := m.editor.SelectionRows(); hasSel {
			content = editor.NormalizeMarkdownTablesInRange(m.editor.Value(), startRow, endRow)
		} else {
			content = editor.NormalizeMarkdownTables(m.editor.Value())
		}
		m.editor.SetContent(content)
		m.refreshMarkerHighlight()
		m.clearInsertHighlight()
		if err := m.writeNote(); err == nil {
			m.savedValue = content
		}
		m.promptFocus = false
		m.ag = m.ag.SetPromptFocus(false)
		m.editor.SetFocused(true)
		m.ag.Indicator.Set("Tables normalized")
		return m, m.ag.Indicator.StaleCmd()

	case agent.SlashNote, agent.SlashChat:
		target := "note"
		if cmd.Kind == agent.SlashChat {
			target = "chat"
		}
		if cmd.CopyText != "" {
			m.ag.PromptBox.SetValue(cmd.CopyText)
		}
		m.mode = target
		m.ag.SetModeLabel("mode: " + m.mode)
		m.ag.Indicator.Set("Switched to " + m.mode + " mode")
		_ = m.writeNote()
		return m, m.ag.Indicator.StaleCmd()

	case agent.SlashWork, agent.SlashRead, agent.SlashAgentOff:
		switch cmd.Kind {
		case agent.SlashWork:
			m.agentMode = "work"
		case agent.SlashRead:
			m.agentMode = "read"
		default:
			m.agentMode = "off"
		}
		if cmd.CopyText != "" {
			m.ag.PromptBox.SetValue(cmd.CopyText)
		}
		m.ag.SetAgentLabel("agent: " + m.agentMode)
		m.ag.Indicator.Set("Agent mode: " + m.agentMode)
		_ = m.writeNote()
		return m.respawnActiveHarness()

	case agent.SlashWeb:
		if cmd.WebQuery != "" {
			return m.executeWebSearch(cmd.WebQuery, 10)
		}
		return m.openWebQueryBar()

	case agent.SlashTodo:
		if m.agentMode == "off" {
			m.ag.Indicator.SetError(`Error: /todo not available in "agent: off" mode`)
			return m, m.ag.Indicator.StaleCmd()
		}
		m.todoMode = true
		m.promptFocus = true
		m.ag = m.ag.OpenTodoBar(m.todos)
		m.editor.SetFocused(false)
		return m, m.maybeResizeEditorCmd()

	case agent.SlashClear:
		return m.executeClear(cmd.ClearTarget)

	case agent.SlashMarkerInclude:
		return m.executeWrapMarker("!>>", "<<!")
	case agent.SlashMarkerScope:
		return m.executeWrapMarker("@>>", "<<@")
	case agent.SlashMarkerReadOnly:
		return m.executeWrapMarker("$>>", "<<$")
	case agent.SlashMarkerExclude:
		return m.executeWrapMarker("%>>", "<<%")

	}
	return m, nil
}

// executeWrapMarker wraps the editor's active selection with the supplied
// marker tokens. With no active selection it surfaces a red indicator error
// (text must be selected) and makes no edit.
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
