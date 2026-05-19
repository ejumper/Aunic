package editor

import (
	"fmt"
	"strings"

	"github.com/mattn/go-runewidth"
)

// MarkerSpan is a raw-byte range + ANSI color number for the marker overlay.
// It mirrors markers.HighlightRange; keeping it in the editor package avoids
// an import cycle.
type MarkerSpan struct {
	Start int
	End   int
	Color int // ANSI color: 1=%, 2=@, 5=$, 6=!
}

// SetMarkerHighlight stores the background-color spans (wrapper tokens) and
// underline-color spans (body text) for the marker overlay applied in View().
// Call with nil slices to clear.
func (m *Model) SetMarkerHighlight(bg, ul []MarkerSpan) {
	m.markerBg = bg
	m.markerUl = ul
}

// applyMarkerOverlay paints marker background and underline-color highlights
// onto the already-rendered viewport lines. It is called at the start of the
// overlay stack in View() so that selection / search / cursor can overwrite.
func (m Model) applyMarkerOverlay(lines []string) {
	if len(m.markerBg) == 0 && len(m.markerUl) == 0 {
		return
	}
	content := m.textarea.Value()
	bufLines := strings.Split(content, "\n")

	for _, span := range m.markerBg {
		m.applySpanOverlay(lines, bufLines, span, false)
	}
	for _, span := range m.markerUl {
		m.applySpanOverlay(lines, bufLines, span, true)
	}
}

// applySpanOverlay applies one MarkerSpan to the visible lines. isUL selects
// between background+foreground styling (false) and underline-color styling
// (true). Follows the same multi-line logic as applySelectionOverlay.
func (m Model) applySpanOverlay(lines, bufLines []string, span MarkerSpan, isUL bool) {
	startRow, startByteCol := rawOffsetToBufferPos(bufLines, span.Start)
	endRow, endByteCol := rawOffsetToBufferPos(bufLines, span.End)

	startVisRow, startVisCol := m.bufferPosToVisual(startRow, runeColFromByte(bufLines, startRow, startByteCol))
	endVisRow, endVisCol := m.bufferPosToVisual(endRow, runeColFromByte(bufLines, endRow, endByteCol))

	for r := startVisRow; r <= endVisRow; r++ {
		visibleR := r - m.viewport.YOffset
		if visibleR < 0 || visibleR >= len(lines) {
			continue
		}

		var fromCol, toCol int
		if r == startVisRow {
			fromCol = m.gutterW + startVisCol
		} else {
			fromCol = m.gutterW
		}
		if r == endVisRow {
			toCol = m.gutterW + endVisCol
		} else {
			toCol = -1
		}

		if isUL {
			lines[visibleR] = applyMarkerUnderlineColor(lines[visibleR], fromCol, toCol, span.Color)
		} else {
			lines[visibleR] = applyMarkerBackground(lines[visibleR], fromCol, toCol, span.Color)
		}
	}
}

// rawOffsetToBufferPos converts a raw-byte offset in the joined note content
// to a (logicalRow, byteCol) buffer position. Content lines are separated by
// '\n'. If offset is past the end of the last line it clamps to line end.
func rawOffsetToBufferPos(bufLines []string, offset int) (row, byteCol int) {
	for i, line := range bufLines {
		lineLen := len(line) + 1 // +1 for '\n'
		if offset <= len(line) {
			return i, offset
		}
		offset -= lineLen
	}
	last := len(bufLines) - 1
	if last < 0 {
		return 0, 0
	}
	return last, len(bufLines[last])
}

// runeColFromByte converts a byte offset within a line to a rune column,
// which is what bufferPosToVisual expects as its col argument.
func runeColFromByte(bufLines []string, row, byteCol int) int {
	if row >= len(bufLines) {
		return 0
	}
	line := bufLines[row]
	if byteCol > len(line) {
		byteCol = len(line)
	}
	return len([]rune(line[:byteCol]))
}

