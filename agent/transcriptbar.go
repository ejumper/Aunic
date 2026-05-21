package agent

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/ejumper/aunic/todos"
	"github.com/ejumper/aunic/transcript"
)

// transcriptbar.go is the public face of the bar: types, messages, the cache
// definition, constructor, accessors, Height, and the Update entry point.
// Internals are split across:
//
//   transcriptbar_pairs.go   — derive pairs/items from rows, filter predicates
//   transcriptbar_nav.go     — cell types, cells(), keyboard navigation math
//   transcriptbar_events.go  — handleKey, handleMouse, activate dispatch
//   transcriptbar_render.go  — View, viewWithCellMap, all render* methods,
//                              chat bubble layout, small text helpers

// ── Messages ────────────────────────────────────────────────────────────────

// TranscriptOpenURLMsg is emitted when the user opens a URL in the browser
// (ctrl+o or shift+enter on a URL cell).
type TranscriptOpenURLMsg struct{ URL string }

// TranscriptOpenInPagerMsg is emitted when the user opens a URL in the web
// pager (Enter or space on a URL cell).
type TranscriptOpenInPagerMsg struct{ URL string }

// TranscriptDeleteMsg is emitted when the user activates an [x] cell.
// RowNum is the Num of the top-level tool_call row. HitIdx >= 0 means the
// user is deleting a single hit out of a search_result; -1 deletes the
// whole tool_call/tool_result pair.
type TranscriptDeleteMsg struct {
	RowNum int
	HitIdx int
}

// TranscriptCopyMsg is emitted when the user presses ctrl+c on a row. Text
// is the content to write to the clipboard (query, URL, or message text).
type TranscriptCopyMsg struct{ Text string }

// TranscriptOpenFileMsg is emitted when the user opens a file or bash output
// in the pager. If Path is non-empty, the file is read; otherwise Content is
// used directly.
type TranscriptOpenFileMsg struct {
	Title   string
	Path    string
	Content string
}

// TodoSummaryClearAllMsg is emitted when the user presses the [x] button on
// the persistent todo summary row, clearing all active todos.
type TodoSummaryClearAllMsg struct{}

// TodoItemToggleMsg is emitted when the user presses the [✔]/[ ] checkbox on
// an expanded todo, toggling its Done state.
type TodoItemToggleMsg struct{ ID int }

// TodoItemDeleteMsg is emitted when the user presses the [x] button on an
// expanded todo, removing it from the list.
type TodoItemDeleteMsg struct{ ID int }

// ── Filter ──────────────────────────────────────────────────────────────────

type FilterMode int

const (
	FilterAll FilterMode = iota
	FilterChat
	FilterSearch
	FilterTools
)

func (f FilterMode) label() string {
	switch f {
	case FilterAll:
		return "all"
	case FilterChat:
		return "chat"
	case FilterSearch:
		return "search"
	case FilterTools:
		return "tools"
	}
	return "?"
}

// ── State ───────────────────────────────────────────────────────────────────

// TranscriptBar is the UI area above the editor that displays parsed
// transcript rows. It owns its own keyboard cursor (focusable cells), its
// own expand state, and its own filter/height/collapsed state.
type TranscriptBar struct {
	rows  []transcript.Row
	todos []todos.Todo // persistent todo list rendered as a summary row

	width      int
	termHeight int
	// availableHeight is the maximum height the bar may grow to in full mode
	// (terminal height minus title row, separator row, and agent pane).
	// Set externally by app.go via SetAvailableHeight.
	availableHeight int

	collapsed  bool
	fullHeight bool
	filter     FilterMode

	expanded   map[int]bool    // top-level row.Num → expanded
	expandedHt map[[2]int]bool // {row.Num, hit index} → snippet expanded

	cursor     int // index into navigable cells; -1 = no focus
	focus      bool
	scrollOff  int // number of content lines (below the top bar) scrolled off the top
	desiredCol int // sticky column for vertical (up/down/pgup/pgdown) cursor moves

	// dragging not yet supported — placeholder for future top-border resize.

	// cache holds memoized derivations of (rows, todos, filter, expanded,
	// expandedHt, collapsed). It is shared across value-copies of TranscriptBar
	// — bubbletea's value semantics produce many copies per Update/View pass,
	// and each call site that needs cells()/topLevelPairs() would otherwise
	// rebuild the full structure. Every mutator (SetRows, SetTodos, activate())
	// must call invalidateCache to drop stale entries.
	cache *tbCache
}

