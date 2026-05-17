package agent

import (
	"fmt"
	"sort"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/ejumper/aunic/transcript"
)

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
	rows []transcript.Row

	width      int
	termHeight int
	// availableHeight is the maximum height the bar may grow to in full mode
	// (terminal height minus title row, separator row, and agent pane).
	// Set externally by app.go via SetAvailableHeight.
	availableHeight int

	collapsed  bool
	fullHeight bool
	filter     FilterMode

	expanded   map[int]bool          // top-level row.Num → expanded
	expandedHt map[[2]int]bool       // {row.Num, hit index} → snippet expanded

	cursor     int // index into navigable cells; -1 = no focus
	focus      bool
	scrollOff  int // number of content lines (below the top bar) scrolled off the top
	desiredCol int // sticky column for vertical (up/down/pgup/pgdown) cursor moves

	// dragging not yet supported — placeholder for future top-border resize.
}

// NewTranscriptBar returns a fresh bar with no rows, collapsed, partial
// height, all filter.
func NewTranscriptBar() TranscriptBar {
	return TranscriptBar{
		collapsed:  true,
		expanded:   map[int]bool{},
		expandedHt: map[[2]int]bool{},
		cursor:     -1,
	}
}

// SetRows replaces the row list.
func (tb *TranscriptBar) SetRows(rows []transcript.Row) {
	tb.rows = rows
	// Drop cursor if it now points out of range.
	cells := tb.cells()
	if tb.cursor >= len(cells) {
		tb.cursor = -1
	}
}

// SetWidth records the available width.
func (tb *TranscriptBar) SetWidth(w int) { tb.width = w }

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

// ── Cells (navigable elements) ──────────────────────────────────────────────

type cellKind int

const (
	cellToggleOpen cellKind = iota
	cellToggleHeight
	cellFilterAll
	cellFilterChat
	cellFilterSearch
	cellFilterTools

	cellRowExpand   // expand/collapse a row (search or fetch)
	cellRowTool     // tool name button (filters when activated)
	cellRowQuery    // query/url body — open in browser
	cellRowDelete   // [x] — delete row

	cellHitExpand
	cellHitURL
	cellHitDelete

	cellMessageDelete // [x] — delete a chat message row
)

type cell struct {
	kind   cellKind
	rowNum int  // 0 for top bar; row.Num for row/hit cells
	hitIdx int  // -1 unless a hit cell
}

// cells returns the linear list of focusable cells in current rendering order.
func (tb TranscriptBar) cells() []cell {
	if tb.collapsed {
		return []cell{{kind: cellToggleOpen, hitIdx: -1}}
	}
	out := []cell{
		{kind: cellToggleOpen, hitIdx: -1},
		{kind: cellToggleHeight, hitIdx: -1},
		{kind: cellFilterAll, hitIdx: -1},
		{kind: cellFilterChat, hitIdx: -1},
		{kind: cellFilterSearch, hitIdx: -1},
		{kind: cellFilterTools, hitIdx: -1},
	}
	for _, it := range tb.items() {
		if !tb.passesFilterItem(it) {
			continue
		}
		switch it.kind {
		case itemMessage:
			out = append(out, cell{kind: cellMessageDelete, rowNum: it.rowNum, hitIdx: -1})
		case itemPair:
			p := it.pair
			out = append(out,
				cell{kind: cellRowExpand, rowNum: p.callNum, hitIdx: -1},
				cell{kind: cellRowTool, rowNum: p.callNum, hitIdx: -1},
				cell{kind: cellRowQuery, rowNum: p.callNum, hitIdx: -1},
				cell{kind: cellRowDelete, rowNum: p.callNum, hitIdx: -1},
			)
			if tb.expanded[p.callNum] && p.tool == transcript.ToolWebSearch {
				hits, _ := transcript.DecodeSearchResult(p.resultContent)
				for i := range hits {
					out = append(out,
						cell{kind: cellHitExpand, rowNum: p.callNum, hitIdx: i},
						cell{kind: cellHitURL, rowNum: p.callNum, hitIdx: i},
						cell{kind: cellHitDelete, rowNum: p.callNum, hitIdx: i},
					)
				}
			}
		}
	}
	return out
}

// ── Top-level pairs ─────────────────────────────────────────────────────────

type pair struct {
	callNum       int
	tool          string
	callContent   []byte
	resultContent []byte
}

// topLevelPairs walks rows and groups each tool_call with its matching
// tool_result by ToolID. tool_results without a paired call are dropped.
func (tb TranscriptBar) topLevelPairs() []pair {
	results := map[string]int{} // ToolID → index in tb.rows
	for i, r := range tb.rows {
		if r.Type == transcript.TypeToolResult {
			results[r.ToolID] = i
		}
	}
	var out []pair
	for _, r := range tb.rows {
		if r.Type != transcript.TypeToolCall {
			continue
		}
		p := pair{
			callNum:     r.Num,
			tool:        r.Tool,
			callContent: r.Content,
		}
		if ri, ok := results[r.ToolID]; ok {
			p.resultContent = tb.rows[ri].Content
		}
		out = append(out, p)
	}
	return out
}

// ── Top-level items (pairs + chat messages, in source order) ────────────────

type itemKind int

const (
	itemPair itemKind = iota
	itemMessage
)