// applyMarkerBackground wraps the visual cells [fromCol, toCol) of an
// ANSI-decorated line with background + foreground colors for a marker token.
// toCol < 0 means to end of line.
//
// ANSI color numbers: bg uses 40+color (standard) or 100+color (bright, not
// needed here), fg is 97 (bright white / ANSI 15 equivalent).
func applyMarkerBackground(line string, fromCol, toCol, ansiColor int) string {
	open := fmt.Sprintf("\x1b[%dm\x1b[97m", 40+ansiColor)
	const close = "\x1b[49m\x1b[39m"

	var b strings.Builder
	vis := 0
	inEscape := false
	inHL := false

	for _, r := range line {
		if r == '\x1b' {
			b.WriteRune(r)
			inEscape = true
			continue
		}
		if inEscape {
			b.WriteRune(r)
			if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') {
				inEscape = false
			}
			continue
		}

		shouldHL := vis >= fromCol && (toCol < 0 || vis < toCol)
		if shouldHL && !inHL {
			b.WriteString(open)
			inHL = true
		} else if !shouldHL && inHL {
			b.WriteString(close)
			inHL = false
		}

		b.WriteRune(r)
		cw := runewidth.RuneWidth(r)
		if r == '\t' {
			cw = tabWidth
		}
		vis += cw
	}
	if inHL {
		b.WriteString(close)
	}
	return b.String()
}

// applyMarkerUnderlineColor wraps [fromCol, toCol) with SGR 4 (underline on)
// and SGR 58 (underline color). Tracks underline state so that at range end
// it restores the correct on/off state: SGR 59 always (reset underline color),
// SGR 24 only if underline was off before the range started.
func applyMarkerUnderlineColor(line string, fromCol, toCol, ansiColor int) string {
	open := fmt.Sprintf("\x1b[4m\x1b[58;5;%dm", ansiColor)

	var b strings.Builder
	// Small buffer to accumulate escape sequences for state tracking.
	var escBuf strings.Builder
	vis := 0
	inEscape := false
	inHL := false
	ulBefore := false // underline state just before range start
	curUL := false    // running underline state

	for _, r := range line {
		if r == '\x1b' {
			inEscape = true
			escBuf.WriteRune(r)
			continue
		}
		if inEscape {
			escBuf.WriteRune(r)
			if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') {
				inEscape = false
				seq := escBuf.String()
				escBuf.Reset()
				// Track underline state from SGR sequences.
				if r == 'm' {
					curUL = parseUnderlineState(seq, curUL)
				}
				b.WriteString(seq)
			}
			continue
		}

		shouldHL := vis >= fromCol && (toCol < 0 || vis < toCol)
		if shouldHL && !inHL {
			ulBefore = curUL
			b.WriteString(open)
			curUL = true
			inHL = true
		} else if !shouldHL && inHL {
			b.WriteString("\x1b[59m") // reset underline color
			if !ulBefore {
				b.WriteString("\x1b[24m") // restore underline-off state
				curUL = false
			}
			inHL = false
		}

		b.WriteRune(r)
		cw := runewidth.RuneWidth(r)
		if r == '\t' {
			cw = tabWidth
		}
		vis += cw
	}

	if inHL {
		b.WriteString("\x1b[59m")
		if !ulBefore {
			b.WriteString("\x1b[24m")
		}
	}
	return b.String()
}

// InsertSpan is a raw-byte range marking content that was just inserted by a
// model edit/write/edit_at. The editor renders it in ANSI 2 (green
// foreground) until the buffer changes again.
type InsertSpan struct {
	Start int
	End   int
}

// SetInsertHighlight stores the inserted-content spans for the insert overlay.
// Call with nil to clear.
func (m *Model) SetInsertHighlight(spans []InsertSpan) {
	m.insertSpans = spans
}

