package agent

import (
	"strings"

	"github.com/atotto/clipboard"
	tea "github.com/charmbracelet/bubbletea"
)

// ── Page pager: rendering, navigation, selection, copy ─────────────────────────

func (wb WebBar) viewPage(innerWidth int) []string {
	h := wb.Height()
	contentRows := h - 1 // all rows except the hint row

	selStart, selEnd := orderSelection(wb.pageSelection.anchor, wb.pageCursor)
	hasSel := wb.pageSelection.active

	var lines []string
	if wb.page != nil && len(wb.pageLines) > 0 {
		end := wb.pageScroll + contentRows
		if end > len(wb.pageLines) {
			end = len(wb.pageLines)
		}
		for i := wb.pageScroll; i < end; i++ {
			display := wb.pageLines[i].display
			if hasSel {
				display = wb.applyLineSelection(display, i, selStart, selEnd)
			}
			display = wb.applySearchHighlights(display, i)
			if i == wb.pageCursor.line && !wb.searchMode {
				if span := wb.linkAtCol(i, wb.pageCursor.col); span != nil {
					display = injectCursorAtRuneRange(display, span.startCol, span.endCol)
				} else {
					display = injectCursorAtRune(display, wb.pageCursor.col)
				}
			}
			lines = append(lines, padTo(display, innerWidth))
		}
	}
	// Pad to contentRows with blank lines.
	for len(lines) < contentRows {
		lines = append(lines, strings.Repeat(" ", innerWidth))
	}

	if wb.searchMode {
		lines = append(lines, padTo(wb.viewSearchBar(), innerWidth))
	} else {
		hint := "↑↓←→ move  enter open link  alt+←/→ back/fwd  / search  ctrl+c copy  ctrl+o browser  esc close"
		lines = append(lines, padTo("\x1b[2m"+hint+"\x1b[0m", innerWidth))
	}
	return lines
}

// applyLineSelection applies the gray-background selection highlight to line
// i if it intersects the selection range [selStart, selEnd).
func (wb WebBar) applyLineSelection(display string, i int, selStart, selEnd pagerCursor) string {
	if i < selStart.line || i > selEnd.line {
		return display
	}
	from := 0
	to := -1
	if i == selStart.line {
		from = selStart.col
	}
	if i == selEnd.line {
		to = selEnd.col
	}
	if i == selStart.line && i == selEnd.line && from >= to {
		return display
	}
	return applySelectionRuneRange(display, from, to)
}

// ── Helpers ───────────────────────────────────────────────────────────────────
// handlePageKey dispatches keyboard input in wbPage state.
func (wb WebBar) handlePageKey(kMsg tea.KeyMsg, key string) (WebBar, tea.Cmd) {
	if wb.searchMode {
		return wb.handleSearchKey(kMsg, key)
	}
	switch key {
	case "/":
		wb.openSearch()
		return wb, nil

	case "esc":
		// If there are no search results to go back to (e.g. URL opened
		// directly from the transcript), close entirely instead of landing
		// on an empty results view.
		if len(wb.results) == 0 {
			return wb, func() tea.Msg { return WebClosedMsg{} }
		}
		// Push the current page onto back history so picking a different
		// result later still allows alt+left to return here.
		if wb.page != nil {
			wb.historyBack = append(wb.historyBack, *wb.page)
			wb.historyFwd = nil
		}
		wb.state = wbResults
		wb.page = nil
		wb.pageLines = nil
		wb.pageSourceLines = nil
		wb.pageScroll = 0
		wb.pageCursor = pagerCursor{}
		wb.pageSelection = pagerSelection{}
		return wb, nil

	case "ctrl+o", "shift+enter":
		if wb.page != nil {
			url := wb.page.URL
			return wb, func() tea.Msg { return WebOpenBrowserMsg{URL: url} }
		}

	case "enter":
		// Enter on a rendered markdown link opens it in the pager.
		// Pane.ApplyWebPage will push the current page onto historyBack when
		// the fetch resolves, so we don't push here.
		if span := wb.linkAtCol(wb.pageCursor.line, wb.pageCursor.col); span != nil {
			wb.state = wbLoading
			wb.loadMsg = "Fetching…"
			return wb, WebFetchCmdNoRecord(span.url)
		}

	case "alt+left":
		// Browser-style back navigation.
		if len(wb.historyBack) > 0 {
			prev := wb.historyBack[len(wb.historyBack)-1]
			wb.historyBack = wb.historyBack[:len(wb.historyBack)-1]
			if wb.page != nil {
				wb.historyFwd = append(wb.historyFwd, *wb.page)
			}
			wb.applyPage(prev)
		}
		return wb, nil

	case "alt+right":
		// Browser-style forward navigation.
		if len(wb.historyFwd) > 0 {
			next := wb.historyFwd[len(wb.historyFwd)-1]
			wb.historyFwd = wb.historyFwd[:len(wb.historyFwd)-1]
			if wb.page != nil {
				wb.historyBack = append(wb.historyBack, *wb.page)
			}
			wb.applyPage(next)
		}
		return wb, nil

	case "ctrl+c":
		return wb, wb.copySelectionCmd()

	// Viewport scrolling — does NOT move the cursor.
	case "pgup", "alt+up":
		pageRows := wb.pageContentRows()
		wb.pageScroll -= pageRows / 2
		if wb.pageScroll < 0 {
			wb.pageScroll = 0
		}
		return wb, nil
	case "pgdn", "alt+down":
		pageRows := wb.pageContentRows()
		maxScroll := wb.maxPageScroll(pageRows)
		wb.pageScroll += pageRows / 2
		if wb.pageScroll > maxScroll {
			wb.pageScroll = maxScroll
		}
		return wb, nil
	}

	// Cursor movement (plain + shift-extended).
	moved, shifted, handled := wb.moveCursor(key)
	if handled {
		wb.applyCursorMove(moved, shifted)
		return wb, nil
	}
	return wb, nil
}

