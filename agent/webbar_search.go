package agent

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
)

// ── In-page search ────────────────────────────────────────────────────────────

// viewSearchBar renders the bottom-row search input. less-style `/` prefix,
// followed by the input, followed by the match counter `n/total`.
func (wb WebBar) viewSearchBar() string {
	var counter string
	if len(wb.searchMatches) == 0 {
		if wb.searchInput.Value() != "" {
			counter = " \x1b[2m0/0\x1b[0m"
		}
	} else {
		counter = fmt.Sprintf(" \x1b[2m%d/%d\x1b[0m", wb.searchCurrent+1, len(wb.searchMatches))
	}
	return "/" + wb.searchInput.View() + counter
}

// openSearch enters search mode with an empty input. Existing matches and
// cursor stay intact until the user types or closes.
func (wb *WebBar) openSearch() {
	ti := textinput.New()
	ti.Prompt = ""
	ti.Focus()
	wb.searchInput = ti
	wb.searchMode = true
	wb.searchMatches = nil
	wb.searchCurrent = -1
}

// closeSearch exits search mode and clears matches.
func (wb *WebBar) closeSearch() {
	wb.searchMode = false
	wb.searchInput = textinput.Model{}
	wb.searchMatches = nil
	wb.searchCurrent = -1
}

// handleSearchKey dispatches input while searchMode is active.
func (wb WebBar) handleSearchKey(kMsg tea.KeyMsg, key string) (WebBar, tea.Cmd) {
	switch key {
	case "esc":
		wb.closeSearch()
		return wb, nil

	case "enter":
		if len(wb.searchMatches) > 0 && wb.searchCurrent >= 0 && wb.searchCurrent < len(wb.searchMatches) {
			m := wb.searchMatches[wb.searchCurrent]
			wb.pageCursor = pagerCursor{line: m.line, col: m.startCol}
			wb.pageSelection = pagerSelection{
				active: true,
				anchor: pagerCursor{line: m.line, col: m.endCol},
			}
			wb.ensureCursorVisible()
		}
		wb.closeSearch()
		return wb, nil

	case "tab":
		if len(wb.searchMatches) > 0 {
			wb.searchCurrent = (wb.searchCurrent + 1) % len(wb.searchMatches)
			wb.scrollToCurrentMatch()
		}
		return wb, nil

	case "shift+tab":
		if len(wb.searchMatches) > 0 {
			wb.searchCurrent = (wb.searchCurrent - 1 + len(wb.searchMatches)) % len(wb.searchMatches)
			wb.scrollToCurrentMatch()
		}
		return wb, nil
	}

	// Default: forward to textinput, recompute matches if value changed.
	prev := wb.searchInput.Value()
	var cmd tea.Cmd
	wb.searchInput, cmd = wb.searchInput.Update(kMsg)
	if wb.searchInput.Value() != prev {
		wb.runSearch()
	}
	return wb, cmd
}

// runSearch rebuilds searchMatches against the current query and resets the
// current-match index to 0 (or -1 if no matches).
func (wb *WebBar) runSearch() {
	query := wb.searchInput.Value()
	wb.searchMatches = nil
	wb.searchCurrent = -1
	if query == "" {
		return
	}
	q := strings.ToLower(query)
	for i, pl := range wb.pageLines {
		stripped := strings.ToLower(stripANSI(pl.display))
		if stripped == "" {
			continue
		}
		// Walk byte by byte but track rune offset.
		runes := []rune(stripped)
		qRunes := []rune(q)
		for j := 0; j+len(qRunes) <= len(runes); j++ {
			matched := true
			for k := 0; k < len(qRunes); k++ {
				if runes[j+k] != qRunes[k] {
					matched = false
					break
				}
			}
			if matched {
				wb.searchMatches = append(wb.searchMatches, pagerSearchMatch{
					line:     i,
					startCol: j,
					endCol:   j + len(qRunes),
				})
			}
		}
	}
	if len(wb.searchMatches) > 0 {
		wb.searchCurrent = 0
		wb.scrollToCurrentMatch()
	}
}

// scrollToCurrentMatch scrolls so the current match line is visible.
func (wb *WebBar) scrollToCurrentMatch() {
	if wb.searchCurrent < 0 || wb.searchCurrent >= len(wb.searchMatches) {
		return
	}
	target := wb.searchMatches[wb.searchCurrent].line
	pageRows := wb.pageContentRows()
	if pageRows < 1 {
		return
	}
	if target < wb.pageScroll {
		wb.pageScroll = target
	} else if target >= wb.pageScroll+pageRows {
		wb.pageScroll = target - pageRows + 1
	}
	if max := wb.maxPageScroll(pageRows); wb.pageScroll > max {
		wb.pageScroll = max
	}
	if wb.pageScroll < 0 {
		wb.pageScroll = 0
	}
}

// applySearchHighlights wraps every match on line i with yellow background;
// the current match gets orange. Applied after applyLineSelection so matches
// stack on top of selection.
func (wb WebBar) applySearchHighlights(display string, i int) string {
	const (
		matchOpen   = "\x1b[48;5;226m\x1b[30m"
		currentOpen = "\x1b[48;5;214m\x1b[30m"
		closeBoth   = "\x1b[39m\x1b[49m"
	)
	if len(wb.searchMatches) == 0 {
		return display
	}
	// Collect matches on this line, sorted by startCol descending so we can
	// apply them right-to-left without invalidating earlier offsets (we use
	// rune indices, so right-to-left preserves left-side positions).
	type lineMatch struct {
		startCol, endCol int
		current          bool
	}
	var ms []lineMatch
	for idx, m := range wb.searchMatches {
		if m.line != i {
			continue
		}
		ms = append(ms, lineMatch{m.startCol, m.endCol, idx == wb.searchCurrent})
	}
	if len(ms) == 0 {
		return display
	}
	// Sort ascending by startCol (no overlapping matches expected since literal search).
	for a := 1; a < len(ms); a++ {
		for b := a; b > 0 && ms[b].startCol < ms[b-1].startCol; b-- {
			ms[b], ms[b-1] = ms[b-1], ms[b]
		}
	}

	var out strings.Builder
	seen := 0
	inEsc := false
	mi := 0
	inMatch := false
	for _, r := range display {
		if r == '\x1b' {
			out.WriteRune(r)
			inEsc = true
			continue
		}
		if inEsc {
			out.WriteRune(r)
			if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') {
				inEsc = false
			}
			continue
		}
		// Open match if we're entering one.
		if !inMatch && mi < len(ms) && seen == ms[mi].startCol {
			if ms[mi].current {
				out.WriteString(currentOpen)
			} else {
				out.WriteString(matchOpen)
			}
			inMatch = true
		}
		out.WriteRune(r)
		seen++
		if inMatch && mi < len(ms) && seen >= ms[mi].endCol {
			out.WriteString(closeBoth)
			inMatch = false
			mi++
		}
	}
	if inMatch {
		out.WriteString(closeBoth)
	}
	return out.String()
}

// truncateToWidth truncates s to at most maxW visual cells, appending "…" if
// truncation occurred.
func truncateToWidth(s string, maxW int) string {
	if visualWidth(s) <= maxW {
		return s
	}
	runes := []rune(s)
	for len(runes) > 0 && visualWidth(string(runes)) > maxW-1 {
		runes = runes[:len(runes)-1]
	}
	return string(runes) + "…"
}
