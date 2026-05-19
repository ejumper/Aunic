package editor

import (
	"fmt"
	"strings"

	"github.com/mattn/go-runewidth"
)

const tabWidth = 4

func gutterWidth(lineCount int) int {
	digits := 1
	for lineCount >= 10 {
		digits++
		lineCount /= 10
	}
	return digits + 1
}

func extractIndent(line string) (indent, content string) {
	for i, r := range line {
		if r != '\t' && r != ' ' {
			return line[:i], line[i:]
		}
	}
	return line, ""
}

func visualWidth(s string) int {
	w := 0
	for _, r := range s {
		if r == '\t' {
			w += tabWidth
		} else {
			w += runewidth.RuneWidth(r)
		}
	}
	return w
}

// ansiVisualWidth returns the visual width of s, skipping ANSI escape sequences.
func ansiVisualWidth(s string) int {
	w := 0
	inEscape := false
	for _, r := range s {
		if r == '\x1b' {
			inEscape = true
			continue
		}
		if inEscape {
			if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') {
				inEscape = false
			}
			continue
		}
		if r == '\t' {
			w += tabWidth
		} else {
			w += runewidth.RuneWidth(r)
		}
	}
	return w
}

// fillH1Underline pads the h1 content with ANSI 4 underlined spaces to fill
// the content width.
func fillH1Underline(wl string, contentW int) string {
	pad := contentW - ansiVisualWidth(wl)
	if pad < 0 {
		pad = 0
	}
	return wl + "\x1b[34;4m" + strings.Repeat(" ", pad) + "\x1b[0m"
}

// countLines returns the number of visible rows that a wrapped block occupies.
// An empty string still occupies one visible row.
func countLines(s string) int {
	if s == "" {
		return 1
	}
	return strings.Count(s, "\n") + 1
}

// wordWrap word-wraps s at width visual cells. It breaks at the last space
// before the limit when possible, falls back to a hard break before the
// overflowing rune when no space is available within the line. ANSI escape
// sequences are emitted unchanged and don't contribute to the visual width.
// Every input rune is preserved — trailing spaces, multi-space runs, and
// spaces at wrap boundaries all survive.
func wordWrap(s string, width int) string {
	if width <= 0 || s == "" {
		return s
	}
	runes := []rune(s)
	var b strings.Builder
	lineStart := 0
	lastSpace := -1
	lineW := 0
	inEsc := false
	inMeta := false // inside link-URL metadata: \x03 ... \x04 — zero-width.

	runeW := func(r rune) int {
		if r == '\t' {
			return tabWidth
		}
		return runewidth.RuneWidth(r)
	}

	for i, r := range runes {
		if r == '\x1b' {
			inEsc = true
			continue
		}
		if inEsc {
			if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') {
				inEsc = false
			}
			continue
		}
		if r == '\x03' {
			inMeta = true
			continue
		}
		if inMeta {
			if r == '\x04' {
				inMeta = false
			}
			continue
		}

		w := runeW(r)

		if lineW+w > width && lineW > 0 {
			breakAt := i
			if lastSpace > lineStart {
				breakAt = lastSpace + 1
			}
			b.WriteString(string(runes[lineStart:breakAt]))
			b.WriteByte('\n')
			lineStart = breakAt
			lastSpace = -1
			lineW = 0
			esc := false
			meta := false
			for j := lineStart; j < i; j++ {
				rj := runes[j]
				if rj == '\x1b' {
					esc = true
					continue
				}
				if esc {
					if (rj >= 'A' && rj <= 'Z') || (rj >= 'a' && rj <= 'z') {
						esc = false
					}
					continue
				}
				if rj == '\x03' {
					meta = true
					continue
				}
				if meta {
					if rj == '\x04' {
						meta = false
					}
					continue
				}
				lineW += runeW(rj)
				if rj == ' ' {
					lastSpace = j
				}
			}
		}

		if r == ' ' {
			lastSpace = i
		}
		lineW += w
	}

	b.WriteString(string(runes[lineStart:]))
	return b.String()
}

