package agent

import (
	"strings"

	"github.com/mattn/go-runewidth"
)

const tabWidth = 4

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
	inEsc := false
	for _, r := range s {
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
		if r == '\t' {
			w += tabWidth
		} else {
			w += runewidth.RuneWidth(r)
		}
	}
	return w
}

// wordWrap word-wraps s at width visual cells. ANSI escape sequences are
// emitted unchanged and don't contribute to visual width.
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

// cursorScreenPos returns (rowOffset, screenCol) for a cursor at byte offset
// col within a logical line, given the content wrap width.
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

// wrapRowBounds returns the [home, end) byte range within line for the visual
// wrap-row containing cursor byte offset col.
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

// injectCursor inserts reverse-video at the given visual column in an
// ANSI-colored string, or appends a highlighted space if past EOL.
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

// applySelectionBackground wraps visual cells [fromCol, toCol) with the
// selection background. toCol < 0 means extend to end of line.
func applySelectionBackground(line string, fromCol, toCol int) string {
	const (
		selOpen  = "\x1b[103m"
		selClose = "\x1b[49m"
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