// moveCursor maps a key string to a new cursor position (the second return),
// whether selection should be extended (shift modifier), and whether the key
// was recognized as a navigation key.
func (wb WebBar) moveCursor(key string) (pagerCursor, bool, bool) {
	cur := wb.pageCursor
	shifted := false
	stripped := func(i int) string {
		if i < 0 || i >= len(wb.pageLines) {
			return ""
		}
		return stripANSI(wb.pageLines[i].display)
	}
	lineLen := func(i int) int {
		return displayRuneCount(wb.lineDisplay(i))
	}
	last := func() pagerCursor {
		if len(wb.pageLines) == 0 {
			return pagerCursor{}
		}
		l := len(wb.pageLines) - 1
		return pagerCursor{line: l, col: lineLen(l)}
	}

	// snapLeft handles "step left" with link-as-atom behavior: if the cursor
	// is currently inside a link span, exit to the column just before the
	// span (or wrap to the previous line if the span starts at col 0).
	snapLeft := func(c pagerCursor) pagerCursor {
		if span := wb.linkAtCol(c.line, c.col); span != nil {
			if span.startCol > 0 {
				c.col = span.startCol - 1
			} else if c.line > 0 {
				c.line--
				c.col = lineLen(c.line)
			}
			return c
		}
		if c.col > 0 {
			c.col--
		} else if c.line > 0 {
			c.line--
			c.col = lineLen(c.line)
		}
		return c
	}
	// snapRight is the symmetric "step right" with link-as-atom behavior.
	snapRight := func(c pagerCursor) pagerCursor {
		if span := wb.linkAtCol(c.line, c.col); span != nil {
			c.col = span.endCol
			return c
		}
		if c.col < lineLen(c.line) {
			c.col++
		} else if c.line < len(wb.pageLines)-1 {
			c.line++
			c.col = 0
		}
		return c
	}
	// snapWordLeft / snapWordRight extend word movement with the same atom
	// rule: when on a link, jump to span boundary instead of into the middle
	// of the link.
	snapWordLeft := func(c pagerCursor) pagerCursor {
		if span := wb.linkAtCol(c.line, c.col); span != nil {
			if span.startCol > 0 {
				c.col = span.startCol - 1
			} else if c.line > 0 {
				c.line--
				c.col = lineLen(c.line)
			}
			return c
		}
		c.col = wordLeft(stripped(c.line), c.col)
		return c
	}
	snapWordRight := func(c pagerCursor) pagerCursor {
		if span := wb.linkAtCol(c.line, c.col); span != nil {
			c.col = span.endCol
			return c
		}
		c.col = wordRight(stripped(c.line), c.col)
		return c
	}

	switch key {
	case "left":
		cur = snapLeft(cur)
	case "right":
		cur = snapRight(cur)
	case "up":
		if cur.line > 0 {
			cur.line--
			if cur.col > lineLen(cur.line) {
				cur.col = lineLen(cur.line)
			}
		}
	case "down":
		if cur.line < len(wb.pageLines)-1 {
			cur.line++
			if cur.col > lineLen(cur.line) {
				cur.col = lineLen(cur.line)
			}
		}
	case "home":
		cur.col = 0
	case "end":
		cur.col = lineLen(cur.line)
	case "ctrl+left":
		cur = snapWordLeft(cur)
	case "ctrl+right":
		cur = snapWordRight(cur)
	case "ctrl+home":
		cur = pagerCursor{}
	case "ctrl+end":
		cur = last()

	case "shift+left":
		shifted = true
		cur = snapLeft(cur)
	case "shift+right":
		shifted = true
		cur = snapRight(cur)
	case "shift+up":
		shifted = true
		if cur.line > 0 {
			cur.line--
			if cur.col > lineLen(cur.line) {
				cur.col = lineLen(cur.line)
			}
		}
	case "shift+down":
		shifted = true
		if cur.line < len(wb.pageLines)-1 {
			cur.line++
			if cur.col > lineLen(cur.line) {
				cur.col = lineLen(cur.line)
			}
		}
	case "shift+home":
		shifted = true
		cur.col = 0
	case "shift+end":
		shifted = true
		cur.col = lineLen(cur.line)
	case "ctrl+shift+left":
		shifted = true
		cur = snapWordLeft(cur)
	case "ctrl+shift+right":
		shifted = true
		cur = snapWordRight(cur)
	case "ctrl+shift+home":
		shifted = true
		cur = pagerCursor{}
	case "ctrl+shift+end":
		shifted = true
		cur = last()

	default:
		return wb.pageCursor, false, false
	}
	return cur, shifted, true
}