// wrapWithIndent word-wraps content at the given limit, prepending indent to
// every resulting line.
func wrapWithIndent(content, indent string, limit int) string {
	indentW := visualWidth(indent)
	wrapLimit := limit - indentW
	if wrapLimit < 10 {
		wrapLimit = 10
	}

	wrapped := wordWrap(content, wrapLimit)
	lines := strings.Split(wrapped, "\n")
	for i := range lines {
		lines[i] = indent + lines[i]
	}
	return strings.Join(lines, "\n")
}

// cursorScreenPos returns where the cursor sits within its logical line:
// rowOffset is the wrapped-row index (0 = first row), screenCol is the visual
// column on that wrapped row (cells from the start of the row, including
// indent on continuation rows).
//
// `col` is a byte offset into `line`.
//
// The full content is wrapped (not just the prefix up to col) so that
// word-break boundaries are computed with full context. Wrapping only the
// prefix gives wrong results at wrap boundaries: the character that triggers
// a break hasn't been seen yet, so the prefix fits on one row while the same
// text in the full wrap already broke to the next.
func cursorScreenPos(line string, col, contentW int) (rowOffset, screenCol int) {
	if col > len(line) {
		col = len(line)
	}
	indent, content := extractIndent(line)
	indentLen := len(indent)

	if col <= indentLen {
		return 0, visualWidth(line[:col])
	}

	contentEnd := col - indentLen
	if contentEnd > len(content) {
		contentEnd = len(content)
	}

	wrapped := wrapWithIndent(content, indent, contentW)
	rows := strings.Split(wrapped, "\n")

	// Walk wrap-rows accumulating content bytes (indent stripped) until we
	// reach the row that contains our target offset. Use strict > so that a
	// position exactly at a row boundary lands on the NEXT row — that is
	// where the character actually appears in the rendered output.
	consumed := 0
	for i, row := range rows {
		contentInRow := row[indentLen:]
		rowBytes := len(contentInRow)
		if consumed+rowBytes > contentEnd || i == len(rows)-1 {
			colInRow := contentEnd - consumed
			if colInRow > len(contentInRow) {
				colInRow = len(contentInRow)
			}
			return i, visualWidth(indent) + visualWidth(contentInRow[:colInRow])
		}
		consumed += rowBytes
	}
	return 0, 0
}

// wrapRowBounds returns the start (inclusive) and end (exclusive) byte offsets
// within the raw line for the visual wrap-row that contains the cursor byte
// position col. Used to implement visual home/end — navigating to the start or
// end of the current wrapped row rather than the logical line.
func wrapRowBounds(line string, col, contentW int) (home, end int) {
	if col > len(line) {
		col = len(line)
	}
	indent, content := extractIndent(line)
	indentLen := len(indent)

	if indentLen > 0 && col <= indentLen {
		return 0, indentLen
	}

	contentEnd := col - indentLen
	if contentEnd > len(content) {
		contentEnd = len(content)
	}

	wrapped := wrapWithIndent(content, indent, contentW)
	rows := strings.Split(wrapped, "\n")

	consumed := 0
	for i, row := range rows {
		contentInRow := row[indentLen:]
		rowBytes := len(contentInRow)

		if consumed+rowBytes > contentEnd || i == len(rows)-1 {
			home = indentLen + consumed
			end = indentLen + consumed + rowBytes
			if end > len(line) {
				end = len(line)
			}
			return
		}
		consumed += rowBytes
	}
	return col, col
}

