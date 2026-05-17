package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/atotto/clipboard"
	"github.com/ejumper/aunic/agent"
	"github.com/ejumper/aunic/editor"
	"github.com/ejumper/aunic/llm"
	"github.com/ejumper/aunic/runner"
	"github.com/ejumper/aunic/transcript"
	"github.com/ejumper/aunic/web"
	"github.com/mattn/go-runewidth"

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

	// pendingChatUserPrompt holds the user prompt that kicked off an in-flight
	// chat-mode run, so the ChatFinishedMsg handler can append the user row
	// alongside the assistant reply.
	pendingChatUserPrompt string
}

func newApp(fp, content string, cfg llm.Config) appModel {
	noteBody, txArea := transcript.Split(content)
	rows, _ := transcript.Parse(txArea)

	m := appModel{
		editor:         editor.New(fp, noteBody),
		filepath:       fp,
		savedValue:     noteBody,
		llmCfg:         cfg,
		transcriptRows: rows,
		mode:           runner.ModeNote,
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
	m.transcriptH = m.transcriptBar.Height()
	// ag is sized in the first WindowSizeMsg; start with a zero-width pane so
	// Height() still returns a valid value before the terminal size is known.
	m.ag = agent.NewPane(80)
	m.agentH = m.ag.Height()
	switch {
	case cfg.Err() != "":
		m.ag.Indicator.SetError("config error: " + cfg.Err())
	case cfg.ModelName != "":
		m.ag.Indicator.Set(filepath.Base(fp) + " loaded · " + cfg.ModelName)
	default:
		m.ag.Indicator.Set(filepath.Base(fp) + " loaded")
	}

	// Populate model button label and valid-names map from the config file.
	if cfg.ModelName != "" {
		m.ag.SetModelLabel(cfg.ModelName)
	}
	m.ag.SetModeLabel("mode: " + m.mode)
	names := make(map[string]bool)
	for _, e := range llm.AllModels() {
		names[strings.ToLower(e.ModelName)] = true
	}
	m.ag.SetModelNames(names)

	return m
}

func (m appModel) Init() tea.Cmd {
	return tea.Batch(m.editor.Init(), m.ag.Indicator.StaleCmd())
}

// writeNote serializes the editor body + transcript rows back to disk. The
// editor's view is the *note body*; transcript rows are appended after a
// "***\n# Transcript" delimiter when there are any.
func (m *appModel) writeNote() error {
	full := transcript.Join(m.editor.Value(), m.transcriptRows)
	return os.WriteFile(m.filepath, []byte(full), 0644)
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
			bar, cmd := m.transcriptBar.Update(msg)
			m.transcriptBar = bar
			// If [+] just promoted the bar to full-height and the editor was
			// holding focus, move focus to the now-only-visible transcript.
			if !wasFull && m.transcriptBar.IsFullHeight() && m.currentFocus() == focusEditor {
				m = m.setFocus(focusTranscript)
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
		em, cmd := m.editor.Update(msg)
		m.editor = em.(editor.Model)
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
		if msg.Content == "@web" {
			return m.openWebQueryBar()
		}
		if atCmd := agent.ParseAtCmd(msg.Content); atCmd != nil {
			return m.executeAtCmd(atCmd)
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
		case agent.CmdExecWebBar:
			return m.openWebQueryBar()
		}
		return m, nil

	case agent.WebQuerySubmitMsg:
		m.webQueryMode = false
		m.ag = m.ag.CloseWebQueryBar()
		return m.executeAtCmd(&agent.AtCmdResult{Kind: agent.AtWeb, Query: msg.Query, N: 10})

	case agent.WebQueryClosedMsg:
		m.webQueryMode = false
		m.ag = m.ag.CloseWebQueryBar()
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

	case agent.ModeTogglePressMsg:
		if m.mode == runner.ModeChat {
			m.mode = runner.ModeNote
		} else {
			m.mode = runner.ModeChat
		}
		m.ag.SetModeLabel("mode: " + m.mode)
		m.ag.Indicator.Set("Switched to " + m.mode + " mode")
		return m, m.ag.Indicator.StaleCmd()

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
		m.ag.Indicator.Set(fmt.Sprintf(
			"Run finished on %s · %d in / %d out · %s",
			msg.EndedOn, msg.InTok, msg.OutTok, roundDuration(msg.Elapsed),
		))
		next := m.runStream.NextCmd()
		m = m.finishRun()
		return m, tea.Batch(next, m.ag.Indicator.StaleCmd())

	case runner.ChatFinishedMsg:
		userPrompt := m.pendingChatUserPrompt
		m.pendingChatUserPrompt = ""
		if userPrompt != "" {
			m.nextToolID++
			m.transcriptRows = append(m.transcriptRows, transcript.Row{
				Num:     m.nextToolID,
				Role:    transcript.RoleUser,
				Type:    transcript.TypeMessage,
				Content: transcript.EncodeMessage(userPrompt),
			})
		}
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

	case runner.RunErrorMsg:
		m.ag.Indicator.SetError("Error: " + msg.Message)
		next := m.runStream.NextCmd()
		m = m.finishRun()
		if m.conflictMode {
			m.conflictMode = false
			m.ag = m.ag.CloseConflict()
			m.pendingNoteEdit = nil
			m.pendingNoteWrite = nil
			m.editor.SetFocused(true)
		}
		return m, tea.Batch(next, m.maybeResizeEditorCmd())

	case runner.RunCancelledMsg:
		m.ag.Indicator.Set("Run cancelled")
		next := m.runStream.NextCmd()
		m = m.finishRun()
		if m.conflictMode {
			m.conflictMode = false
			m.ag = m.ag.CloseConflict()
			m.pendingNoteEdit = nil
			m.pendingNoteWrite = nil
			m.editor.SetFocused(true)
		}
		return m, tea.Batch(next, m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())

	case runner.RunStreamDoneMsg:
		m.runStream = nil
		return m, nil

	case agent.ConflictUserWinsMsg:
		m.conflictMode = false
		m.ag = m.ag.CloseConflict()
		m.editor.SetFocused(true)
		m.conflictJustResolved = true
		if m.pendingNoteEdit != nil {
			clipboard.WriteAll(m.pendingNoteEdit.New)
			m.pendingNoteEdit.Reply <- runner.NoteEditApplyReply{ConflictNotFound: true}
			m.pendingNoteEdit = nil
		} else if m.pendingNoteWrite != nil {
			clipboard.WriteAll(m.pendingNoteWrite.Content)
			m.pendingNoteWrite.Reply <- runner.NoteWriteApplyReply{HashMismatch: true}
			m.pendingNoteWrite = nil
		}
		m.ag.Indicator.Set("Edit copied to clipboard and not applied")
		return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.runStream.NextCmd(), m.maybeResizeEditorCmd())

	case agent.ConflictModelWinsMsg:
		m.conflictMode = false
		m.ag = m.ag.CloseConflict()
		m.editor.SetFocused(true)
		if m.pendingNoteEdit != nil {
			// Revert to snapshot so old_string is guaranteed to be present.
			m.editor.SetContent(m.runSnapshotContent)
			res := m.editor.ApplyNoteEdit(m.pendingNoteEdit.Old, m.pendingNoteEdit.New, m.pendingNoteEdit.ReplaceAll)
			content := editor.NormalizeMarkdownTables(m.editor.Value())
			m.editor.SetContent(content)
			if err := m.writeNote(); err == nil {
				m.savedValue = content
			}
			m.pendingNoteEdit.Reply <- runner.NoteEditApplyReply{Applied: res.Applied, Count: res.Count}
			m.pendingNoteEdit = nil
		} else if m.pendingNoteWrite != nil {
			normalized := editor.NormalizeMarkdownTables(m.pendingNoteWrite.Content)
			m.editor.SetContent(normalized)
			if err := m.writeNote(); err == nil {
				m.savedValue = normalized
			}
			m.pendingNoteWrite.Reply <- runner.NoteWriteApplyReply{Applied: true}
			m.pendingNoteWrite = nil
		}
		return m, tea.Batch(m.runStream.NextCmd(), m.maybeResizeEditorCmd())

	case runner.NoteEditApplyMsg:
		res := m.editor.ApplyNoteEdit(msg.Old, msg.New, msg.ReplaceAll)
		if res.Conflict != editor.ConflictNone {
			// Open conflict UI — don't fill Reply yet; runner goroutine stays blocked.
			m.pendingNoteEdit = &msg
			m.conflictMode = true
			m.ag = m.ag.OpenConflict()
			m.promptFocus = false
			m.editor.SetFocused(false)
			if res.Conflict == editor.ConflictAmbiguous {
				m.ag.Indicator.SetError(fmt.Sprintf("Conflict on note edit! (%d matches)", res.Count))
			} else {
				m.ag.Indicator.SetError("Conflict on note edit!")
			}
			return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())
		}
		reply := runner.NoteEditApplyReply{Applied: true, Count: res.Count}
		content := editor.NormalizeMarkdownTables(m.editor.Value())
		m.editor.SetContent(content)
		if err := m.writeNote(); err == nil {
			m.savedValue = content
		}
		msg.Reply <- reply
		return m, m.runStream.NextCmd()

	case runner.NoteWriteApplyMsg:
		if runner.HashContent(m.editor.Value()) != m.runSnapshotHash {
			// Open conflict UI — don't fill Reply yet.
			m.pendingNoteWrite = &msg
			m.conflictMode = true
			m.ag = m.ag.OpenConflict()
			m.promptFocus = false
			m.editor.SetFocused(false)
			m.ag.Indicator.SetError("Conflict on note write!")
			return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())
		}
		normalized := editor.NormalizeMarkdownTables(msg.Content)
		m.editor.SetContent(normalized)
		if err := m.writeNote(); err == nil {
			m.savedValue = normalized
		}
		msg.Reply <- runner.NoteWriteApplyReply{Applied: true}
		return m, m.runStream.NextCmd()
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
		bar, cmd := m.transcriptBar.Update(msg)
		m.transcriptBar = bar
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

	em, cmd := m.editor.Update(msg)
	m.editor = em.(editor.Model)
	return m, cmd
}