// applyCursorMove writes the new cursor position back to the WebBar, manages
// selection state, and scrolls the viewport to keep the cursor visible.
func (wb *WebBar) applyCursorMove(newCur pagerCursor, shifted bool) {
	if shifted {
		if !wb.pageSelection.active {
			wb.pageSelection.anchor = wb.pageCursor
			wb.pageSelection.active = true
		}
	} else {
		wb.pageSelection = pagerSelection{}
	}
	wb.pageCursor = newCur
	wb.ensureCursorVisible()
}

// ensureCursorVisible scrolls so wb.pageCursor.line is within the visible
// rows.
func (wb *WebBar) ensureCursorVisible() {
	pageRows := wb.pageContentRows()
	if pageRows < 1 {
		return
	}
	if wb.pageCursor.line < wb.pageScroll {
		wb.pageScroll = wb.pageCursor.line
		return
	}
	if wb.pageCursor.line >= wb.pageScroll+pageRows {
		wb.pageScroll = wb.pageCursor.line - pageRows + 1
	}
	maxScroll := wb.maxPageScroll(pageRows)
	if wb.pageScroll > maxScroll {
		wb.pageScroll = maxScroll
	}
	if wb.pageScroll < 0 {
		wb.pageScroll = 0
	}
}

// handlePageMouse dispatches a mouse event in wbPage state. Coordinates are
// in pane-local space (Y=0 is the indicator row).
func (wb WebBar) handlePageMouse(msg tea.MouseMsg) (WebBar, tea.Cmd) {
	const contentTopY = 2 // indicator row (0) + top border (1) → content starts at row 2
	const contentLeftX = 1
	switch msg.Action {
	case tea.MouseActionPress:
		if msg.Button != tea.MouseButtonLeft {
			return wb, nil
		}
		if msg.Y < contentTopY || msg.Y >= contentTopY+wb.pageContentRows() {
			return wb, nil
		}
		pos := wb.screenToPager(msg.X-contentLeftX, msg.Y-contentTopY)
		wb.pageCursor = pos
		wb.pageSelection = pagerSelection{anchor: pos}
		wb.pageDragging = true
		return wb, nil

	case tea.MouseActionMotion:
		if !wb.pageDragging {
			return wb, nil
		}
		if msg.Y < contentTopY {
			return wb, nil
		}
		pos := wb.screenToPager(msg.X-contentLeftX, msg.Y-contentTopY)
		wb.pageCursor = pos
		if pos != wb.pageSelection.anchor {
			wb.pageSelection.active = true
		}
		wb.ensureCursorVisible()
		return wb, nil

	case tea.MouseActionRelease:
		if msg.Button == tea.MouseButtonLeft {
			wb.pageDragging = false
		}
		return wb, nil
	}
	return wb, nil
}

