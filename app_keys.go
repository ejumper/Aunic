package main

import (
	"github.com/atotto/clipboard"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/ejumper/aunic/editor"
)

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
			m.saveNote()
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