// focusArea identifies one of the three navigable panes.
type focusArea int

const (
	focusEditor focusArea = iota
	focusTranscript
	focusPrompt
)

// currentFocus returns which pane currently has keyboard focus.
func (m appModel) currentFocus() focusArea {
	if m.transcriptFocus {
		return focusTranscript
	}
	if m.promptFocus {
		return focusPrompt
	}
	return focusEditor
}

// setFocus clears focus from all panes and grants it to target. Also records
// prevFocusWasPrompt so esc-from-transcript can return to the originating
// pane. Cursor position inside the transcript bar is preserved across visits.
func (m appModel) setFocus(target focusArea) appModel {
	prev := m.currentFocus()
	// Clear all.
	m.transcriptFocus = false
	m.transcriptBar.SetFocused(false)
	m.promptFocus = false
	m.ag = m.ag.SetPromptFocus(false)
	m.editor.SetFocused(false)
	// Grant target.
	switch target {
	case focusEditor:
		m.editor.SetFocused(true)
		// Unfocus the web bar (keep it rendered but stop routing keys to it).
		if m.webOpen {
			m.webMode = false
		}
	case focusTranscript:
		m.transcriptFocus = true
		m.transcriptBar.SetFocused(true)
		m.prevFocusWasPrompt = prev == focusPrompt
		// Unfocus the web bar (keep it rendered but stop routing keys to it).
		if m.webOpen {
			m.webMode = false
		}
	case focusPrompt:
		m.promptFocus = true
		m.ag = m.ag.SetPromptFocus(true)
		// Refocus the web bar if it was open.
		if m.webOpen {
			m.webMode = true
		}
	}
	return m
}