// injectCursor inserts a reverse-video block at the given visual column in an
// ANSI-colored string. It skips over ANSI escape sequences when counting width
// so the cursor lands on the right terminal cell.  If the column is past the
// end of the line a highlighted space is appended instead.
func injectCursor(s string, visualCol int) string {
	var out strings.Builder
	vis := 0
	inEscape := false
	injected := false

	for _, r := range s {
		if injected {
			out.WriteRune(r)
			continue
		}
		if r == '\x1b' {
			inEscape = true
			out.WriteRune(r)
			continue
		}
		if inEscape {
			out.WriteRune(r)
			if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') {
				inEscape = false
			}
			continue
		}

		var cw int
		if r == '\t' {
			cw = tabWidth
		} else {
			cw = runewidth.RuneWidth(r)
		}

		if vis == visualCol || (vis < visualCol && vis+cw > visualCol) {
			out.WriteString("\x1b[7m")
			out.WriteRune(r)
			out.WriteString("\x1b[27m")
			vis += cw
			injected = true
			continue
		}

		out.WriteRune(r)
		vis += cw
	}

	if !injected {
		out.WriteString("\x1b[7m \x1b[27m")
	}
	return out.String()
}

// bufferPosToVisual maps a textarea-buffer position (logical row, rune col)
// to its rendered location: an absolute visual row (counting all wrapped
// rows from line 0) and a visual column within the content area (excluding
// the gutter).
//
// Callers pass row from m.textarea.Line() (always valid) or from search/marker
// positions that could be stale if the buffer changed between scan and render.
// The clamping below is the defensive guard for that case; it is not a hot
// path and the cost is negligible.
func (m Model) bufferPosToVisual(row, col int) (int, int) {
	lines := strings.Split(m.textarea.Value(), "\n")
	if row < 0 {
		return 0, 0
	}
	if row >= len(lines) {
		row = len(lines) - 1
		// strings.Split returns at least one element, so row >= 0 here;
		// the inner check below is defensive only.
		if row < 0 {
			return 0, 0
		}
	}

	runes := []rune(lines[row])
	if col > len(runes) {
		col = len(runes)
	}
	if col < 0 {
		col = 0
	}
	byteCol := len(string(runes[:col]))
	rowOff, screenCol := cursorScreenPos(lines[row], byteCol, m.contentW)

	base := 0
	for i := 0; i < row; i++ {
		indent, content := extractIndent(lines[i])
		wrapped := wrapWithIndent(content, indent, m.contentW)
		base += countLines(wrapped)
	}
	return base + rowOff, screenCol
}

// visualToBuffer is the inverse of bufferPosToVisual: it converts a visual
// cell coordinate (absolute row in the rendered output, column in the full
// rendered line including gutter) to a textarea-buffer position. Used by
// mouse drag-select to translate clicks into buffer positions.
func (m Model) visualToBuffer(visualRow, visualCol int) (row, col int) {
	lines := strings.Split(m.textarea.Value(), "\n")
	if len(lines) == 0 {
		return 0, 0
	}
	if visualRow < 0 {
		return 0, 0
	}

	cumulative := 0
	for i, line := range lines {
		indent, content := extractIndent(line)
		wrapped := wrapWithIndent(content, indent, m.contentW)
		wrappedRows := strings.Split(wrapped, "\n")

		if cumulative+len(wrappedRows) > visualRow {
			wrapRowIdx := visualRow - cumulative
			indentRuneLen := len([]rune(indent))
			indentW := visualWidth(indent)

			// Rune offset within the line content of where this wrap-row's
			// content begins (sum of previous wrap-rows' content rune counts).
			originContentRuneOffset := 0
			for k := 0; k < wrapRowIdx; k++ {
				prev := wrappedRows[k]
				prevRunes := len([]rune(prev))
				if prevRunes > indentRuneLen {
					originContentRuneOffset += prevRunes - indentRuneLen
				}
			}

			// Strip indent from current wrap-row to walk its content portion.
			currentRunes := []rune(wrappedRows[wrapRowIdx])
			contentRunes := currentRunes
			if len(contentRunes) > indentRuneLen {
				contentRunes = contentRunes[indentRuneLen:]
			} else {
				contentRunes = nil
			}

			targetCol := visualCol - m.gutterW
			if targetCol < indentW {
				return i, indentRuneLen + originContentRuneOffset
			}

			vis := indentW
			runeOffset := 0
			for _, r := range contentRunes {
				cw := runewidth.RuneWidth(r)
				if r == '\t' {
					cw = tabWidth
				}
				if vis+cw > targetCol {
					break
				}
				vis += cw
				runeOffset++
			}
			return i, indentRuneLen + originContentRuneOffset + runeOffset
		}
		cumulative += len(wrappedRows)
	}

	// Past last visible row: snap to end of document.
	lastIdx := len(lines) - 1
	return lastIdx, len([]rune(lines[lastIdx]))
}

