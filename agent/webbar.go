package agent

import (
	"context"
	"strings"

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
//
//	line = index into pageLines
//	col  = rune offset into the stripped (no-ANSI) display of pageLines[line]
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
	state       wbState
	loadMsg     string
	results     []web.Result
	cursor      int
	topResult   int          // index of first visible result (for scrolling)
	expanded    map[int]bool // result indices with expanded snippet
	page        *web.Page
	pageLines   []pageLine // page markdown rendered (highlighted + tables + wrapped)
	pageScroll  int
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

// lineDisplay returns pageLines[i].display, or "" if out of range.
func (wb WebBar) lineDisplay(i int) string {
	if i < 0 || i >= len(wb.pageLines) {
		return ""
	}
	return wb.pageLines[i].display
}
