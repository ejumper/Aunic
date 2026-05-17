package agent

import (
	"context"
	"fmt"
	"strings"

	"github.com/atotto/clipboard"
	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/ejumper/aunic/web"
)

// ── Async messages ────────────────────────────────────────────────────────────

// WebSearchDoneMsg is delivered when the background ddgr search completes.
type WebSearchDoneMsg struct {
	Results []web.Result
	Err     error
}

// WebFetchDoneMsg is delivered when the background page fetch completes.
type WebFetchDoneMsg struct {
	Page       web.Page
	Err        error
	SkipRecord bool // true when opened from transcript — don't add a new transcript entry
}

// WebClosedMsg is emitted by WebBar when the user closes the web bar (ESC).
type WebClosedMsg struct{}

// WebOpenBrowserMsg is emitted when the user opens the current URL in the
// system browser (ctrl+o or shift+enter in results or page view).
type WebOpenBrowserMsg struct{ URL string }

// WebCopiedMsg is emitted after ctrl+c successfully copies a selection.
type WebCopiedMsg struct{}

// ── Async commands ────────────────────────────────────────────────────────────

// WebSearchCmd returns a tea.Cmd that runs a DDG search and delivers
// WebSearchDoneMsg.
func WebSearchCmd(query string, n int) tea.Cmd {
	return func() tea.Msg {
		results, err := web.Search(context.Background(), query, n)
		return WebSearchDoneMsg{Results: results, Err: err}
	}
}

// WebFetchCmd returns a tea.Cmd that fetches and converts a page, delivering
// WebFetchDoneMsg.
func WebFetchCmd(url string) tea.Cmd {
	return func() tea.Msg {
		page, err := web.Fetch(context.Background(), url)
		return WebFetchDoneMsg{Page: page, Err: err}
	}
}

// WebFetchCmdNoRecord is like WebFetchCmd but sets SkipRecord=true so the app
// skips adding a new transcript entry. Used when opening a URL from the
// transcript bar.
func WebFetchCmdNoRecord(url string) tea.Cmd {
	return func() tea.Msg {
		page, err := web.Fetch(context.Background(), url)
		return WebFetchDoneMsg{Page: page, Err: err, SkipRecord: true}
	}
}

// ── Internal state ────────────────────────────────────────────────────────────

type wbState int

const (
	wbLoading wbState = iota
	wbResults
	wbPage
)

// pagerCursor identifies a position in the page pager.
//   line = index into pageLines
//   col  = rune offset into the stripped (no-ANSI) display of pageLines[line]
type pagerCursor struct {
	line int
	col  int
}

// pagerSelection is an anchored selection in the page pager. The "head" of the
// selection is always wb.pageCursor; only the anchor is stored here.
type pagerSelection struct {
	active bool
	anchor pagerCursor
}

// WebBar is the UI component that replaces the prompt box when @web is active.
// It has two user-facing modes: a scrollable result list and a page pager.
type WebBar struct {
	state      wbState
	loadMsg    string
	results    []web.Result
	cursor     int
	topResult  int          // index of first visible result (for scrolling)
	expanded   map[int]bool // result indices with expanded snippet
	page       *web.Page
	pageLines  []pageLine // page markdown rendered (highlighted + tables + wrapped)
	pageScroll int
	maxRows     int // computed default — 2/3 termH - 3
	userMaxRows int // user-set override via top-border drag; 0 means none
	innerWidth  int // pane inner width (terminal width - 2 for borders)

	// Page-state pager cursor/selection.
	pageCursor      pagerCursor
	pageSelection   pagerSelection
	pageDragging    bool
	pageSourceLines []string // original markdown lines, for table-source copy

	// Browser-style history. historyBack[len-1] is the most recently visited
	// previous page; historyFwd[len-1] is the page next-in-line if alt+right.
	historyBack []web.Page
	historyFwd  []web.Page

	// In-page search (less-style `/`). When searchMode is true the bottom hint
	// row is replaced by the search input. searchMatches/searchCurrent persist
	// across non-search operations so highlights stay until the user closes
	// the search.
	searchMode    bool
	searchInput   textinput.Model
	searchMatches []pagerSearchMatch
	searchCurrent int
}

// pagerSearchMatch is a single match of the current search query in the page.
type pagerSearchMatch struct {
	line     int // index into pageLines
	startCol int // rune offset in stripped display
	endCol   int // rune offset in stripped display (exclusive)
}

// NewWebBar creates a WebBar in loading state, sized to innerWidth and capped
// at maxRows total rows.
func NewWebBar(innerWidth, maxRows int) WebBar {
	return WebBar{
		state:      wbLoading,
		loadMsg:    "Searching…",
		expanded:   make(map[int]bool),
		innerWidth: innerWidth,
		maxRows:    maxRows,
	}
}

// ── Page navigation helpers ───────────────────────────────────────────────────

// applyPage replaces the current page in-place and resets cursor/selection. It
// does NOT touch the back/forward history stacks — callers handle that.
func (wb *WebBar) applyPage(page web.Page) {
	wb.page = &page
	wb.pageLines = renderMarkdownPage(page.Markdown, wb.innerWidth)
	wb.pageSourceLines = strings.Split(page.Markdown, "\n")
	wb.state = wbPage
	wb.pageScroll = 0
	wb.pageCursor = pagerCursor{}
	wb.pageSelection = pagerSelection{}
	wb.pageDragging = false
	wb.searchMode = false
	wb.searchMatches = nil
	wb.searchCurrent = 0
}