// cycleFocusNext returns the model with focus moved to the next pane in cycle
// editor → transcript → prompt → editor, skipping panes that are currently
// "closed": the transcript when collapsed, and the editor when the transcript
// is in full-height mode (which hides the editor).
func (m appModel) cycleFocusNext() appModel {
	order := []focusArea{focusEditor, focusTranscript, focusPrompt}
	cur := m.currentFocus()
	start := 0
	for i, f := range order {
		if f == cur {
			start = i
			break
		}
	}
	txCollapsed := m.transcriptBar.IsCollapsed()
	txFull := m.transcriptBar.IsFullHeight()
	for i := 1; i <= len(order); i++ {
		cand := order[(start+i)%len(order)]
		if cand == focusTranscript && txCollapsed {
			continue
		}
		if cand == focusEditor && txFull {
			continue
		}
		return m.setFocus(cand)
	}
	return m
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
		return m, m.ag.Indicator.StaleCmd()
	}
	return m, nil
}

func (m appModel) openWebQueryBar() (tea.Model, tea.Cmd) {
	m.webQueryMode = true
	m.promptFocus = true
	m.ag = m.ag.OpenWebQueryBar()
	m.editor.SetFocused(false)
	return m, m.maybeResizeEditorCmd()
}

func (m appModel) executeAtCmd(cmd *agent.AtCmdResult) (tea.Model, tea.Cmd) {
	switch cmd.Kind {
	case agent.AtWeb:
		m.promptFocus = true
		m.webMode = true
		m.webOpen = true
		m.lastWebQuery = cmd.Query
		var searchCmd tea.Cmd
		m.ag, searchCmd = m.ag.OpenWeb(cmd.Query, cmd.N)
		m.editor.SetFocused(false)
		m.ag.Indicator.Set("Searching…")
		return m, tea.Batch(searchCmd, m.maybeResizeEditorCmd())
	}
	return m, nil
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
func dialogOptionCols(termWidth int) (prefix string, starts, ends [3]int) {
	prefix = "Unsaved Changes! "
	labels := [3]string{"[save]", "[exit]", "[cancel]"}
	total := len(prefix)
	for _, l := range labels {
		total += len(l)
	}
	leftPad := (termWidth - total) / 2
	if leftPad < 0 {
		leftPad = 0
	}
	pos := leftPad + len(prefix)
	for i, l := range labels {
		starts[i] = pos
		ends[i] = pos + len(l)
		pos = ends[i]
	}
	return
}

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
		renderTitleBar(m.width, m.filepath, unsaved, m.showExitDialog, m.dialogFocus),
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

const (
	titleSaveIcon   = "🖫"
	titleCloseLabel = "X"
	titleMinLabel   = "–"
)

// titleBarLayout returns the column boundaries of the interactive title bar
// elements for mouse hit-testing. All values are 0-indexed absolute columns.
//
//	save icon:  [0, saveEnd)   — col 0 is leading space, icon follows
//	minimize:   [minStart, minEnd)
//	close:      [closeStart, width)  — last col is trailing space
func titleBarLayout(width int) (saveEnd, minStart, minEnd, closeStart int) {
	saveW := runewidth.StringWidth(titleSaveIcon)
	closeW := runewidth.StringWidth(titleCloseLabel) // 1
	minW := runewidth.StringWidth(titleMinLabel)     // 1

	// col 0 = leading space, cols 1..saveW = icon
	saveEnd = 1 + saveW
	// col width-1 = trailing space, close occupies width-1-closeW..width-2
	closeStart = width - 1 - closeW
	// 1-space gap, then minimize
	minEnd = closeStart - 1
	minStart = minEnd - minW
	return
}

// truncateTitleStr truncates s to at most maxW visual cells, appending "…".
func truncateTitleStr(s string, maxW int) string {
	if runewidth.StringWidth(s) <= maxW {
		return s
	}
	budget := maxW - 1 // reserve 1 cell for "…"
	if budget <= 0 {
		return "…"
	}
	w := 0
	for i, r := range s {
		rw := runewidth.RuneWidth(r)
		if w+rw > budget {
			return s[:i] + "…"
		}
		w += rw
	}
	return s + "…"
}

// formatTitlePath splits fp into a faint directory prefix and a bold filename.
// Priority: ~/... when under $HOME, then cwd-relative when strictly under cwd
// (no ".." traversal), then absolute path as fallback.
func formatTitlePath(fp string) (dir, base string) {
	base = filepath.Base(fp)
	if base == "" || base == "." {
		base = "Untitled"
	}

	// ~/... when the file lives under the home directory.
	if home, err := os.UserHomeDir(); err == nil {
		if rel, err := filepath.Rel(home, fp); err == nil && !strings.HasPrefix(rel, "..") {
			d := filepath.Dir(rel)
			if d == "." || d == "" {
				return "~/", base
			}
			return "~/" + d + string(filepath.Separator), base
		}
	}

	// Cwd-relative, but only when the file is strictly under cwd (no "..").
	if wd, err := os.Getwd(); err == nil {
		if rel, err := filepath.Rel(wd, fp); err == nil && !strings.HasPrefix(rel, "..") {
			d := filepath.Dir(rel)
			if d != "." && d != "" {
				return d + string(filepath.Separator), base
			}
			return "", base
		}
	}

	// Absolute path fallback.
	d := filepath.Dir(fp)
	if d != "" && d != "." {
		return d + string(filepath.Separator), base
	}
	return "", base
}

func renderTitleBar(width int, fp string, unsaved, showDialog bool, dialogFocus int) string {
	if showDialog {
		return renderDialogBar(width, dialogFocus)
	}

	dir, base := formatTitlePath(fp)
	if unsaved {
		base += "*"
	}

	saveW := runewidth.StringWidth(titleSaveIcon)
	leftW := 1 + saveW + 1 // leading space + icon + trailing space

	closeW := runewidth.StringWidth(titleCloseLabel)
	minW := runewidth.StringWidth(titleMinLabel)
	rightW := minW + 1 + closeW + 1 // min + space + close + trailing space

	centerAvail := width - leftW - rightW
	if centerAvail < 0 {
		centerAvail = 0
	}

	// Truncate to fit: drop dir first, then truncate base with "…".
	dirW := runewidth.StringWidth(dir)
	baseW := runewidth.StringWidth(base)
	if dirW+baseW > centerAvail {
		if baseW <= centerAvail {
			dir = "" // drop directory prefix; base alone fits
			dirW = 0
		} else {
			dir = ""
			dirW = 0
			base = truncateTitleStr(base, centerAvail)
			baseW = runewidth.StringWidth(base)
		}
	}

	centerPlain := dirW + baseW
	leftPad := (centerAvail - centerPlain) / 2
	if leftPad < 0 {
		leftPad = 0
	}
	rightPad := centerAvail - leftPad - centerPlain
	if rightPad < 0 {
		rightPad = 0
	}

	const (
		bg      = "\x1b[44m" // ANSI 4 background (blue)
		fgReset = "\x1b[39m" // reset foreground only, keep background
		rst     = "\x1b[0m"  // full reset
	)

	saveColor := "\x1b[37m" // ANSI 7 (white) — nothing to save
	if unsaved {
		saveColor = "\x1b[97m" // ANSI 15 (bright white) — unsaved
	}

	var b strings.Builder
	b.WriteString(bg)
	// Leading space + save icon
	b.WriteString(" ")
	b.WriteString(saveColor)
	b.WriteString(titleSaveIcon)
	b.WriteString(fgReset + " ")
	// Center padding + path
	b.WriteString(strings.Repeat(" ", leftPad))
	if dir != "" {
		b.WriteString("\x1b[37m") // ANSI 7 (white) — file path
		b.WriteString(dir)
		b.WriteString(fgReset)
	}
	b.WriteString("\x1b[1;3;97m") // ANSI 15 (bright white) bold italic — file name
	b.WriteString(base)
	b.WriteString("\x1b[22;23;39m") // reset bold, italic, fg — keep bg
	b.WriteString(strings.Repeat(" ", rightPad))
	// Minimize — bold ANSI 11 (bright yellow)
	b.WriteString("\x1b[1;93m")
	b.WriteString(titleMinLabel)
	b.WriteString("\x1b[22;39m ")
	// Close — bold ANSI 9 (bright red) + trailing space
	b.WriteString("\x1b[1;91m")
	b.WriteString(titleCloseLabel)
	b.WriteString("\x1b[22;39m " + rst)

	return b.String()
}

func renderDialogBar(width, dialogFocus int) string {
	const (
		base       = "\x1b[4m\x1b[34m"
		focusOpen  = "\x1b[44m\x1b[97m"
		focusClose = "\x1b[0m\x1b[4m\x1b[34m"
		rst        = "\x1b[0m"
	)

	prefix, _, _ := dialogOptionCols(width)
	labels := [3]string{"[save]", "[exit]", "[cancel]"}

	total := len(prefix)
	for _, l := range labels {
		total += len(l)
	}
	leftPad := (width - total) / 2
	if leftPad < 0 {
		leftPad = 0
	}
	rightPad := width - leftPad - total
	if rightPad < 0 {
		rightPad = 0
	}

	italicLabel := "\x1b[3mUnsaved Changes!\x1b[23m "

	var b strings.Builder
	b.WriteString(base)
	b.WriteString(strings.Repeat(" ", leftPad))
	b.WriteString(italicLabel)
	for i, label := range labels {
		if i == dialogFocus {
			b.WriteString(focusOpen)
			b.WriteString(label)
			b.WriteString(focusClose)
		} else {
			b.WriteString(label)
		}
	}
	b.WriteString(strings.Repeat(" ", rightPad))
	b.WriteString(rst)
	return b.String()
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
	if err := m.writeNote(); err != nil {
		m.ag.Indicator.SetError("auto-save failed: " + err.Error())
		return m, m.ag.Indicator.StaleCmd()
	}
	m.savedValue = content

	hash := runner.HashContent(content)
	rc := &runner.RunContext{
		ActivePath:      m.filepath,
		SnapshotContent: content,
		SnapshotHash:    hash,
	}
	m.runSnapshotHash = hash
	m.runSnapshotContent = content

	ctx, cancel := context.WithCancel(context.Background())
	m.runCancel = cancel
	m.runActive = true
	m.ag.SetRunActive(true)

	if m.mode == runner.ModeChat {
		m.pendingChatUserPrompt = userPrompt
	}

	opts := runner.RunOptions{
		Mode:           m.mode,
		UserPrompt:     userPrompt,
		TranscriptRows: m.transcriptRows,
	}
	stream, first := runner.StartCmd(ctx, m.llmCfg, rc, opts)
	m.runStream = stream
	return m, first
}

// finishRun clears run-active state and resets the send button. The stream
// pointer is left in place until RunStreamDoneMsg drains the channel close.
func (m appModel) finishRun() appModel {
	m.runActive = false
	m.runCancel = nil
	m.pendingChatUserPrompt = ""
	m.ag.SetRunActive(false)
	return m
}

func roundDuration(d time.Duration) time.Duration {
	if d < time.Second {
		return d.Round(10 * time.Millisecond)
	}
	return d.Round(100 * time.Millisecond)
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
		if err := json.Unmarshal([]byte(resultJSON), &raw); err != nil {
			return nil
		}
		results := make([]web.Result, len(raw))
		for i, r := range raw {
			results[i] = web.Result{Title: r.Title, URL: r.URL, Domain: r.Domain, Abstract: r.Abstract}
		}
		return m.recordSearchInTranscript(args.Query, results)
	case "web_fetch":
		var page struct {
			Title    string `json:"title"`
			URL      string `json:"url"`
			Markdown string `json:"markdown"`
		}
		if err := json.Unmarshal([]byte(resultJSON), &page); err != nil || page.URL == "" {
			return nil
		}
		return m.recordFetchInTranscript(web.Page{Title: page.Title, URL: page.URL, Markdown: page.Markdown})
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
