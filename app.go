package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/atotto/clipboard"
	"github.com/ejumper/aunic/agent"
	"github.com/ejumper/aunic/editor"
	"github.com/ejumper/aunic/llm"
	"github.com/ejumper/aunic/markers"
	"github.com/ejumper/aunic/runner"
	"github.com/ejumper/aunic/todos"
	"github.com/ejumper/aunic/transcript"
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

	// Model run state.
	runActive          bool
	runCancel          context.CancelFunc
	runStream          *runner.Stream
	runSnapshotHash    string // sha256 of editor buffer at run start; used for note_write conflict detection
	runSnapshotContent string // full editor buffer at run start; used for "model wins" revert

	// Conflict resolution state.
	conflictMode         bool
	pendingNoteEdit      *runner.NoteEditApplyMsg
	pendingNoteWrite     *runner.NoteWriteApplyMsg
	conflictJustResolved bool // suppresses next ToolResultMsg error indicator after "user wins"

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
	transcriptFocus     bool
	prevFocusWasPrompt  bool

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

	// /chat2note state. chat2noteRowsToClear is the set of transcript Row
	// numbers that should be removed when the current chat2note step-2 run
	// finishes successfully. Non-empty implies a chat2note flow is in
	// progress; an empty slice means a normal run is active.
	chat2noteRowsToClear []int
	chat2noteExtra       string

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
}