// tbCache memoizes the focusable-cell list and top-level-pair list. Lives on
// the heap so value-copies of TranscriptBar share it; readers fall back to a
// fresh rebuild when cache is nil (which happens only for the zero value of
// TranscriptBar, never for a value returned by NewTranscriptBar).
type tbCache struct {
	cells      []cell
	cellsValid bool
	// cellsIndex is a {kind,rowNum,hitIdx}→index reverse map of cells. Built
	// lazily on first indexOfCell call for a given cells list and dropped any
	// time cellsValid is cleared. Replaces a per-renderer O(N) linear search
	// with an O(1) map lookup; renderers call indexOfCell K times per row, so
	// the prior cost was O(K²·R) per full render.
	cellsIndex map[cell]int
	pairs      []pair
	pairsValid bool
	// bubbleHeights caches the rendered line count of each chat-message bubble
	// keyed by (rowNum, innerWidth). Height() runs renderMarkdownPage just to
	// count lines; the actual View pass then runs renderMarkdownPage again to
	// produce the lines. Without caching every chat message gets laid out
	// twice per render. Invalidated by SetRows (which is the only mutator of
	// row content); width is part of the key so terminal resizes don't desync.
	bubbleHeights map[bubbleKey]int
}

type bubbleKey struct {
	rowNum int
	width  int
}

// NewTranscriptBar returns a fresh bar with no rows, collapsed, partial
// height, all filter.
func NewTranscriptBar() TranscriptBar {
	return TranscriptBar{
		collapsed:  true,
		expanded:   map[int]bool{},
		expandedHt: map[[2]int]bool{},
		cursor:     -1,
		cache:      &tbCache{},
	}
}

// invalidateCache drops the memoized cells/pairs lists and bubble heights.
// Cheap; safe to call even when nothing has actually changed (a redundant call
// just forces one rebuild on the next cells()/topLevelPairs()/bubble layout).
func (tb TranscriptBar) invalidateCache() {
	if tb.cache != nil {
		tb.cache.cellsValid = false
		tb.cache.pairsValid = false
		tb.cache.cellsIndex = nil
		tb.cache.bubbleHeights = nil
	}
}

// SetRows replaces the row list.
func (tb *TranscriptBar) SetRows(rows []transcript.Row) {
	tb.rows = rows
	tb.invalidateCache()
	// Drop cursor if it now points out of range.
	cells := tb.cells()
	if tb.cursor >= len(cells) {
		tb.cursor = -1
	}
}

// SetTodos replaces the active todo list rendered as a summary row at the
// top of the bar (below the filter row). When the list is empty the row is
// hidden entirely.
func (tb *TranscriptBar) SetTodos(items []todos.Todo) {
	tb.todos = items
	tb.invalidateCache()
	cells := tb.cells()
	if tb.cursor >= len(cells) {
		tb.cursor = -1
	}
}

// SetWidth records the available width. Drops bubble-height entries because
// they are width-keyed and the prior width is no longer relevant (otherwise
// the map grows unboundedly across terminal resizes).
func (tb *TranscriptBar) SetWidth(w int) {
	if tb.width != w && tb.cache != nil {
		tb.cache.bubbleHeights = nil
	}
	tb.width = w
}

// SetTermHeight records terminal height for the 40% partial cap.
func (tb *TranscriptBar) SetTermHeight(h int) { tb.termHeight = h }

// SetAvailableHeight records the maximum height the bar may take when in
// full-height mode. Computed by the app as termHeight - title - separator -
// agent pane.
func (tb *TranscriptBar) SetAvailableHeight(h int) { tb.availableHeight = h }

// IsFocused reports whether the bar currently holds keyboard focus.
func (tb TranscriptBar) IsFocused() bool { return tb.focus }

// SetFocused toggles keyboard focus. Cursor is preserved across focus
// transitions so the user returns to where they left off; on the very first
// focus (or if the prior cursor is stale), it lands on cell 0.
func (tb *TranscriptBar) SetFocused(f bool) {
	tb.focus = f
	if f {
		cells := tb.cells()
		if tb.cursor < 0 || tb.cursor >= len(cells) {
			if len(cells) == 0 {
				tb.cursor = -1
			} else {
				tb.cursor = 0
			}
		}
		// Seed desiredCol from the focused cell so the first up/down has a
		// sensible sticky column.
		if tb.cursor >= 0 {
			_, _, cols := tb.cellNavigation()
			if tb.cursor < len(cols) {
				tb.desiredCol = cols[tb.cursor]
			}
		}
	}
}

// IsCollapsed reports whether the bar is in single-line collapsed state.
func (tb TranscriptBar) IsCollapsed() bool { return tb.collapsed }

