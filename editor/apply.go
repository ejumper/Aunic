package editor

import "strings"

// ConflictKind classifies why an ApplyNoteEdit refused to apply. ConflictNone
// means the edit succeeded.
type ConflictKind int

const (
	ConflictNone ConflictKind = iota
	ConflictNotFound
	ConflictAmbiguous
)

// ApplyEditResult reports the outcome of an ApplyNoteEdit call.
type ApplyEditResult struct {
	Applied  bool
	Count    int
	Conflict ConflictKind
}

// ApplyNoteEdit performs the same find/replace operation a note_edit tool call
// would perform against the file on disk, but against the live editor buffer
// instead. If old isn't present, or appears more than once when replaceAll is
// false, the buffer is left untouched and the result reports the conflict.
//
// The cursor is preserved heuristically: if it was before the first match, it
// stays put; otherwise it shifts by the byte delta so its logical position
// still tracks the same content.
func (m *Model) ApplyNoteEdit(old, new string, replaceAll bool) ApplyEditResult {
	cur := m.textarea.Value()
	count := strings.Count(cur, old)
	switch {
	case count == 0:
		return ApplyEditResult{Conflict: ConflictNotFound}
	case count > 1 && !replaceAll:
		return ApplyEditResult{Conflict: ConflictAmbiguous, Count: count}
	}

	row, col := m.textarea.Line(), m.cursorCol()
	cursorOffset := bufferPosToByteOffset(cur, row, col)
	firstEditOffset := strings.Index(cur, old)

	var updated string
	if replaceAll {
		updated = strings.ReplaceAll(cur, old, new)
	} else {
		updated = strings.Replace(cur, old, new, 1)
	}

	var newCursorOffset int
	if cursorOffset <= firstEditOffset {
		newCursorOffset = cursorOffset
	} else {
		delta := len(updated) - len(cur)
		newCursorOffset = cursorOffset + delta
	}
	if newCursorOffset < 0 {
		newCursorOffset = 0
	}
	if newCursorOffset > len(updated) {
		newCursorOffset = len(updated)
	}

	m.textarea.SetValue(updated)
	newRow, newCol := byteOffsetToBufferPos(updated, newCursorOffset)
	m.moveCursorTo(newRow, newCol)
	m.refreshAfterChange()
	return ApplyEditResult{Applied: true, Count: count}
}

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

// bufferPosToByteOffset converts a (row, col) buffer position to a byte
// offset within content. col is rune-based.
func bufferPosToByteOffset(content string, row, col int) int {
	if row < 0 {
		row = 0
	}
	if col < 0 {
		col = 0
	}
	lines := strings.Split(content, "\n")
	if row >= len(lines) {
		row = len(lines) - 1
	}
	if row < 0 {
		return 0
	}
	offset := 0
	for i := 0; i < row; i++ {
		offset += len(lines[i]) + 1
	}
	runes := []rune(lines[row])
	if col > len(runes) {
		col = len(runes)
	}
	offset += len(string(runes[:col]))
	return offset
}

// byteOffsetToBufferPos converts a byte offset within content to a (row, col)
// position. col is rune-based.
func byteOffsetToBufferPos(content string, offset int) (row, col int) {
	if offset < 0 {
		offset = 0
	}
	if offset > len(content) {
		offset = len(content)
	}
	before := content[:offset]
	row = strings.Count(before, "\n")
	if i := strings.LastIndex(before, "\n"); i >= 0 {
		col = len([]rune(before[i+1:]))
	} else {
		col = len([]rune(before))
	}
	return
}
