package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/atotto/clipboard"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/ejumper/aunic/agent"
	"github.com/ejumper/aunic/editor"
	"github.com/ejumper/aunic/llm"
	"github.com/ejumper/aunic/todos"
	"github.com/ejumper/aunic/voice"
	"github.com/ejumper/aunic/web"
)

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
		return m.handleMouse(msg)

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
