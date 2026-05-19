package main

// This file owns the focus model: which pane currently routes keyboard input
// (editor / transcript / prompt), the helper to switch focus cleanly, and the
// tab-style focus cycle. It is the single source of truth for "what does it
// mean for the prompt to be focused" and the place to add future focus
// invariants (web-bar coupling, dialog suppression, etc.) without scattering
// changes through Update.
//
// NOTE: many Update cases in app.go still mutate m.promptFocus / m.ag /
// m.editor inline alongside other state (e.g. opening the web bar in the same
// block as taking focus). Those sites were left in place — the surrounding
// state varies enough that mechanically replacing them with setFocus() carries
// real regression risk. New focus transitions should call setFocus().

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
