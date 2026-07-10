package agent

import (
	"strings"
)

// ── Results-list rendering ────────────────────────────────────────────────────

func (wb WebBar) viewResults(innerWidth int) []string {
	if len(wb.results) == 0 {
		return []string{
			padTo("No results.", innerWidth),
			padTo("\x1b[2mesc close\x1b[0m", innerWidth),
		}
	}

	avail := wb.effectiveMaxRows() - 1
	var lines []string
	for i := wb.topResult; i < len(wb.results); i++ {
		h := wb.resultItemHeight(i)
		if len(lines)+h > avail && len(lines) > 0 {
			break
		}
		lines = append(lines, wb.renderResult(i, innerWidth)...)
	}

	hint := "↑↓ nav  → expand  ← collapse  enter fetch  ctrl+o browser  esc close"
	lines = append(lines, padTo("\x1b[2m"+hint+"\x1b[0m", innerWidth))
	return lines
}
func (wb WebBar) renderResult(i, innerWidth int) []string {
	r := wb.results[i]
	focused := i == wb.cursor

	title := truncateToWidth(r.Title, innerWidth)
	titleLine := padTo(title, innerWidth)

	// Domain line: "    domain.com"
	domainLine := padTo("    "+r.Domain, innerWidth)

	if focused {
		titleLine = "\x1b[7m" + titleLine + "\x1b[27m"
		domainLine = "\x1b[7m" + domainLine + "\x1b[27m"
	}

	out := []string{titleLine, domainLine}

	if wb.expanded[i] && r.Abstract != "" {
		for _, sl := range wb.snippetLines(r.Abstract) {
			line := padTo("    "+sl, innerWidth)
			if focused {
				line = "\x1b[7m" + line + "\x1b[27m"
			}
			out = append(out, line)
		}
	}

	return out
}
func (wb WebBar) resultItemHeight(i int) int {
	if i < 0 || i >= len(wb.results) {
		return 0
	}
	base := 2 // title + domain
	if wb.expanded[i] && wb.results[i].Abstract != "" {
		return base + len(wb.snippetLines(wb.results[i].Abstract))
	}
	return base
}
func (wb WebBar) snippetLines(abstract string) []string {
	if abstract == "" || wb.innerWidth < 5 {
		return nil
	}
	wrapW := wb.innerWidth - 4 // 4-char "    " indent
	if wrapW < 10 {
		wrapW = 10
	}
	wrapped := wordWrap(abstract, wrapW)
	lines := strings.Split(wrapped, "\n")
	if len(lines) > 3 {
		lines = lines[:3]
	}
	return lines
}

// ensureVisible adjusts topResult so that wb.cursor is visible within the
// current maxRows allocation.
func ensureVisible(wb *WebBar) {
	if len(wb.results) == 0 {
		return
	}
	if wb.cursor < wb.topResult {
		wb.topResult = wb.cursor
		return
	}
	avail := wb.effectiveMaxRows() - 1
	for wb.topResult < wb.cursor {
		rows := 0
		cursorSeen := false
		for i := wb.topResult; i < len(wb.results); i++ {
			h := wb.resultItemHeight(i)
			if rows+h > avail && rows > 0 {
				break
			}
			rows += h
			if i == wb.cursor {
				cursorSeen = true
				break
			}
		}
		if cursorSeen {
			break
		}
		wb.topResult++
	}
}