// IsFullHeight reports whether the bar is in maximum-height mode.
// A collapsed bar is never full-height regardless of the flag.
func (tb TranscriptBar) IsFullHeight() bool { return tb.fullHeight && !tb.collapsed }

// SetCollapsed forces the collapsed flag (used to restore persisted UI
// state on file load). Invalidates cached layout.
func (tb *TranscriptBar) SetCollapsed(v bool) {
	tb.collapsed = v
	tb.invalidateCache()
}

// SetFullHeight forces the fullHeight flag (used to restore persisted UI
// state on file load).
func (tb *TranscriptBar) SetFullHeight(v bool) { tb.fullHeight = v }

// ── Height ──────────────────────────────────────────────────────────────────

// Height returns the number of terminal rows the bar wants. The caller (app.go)
// may pass a smaller height to View; the bar will then scroll/truncate.
func (tb TranscriptBar) Height() int {
	if tb.collapsed {
		return 1
	}
	want := 1 // top bar
	if len(tb.todos) > 0 {
		want++ // todo summary row
		if tb.expanded[0] {
			want += len(tb.todos)
		}
	}
	for _, it := range tb.items() {
		if !tb.passesFilterItem(it) {
			continue
		}
		switch it.kind {
		case itemMessage:
			lines := tb.chatBubbleLineCount(it, tb.width)
			want += lines
		case itemPair:
			p := it.pair
			want++
			if !tb.expanded[p.callNum] {
				continue
			}
			switch p.tool {
			case transcript.ToolWebSearch:
				hits := decodeWarn(p.resultContent, "search_result", transcript.DecodeSearchResult)
				for i := range hits {
					want++
					if tb.expandedHt[[2]int{p.callNum, i}] {
						want += 3
					}
				}
			case transcript.ToolWebFetch:
				want += 3
			case transcript.ToolRead, transcript.ToolWrite, transcript.ToolNoteWrite:
				r := decodeWarn(p.resultContent, "preview_result", transcript.DecodeAgentPreviewResult)
				want += len(r.Lines)
			case transcript.ToolEdit, transcript.ToolNoteEdit:
				c := decodeWarn(p.callContent, "file_call", transcript.DecodeAgentFileCall)
				nOld := len(strings.Split(strings.TrimRight(c.OldString, "\n"), "\n"))
				nNew := len(strings.Split(strings.TrimRight(c.NewString, "\n"), "\n"))
				n := nOld
				if nNew > n {
					n = nNew
				}
				if n > 5 {
					n = 5
				}
				want += n
			case transcript.ToolBash:
				r := decodeWarn(p.resultContent, "output_result", transcript.DecodeAgentOutputResult)
				if r.Output != "" {
					n := strings.Count(r.Output, "\n") + 1
					if n > 5 {
						n = 5
					}
					want += n
				}
			case transcript.ToolGrep, transcript.ToolGlob:
				r := decodeWarn(p.resultContent, "preview_result", transcript.DecodeAgentPreviewResult)
				want += len(r.Lines)
			}
		}
	}
	if tb.fullHeight {
		// Always fill the available area; scrolling kicks in when content
		// exceeds this height. Fall back to want if app.go has not set an
		// available height yet (early init).
		if tb.availableHeight > 1 {
			return tb.availableHeight
		}
		return want
	}
	// Partial-height cap: take the tighter of two limits so the transcript
	// can't push the agent pane above the title bar when something tall
	// (web pager, expanded prompt, etc.) is open.
	//   capTerm:  40% of the terminal — preserves the "normal" feel when the
	//             agent pane is small.
	//   capAvail: 50% of the space between the title bar and the agent pane —
	//             kicks in only when the agent grows large enough that the
	//             terminal-based cap would overflow.
	cap := tb.termHeight * 40 / 100
	if tb.availableHeight > 1 {
		capAvail := tb.availableHeight * 50 / 100
		if capAvail < cap {
			cap = capAvail
		}
	}
	if cap < 2 {
		cap = 2
	}
	if want > cap {
		return cap
	}
	return want
}

// ── Update ──────────────────────────────────────────────────────────────────

// Update routes input to the bar. Mouse Y is expected pre-translated by
// app.go (Y=0 is the bar's first row).
func (tb TranscriptBar) Update(msg tea.Msg) (TranscriptBar, tea.Cmd) {
	switch m := msg.(type) {
	case tea.KeyMsg:
		if !tb.focus {
			return tb, nil
		}
		return tb.handleKey(m)
	case tea.MouseMsg:
		return tb.handleMouse(m)
	}
	return tb, nil
}
