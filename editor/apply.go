package editor

import "strings"

// SetContent replaces the entire buffer with content. The cursor is clamped
// into the new buffer's extents but otherwise left where it was.
func (m *Model) SetContent(content string) {
	row, col := m.textarea.Line(), m.cursorCol()
	m.textarea.SetValue(content)

	lines := strings.Split(content, "\n")
	if row >= len(lines) {
		row = len(lines) - 1
	}
	if row < 0 {
		row = 0
	}
	if row < len(lines) {
		if rc := len([]rune(lines[row])); col > rc {
			col = rc
		}
	} else {
		col = 0
	}
	m.moveCursorTo(row, col)
	m.refreshAfterChange()
}