// screenToPager maps (contentX, contentY) — coordinates relative to the page
// content origin — to a pagerCursor, clamped to valid positions.
func (wb WebBar) screenToPager(contentX, contentY int) pagerCursor {
	if contentX < 0 {
		contentX = 0
	}
	if contentY < 0 {
		contentY = 0
	}
	line := wb.pageScroll + contentY
	if line < 0 {
		line = 0
	}
	if line >= len(wb.pageLines) {
		line = len(wb.pageLines) - 1
		if line < 0 {
			line = 0
		}
	}
	col := visualColToRuneOffset(wb.lineDisplay(line), contentX)
	return pagerCursor{line: line, col: col}
}

// pageContentRows is the number of visible page-content rows (excluding hint).
func (wb WebBar) pageContentRows() int {
	r := wb.Height() - 1
	if r < 1 {
		r = 1
	}
	return r
}

// maxPageScroll is the maximum valid pageScroll for the given content rows.
func (wb WebBar) maxPageScroll(pageRows int) int {
	m := len(wb.pageLines) - pageRows
	if m < 0 {
		return 0
	}
	return m
}

// ── Copy ──────────────────────────────────────────────────────────────────────
// copySelectionCmd writes the current selection to the system clipboard and
// returns a tea.Cmd that delivers WebCopiedMsg. Returns nil if there's nothing
// to copy.
//
// Copy semantics:
//   - Selection wholly within a single non-table line: copy the visible
//     (stripped-display) substring. Bold/italic/etc. asterisks preserved.
//   - Multi-line selection: copy pageLine.source for fully-enclosed lines and
//     visible substrings for partial start/end lines.
//   - Any selection that touches a table line: include the entire original
//     pipe-syntax markdown for that table, deduped by tableStart.
func (wb WebBar) copySelectionCmd() tea.Cmd {
	if !wb.pageSelection.active {
		return nil
	}
	a, b := orderSelection(wb.pageSelection.anchor, wb.pageCursor)
	if a == b {
		return nil
	}

	text := wb.extractSelection(a, b)
	if text == "" {
		return nil
	}
	if err := clipboard.WriteAll(text); err != nil {
		return nil
	}
	return func() tea.Msg { return WebCopiedMsg{} }
}

// orderSelection returns the two cursors in document order (start, end).
func orderSelection(a, b pagerCursor) (pagerCursor, pagerCursor) {
	if a.line < b.line || (a.line == b.line && a.col <= b.col) {
		return a, b
	}
	return b, a
}

// extractSelection materializes the selected text between start and end.
func (wb WebBar) extractSelection(start, end pagerCursor) string {
	var b strings.Builder
	emittedTables := make(map[int]bool)

	for i := start.line; i <= end.line && i < len(wb.pageLines); i++ {
		pl := wb.pageLines[i]
		if pl.inTable {
			if emittedTables[pl.tableStart] {
				continue
			}
			emittedTables[pl.tableStart] = true
			tEnd := pl.tableEnd
			if tEnd > len(wb.pageSourceLines) {
				tEnd = len(wb.pageSourceLines)
			}
			b.WriteString(strings.Join(wb.pageSourceLines[pl.tableStart:tEnd], "\n"))
			if i < end.line {
				b.WriteByte('\n')
			}
			continue
		}

		switch {
		case i == start.line && i == end.line:
			b.WriteString(strippedSubstring(pl.display, start.col, end.col))
		case i == start.line:
			b.WriteString(strippedSubstring(pl.display, start.col, -1))
			b.WriteByte('\n')
		case i == end.line:
			if end.col < displayRuneCount(pl.display) {
				b.WriteString(strippedSubstring(pl.display, 0, end.col))
			} else {
				b.WriteString(pl.source)
			}
		default:
			b.WriteString(pl.source)
			b.WriteByte('\n')
		}
	}
	return b.String()
}
