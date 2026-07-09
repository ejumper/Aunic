package main

import (
	tea "github.com/charmbracelet/bubbletea"
	"github.com/ejumper/aunic/editor"
)

// handleMouse routes a mouse event to the title bar, editor, transcript
// bar, or agent pane based on the vertical layout bands. Lifted verbatim
// from Update's tea.MouseMsg case.
func (m appModel) handleMouse(msg tea.MouseMsg) (tea.Model, tea.Cmd) {
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
}