func newApp(fp, content string, cfg llm.Config) appModel {
	content, savedState, _ := transcript.ExtractState(content)
	noteBody, txArea := transcript.Split(content)
	tableArea, todosArea := transcript.SplitArea(txArea)
	rows, _ := transcript.Parse(tableArea)
	todoList := todos.Parse(todosArea)

	// Apply persisted state with validation. Unknown values fall back to
	// defaults silently; the next writeNote will rewrite the state line with
	// the corrected values.
	mode := runner.ModeNote
	if savedState.Mode == runner.ModeChat || savedState.Mode == runner.ModeNote {
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
		editor:         editor.New(fp, noteBody),
		filepath:       fp,
		savedValue:     noteBody,
		llmCfg:         appliedCfg,
		transcriptRows: rows,
		todos:          todoList,
		mode:           mode,
		agentMode:      agentMode,
		homeDir:        home,
		cwd:            wd,
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

	return m
}

func (m appModel) Init() tea.Cmd {
	return tea.Batch(m.editor.Init(), m.ag.Indicator.StaleCmd())
}

// setInsertHighlight computes the byte range in `next` that differs from
// `prev` (common prefix + common suffix diff, snapped to rune boundaries)
// and stores it as the editor's insert overlay. The range stays in place
// until clearInsertHighlight is called or the user changes the buffer.
func (m *appModel) setInsertHighlight(prev, next string) {
	if prev == next {
		m.editor.SetInsertHighlight(nil)
		return
	}
	start, end := diffRange(prev, next)
	start, end = snapToRuneBoundaries(next, start, end)
	if start >= end {
		m.editor.SetInsertHighlight(nil)
		return
	}
	m.editor.SetInsertHighlight([]editor.InsertSpan{{Start: start, End: end}})
}

// clearInsertHighlight removes any active insert highlight.
func (m *appModel) clearInsertHighlight() {
	m.editor.SetInsertHighlight(nil)
}

// diffRange returns the byte range [start, end) within `next` that differs
// from `prev`. (0, 0) when identical or when only a deletion occurred (no
// new bytes to highlight).
func diffRange(prev, next string) (start, end int) {
	p, n := len(prev), len(next)
	minLen := p
	if n < p {
		minLen = n
	}
	i := 0
	for i < minLen && prev[i] == next[i] {
		i++
	}
	j := 0
	maxJ := n - i
	if p-i < maxJ {
		maxJ = p - i
	}
	for j < maxJ && prev[p-1-j] == next[n-1-j] {
		j++
	}
	return i, n - j
}

// snapToRuneBoundaries widens [start, end) outward until both edges sit on
// UTF-8 rune-start bytes. Prevents the diff from splitting a multi-byte rune.
func snapToRuneBoundaries(s string, start, end int) (int, int) {
	for start > 0 && !isRuneStart(s[start]) {
		start--
	}
	for end < len(s) && !isRuneStart(s[end]) {
		end++
	}
	return start, end
}

func isRuneStart(b byte) bool {
	return b < 0x80 || b >= 0xC0
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

// writeNote serializes the editor body + transcript rows + UI state back to
// disk. The editor's view is the *note body*; transcript rows are appended
// after a "***\n# Transcript" delimiter when there are any; the persistent
// UI state is the final line of the file as an HTML comment.
func (m *appModel) writeNote() error {
	full := transcript.Join(m.editor.Value(), m.transcriptRows, todos.Render(m.todos))
	full = transcript.AppendStateLine(full, m.currentState())
	return os.WriteFile(m.filepath, []byte(full), 0644)
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
	if m.llmCfg.ProviderKey != "" && m.llmCfg.ModelKey != "" {
		model = m.llmCfg.ProviderKey + "/" + m.llmCfg.ModelKey
	}
	return transcript.State{
		Mode:       m.mode,
		Agent:      m.agentMode,
		Model:      model,
		Transcript: transcriptVis,
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
		// Runner picks up m.todos via RunOptions.Todos.
		return m.startRun(msg.Prompt)

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
		return m, m.ag.Indicator.StaleCmd()

	case agent.ModeTogglePressMsg:
		if m.mode == runner.ModeChat {
			m.mode = runner.ModeNote
		} else {
			m.mode = runner.ModeChat
		}
		m.ag.SetModeLabel("mode: " + m.mode)
		m.ag.Indicator.Set("Switched to " + m.mode + " mode")
		_ = m.writeNote()
		return m, m.ag.Indicator.StaleCmd()

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
		cfg, err := llm.ConfigForModel(msg.ProviderKey, msg.ModelKey)
		if err == nil {
			m.llmCfg = cfg
			m.ag.SetModelLabel(cfg.ModelName)
			m.ag.Indicator.Set("Model: " + cfg.ModelName)
			_ = m.writeNote()
		} else {
			m.ag.Indicator.SetError("model error: " + err.Error())
		}
		return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())

	case agent.ModelBarClosedMsg:
		m.modelMode = false
		m.ag = m.ag.CloseModel()
		m.editor.SetFocused(true)
		return m, m.maybeResizeEditorCmd()

	case agent.RunCancelRequestedMsg:
		if m.runCancel != nil {
			m.runCancel()
		}
		return m, nil

	case runner.RunStartedMsg:
		m.ag.Indicator.Set("Run started")
		return m, m.runStream.NextCmd()

	case runner.ToolDispatchedMsg:
		label := msg.Name
		if msg.ArgsPreview != "" {
			label += ": " + msg.ArgsPreview
		}
		m.ag.Indicator.Set(label)
		return m, m.runStream.NextCmd()

	case runner.ToolResultMsg:
		if m.conflictJustResolved && msg.IsError {
			// After "user wins", the model sees a tool error; keep the
			// "copied to clipboard" indicator rather than showing the error.
			m.conflictJustResolved = false
			return m, m.runStream.NextCmd()
		}
		m.conflictJustResolved = false
		if msg.IsError {
			m.ag.Indicator.SetError(msg.Name + " failed: " + msg.Summary)
			return m, m.runStream.NextCmd()
		}
		m.ag.Indicator.Set(msg.Name + " done · " + msg.Summary)
		recordCmd := m.recordRunnerToolInTranscript(msg.Name, msg.CallJSON, msg.ResultJSON)
		return m, tea.Batch(m.runStream.NextCmd(), recordCmd)

	case runner.RunFinishedMsg:
		// If a /chat2note flow is in progress, this RunFinishedMsg marks the
		// end of step 2 — drop the rows we captured at step-1 invocation.
		if len(m.chat2noteRowsToClear) > 0 {
			m.transcriptRows = chat2noteRowFilter(m.transcriptRows, m.chat2noteRowsToClear)
			m.transcriptBar.SetRows(m.transcriptRows)
			m.chat2noteRowsToClear = nil
			m.chat2noteExtra = ""
			_ = m.writeNote()
			m.ag.Indicator.Set(fmt.Sprintf(
				"chat2note done · integrated %s · %d in / %d out · %s",
				msg.EndedOn, msg.InTok, msg.OutTok, roundDuration(msg.Elapsed),
			))
		} else {
			m.ag.Indicator.Set(fmt.Sprintf(
				"Run finished on %s · %d in / %d out · %s",
				msg.EndedOn, msg.InTok, msg.OutTok, roundDuration(msg.Elapsed),
			))
		}
		next := m.runStream.NextCmd()
		m = m.finishRun()
		return m, tea.Batch(next, m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())

	case runner.ChatFinishedMsg:
		m.nextToolID++
		m.transcriptRows = append(m.transcriptRows, transcript.Row{
			Num:     m.nextToolID,
			Role:    transcript.RoleAssistant,
			Type:    transcript.TypeMessage,
			Content: transcript.EncodeMessage(msg.Text),
		})
		m.transcriptBar.SetRows(m.transcriptRows)
		if err := m.writeNote(); err != nil {
			m.ag.Indicator.SetError("Save failed: " + err.Error())
		} else {
			m.ag.Indicator.Set(fmt.Sprintf(
				"Chat reply · %d in / %d out · %s",
				msg.InTok, msg.OutTok, roundDuration(msg.Elapsed),
			))
		}
		next := m.runStream.NextCmd()
		m = m.finishRun()
		return m, tea.Batch(next, m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())

	case runner.VisionUnsupportedMsg:
		m.ag.Indicator.Set("Images not supported. Only text was sent.")
		return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.runStream.NextCmd())

	case runner.RunErrorMsg:
		m.ag.Indicator.SetError("Error: " + msg.Message)
		next := m.runStream.NextCmd()
		m = m.finishRun()
		m = m.cancelPendingConflict()
		// Abort any in-flight /chat2note. Transcript is left untouched.
		m.chat2noteRowsToClear = nil
		m.chat2noteExtra = ""
		return m, tea.Batch(next, m.maybeResizeEditorCmd())

	case runner.RunCancelledMsg:
		m.ag.Indicator.Set("Run cancelled")
		next := m.runStream.NextCmd()
		m = m.finishRun()
		m = m.cancelPendingConflict()
		m.chat2noteRowsToClear = nil
		m.chat2noteExtra = ""
		return m, tea.Batch(next, m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())

	case runner.RunStreamDoneMsg:
		m.runStream = nil
		return m, nil

	case chat2noteStep1DoneMsg:
		// Structuring step finished. Strip Superfluous + empty sections, then
		// kick off step 2 as a normal note-mode run whose user-prompt is the
		// integration directive followed by the cleaned digest. The runner
		// sees the transcript as it stands now — but since the integration
		// directive is self-contained, the transcript context is redundant
		// for step 2; we pass it through unchanged for simplicity.
		cleaned := runner.CleanChat2NoteIntermediate(msg.Intermediate)
		if strings.TrimSpace(cleaned) == "" {
			m.ag.Indicator.SetError("chat2note: structuring step produced nothing usable")
			m.chat2noteRowsToClear = nil
			m.chat2noteExtra = ""
			return m, m.ag.Indicator.StaleCmd()
		}
		prompt := runner.Chat2NoteStep2Prompt(cleaned, m.chat2noteExtra)
		m.ag.Indicator.Set("integrating into note…")
		// Record the row Num that startRun will assign to the step-2 user
		// prompt so we can also clear it on success. The next row's Num is
		// m.nextToolID + 1 because startRun does nextToolID++ before using it.
		m.chat2noteRowsToClear = append(m.chat2noteRowsToClear, m.nextToolID+1)
		return m.startRun(prompt)

	case chat2noteStep1ErrMsg:
		m.ag.Indicator.SetError("chat2note: " + msg.Err.Error())
		m.chat2noteRowsToClear = nil
		m.chat2noteExtra = ""
		return m, m.ag.Indicator.StaleCmd()

	case agent.ConflictUserWinsMsg:
		return m.resolveConflictUserWins()

	case agent.ConflictModelWinsMsg:
		return m.resolveConflictModelWins()

	case runner.NoteEditApplyMsg:
		live := m.editor.Value()
		liveSnap := markers.Scan(live).BuildSnapshot()
		if liveSnap.HasShaping {
			updated, count, conflict := liveSnap.ResolveEdit(msg.Old, msg.New, msg.ReplaceAll)
			if conflict == markers.EditConflictProtected {
				m.ag.Indicator.SetError("Edit blocked by $>> <<$ protected range")
				msg.Reply <- runner.NoteEditApplyReply{ConflictProtected: true, Count: count}
				return m, tea.Batch(m.runStream.NextCmd(), m.ag.Indicator.StaleCmd())
			}
			if conflict != markers.EditConflictNone {
				label := editConflictLabel(conflict == markers.EditConflictAmbiguous, count)
				return m.enterEditConflict(&msg, label)
			}
			normalized := editor.NormalizeMarkdownTables(updated)
			m.editor.SetContent(normalized)
			m.refreshMarkerHighlight()
			m.setInsertHighlight(live, normalized)
			if err := m.writeNote(); err == nil {
				m.savedValue = normalized
			}
			msg.Reply <- runner.NoteEditApplyReply{Applied: true, Count: count}
			return m, m.runStream.NextCmd()
		}

		prevForInsert := m.editor.Value()
		res := m.editor.ApplyNoteEdit(msg.Old, msg.New, msg.ReplaceAll)
		if res.Conflict != editor.ConflictNone {
			// Open conflict UI — don't fill Reply yet; runner goroutine stays blocked.
			label := editConflictLabel(res.Conflict == editor.ConflictAmbiguous, res.Count)
			return m.enterEditConflict(&msg, label)
		}
		reply := runner.NoteEditApplyReply{Applied: true, Count: res.Count}
		content := editor.NormalizeMarkdownTables(m.editor.Value())
		m.editor.SetContent(content)
		m.refreshMarkerHighlight()
		m.setInsertHighlight(prevForInsert, content)
		if err := m.writeNote(); err == nil {
			m.savedValue = content
		}
		msg.Reply <- reply
		return m, m.runStream.NextCmd()

	case runner.NoteWriteApplyMsg:
		if runner.HashContent(m.editor.Value()) != m.runSnapshotHash {
			// Open conflict UI — don't fill Reply yet.
			return m.enterWriteConflict(&msg)
		}
		prevWrite := m.editor.Value()
		liveSnap := markers.Scan(prevWrite).BuildSnapshot()
		resolved, ok := liveSnap.ResolveWrite(msg.Content)
		if !ok {
			msg.Reply <- runner.NoteWriteApplyReply{Applied: false}
			return m, m.runStream.NextCmd()
		}
		normalized := editor.NormalizeMarkdownTables(resolved)
		m.editor.SetContent(normalized)
		m.refreshMarkerHighlight()
		m.setInsertHighlight(prevWrite, normalized)
		if err := m.writeNote(); err == nil {
			m.savedValue = normalized
		}
		msg.Reply <- runner.NoteWriteApplyReply{Applied: true}
		return m, m.runStream.NextCmd()

	case runner.NoteEditAtApplyMsg:
		live := m.editor.Value()
		parsed := markers.Scan(live)
		updated, applied, err := parsed.ApplyEdits(live, msg.Edits)
		if err != nil {
			msg.Reply <- runner.NoteEditAtApplyReply{ValidationError: err.Error()}
			return m, m.runStream.NextCmd()
		}
		if len(applied) > 0 {
			normalized := editor.NormalizeMarkdownTables(updated)
			m.editor.SetContent(normalized)
			m.refreshMarkerHighlight()
			m.setInsertHighlight(live, normalized)
			if werr := m.writeNote(); werr == nil {
				m.savedValue = normalized
			}
		}
		msg.Reply <- runner.NoteEditAtApplyReply{Applied: true, AppliedSlots: applied}
		return m, m.runStream.NextCmd()

	case runner.TodoWriteApplyMsg:
		items := todos.AssignIDs(msg.Texts)
		m.todos = items
		m.transcriptBar.SetTodos(items)
		if err := m.writeNote(); err != nil {
			m.ag.Indicator.SetError("todo write failed: " + err.Error())
		}
		msg.Reply <- runner.TodoWriteApplyReply{Applied: true, Items: items}
		return m, m.runStream.NextCmd()

	case runner.TodoDoneApplyMsg:
		updated, ok := todos.MarkDone(m.todos, msg.ID)
		if !ok {
			msg.Reply <- runner.TodoDoneApplyReply{NotFound: true}
			return m, m.runStream.NextCmd()
		}
		m.todos = updated
		m.transcriptBar.SetTodos(updated)
		if err := m.writeNote(); err != nil {
			m.ag.Indicator.SetError("todo update failed: " + err.Error())
		}
		msg.Reply <- runner.TodoDoneApplyReply{Applied: true, Items: updated}
		return m, m.runStream.NextCmd()

	case runner.TodosClearedMsg:
		m.todos = nil
		m.transcriptBar.SetTodos(nil)
		if err := m.writeNote(); err != nil {
			m.ag.Indicator.SetError("todo clear failed: " + err.Error())
		}
		return m, m.runStream.NextCmd()

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

	// Escape cancels an in-flight model run.
	if m.runActive && msg.String() == "esc" {
		if m.runCancel != nil {
			m.runCancel()
		}
		return m, nil
	}

	// Conflict resolution bar is open: route all keys to the agent pane.
	if m.conflictMode {
		pane, cmd := m.ag.Update(msg)
		m.ag = pane
		return m, tea.Batch(cmd, m.maybeResizeEditorCmd())
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
					cfg, err := llm.ConfigForModel(e.ProviderKey, e.ModelKey)
					if err == nil {
						m.llmCfg = cfg
						m.ag.SetModelLabel(cfg.ModelName)
						m.ag.Indicator.Set("Model: " + cfg.ModelName)
						_ = m.writeNote()
						return m, m.ag.Indicator.StaleCmd()
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
		target := runner.ModeNote
		if cmd.Kind == agent.SlashChat {
			target = runner.ModeChat
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
		return m, m.ag.Indicator.StaleCmd()

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

	case agent.SlashChat2Note:
		return m.executeChat2Note(cmd.Chat2NoteExtra)
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

func gutterLine(gutterW int) string {

	return strings.Repeat(" ", gutterW-1) + "\x1b[34m▏\x1b[0m"
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
	for _, ln := range m.transcriptBar.View(m.width) {
		parts = append(parts, ln)
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
			ProviderKey: e.ProviderKey,
			ModelKey:    e.ModelKey,
			Name:        e.ModelName,
		}
	}
	return items
}

// startRun auto-saves the active note, snapshots its content, and kicks off
// the runner. The send button swaps to ■ for the duration.
func (m appModel) startRun(userPrompt string) (tea.Model, tea.Cmd) {
	if m.runActive {
		return m, nil
	}
	if m.llmCfg.Model == "" {
		m.ag.Indicator.SetError("No model configured — check ~/.config/aunic/aunic.json")
		return m, nil
	}

	content := m.editor.Value()

	modelSnapshot := content
	scopeCount := 0
	noteWriteForbidden := false
	parsed := markers.Scan(content)
	if vErr := parsed.Validate(); vErr != nil {
		m.ag.Indicator.SetError(vErr.Message)
		m.ag.PromptBox.SetValue(userPrompt)
		return m, m.ag.Indicator.StaleCmd()
	}
	snap := parsed.BuildSnapshot()
	modelSnapshot = snap.Visible
	if m.mode != runner.ModeChat {
		scopeCount = len(snap.Slots)
	}
	if snap.WritePolicy == markers.WritePolicyForbidden {
		noteWriteForbidden = true
	}

	// Snapshot rows for the runner before recording the current user prompt so
	// the runner sees prior-turn history without the current prompt (which is
	// sent explicitly via opts.UserPrompt).
	runRows := m.transcriptRows

	// Record user prompt in the transcript immediately so it precedes the tool
	// call rows that will be appended during the run.
	m.nextToolID++
	m.transcriptRows = append(m.transcriptRows, transcript.Row{
		Num:     m.nextToolID,
		Role:    transcript.RoleUser,
		Type:    transcript.TypeMessage,
		Content: transcript.EncodeMessage(userPrompt),
	})
	m.transcriptBar.SetRows(m.transcriptRows)

	if err := m.writeNote(); err != nil {
		m.ag.Indicator.SetError("auto-save failed: " + err.Error())
		return m, m.ag.Indicator.StaleCmd()
	}
	m.savedValue = content

	hash := runner.HashContent(content)
	rc := &runner.RunContext{
		ActivePath:      m.filepath,
		SnapshotContent: modelSnapshot,
		SnapshotHash:    hash,
	}
	m.runSnapshotHash = hash
	m.runSnapshotContent = content

	ctx, cancel := context.WithCancel(context.Background())
	m.runCancel = cancel
	m.runActive = true
	m.ag.SetRunActive(true)

	// Parse @path tokens, load file contents, record transcript rows.
	var fileAttachments []runner.FileAttachment
	for _, path := range agent.ParseAtFiles(userPrompt) {
		data, err := os.ReadFile(path)
		if err != nil {
			m.ag.Indicator.SetError("Cannot read: " + path)
			continue
		}
		content := string(data)
		lines := strings.SplitN(content, "\n", 6)
		if len(lines) > 5 {
			lines = lines[:5]
		}
		if len(lines) > 0 && lines[len(lines)-1] == "" {
			lines = lines[:len(lines)-1]
		}
		cmd := m.appendTranscriptPair(
			transcript.ToolRead,
			transcript.EncodeAgentFileCall(path, "", ""),
			transcript.EncodeAgentPreviewResult(lines),
		)
		if cmd != nil {
			_ = cmd // transcript rows recorded synchronously via appendTranscriptPair
		}
		fileAttachments = append(fileAttachments, runner.FileAttachment{
			Path:    path,
			Content: content,
		})
	}
	cleanPrompt := agent.StripAtFiles(userPrompt)

	// Consume pending images for this run.
	pendingImgs := m.pendingImages
	m.pendingImages = nil

	opts := runner.RunOptions{
		Mode:            m.mode,
		AgentMode:       m.agentMode,
		UserPrompt:      cleanPrompt,
		TranscriptRows:  runRows,
		FileAttachments: fileAttachments,
		PendingImages:   pendingImgs,
		Todos:              m.todos,
		WriteScopeCount:    scopeCount,
		NoteWriteForbidden: noteWriteForbidden,
	}
	stream, first := runner.StartCmd(ctx, m.llmCfg, rc, opts)
	m.runStream = stream
	return m, tea.Batch(first, m.maybeResizeEditorCmd())
}

// finishRun clears run-active state and resets the send button. The stream
// pointer is left in place until RunStreamDoneMsg drains the channel close.
func (m appModel) finishRun() appModel {
	m.runActive = false
	m.runCancel = nil
	m.ag.SetRunActive(false)
	return m
}

func roundDuration(d time.Duration) time.Duration {
	if d < time.Second {
		return d.Round(10 * time.Millisecond)
	}
	return d.Round(100 * time.Millisecond)
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
func (m *appModel) recordRunnerToolInTranscript(name, callJSON, resultJSON string) tea.Cmd {
	switch name {
	case "web_search":
		var args struct {
			Query string `json:"query"`
		}
		if err := json.Unmarshal([]byte(callJSON), &args); err != nil || args.Query == "" {
			return nil
		}
		var raw []struct {
			Title    string `json:"title"`
			URL      string `json:"url"`
			Domain   string `json:"domain"`
			Abstract string `json:"abstract"`
		}
		_ = json.Unmarshal([]byte(resultJSON), &raw)
		results := make([]web.Result, len(raw))
		for i, r := range raw {
			results[i] = web.Result{Title: r.Title, URL: r.URL, Domain: r.Domain, Abstract: r.Abstract}
		}
		return m.recordSearchInTranscript(args.Query, results)
	case "web_fetch":
		var args struct {
			URL string `json:"url"`
		}
		if err := json.Unmarshal([]byte(callJSON), &args); err != nil || args.URL == "" {
			return nil
		}
		var page struct {
			Title    string `json:"title"`
			URL      string `json:"url"`
			Markdown string `json:"markdown"`
		}
		_ = json.Unmarshal([]byte(resultJSON), &page)
		if page.URL == "" {
			page.URL = args.URL
		}
		return m.recordFetchInTranscript(web.Page{Title: page.Title, URL: page.URL, Markdown: page.Markdown})

	case "Read":
		var args struct {
			FilePath string `json:"file_path"`
		}
		if err := json.Unmarshal([]byte(callJSON), &args); err != nil || args.FilePath == "" {
			return nil
		}
		var result struct {
			Content string `json:"content"`
		}
		_ = json.Unmarshal([]byte(resultJSON), &result)
		lines := strings.SplitN(result.Content, "\n", 6)
		if len(lines) > 5 {
			lines = lines[:5]
		}
		// Strip trailing empty line from split
		if len(lines) > 0 && lines[len(lines)-1] == "" {
			lines = lines[:len(lines)-1]
		}
		return m.appendTranscriptPair(
			transcript.ToolRead,
			transcript.EncodeAgentFileCall(args.FilePath, "", ""),
			transcript.EncodeAgentPreviewResult(lines),
		)

	case "Write":
		var args struct {
			FilePath string `json:"file_path"`
			Content  string `json:"content"`
		}
		if err := json.Unmarshal([]byte(callJSON), &args); err != nil || args.FilePath == "" {
			return nil
		}
		lines := strings.SplitN(args.Content, "\n", 6)
		if len(lines) > 5 {
			lines = lines[:5]
		}
		if len(lines) > 0 && lines[len(lines)-1] == "" {
			lines = lines[:len(lines)-1]
		}
		return m.appendTranscriptPair(
			transcript.ToolWrite,
			transcript.EncodeAgentFileCall(args.FilePath, "", ""),
			transcript.EncodeAgentPreviewResult(lines),
		)

	case "Edit":
		var args struct {
			FilePath  string `json:"file_path"`
			OldString string `json:"old_string"`
			NewString string `json:"new_string"`
		}
		if err := json.Unmarshal([]byte(callJSON), &args); err != nil || args.FilePath == "" {
			return nil
		}
		return m.appendTranscriptPair(
			transcript.ToolEdit,
			transcript.EncodeAgentFileCall(args.FilePath, args.OldString, args.NewString),
			transcript.EncodeAgentPreviewResult(nil),
		)

	case "Bash":
		var args struct {
			Command string `json:"command"`
		}
		if err := json.Unmarshal([]byte(callJSON), &args); err != nil || args.Command == "" {
			return nil
		}
		var result struct {
			Output string `json:"output"`
		}
		_ = json.Unmarshal([]byte(resultJSON), &result)
		return m.appendTranscriptPair(
			transcript.ToolBash,
			transcript.EncodeAgentCmdCall(args.Command),
			transcript.EncodeAgentOutputResult(result.Output),
		)

	case "Grep":
		var args struct {
			Pattern string `json:"pattern"`
		}
		if err := json.Unmarshal([]byte(callJSON), &args); err != nil || args.Pattern == "" {
			return nil
		}
		var result struct {
			Mode      string   `json:"mode"`
			Filenames []string `json:"filenames"`
			Content   string   `json:"content"`
		}
		_ = json.Unmarshal([]byte(resultJSON), &result)
		var previewLines []string
		if result.Mode == "content" {
			all := strings.SplitN(result.Content, "\n", 6)
			if len(all) > 5 {
				all = all[:5]
			}
			previewLines = all
		} else {
			n := 5
			if len(result.Filenames) < n {
				n = len(result.Filenames)
			}
			previewLines = result.Filenames[:n]
		}
		return m.appendTranscriptPair(
			transcript.ToolGrep,
			transcript.EncodeAgentPatternCall(args.Pattern),
			transcript.EncodeAgentPreviewResult(previewLines),
		)

	case "Glob":
		var args struct {
			Pattern string `json:"pattern"`
		}
		if err := json.Unmarshal([]byte(callJSON), &args); err != nil || args.Pattern == "" {
			return nil
		}
		var result struct {
			Filenames []string `json:"filenames"`
		}
		_ = json.Unmarshal([]byte(resultJSON), &result)
		n := 5
		if len(result.Filenames) < n {
			n = len(result.Filenames)
		}
		return m.appendTranscriptPair(
			transcript.ToolGlob,
			transcript.EncodeAgentPatternCall(args.Pattern),
			transcript.EncodeAgentPreviewResult(result.Filenames[:n]),
		)

	case "note_edit":
		var args struct {
			OldString string `json:"old_string"`
			NewString string `json:"new_string"`
		}
		if err := json.Unmarshal([]byte(callJSON), &args); err != nil {
			return nil
		}
		return m.appendTranscriptPair(
			transcript.ToolNoteEdit,
			transcript.EncodeAgentFileCall(m.filepath, args.OldString, args.NewString),
			transcript.EncodeAgentPreviewResult(nil),
		)

	case "note_write":
		var args struct {
			Content string `json:"content"`
		}
		if err := json.Unmarshal([]byte(callJSON), &args); err != nil {
			return nil
		}
		lines := strings.SplitN(args.Content, "\n", 6)
		if len(lines) > 5 {
			lines = lines[:5]
		}
		if len(lines) > 0 && lines[len(lines)-1] == "" {
			lines = lines[:len(lines)-1]
		}
		return m.appendTranscriptPair(
			transcript.ToolNoteWrite,
			transcript.EncodeAgentFileCall(m.filepath, "", ""),
			transcript.EncodeAgentPreviewResult(lines),
		)
	}
	return nil
}

// recordFetchInTranscript appends a tool_call/tool_result pair for a web fetch
// to the transcript. Only the URL, title, and a short snippet are persisted —
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

// ─── /chat2note ──────────────────────────────────────────────────────────────

// chat2noteStep1DoneMsg carries the structuring step's raw text output.
type chat2noteStep1DoneMsg struct {
	Intermediate string
}

// chat2noteStep1ErrMsg signals that step 1 failed; the transcript is left
// untouched and the flow aborts.
type chat2noteStep1ErrMsg struct {
	Err error
}

// executeChat2Note kicks off the two-step "condense chat into note" flow.
// Step 1 (structuring) runs via a one-shot LLM call wrapped in a tea.Cmd;
// when it returns, the chat2noteStep1DoneMsg handler proceeds to step 2 by
// invoking the normal runner with the cleaned intermediate as the prompt.
func (m appModel) executeChat2Note(extra string) (tea.Model, tea.Cmd) {
	if m.runActive || len(m.chat2noteRowsToClear) > 0 {
		m.ag.Indicator.SetError("Another run is already active")
		return m, m.ag.Indicator.StaleCmd()
	}
	if m.llmCfg.Model == "" {
		m.ag.Indicator.SetError("No model configured — check ~/.config/aunic/aunic.json")
		return m, m.ag.Indicator.StaleCmd()
	}
	if len(m.transcriptRows) == 0 {
		m.ag.Indicator.SetError("No transcript content to integrate")
		return m, m.ag.Indicator.StaleCmd()
	}

	// Capture the row Nums currently in scope. These get cleared after
	// step 2 completes successfully.
	rowNums := make([]int, len(m.transcriptRows))
	rowsCopy := make([]transcript.Row, len(m.transcriptRows))
	for i, r := range m.transcriptRows {
		rowNums[i] = r.Num
		rowsCopy[i] = r
	}
	m.chat2noteRowsToClear = rowNums
	m.chat2noteExtra = extra

	m.ag.Indicator.Set("structuring chat…")
	return m, tea.Batch(
		m.ag.Indicator.StaleCmd(),
		chat2noteStep1Cmd(m.llmCfg, rowsCopy),
	)
}

// chat2noteStep1Cmd runs the one-shot structuring LLM call in a goroutine.
func chat2noteStep1Cmd(cfg llm.Config, rows []transcript.Row) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		text, err := runner.Chat2NoteStep1(ctx, cfg, rows)
		if err != nil {
			return chat2noteStep1ErrMsg{Err: err}
		}
		return chat2noteStep1DoneMsg{Intermediate: text}
	}
}

// chat2noteRowFilter returns a copy of rows with any row whose Num appears
// in clearSet removed.
func chat2noteRowFilter(rows []transcript.Row, clearSet []int) []transcript.Row {
	if len(clearSet) == 0 {
		return rows
	}
	idx := make(map[int]bool, len(clearSet))
	for _, n := range clearSet {
		idx[n] = true
	}
	out := make([]transcript.Row, 0, len(rows))
	for _, r := range rows {
		if idx[r.Num] {
			continue
		}
		out = append(out, r)
	}
	return out
}