type item struct {
	kind itemKind

	// common
	rowNum int

	// itemMessage:
	role transcript.Role
	text string

	// itemPair:
	pair pair
}

// items walks tb.rows in source order, yielding either pair items (tool_call
// rows joined with their tool_result) or message items (chat). tool_result
// rows are absorbed into their paired call; orphan tool_results are skipped.
func (tb TranscriptBar) items() []item {
	results := map[string]int{} // ToolID → index in tb.rows
	for i, r := range tb.rows {
		if r.Type == transcript.TypeToolResult {
			results[r.ToolID] = i
		}
	}
	var out []item
	for _, r := range tb.rows {
		switch r.Type {
		case transcript.TypeMessage:
			mc, _ := transcript.DecodeMessage(r.Content)
			out = append(out, item{
				kind:   itemMessage,
				rowNum: r.Num,
				role:   r.Role,
				text:   mc.Text,
			})
		case transcript.TypeToolCall:
			p := pair{
				callNum:     r.Num,
				tool:        r.Tool,
				callContent: r.Content,
			}
			if ri, ok := results[r.ToolID]; ok {
				p.resultContent = tb.rows[ri].Content
			}
			out = append(out, item{
				kind:   itemPair,
				rowNum: r.Num,
				pair:   p,
			})
		}
	}
	return out
}

func (tb TranscriptBar) passesFilter(p pair) bool {
	switch tb.filter {
	case FilterAll:
		return true
	case FilterChat:
		return false // chat rows aren't pairs
	case FilterSearch:
		return p.tool == transcript.ToolWebSearch
	case FilterTools:
		return p.tool != transcript.ToolWebSearch
	}
	return true
}

func (tb TranscriptBar) passesFilterItem(it item) bool {
	switch tb.filter {
	case FilterAll:
		return true
	case FilterChat:
		return it.kind == itemMessage
	case FilterSearch:
		return it.kind == itemPair && it.pair.tool == transcript.ToolWebSearch
	case FilterTools:
		return it.kind == itemPair && it.pair.tool != transcript.ToolWebSearch
	}
	return true
}

// ── Height ──────────────────────────────────────────────────────────────────