// applyInsertOverlay paints ANSI 2 (green) foreground onto bytes that match
// any of the stored insert spans. Walks the live editor buffer to convert raw
// byte offsets to visual positions, then applies the fg-color change on each
// visible line.
func (m Model) applyInsertOverlay(lines []string) {
	if len(m.insertSpans) == 0 {
		return
	}
	content := m.textarea.Value()
	bufLines := strings.Split(content, "\n")

	for _, span := range m.insertSpans {
		startRow, startByteCol := rawOffsetToBufferPos(bufLines, span.Start)
		endRow, endByteCol := rawOffsetToBufferPos(bufLines, span.End)

		startVisRow, startVisCol := m.bufferPosToVisual(startRow, runeColFromByte(bufLines, startRow, startByteCol))
		endVisRow, endVisCol := m.bufferPosToVisual(endRow, runeColFromByte(bufLines, endRow, endByteCol))

		for r := startVisRow; r <= endVisRow; r++ {
			visibleR := r - m.viewport.YOffset
			if visibleR < 0 || visibleR >= len(lines) {
				continue
			}
			var fromCol, toCol int
			if r == startVisRow {
				fromCol = m.gutterW + startVisCol
			} else {
				fromCol = m.gutterW
			}
			if r == endVisRow {
				toCol = m.gutterW + endVisCol
			} else {
				toCol = -1
			}
			lines[visibleR] = applyInsertFgColor(lines[visibleR], fromCol, toCol, 2)
		}
	}
}

// applyInsertFgColor wraps [fromCol, toCol) with SGR 3X (foreground color)
// while tracking the underlying fg state so that at range end the prior
// foreground color is restored — preserving the rest of the line's markdown
// styling. toCol < 0 means to end of line.
func applyInsertFgColor(line string, fromCol, toCol, ansiColor int) string {
	open := fmt.Sprintf("\x1b[%dm", 30+ansiColor)
	var b strings.Builder
	var escBuf strings.Builder
	vis := 0
	inEscape := false
	inHL := false
	fgBefore := -1 // -1 = default fg (SGR 39)
	curFg := -1

	restore := func() string {
		if fgBefore == -1 {
			return "\x1b[39m"
		}
		return fmt.Sprintf("\x1b[%dm", fgBefore)
	}

	for _, r := range line {
		if r == '\x1b' {
			inEscape = true
			escBuf.WriteRune(r)
			continue
		}
		if inEscape {
			escBuf.WriteRune(r)
			if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') {
				inEscape = false
				seq := escBuf.String()
				escBuf.Reset()
				if r == 'm' {
					curFg = parseFgState(seq, curFg)
				}
				b.WriteString(seq)
			}
			continue
		}

		shouldHL := vis >= fromCol && (toCol < 0 || vis < toCol)
		if shouldHL && !inHL {
			fgBefore = curFg
			b.WriteString(open)
			curFg = 30 + ansiColor
			inHL = true
		} else if !shouldHL && inHL {
			b.WriteString(restore())
			curFg = fgBefore
			inHL = false
		}

		b.WriteRune(r)
		cw := runewidth.RuneWidth(r)
		if r == '\t' {
			cw = tabWidth
		}
		vis += cw
	}
	if inHL {
		b.WriteString(restore())
	}
	return b.String()
}

// parseFgState updates a foreground-color state from an SGR escape sequence.
// Returns -1 for "default foreground" (no explicit color active).
func parseFgState(seq string, current int) int {
	start := strings.IndexByte(seq, '[')
	if start < 0 {
		return current
	}
	params := seq[start+1 : len(seq)-1]
	if params == "" {
		return -1
	}
	fg := current
	for _, part := range strings.Split(params, ";") {
		n := 0
		for _, c := range part {
			if c >= '0' && c <= '9' {
				n = n*10 + int(c-'0')
			}
		}
		switch {
		case n == 0, n == 39:
			fg = -1
		case (n >= 30 && n <= 37) || (n >= 90 && n <= 97):
			fg = n
		}
	}
	return fg
}

// parseUnderlineState extracts the final underline on/off state from an SGR
// escape sequence (e.g. "\x1b[1;4;32m"). current is the state before this
// sequence arrived.
func parseUnderlineState(seq string, current bool) bool {
	// seq is "\x1b[...m"; extract the params between '[' and 'm'.
	start := strings.IndexByte(seq, '[')
	if start < 0 {
		return current
	}
	params := seq[start+1 : len(seq)-1] // strip leading '[' and trailing 'm'
	if params == "" {
		return false // bare \x1b[m = SGR 0 reset
	}
	ul := current
	for _, part := range strings.Split(params, ";") {
		n := 0
		for _, c := range part {
			if c >= '0' && c <= '9' {
				n = n*10 + int(c-'0')
			}
		}
		switch n {
		case 0:
			ul = false
		case 4:
			ul = true
		case 24:
			ul = false
		}
	}
	return ul
}