// applySelectionBackground wraps the visual cells [fromCol, toCol) of a
// rendered line with the selection background ANSI. toCol < 0 means "to end
// of line". ANSI escape sequences in the input are passed through without
// counting toward visual width.
func applySelectionBackground(line string, fromCol, toCol int) string {
	const (
		selOpen  = "\x1b[103m" // bright-yellow background (ANSI 11)
		selClose = "\x1b[49m"  // background reset
	)
	var b strings.Builder
	vis := 0
	inEscape := false
	inSelection := false

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

		shouldBeInSel := vis >= fromCol && (toCol < 0 || vis < toCol)
		if shouldBeInSel && !inSelection {
			b.WriteString(selOpen)
			inSelection = true
		} else if !shouldBeInSel && inSelection {
			b.WriteString(selClose)
			inSelection = false
		}

		b.WriteRune(r)
		cw := runewidth.RuneWidth(r)
		if r == '\t' {
			cw = tabWidth
		}
		vis += cw
	}

	if inSelection {
		b.WriteString(selClose)
	}
	return b.String()
}

// applySelectionOverlay paints selection-background ANSI onto the visible
// lines (already split from viewport output) in place. It's a no-op when no
// selection is active or the selection has zero width.
func (m Model) applySelectionOverlay(lines []string) {
	if !m.selection.active {
		return
	}
	head := m.currentCursorPos()
	if m.selection.isEmpty(head) {
		return
	}
	start, end := m.selection.ordered(head)

	startVisRow, startVisCol := m.bufferPosToVisual(start.row, start.col)
	endVisRow, endVisCol := m.bufferPosToVisual(end.row, end.col)

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
		lines[visibleR] = applySelectionBackground(lines[visibleR], fromCol, toCol)
	}
}

// applyBracketOverlay underlines the bracket under (or just left of) the cursor
// and its matching counterpart. Skipped when a selection is active.
func (m Model) applyBracketOverlay(lines []string) {
	if m.selection.active {
		return
	}
	docLines := strings.Split(m.textarea.Value(), "\n")
	bm, found := findBracketPair(docLines, m.textarea.Line(), m.cursorCol())
	if !found {
		return
	}

	for _, pos := range [2][2]int{{bm.aRow, bm.aCol}, {bm.bRow, bm.bCol}} {
		visRow, contentCol := m.bufferPosToVisual(pos[0], pos[1])
		visibleR := visRow - m.viewport.YOffset
		if visibleR < 0 || visibleR >= len(lines) {
			continue
		}
		lines[visibleR] = applyBracketUnderline(lines[visibleR], m.gutterW+contentCol)
	}
}