// Height returns the number of terminal rows the bar wants. The caller (app.go)
// may pass a smaller height to View; the bar will then scroll/truncate.
func (tb TranscriptBar) Height() int {
	if tb.collapsed {
		return 1
	}
	want := 1 // top bar
	for _, it := range tb.items() {
		if !tb.passesFilterItem(it) {
			continue
		}
		switch it.kind {
		case itemMessage:
			lines := chatBubbleLineCount(it, tb.width)
			want += lines
		case itemPair:
			p := it.pair
			want++
			if !tb.expanded[p.callNum] {
				continue
			}
			switch p.tool {
			case transcript.ToolWebSearch:
				hits, _ := transcript.DecodeSearchResult(p.resultContent)
				for i := range hits {
					want++
					if tb.expandedHt[[2]int{p.callNum, i}] {
						want += 3
					}
				}
			case transcript.ToolWebFetch:
				want += 3
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

func (tb TranscriptBar) handleKey(msg tea.KeyMsg) (TranscriptBar, tea.Cmd) {
	cells, lines, cols := tb.cellNavigation()
	if len(cells) == 0 {
		return tb, nil
	}
	if tb.cursor < 0 || tb.cursor >= len(cells) {
		tb.cursor = 0
		tb.desiredCol = cols[0]
	}
	cur := tb.cursor
	curLine := lines[cur]

	distinctLines := uniqueSortedInts(lines)
	rowCells := indicesOnLine(lines, curLine)
	isFirstLine := distinctLines[0] == curLine
	isLastLine := distinctLines[len(distinctLines)-1] == curLine

	switch msg.String() {
	case "left":
		idx := indexOfInt(rowCells, cur)
		next := rowCells[(idx-1+len(rowCells))%len(rowCells)]
		tb.cursor = next
		tb.desiredCol = cols[next]
	case "right":
		idx := indexOfInt(rowCells, cur)
		next := rowCells[(idx+1)%len(rowCells)]
		tb.cursor = next
		tb.desiredCol = cols[next]
	case "home", "ctrl+left":
		tb.cursor = rowCells[0]
		tb.desiredCol = cols[rowCells[0]]
	case "end", "ctrl+right":
		tb.cursor = rowCells[len(rowCells)-1]
		tb.desiredCol = cols[rowCells[len(rowCells)-1]]
	case "ctrl+home", "ctrl+up":
		tb.cursor = 0
		tb.desiredCol = cols[0]
	case "ctrl+end", "ctrl+down":
		last := len(cells) - 1
		tb.cursor = last
		tb.desiredCol = cols[last]
	case "up":
		if isFirstLine {
			if tb.IsFullHeight() {
				return tb, nil
			}
			return tb, func() tea.Msg { return FocusEditorMsg{} }
		}
		nl := neighborLine(distinctLines, curLine, -1)
		if nl < 0 {
			return tb, nil
		}
		next := closestCellOnLine(lines, cols, nl, tb.desiredCol)
		if next >= 0 {
			tb.cursor = next
		}
	case "down":
		if isLastLine {
			return tb, func() tea.Msg { return FocusPromptMsg{} }
		}
		nl := neighborLine(distinctLines, curLine, 1)
		if nl < 0 {
			return tb, func() tea.Msg { return FocusPromptMsg{} }
		}
		next := closestCellOnLine(lines, cols, nl, tb.desiredCol)
		if next >= 0 {
			tb.cursor = next
		}
	case "pgup":
		contentH := tb.Height() - 1
		if contentH < 1 {
			contentH = 1
		}
		target := curLine - contentH
		nl := nearestLine(distinctLines, target)
		if nl >= 0 && nl != curLine {
			next := closestCellOnLine(lines, cols, nl, tb.desiredCol)
			if next >= 0 {
				tb.cursor = next
			}
		}
	case "pgdown":
		contentH := tb.Height() - 1
		if contentH < 1 {
			contentH = 1
		}
		target := curLine + contentH
		nl := nearestLine(distinctLines, target)
		if nl >= 0 && nl != curLine {
			next := closestCellOnLine(lines, cols, nl, tb.desiredCol)
			if next >= 0 {
				tb.cursor = next
			}
		}
	case "enter", " ":
		tb2, cmd := tb.activate(cells[cur])
		return tb2.withCursorVisible(), cmd
	case "ctrl+o", "shift+enter":
		c := cells[cur]
		var cmd tea.Cmd
		switch c.kind {
		case cellRowQuery:
			cmd = tb.openRowURL(c.rowNum, true)
		case cellHitURL:
			cmd = tb.openHitURL(c.rowNum, c.hitIdx, true)
		}
		if cmd != nil {
			return tb.withCursorVisible(), cmd
		}
	case "ctrl+c":
		text := tb.copyText(cells[cur])
		if text != "" {
			return tb.withCursorVisible(), func() tea.Msg { return TranscriptCopyMsg{Text: text} }
		}
	}
	return tb.withCursorVisible(), nil
}

// cellNavigation rebuilds the focusable cell list from viewWithCellMap, along
// with each cell's visual line index and screen column. The cell order matches
// what cells() returns, so cursor indices stay valid across the two.
func (tb TranscriptBar) cellNavigation() (cells []cell, lines []int, cols []int) {
	if tb.width <= 0 {
		return nil, nil, nil
	}
	_, cellMap := tb.viewWithCellMap(tb.width)
	if len(cellMap) == 0 {
		return nil, nil, nil
	}
	lineKeys := make([]int, 0, len(cellMap))
	for k := range cellMap {
		lineKeys = append(lineKeys, k)
	}
	sort.Ints(lineKeys)
	for _, k := range lineKeys {
		hrs := append([]hitRange(nil), cellMap[k]...)
		sort.Slice(hrs, func(i, j int) bool { return hrs[i].x0 < hrs[j].x0 })
		for _, hr := range hrs {
			cells = append(cells, hr.cell)
			lines = append(lines, k)
			cols = append(cols, hr.x0)
		}
	}
	return
}

// uniqueSortedInts returns the unique values of xs in ascending order.
func uniqueSortedInts(xs []int) []int {
	seen := map[int]bool{}
	out := make([]int, 0, len(xs))
	for _, x := range xs {
		if !seen[x] {
			seen[x] = true
			out = append(out, x)
		}
	}
	sort.Ints(out)
	return out
}

// indicesOnLine returns the cell indices whose line index equals line.
func indicesOnLine(lines []int, line int) []int {
	var out []int
	for i, l := range lines {
		if l == line {
			out = append(out, i)
		}
	}
	return out
}

// indexOfInt returns the index of v in s, or -1 if not present.
func indexOfInt(s []int, v int) int {
	for i, x := range s {
		if x == v {
			return i
		}
	}
	return -1
}

// neighborLine returns the line in distinctLines adjacent to cur in the given
// direction (±1), or -1 if no such line exists. distinctLines must be sorted.
func neighborLine(distinctLines []int, cur, direction int) int {
	for i, l := range distinctLines {
		if l != cur {
			continue
		}
		ni := i + direction
		if ni < 0 || ni >= len(distinctLines) {
			return -1
		}
		return distinctLines[ni]
	}
	return -1
}

// nearestLine returns the line in distinctLines closest to target. Returns -1
// if the slice is empty.
func nearestLine(distinctLines []int, target int) int {
	if len(distinctLines) == 0 {
		return -1
	}
	best := distinctLines[0]
	bestDist := absInt(best - target)
	for _, l := range distinctLines[1:] {
		d := absInt(l - target)
		if d < bestDist {
			best = l
			bestDist = d
		}
	}
	return best
}

// closestCellOnLine returns the cell index on lineIdx whose col is closest to
// targetCol. Returns -1 if no cells live on that line.
func closestCellOnLine(lines, cols []int, lineIdx, targetCol int) int {
	best := -1
	bestDist := 0
	for i, l := range lines {
		if l != lineIdx {
			continue
		}
		d := absInt(cols[i] - targetCol)
		if best < 0 || d < bestDist {
			best = i
			bestDist = d
		}
	}
	return best
}

func absInt(x int) int {
	if x < 0 {
		return -x
	}
	return x
}

// cursorLineIdx returns the absolute line index (in the full viewWithCellMap
// output) where the currently focused cell appears. Returns -1 if not found.
func (tb TranscriptBar) cursorLineIdx() int {
	if tb.cursor < 0 || tb.width <= 0 {
		return -1
	}
	cells := tb.cells()
	if tb.cursor >= len(cells) {
		return -1
	}
	target := cells[tb.cursor]
	_, cellMap := tb.viewWithCellMap(tb.width)
	for lineIdx, hrs := range cellMap {
		for _, hr := range hrs {
			if hr.cell == target {
				return lineIdx
			}
		}
	}
	return -1
}

// withCursorVisible adjusts scrollOff so the focused cell is within the
// visible content window. The top bar (line 0) is always visible and never
// needs scrolling.
func (tb TranscriptBar) withCursorVisible() TranscriptBar {
	lineIdx := tb.cursorLineIdx()
	if lineIdx <= 0 {
		return tb // top bar or not found — always visible
	}
	h := tb.Height()
	contentH := h - 1
	if contentH <= 0 {
		return tb
	}
	contentIdx := lineIdx - 1 // 0-based index within content lines
	if contentIdx < tb.scrollOff {
		tb.scrollOff = contentIdx
	}
	if contentIdx >= tb.scrollOff+contentH {
		tb.scrollOff = contentIdx - contentH + 1
	}
	return tb
}

func (tb TranscriptBar) activate(c cell) (TranscriptBar, tea.Cmd) {
	switch c.kind {
	case cellToggleOpen:
		tb.collapsed = !tb.collapsed
		if tb.collapsed {
			tb.cursor = 0
		}
	case cellToggleHeight:
		tb.fullHeight = !tb.fullHeight
	case cellFilterAll:
		tb.filter = FilterAll
	case cellFilterChat:
		tb.filter = FilterChat
	case cellFilterSearch:
		tb.filter = FilterSearch
	case cellFilterTools:
		tb.filter = FilterTools
	case cellRowExpand, cellHitExpand:
		if c.hitIdx >= 0 {
			key := [2]int{c.rowNum, c.hitIdx}
			tb.expandedHt[key] = !tb.expandedHt[key]
		} else {
			tb.expanded[c.rowNum] = !tb.expanded[c.rowNum]
		}
	case cellRowTool:
		// Filter by the row's tool.
		for _, p := range tb.topLevelPairs() {
			if p.callNum == c.rowNum {
				if p.tool == transcript.ToolWebSearch {
					tb.filter = FilterSearch
				} else {
					tb.filter = FilterTools
				}
				break
			}
		}
	case cellRowQuery:
		return tb, tb.openRowURL(c.rowNum, false)
	case cellRowDelete:
		return tb, func() tea.Msg { return TranscriptDeleteMsg{RowNum: c.rowNum, HitIdx: -1} }
	case cellHitURL:
		return tb, tb.openHitURL(c.rowNum, c.hitIdx, false)
	case cellHitDelete:
		return tb, func() tea.Msg { return TranscriptDeleteMsg{RowNum: c.rowNum, HitIdx: c.hitIdx} }
	case cellMessageDelete:
		return tb, func() tea.Msg { return TranscriptDeleteMsg{RowNum: c.rowNum, HitIdx: -1} }
	}
	return tb, nil
}

func (tb TranscriptBar) openRowURL(rowNum int, browser bool) tea.Cmd {
	for _, p := range tb.topLevelPairs() {
		if p.callNum != rowNum {
			continue
		}
		var url string
		switch p.tool {
		case transcript.ToolWebSearch:
			c, _ := transcript.DecodeSearchCall(p.callContent)
			url = "https://duckduckgo.com/?q=" + queryEscape(c.Query)
		case transcript.ToolWebFetch:
			c, _ := transcript.DecodeFetchCall(p.callContent)
			url = c.URL
		}
		if url == "" {
			return nil
		}
		if browser {
			return func() tea.Msg { return TranscriptOpenURLMsg{URL: url} }
		}
		return func() tea.Msg { return TranscriptOpenInPagerMsg{URL: url} }
	}
	return nil
}

func (tb TranscriptBar) openHitURL(rowNum, hitIdx int, browser bool) tea.Cmd {
	for _, p := range tb.topLevelPairs() {
		if p.callNum != rowNum || p.tool != transcript.ToolWebSearch {
			continue
		}
		hits, _ := transcript.DecodeSearchResult(p.resultContent)
		if hitIdx < 0 || hitIdx >= len(hits) {
			return nil
		}
		url := hits[hitIdx].URL
		if browser {
			return func() tea.Msg { return TranscriptOpenURLMsg{URL: url} }
		}
		return func() tea.Msg { return TranscriptOpenInPagerMsg{URL: url} }
	}
	return nil
}

// copyText returns the text that should be copied to the clipboard for the
// given cell. Works from any cell in a row (expand, tool, query, delete, hit).
func (tb TranscriptBar) copyText(c cell) string {
	switch c.kind {
	case cellRowExpand, cellRowTool, cellRowQuery, cellRowDelete:
		for _, p := range tb.topLevelPairs() {
			if p.callNum != c.rowNum {
				continue
			}
			switch p.tool {
			case transcript.ToolWebSearch:
				sc, _ := transcript.DecodeSearchCall(p.callContent)
				return sc.Query
			case transcript.ToolWebFetch:
				fc, _ := transcript.DecodeFetchCall(p.callContent)
				return fc.URL
			}
		}
	case cellHitExpand, cellHitURL, cellHitDelete:
		for _, p := range tb.topLevelPairs() {
			if p.callNum != c.rowNum || p.tool != transcript.ToolWebSearch {
				continue
			}
			hits, _ := transcript.DecodeSearchResult(p.resultContent)
			if c.hitIdx >= 0 && c.hitIdx < len(hits) {
				return hits[c.hitIdx].URL
			}
		}
	case cellMessageDelete:
		for _, r := range tb.rows {
			if r.Num == c.rowNum {
				mc, _ := transcript.DecodeMessage(r.Content)
				return mc.Text
			}
		}
	}
	return ""
}

// queryEscape is a tiny URL-query escape: enough for normal search terms.
func queryEscape(q string) string {
	var b strings.Builder
	for _, r := range q {
		switch {
		case r == ' ':
			b.WriteByte('+')
		case (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') || r == '-' || r == '_' || r == '.' || r == '~':
			b.WriteRune(r)
		default:
			s := string(r)
			for i := 0; i < len(s); i++ {
				b.WriteString(fmt.Sprintf("%%%02X", s[i]))
			}
		}
	}
	return b.String()
}

// ── Mouse handling ──────────────────────────────────────────────────────────

func (tb TranscriptBar) handleMouse(msg tea.MouseMsg) (TranscriptBar, tea.Cmd) {
	if msg.Action == tea.MouseActionPress {
		switch msg.Button {
		case tea.MouseButtonWheelUp:
			if tb.scrollOff > 0 {
				tb.scrollOff--
			}
			return tb, nil
		case tea.MouseButtonWheelDown:
			lines, _ := tb.viewWithCellMap(tb.width)
			maxOff := len(lines) - 1 - (tb.Height() - 1) // content lines minus visible content rows
			if maxOff > 0 && tb.scrollOff < maxOff {
				tb.scrollOff++
			}
			return tb, nil
		}
	}

	if msg.Action != tea.MouseActionPress || msg.Button != tea.MouseButtonLeft {
		return tb, nil
	}

	// Recompute the full rendered lines and figure out which cell was clicked.
	// Y=0 always maps to line 0 (pinned top bar); Y>0 is offset by scrollOff.
	lines, cellMap := tb.viewWithCellMap(tb.width)
	if msg.Y < 0 {
		return tb, nil
	}
	var fullY int
	if msg.Y == 0 {
		fullY = 0
	} else {
		fullY = tb.scrollOff + msg.Y
	}
	if fullY >= len(lines) {
		return tb, nil
	}
	row := cellMap[fullY]
	for _, hr := range row {
		if msg.X >= hr.x0 && msg.X < hr.x1 {
			tb.focus = true
			// Find the cell index in the linear list to keep keyboard focus consistent.
			cells := tb.cells()
			for i, c := range cells {
				if c == hr.cell {
					tb.cursor = i
					break
				}
			}
			return tb.activate(hr.cell)
		}
	}
	return tb, nil
}

// ── Rendering ───────────────────────────────────────────────────────────────

// hitRange records the screen X range of one focusable cell on one line.
type hitRange struct {
	cell cell
	x0   int
	x1   int
}

// View renders the bar to exactly Height() lines of innerWidth cells each.
// The top bar (filter row) is always pinned; content rows below it scroll.
func (tb TranscriptBar) View(innerWidth int) []string {
	lines, _ := tb.viewWithCellMap(innerWidth)
	h := tb.Height()

	if tb.collapsed {
		if len(lines) > h {
			lines = lines[:h]
		}
		for len(lines) < h {
			lines = append(lines, padTo("", innerWidth))
		}
		return lines
	}

	result := make([]string, 0, h)
	// Line 0 is always the top bar — pinned regardless of scroll.
	if len(lines) > 0 {
		result = append(result, lines[0])
	}

	// Content lines start at index 1 in the full render.
	content := lines[1:]
	contentH := h - 1

	// Clamp scrollOff to valid range.
	scrollOff := tb.scrollOff
	maxOff := len(content) - contentH
	if maxOff < 0 {
		maxOff = 0
	}
	if scrollOff > maxOff {
		scrollOff = maxOff
	}
	if scrollOff < 0 {
		scrollOff = 0
	}

	start := scrollOff
	end := start + contentH
	if start > len(content) {
		start = len(content)
	}
	if end > len(content) {
		end = len(content)
	}
	result = append(result, content[start:end]...)

	for len(result) < h {
		result = append(result, padTo("", innerWidth))
	}
	return result
}

// viewWithCellMap builds the rendered lines AND a per-line cell hit map for
// mouse routing. Cell hit-tests use *plain* X (after stripping ANSI).
func (tb TranscriptBar) viewWithCellMap(innerWidth int) ([]string, map[int][]hitRange) {
	hits := map[int][]hitRange{}
	if tb.collapsed {
		line, hr := tb.renderCollapsedBar(innerWidth)
		hits[0] = hr
		return []string{line}, hits
	}
	var lines []string
	// Top bar.
	tbLine, tbHits := tb.renderTopBar(innerWidth)
	hits[0] = tbHits
	lines = append(lines, tbLine)

	for _, it := range tb.items() {
		if !tb.passesFilterItem(it) {
			continue
		}
		switch it.kind {
		case itemMessage:
			bubbleLines, xLineIdx, xHit := tb.renderChatBubble(it, innerWidth)
			for i, ln := range bubbleLines {
				idx := len(lines)
				lines = append(lines, ln)
				if i == xLineIdx {
					hits[idx] = []hitRange{xHit}
				}
			}
		case itemPair:
			p := it.pair
			idx := len(lines)
			ln, hrs := tb.renderRow(p, innerWidth)
			lines = append(lines, ln)
			hits[idx] = hrs
			if !tb.expanded[p.callNum] {
				continue
			}
			switch p.tool {
			case transcript.ToolWebSearch:
				hitRows, _ := transcript.DecodeSearchResult(p.resultContent)
				for i, h := range hitRows {
					idx := len(lines)
					ln, hrs := tb.renderHitRow(p.callNum, i, h, innerWidth)
					lines = append(lines, ln)
					hits[idx] = hrs
					if tb.expandedHt[[2]int{p.callNum, i}] {
						for _, sl := range snippetLines(h.Snippet, 3, innerWidth-6) {
							lines = append(lines, padTo("      "+sl, innerWidth))
						}
					}
				}
			case transcript.ToolWebFetch:
				fr, _ := transcript.DecodeFetchResult(p.resultContent)
				for _, sl := range snippetLines(fr.Snippet, 3, innerWidth-3) {
					lines = append(lines, padTo("   "+sl, innerWidth))
				}
			}
		}
	}
	return lines, hits
}

func (tb TranscriptBar) renderCollapsedBar(width int) (string, []hitRange) {
	const label = "[^] Open transcript"
	cells := tb.cells()
	var hr []hitRange
	if len(cells) > 0 {
		hr = append(hr, hitRange{cell: cells[0], x0: 0, x1: 3})
	}
	focused := tb.focus && tb.cursor == 0
	prefix := "[^]"
	if focused {
		prefix = "\x1b[7m[^]\x1b[0m"
	}
	body := prefix + " Open transcript"
	return padTo("\x1b[2m"+body+"\x1b[0m", width), hr
}

func (tb TranscriptBar) renderTopBar(width int) (string, []hitRange) {
	cells := tb.cells()
	// Buttons: [v] [+] [all] [chat] [search] [tools]
	openLbl := "[v]"
	if tb.collapsed {
		openLbl = "[^]"
	}
	heightLbl := "[+]"
	if tb.fullHeight {
		heightLbl = "[-]"
	}

	type btn struct {
		label   string
		focused bool
		active  bool
		cellIdx int
	}
	buttons := []btn{
		{label: openLbl, cellIdx: indexOfCell(cells, cellToggleOpen, 0, -1)},
		{label: heightLbl, cellIdx: indexOfCell(cells, cellToggleHeight, 0, -1)},
		{label: "[all]", active: tb.filter == FilterAll, cellIdx: indexOfCell(cells, cellFilterAll, 0, -1)},
		{label: "[chat]", active: tb.filter == FilterChat, cellIdx: indexOfCell(cells, cellFilterChat, 0, -1)},
		{label: "[search]", active: tb.filter == FilterSearch, cellIdx: indexOfCell(cells, cellFilterSearch, 0, -1)},
		{label: "[tools]", active: tb.filter == FilterTools, cellIdx: indexOfCell(cells, cellFilterTools, 0, -1)},
	}
	var b strings.Builder
	var hrs []hitRange
	x := 0
	for i, bt := range buttons {
		if i > 0 {
			b.WriteByte(' ')
			x++
		}
		open, close := "\x1b[2m", "\x1b[0m"
		if tb.focus && tb.cursor == bt.cellIdx {
			open, close = "\x1b[7m", "\x1b[0m"
		} else if bt.active {
			open, close = "\x1b[1m", "\x1b[0m"
		}
		b.WriteString(open + bt.label + close)
		lblLen := len(bt.label)
		if bt.cellIdx >= 0 {
			hrs = append(hrs, hitRange{cell: cells[bt.cellIdx], x0: x, x1: x + lblLen})
		}
		x += lblLen
	}
	return padTo(b.String(), width), hrs
}

func indexOfCell(cells []cell, kind cellKind, rowNum, hitIdx int) int {
	for i, c := range cells {
		if c.kind == kind && c.rowNum == rowNum && c.hitIdx == hitIdx {
			return i
		}
	}
	return -1
}

// renderRow renders a top-level row: [↓]|tool|query|x|. Tool cell is 8 cells
// total (incl. brackets); toggle/delete are 3; query fills remainder.
func (tb TranscriptBar) renderRow(p pair, width int) (string, []hitRange) {
	const toggleW = 3
	const toolW = 8
	const xW = 3
	queryW := width - toggleW - toolW - xW
	if queryW < 4 {
		queryW = 4
	}

	cells := tb.cells()
	// Toggle label
	expanded := tb.expanded[p.callNum]
	toggle := "[↓]"
	if expanded {
		toggle = "[↑]"
	}

	// Tool cell — right-justify name in 6 inner chars.
	var toolName string
	switch p.tool {
	case transcript.ToolWebSearch:
		toolName = "search"
	case transcript.ToolWebFetch:
		toolName = "fetch"
	default:
		toolName = p.tool
	}
	if len(toolName) > 6 {
		toolName = toolName[:6]
	}
	toolCell := "[" + strings.Repeat(" ", 6-len(toolName)) + toolName + "]"

	// Query text.
	var queryText string
	switch p.tool {
	case transcript.ToolWebSearch:
		c, _ := transcript.DecodeSearchCall(p.callContent)
		queryText = c.Query
	case transcript.ToolWebFetch:
		c, _ := transcript.DecodeFetchCall(p.callContent)
		queryText = cleanURL(c.URL)
	}
	innerQ := queryW - 2
	if innerQ < 1 {
		innerQ = 1
	}
	queryText = truncate(queryText, innerQ)
	queryCell := "[" + queryText + strings.Repeat(" ", innerQ-visualWidth(queryText)) + "]"

	delCell := "[x]"

	// Style each cell based on focus.
	r := func(kind cellKind, s string) string {
		idx := indexOfCell(cells, kind, p.callNum, -1)
		if idx >= 0 && tb.focus && tb.cursor == idx {
			return "\x1b[7m" + s + "\x1b[0m"
		}
		return s
	}

	line := r(cellRowExpand, toggle) + r(cellRowTool, toolCell) + r(cellRowQuery, queryCell) + r(cellRowDelete, delCell)
	x := 0
	hrs := []hitRange{
		{cell: cell{kind: cellRowExpand, rowNum: p.callNum, hitIdx: -1}, x0: x, x1: x + toggleW},
	}
	x += toggleW
	hrs = append(hrs, hitRange{cell: cell{kind: cellRowTool, rowNum: p.callNum, hitIdx: -1}, x0: x, x1: x + toolW})
	x += toolW
	hrs = append(hrs, hitRange{cell: cell{kind: cellRowQuery, rowNum: p.callNum, hitIdx: -1}, x0: x, x1: x + queryW})
	x += queryW
	hrs = append(hrs, hitRange{cell: cell{kind: cellRowDelete, rowNum: p.callNum, hitIdx: -1}, x0: x, x1: x + xW})
	return padTo(line, width), hrs
}

// renderHitRow renders an indented (3 spaces) sub-row for a single search hit.
func (tb TranscriptBar) renderHitRow(rowNum, hitIdx int, h transcript.SearchResultHit, width int) (string, []hitRange) {
	const indent = 3
	const toggleW = 3
	const xW = 3
	urlW := width - indent - toggleW - xW
	if urlW < 4 {
		urlW = 4
	}

	cells := tb.cells()
	expanded := tb.expandedHt[[2]int{rowNum, hitIdx}]
	toggle := "[↓]"
	if expanded {
		toggle = "[↑]"
	}
	urlText := cleanURL(h.URL)
	innerU := urlW - 2
	if innerU < 1 {
		innerU = 1
	}
	urlText = truncate(urlText, innerU)
	urlCell := "[" + urlText + strings.Repeat(" ", innerU-visualWidth(urlText)) + "]"
	delCell := "[x]"

	r := func(kind cellKind, s string) string {
		idx := indexOfCell(cells, kind, rowNum, hitIdx)
		if idx >= 0 && tb.focus && tb.cursor == idx {
			return "\x1b[7m" + s + "\x1b[0m"
		}
		return s
	}

	line := strings.Repeat(" ", indent) + r(cellHitExpand, toggle) + r(cellHitURL, urlCell) + r(cellHitDelete, delCell)
	x := indent
	hrs := []hitRange{
		{cell: cell{kind: cellHitExpand, rowNum: rowNum, hitIdx: hitIdx}, x0: x, x1: x + toggleW},
	}
	x += toggleW
	hrs = append(hrs, hitRange{cell: cell{kind: cellHitURL, rowNum: rowNum, hitIdx: hitIdx}, x0: x, x1: x + urlW})
	x += urlW
	hrs = append(hrs, hitRange{cell: cell{kind: cellHitDelete, rowNum: rowNum, hitIdx: hitIdx}, x0: x, x1: x + xW})
	return padTo(line, width), hrs
}

// ── Chat bubble rendering ──────────────────────────────────────────────────

// chatBubbleBoxWidth returns the outer width of the border box
// (╭/│/╰ + space + content + space + ╮/│/╯ = boxInnerW + 4).
// User boxes cap at 50% of innerWidth; assistant at 85%.
// The far-right 4 cols are reserved for " [x]".
func chatBubbleBoxWidth(role transcript.Role, innerWidth int) int {
	avail := innerWidth - 4 // reserve " [x]" at far right
	var boxW int
	if role == transcript.RoleUser {
		boxW = innerWidth / 2
	} else {
		boxW = innerWidth * 17 / 20
	}
	if boxW > avail {
		boxW = avail
	}
	if boxW < 8 {
		boxW = 8
	}
	return boxW
}

// chatBubbleContentWidth returns the text render width inside the border box
// (boxW - 4: subtract left border + space + space + right border).
func chatBubbleContentWidth(role transcript.Role, innerWidth int) int {
	boxW := chatBubbleBoxWidth(role, innerWidth)
	contentW := boxW - 4
	if contentW < 4 {
		contentW = 4
	}
	return contentW
}

// chatBubbleLineCount returns how many terminal rows a bubble will consume,
// including the top and bottom border lines.
func chatBubbleLineCount(it item, innerWidth int) int {
	if innerWidth <= 0 {
		return 3 // top border + 1 content + bottom border
	}
	contentW := chatBubbleContentWidth(it.role, innerWidth)
	pageLines := renderMarkdownPage(it.text, contentW)
	n := len(pageLines)
	if n == 0 {
		n = 1
	}
	return n + 2 // top border + content + bottom border
}

// renderChatBubble renders a chat message as a rounded border box. User
// messages are right-justified; assistant messages are left-justified.
// [x] always appears at the far-right of the top border line, aligned with
// the [x] columns of tool rows.
func (tb TranscriptBar) renderChatBubble(it item, innerWidth int) ([]string, int, hitRange) {
	isUser := it.role == transcript.RoleUser
	boxW := chatBubbleBoxWidth(it.role, innerWidth)
	contentW := boxW - 4 // inner text area: boxW minus borders and space padding

	var leftPad int
	if isUser {
		leftPad = innerWidth - boxW - 3 // right-justify: │ lands directly adjacent to [x]
		if leftPad < 0 {
			leftPad = 0
		}
	}

	pageLines := renderMarkdownPage(it.text, contentW)
	contentLines := make([]string, 0, len(pageLines))
	for _, pl := range pageLines {
		contentLines = append(contentLines, pl.display)
	}
	if len(contentLines) == 0 {
		contentLines = []string{""}
	}

	cs := tb.cells()
	xIdx := indexOfCell(cs, cellMessageDelete, it.rowNum, -1)
	delFocused := xIdx >= 0 && tb.focus && tb.cursor == xIdx
	delCell := "[x]"
	if delFocused {
		delCell = "\x1b[7m[x]\x1b[0m"
	}

	// [x] sits on the first content line (out index 1), at the far right.
	xHit := hitRange{
		cell: cell{kind: cellMessageDelete, rowNum: it.rowNum, hitIdx: -1},
		x0:   innerWidth - 3,
		x1:   innerWidth,
	}

	prefix := strings.Repeat(" ", leftPad)
	hRule := strings.Repeat("─", contentW+2) // fills between ╭ and ╮ (content + 2 spaces)

	totalLines := len(contentLines) + 2
	out := make([]string, totalLines)

	// Top border line (no [x]).
	out[0] = padTo(prefix+"╭"+hRule+"╮", innerWidth)

	// Content lines. [x] appears on the first content line (i == 0).
	for i, ln := range contentLines {
		lineW := visualWidth(ln)
		padR := contentW - lineW
		if padR < 0 {
			padR = 0
		}
		base := prefix + "│ " + ln + strings.Repeat(" ", padR) + " │"
		if i == 0 {
			// Pad to innerWidth-3 first so [x] always lands flush at the far right.
			out[i+1] = padTo(base, innerWidth-3) + delCell
		} else {
			out[i+1] = padTo(base, innerWidth)
		}
	}

	// Bottom border line.
	out[totalLines-1] = padTo(prefix+"╰"+hRule+"╯", innerWidth)

	return out, 1, xHit
}

// ── Small helpers ───────────────────────────────────────────────────────────

// cleanURL strips scheme and "www." for compact display.
func cleanURL(raw string) string {
	s := raw
	s = strings.TrimPrefix(s, "https://")
	s = strings.TrimPrefix(s, "http://")
	s = strings.TrimPrefix(s, "www.")
	return s
}

// truncate cuts s to at most w visual cells, appending "…" when cut.
func truncate(s string, w int) string {
	if visualWidth(s) <= w {
		return s
	}
	if w <= 1 {
		return "…"
	}
	out := strings.Builder{}
	used := 0
	for _, r := range s {
		rw := 1
		if r > 0x7f {
			rw = 1
		}
		_ = rw
		if used+1 > w-1 {
			break
		}
		out.WriteRune(r)
		used++
	}
	return out.String() + "…"
}

// snippetLines splits a snippet string into up to maxLines wrapped lines of
// maxW visual cells each. Long lines are broken at spaces when possible.
func snippetLines(s string, maxLines, maxW int) []string {
	if maxW < 4 {
		maxW = 4
	}
	if s == "" {
		return nil
	}
	var out []string
	rem := strings.TrimSpace(s)
	for len(rem) > 0 && len(out) < maxLines {
		if visualWidth(rem) <= maxW {
			out = append(out, rem)
			break
		}
		// Find last space within maxW.
		cut := maxW
		runes := []rune(rem)
		if cut > len(runes) {
			cut = len(runes)
		}
		bestSpace := -1
		for i := 0; i < cut; i++ {
			if runes[i] == ' ' {
				bestSpace = i
			}
		}
		if bestSpace <= 0 {
			bestSpace = cut
		}
		out = append(out, string(runes[:bestSpace]))
		rem = strings.TrimLeft(string(runes[bestSpace:]), " ")
	}
	if len(out) == maxLines && len(rem) > 0 {
		// Indicate truncation on the last line.
		last := out[len(out)-1]
		if visualWidth(last) < maxW {
			out[len(out)-1] = last + "…"
		} else {
			out[len(out)-1] = string([]rune(last)[:maxW-1]) + "…"
		}
	}
	return out
}