// linkAtCol returns the linkSpan covering (line, col), or nil if no link is
// under that position. col is a rune offset in the stripped display.
func (wb WebBar) linkAtCol(line, col int) *linkSpan {
	if line < 0 || line >= len(wb.pageLines) {
		return nil
	}
	for i := range wb.pageLines[line].linkSpans {
		sp := &wb.pageLines[line].linkSpans[i]
		if col >= sp.startCol && col < sp.endCol {
			return sp
		}
	}
	return nil
}

// ── Height ────────────────────────────────────────────────────────────────────

// effectiveMaxRows returns the user-overridden max if set, otherwise the
// default computed from terminal height.
func (wb WebBar) effectiveMaxRows() int {
	if wb.userMaxRows > 0 {
		return wb.userMaxRows
	}
	return wb.maxRows
}

// Height returns the exact number of content rows this WebBar will render.
// The Pane adds 3 more rows (indicator + top border + bottom border).
func (wb WebBar) Height() int {
	max := wb.effectiveMaxRows()
	switch wb.state {
	case wbLoading:
		return 1

	case wbResults:
		if len(wb.results) == 0 {
			return 2 // "No results." + hint
		}
		avail := max - 1 // reserve 1 row for the hint
		rows := 0
		for i := wb.topResult; i < len(wb.results); i++ {
			h := wb.resultItemHeight(i)
			if rows+h > avail && rows > 0 {
				break
			}
			rows += h
		}
		if rows < 1 {
			rows = 1
		}
		return rows + 1 // +1 hint

	case wbPage:
		if wb.page == nil || len(wb.pageLines) == 0 {
			return max
		}
		h := len(wb.pageLines) + 1 // page content + hint
		if h > max {
			h = max
		}
		if h < 2 {
			h = 2
		}
		return h
	}
	return 1
}

// ── Update ────────────────────────────────────────────────────────────────────

func (wb WebBar) Update(msg tea.Msg) (WebBar, tea.Cmd) {
	// Mouse events only matter in page state — handle them before the key
	// dispatch and short-circuit other states (results uses keyboard nav only).
	if mMsg, ok := msg.(tea.MouseMsg); ok {
		if wb.state == wbPage {
			return wb.handlePageMouse(mMsg)
		}
		return wb, nil
	}

	kMsg, ok := msg.(tea.KeyMsg)
	if !ok {
		return wb, nil
	}
	key := kMsg.String()

	switch wb.state {
	case wbLoading:
		if key == "esc" {
			return wb, func() tea.Msg { return WebClosedMsg{} }
		}

	case wbResults:
		n := len(wb.results)
		switch key {
		case "up":
			if wb.cursor > 0 {
				wb.cursor--
				ensureVisible(&wb)
			}
		case "down":
			if wb.cursor < n-1 {
				wb.cursor++
				ensureVisible(&wb)
			}
		case "right":
			if n > 0 {
				wb.expanded[wb.cursor] = true
				ensureVisible(&wb)
			}
		case "left":
			if wb.expanded[wb.cursor] {
				delete(wb.expanded, wb.cursor)
				ensureVisible(&wb)
			}
		case "enter":
			if n > 0 {
				url := wb.results[wb.cursor].URL
				wb.state = wbLoading
				wb.loadMsg = "Fetching…"
				return wb, WebFetchCmd(url)
			}
		case "ctrl+o", "shift+enter":
			if n > 0 {
				url := wb.results[wb.cursor].URL
				return wb, func() tea.Msg { return WebOpenBrowserMsg{URL: url} }
			}
		case "esc":
			return wb, func() tea.Msg { return WebClosedMsg{} }
		}

	case wbPage:
		return wb.handlePageKey(kMsg, key)
	}

	return wb, nil
}

// ── View ──────────────────────────────────────────────────────────────────────

// View renders exactly Height() lines each innerWidth cells wide.
func (wb WebBar) View(innerWidth int) []string {
	switch wb.state {
	case wbLoading:
		return []string{padTo(wb.loadMsg, innerWidth)}
	case wbResults:
		return wb.viewResults(innerWidth)
	case wbPage:
		return wb.viewPage(innerWidth)
	}
	return []string{strings.Repeat(" ", innerWidth)}
}

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

// ── Page-state input handling ─────────────────────────────────────────────────
//
// The pager supports keyboard navigation (arrows, home/end, ctrl+arrows,
// ctrl+home/end), keyboard selection (shift+nav), mouse click/drag selection,
// and ctrl+c copy. pgup/pgdn scroll the viewport without moving the cursor —
// the deliberate difference from the editor's pgup/pgdn semantics.

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

// lineDisplay returns pageLines[i].display, or "" if out of range.
func (wb WebBar) lineDisplay(i int) string {
	if i < 0 || i >= len(wb.pageLines) {
		return ""
	}
	return wb.pageLines[i].display
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

// ── In-page search ────────────────────────────────────────────────────────────

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