// applyBracketUnderline underlines the single character at visualCol in an
// already-ANSI-decorated line. Same traversal pattern as injectCursor.
func applyBracketUnderline(s string, visualCol int) string {
	var out strings.Builder
	vis := 0
	inEscape := false
	done := false

	for _, r := range s {
		if done {
			out.WriteRune(r)
			continue
		}
		if r == '\x1b' {
			inEscape = true
			out.WriteRune(r)
			continue
		}
		if inEscape {
			out.WriteRune(r)
			if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') {
				inEscape = false
			}
			continue
		}

		var cw int
		if r == '\t' {
			cw = tabWidth
		} else {
			cw = runewidth.RuneWidth(r)
		}

		if vis == visualCol || (vis < visualCol && vis+cw > visualCol) {
			out.WriteString("\x1b[4m")
			out.WriteRune(r)
			out.WriteString("\x1b[24m")
			vis += cw
			done = true
			continue
		}

		out.WriteRune(r)
		vis += cw
	}
	return out.String()
}

// applySearchBackground is the search-match equivalent of applySelectionBackground.
// isCurrent selects orange (current match) vs yellow (other matches).
func applySearchBackground(line string, fromCol, toCol int, isCurrent bool) string {
	var open string
	if isCurrent {
		open = "\x1b[48;5;214m\x1b[30m" // orange bg, black fg
	} else {
		open = "\x1b[48;5;226m\x1b[30m" // yellow bg, black fg
	}
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

		shouldBeHL := vis >= fromCol && (toCol < 0 || vis < toCol)
		if shouldBeHL && !inHL {
			b.WriteString(open)
			inHL = true
		} else if !shouldBeHL && inHL {
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

// applySearchOverlay paints search-match highlights onto the visible lines in
// place. The current match gets an orange background; all others get yellow.
func (m Model) applySearchOverlay(lines []string) {
	if len(m.searchMatches) == 0 {
		return
	}
	for i, match := range m.searchMatches {
		startVisRow, startVisCol := m.bufferPosToVisual(match.start.row, match.start.col)
		endVisRow, endVisCol := m.bufferPosToVisual(match.end.row, match.end.col)
		isCurrent := i == m.searchCurrent

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
			lines[visibleR] = applySearchBackground(lines[visibleR], fromCol, toCol, isCurrent)
		}
	}
}

// ansiState tracks the active SGR (Select Graphic Rendition) terminal
// attributes while walking an ANSI-decorated string. It is used to re-emit
// the correct opening codes on continuation wrap-rows so that inline styles
// (bold, italic, …) survive soft-wraps even though the gutter separator
// contains a hard \x1b[0m reset.
type ansiState struct {
	bold      bool
	italic    bool
	underline bool
	strike    bool
	reverse   bool
	fg        int // -1 = terminal default; 30-37 standard; 90-97 bright
	bg        int // -1 = terminal default; 40-47 standard; 100-107 bright
}

func newAnsiState() ansiState { return ansiState{fg: -1, bg: -1} }

// emit serializes the state to zero or more ANSI escape sequences. Returns ""
// when the state is clean (nothing active to re-emit).
func (s ansiState) emit() string {
	var b strings.Builder
	if s.bold {
		b.WriteString("\x1b[1m")
	}
	if s.italic {
		b.WriteString("\x1b[3m")
	}
	if s.underline {
		b.WriteString("\x1b[4m")
	}
	if s.reverse {
		b.WriteString("\x1b[7m")
	}
	if s.strike {
		b.WriteString("\x1b[9m")
	}
	if s.fg != -1 {
		b.WriteString(fmt.Sprintf("\x1b[%dm", s.fg))
	}
	if s.bg != -1 {
		b.WriteString(fmt.Sprintf("\x1b[%dm", s.bg))
	}
	return b.String()
}

// applySGR updates the state for a single SGR parameter number.
func (s *ansiState) applySGR(n int) {
	switch {
	case n == 0:
		*s = newAnsiState()
	case n == 1:
		s.bold = true
	case n == 3:
		s.italic = true
	case n == 4:
		s.underline = true
	case n == 7:
		s.reverse = true
	case n == 9:
		s.strike = true
	case n == 22:
		s.bold = false
	case n == 23:
		s.italic = false
	case n == 24:
		s.underline = false
	case n == 27:
		s.reverse = false
	case n == 29:
		s.strike = false
	case n == 39:
		s.fg = -1
	case n == 49:
		s.bg = -1
	case (n >= 30 && n <= 37) || (n >= 90 && n <= 97):
		s.fg = n
	case (n >= 40 && n <= 47) || (n >= 100 && n <= 107):
		s.bg = n
	}
}

// walkAnsiState walks s, applies every CSI SGR sequence found, and returns the
// resulting state. Call with the state active at the start of s so that "off"
// codes (e.g. \x1b[23m italic-off) correctly clear carry-forward attributes.
func walkAnsiState(s string, state ansiState) ansiState {
	i := 0
	for i < len(s) {
		if s[i] != '\x1b' || i+1 >= len(s) || s[i+1] != '[' {
			i++
			continue
		}
		// Find the terminating letter of the CSI sequence.
		j := i + 2
		for j < len(s) && !((s[j] >= 'A' && s[j] <= 'Z') || (s[j] >= 'a' && s[j] <= 'z')) {
			j++
		}
		if j < len(s) && s[j] == 'm' {
			params := s[i+2 : j]
			if params == "" {
				state.applySGR(0)
			} else {
				for _, part := range strings.Split(params, ";") {
					n := 0
					for _, c := range part {
						if c >= '0' && c <= '9' {
							n = n*10 + int(c-'0')
						}
					}
					state.applySGR(n)
				}
			}
		}
		i = j + 1
	}
	return state
}

// buildView renders the full editor content with line numbers, a gutter,
// syntax highlighting, and indent-aware word wrapping.
//
// Inline styles (bold, italic, …) survive soft-wraps via ANSI state injection:
// the gutter separator always contains \x1b[0m (clean reset for the gutter
// itself), and continuation rows re-emit whatever SGR attributes were active at
// the end of the previous visual row before writing the content slice.
func buildView(lines []string, contentWidth, gutterW, cursorLine int, hlCache map[string]string, isMarkdown bool, filepath string) string {
	var b strings.Builder
	lineNumFmt := fmt.Sprintf("%%%dd", gutterW-1)

	// lineMap holds pre-highlighted content for lines that bypass the per-line
	// markdown highlighter: code block ranges (markdown) or the whole file
	// (non-markdown Chroma). A nil map means plain text for all lines.
	var lineMap map[int]string
	if isMarkdown {
		lineMap = parseCodeBlockRanges(lines, hlCache)
	} else {
		lineMap = highlightWholeFile(filepath, lines, hlCache)
	}

	for i, line := range lines {
		indent, content := extractIndent(line)
		h1 := isMarkdown && isH1(content)

		var highlighted string
		if hl, ok := lineMap[i]; ok {
			// Already fully highlighted by Chroma (indent baked in).
			highlighted = hl
			indent = ""
		} else if isMarkdown {
			highlighted = cachedHighlight(content, hlCache)
		} else {
			highlighted = content
		}
		wrapped := wrapWithIndent(highlighted, indent, contentWidth)
		wrappedLines := strings.Split(wrapped, "\n")

		rowState := newAnsiState() // active attributes at end of the previous visual row

		for j, wl := range wrappedLines {
			if j == 0 {
				if i == cursorLine {
					b.WriteString("\x1b[1m")
				} else {
					b.WriteString("\x1b[2m")
				}
				b.WriteString(fmt.Sprintf(lineNumFmt, i+1))
				b.WriteString("\x1b[22m")
			} else {
				b.WriteString(strings.Repeat(" ", gutterW-1))
			}
			b.WriteString("\x1b[90m│\x1b[0m")
			if j > 0 {
				b.WriteString(rowState.emit())
			}

		var written string
		if h1 {
			written = fillH1Underline(wl, contentWidth)
		} else {
			written = wl
		}
			b.WriteString(written)
			rowState = walkAnsiState(written, rowState)
			b.WriteByte('\n')
		}
	}
	return b.String()
}
