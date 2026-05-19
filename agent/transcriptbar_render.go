package agent

import (
	"strings"

	"github.com/ejumper/aunic/todos"
	"github.com/ejumper/aunic/transcript"
)

// This file holds the bar's render pipeline: View (the public render that
// produces exactly Height() lines), viewWithCellMap (the source-of-truth that
// also yields mouse hit-test ranges), the per-row renderers for tool/hit/todo
// rows, the chat bubble layout, and the small text helpers used by all of the
// above. Pure: nothing here mutates tb's state.

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

	// Todo summary row sits directly below the top bar when active.
	if len(tb.todos) > 0 {
		idx := len(lines)
		ln, hrs := tb.renderTodoSummaryRow(innerWidth)
		lines = append(lines, ln)
		hits[idx] = hrs
		if tb.expanded[0] {
			for _, t := range tb.todos {
				idx := len(lines)
				ln, hrs := tb.renderTodoItemRow(t, innerWidth)
				lines = append(lines, ln)
				hits[idx] = hrs
			}
		}
	}

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
				hitRows := decodeWarn(p.resultContent, "search_result", transcript.DecodeSearchResult)
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
				fr := decodeWarn(p.resultContent, "fetch_result", transcript.DecodeFetchResult)
				for _, sl := range snippetLines(fr.Snippet, 3, innerWidth-3) {
					lines = append(lines, padTo("   "+sl, innerWidth))
				}
			case transcript.ToolRead, transcript.ToolWrite, transcript.ToolNoteWrite:
				r := decodeWarn(p.resultContent, "preview_result", transcript.DecodeAgentPreviewResult)
				for _, line := range r.Lines {
					lines = append(lines, padTo("   "+line, innerWidth))
				}
			case transcript.ToolEdit, transcript.ToolNoteEdit:
				c := decodeWarn(p.callContent, "file_call", transcript.DecodeAgentFileCall)
				for _, line := range renderEditExpand(c.OldString, c.NewString, innerWidth) {
					lines = append(lines, padTo(line, innerWidth))
				}
			case transcript.ToolBash:
				r := decodeWarn(p.resultContent, "output_result", transcript.DecodeAgentOutputResult)
				if r.Output != "" {
					bashLines := strings.SplitN(r.Output, "\n", 6)
					if len(bashLines) > 5 {
						bashLines = bashLines[:5]
					}
					for _, line := range bashLines {
						lines = append(lines, padTo("   "+line, innerWidth))
					}
				}
			case transcript.ToolGrep, transcript.ToolGlob:
				r := decodeWarn(p.resultContent, "preview_result", transcript.DecodeAgentPreviewResult)
				for _, line := range r.Lines {
					lines = append(lines, padTo("   "+line, innerWidth))
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
		{label: openLbl, cellIdx: tb.cellIdx(cellToggleOpen, 0, -1)},
		{label: heightLbl, cellIdx: tb.cellIdx(cellToggleHeight, 0, -1)},
		{label: "[all]", active: tb.filter == FilterAll, cellIdx: tb.cellIdx(cellFilterAll, 0, -1)},
		{label: "[chat]", active: tb.filter == FilterChat, cellIdx: tb.cellIdx(cellFilterChat, 0, -1)},
		{label: "[search]", active: tb.filter == FilterSearch, cellIdx: tb.cellIdx(cellFilterSearch, 0, -1)},
		{label: "[tools]", active: tb.filter == FilterTools, cellIdx: tb.cellIdx(cellFilterTools, 0, -1)},
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

// renderTodoSummaryRow renders the persistent summary row showing a brief view
// of the active todo list. Column widths match renderRow so the layout aligns
// with tool rows. ANSI 6 (cyan) is applied to every cell. Focus is rendered as
// reverse video, layered on top of cyan.
func (tb TranscriptBar) renderTodoSummaryRow(width int) (string, []hitRange) {
	const toggleW = 3
	const toolW = 8
	const xW = 3
	queryW := width - toggleW - toolW - xW
	if queryW < 4 {
		queryW = 4
	}

	expanded := tb.expanded[0]
	toggle := "[↓]"
	if expanded {
		toggle = "[↑]"
	}

	// "✔todos" is 6 inner chars, matching the tool name slot's 6-char inner cap.
	labelCell := "[✔todos]"

	// Body: first not-done todo, or empty if all done.
	var bodyText string
	for _, t := range tb.todos {
		if !t.Done {
			bodyText = t.Text
			break
		}
	}
	innerB := queryW - 2
	if innerB < 1 {
		innerB = 1
	}
	bodyText = truncate(bodyText, innerB)
	bodyCell := "[" + bodyText + strings.Repeat(" ", innerB-visualWidth(bodyText)) + "]"

	delCell := "[x]"

	// Wrap each piece in cyan (ANSI 36). Focused cells get reverse video too.
	cyan := func(kind cellKind, s string) string {
		idx := tb.cellIdx(kind, 0, -1)
		if idx >= 0 && tb.focus && tb.cursor == idx {
			return "\x1b[36m\x1b[7m" + s + "\x1b[0m"
		}
		return "\x1b[36m" + s + "\x1b[0m"
	}
	cyanStatic := func(s string) string { return "\x1b[36m" + s + "\x1b[0m" }

	line := cyan(cellTodoExpand, toggle) + cyanStatic(labelCell) + cyanStatic(bodyCell) + cyan(cellTodoClear, delCell)

	x := 0
	hrs := []hitRange{
		{cell: cell{kind: cellTodoExpand, rowNum: 0, hitIdx: -1}, x0: x, x1: x + toggleW},
	}
	x += toggleW + toolW + queryW
	hrs = append(hrs, hitRange{cell: cell{kind: cellTodoClear, rowNum: 0, hitIdx: -1}, x0: x, x1: x + xW})
	return padTo(line, width), hrs
}

// renderTodoItemRow renders one indented sub-row for an expanded todo. Column
// widths match renderHitRow so the layout aligns with web-search hit rows.
func (tb TranscriptBar) renderTodoItemRow(t todos.Todo, width int) (string, []hitRange) {
	const indent = 3
	const toggleW = 3
	const xW = 3
	textW := width - indent - toggleW - xW
	if textW < 4 {
		textW = 4
	}

	mark := " "
	if t.Done {
		mark = "✔"
	}
	toggle := "[" + mark + "]"

	innerT := textW - 2
	if innerT < 1 {
		innerT = 1
	}
	body := truncate(t.Text, innerT)
	textCell := "[" + body + strings.Repeat(" ", innerT-visualWidth(body)) + "]"
	delCell := "[x]"

	cyan := func(kind cellKind, s string) string {
		idx := tb.cellIdx(kind, 0, t.ID)
		if idx >= 0 && tb.focus && tb.cursor == idx {
			return "\x1b[36m\x1b[7m" + s + "\x1b[0m"
		}
		return "\x1b[36m" + s + "\x1b[0m"
	}
	cyanStatic := func(s string) string { return "\x1b[36m" + s + "\x1b[0m" }

	line := strings.Repeat(" ", indent) + cyan(cellTodoItemToggle, toggle) + cyanStatic(textCell) + cyan(cellTodoItemDelete, delCell)

	x := indent
	hrs := []hitRange{
		{cell: cell{kind: cellTodoItemToggle, rowNum: 0, hitIdx: t.ID}, x0: x, x1: x + toggleW},
	}
	x += toggleW + textW
	hrs = append(hrs, hitRange{cell: cell{kind: cellTodoItemDelete, rowNum: 0, hitIdx: t.ID}, x0: x, x1: x + xW})
	return padTo(line, width), hrs
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
	case transcript.ToolNoteEdit:
		toolName = "Nedit"
	case transcript.ToolNoteWrite:
		toolName = "Nwrite"
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
		c := decodeWarn(p.callContent, "search_call", transcript.DecodeSearchCall)
		queryText = c.Query
	case transcript.ToolWebFetch:
		c := decodeWarn(p.callContent, "fetch_call", transcript.DecodeFetchCall)
		queryText = cleanURL(c.URL)
	case transcript.ToolRead, transcript.ToolWrite, transcript.ToolEdit,
		transcript.ToolNoteEdit, transcript.ToolNoteWrite:
		c := decodeWarn(p.callContent, "file_call", transcript.DecodeAgentFileCall)
		queryText = c.FilePath
	case transcript.ToolBash:
		c := decodeWarn(p.callContent, "cmd_call", transcript.DecodeAgentCmdCall)
		queryText = c.Command
	case transcript.ToolGrep, transcript.ToolGlob:
		c := decodeWarn(p.callContent, "pattern_call", transcript.DecodeAgentPatternCall)
		queryText = c.Pattern
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
		idx := tb.cellIdx(kind, p.callNum, -1)
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
		idx := tb.cellIdx(kind, rowNum, hitIdx)
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
//
// Memoized via tb.cache.bubbleHeights keyed by (rowNum, innerWidth). Cheap to
// invalidate (SetRows drops the whole map). The compute path runs
// renderMarkdownPage — the same expensive call viewWithCellMap will make a
// second time during render — so this cache eliminates the second layout pass.
func (tb TranscriptBar) chatBubbleLineCount(it item, innerWidth int) int {
	if innerWidth <= 0 {
		return 3 // top border + 1 content + bottom border
	}
	if tb.cache != nil {
		if n, ok := tb.cache.bubbleHeights[bubbleKey{rowNum: it.rowNum, width: innerWidth}]; ok {
			return n
		}
	}
	contentW := chatBubbleContentWidth(it.role, innerWidth)
	pageLines := renderMarkdownPage(it.text, contentW)
	n := len(pageLines)
	if n == 0 {
		n = 1
	}
	out := n + 2 // top border + content + bottom border
	if tb.cache != nil {
		if tb.cache.bubbleHeights == nil {
			tb.cache.bubbleHeights = map[bubbleKey]int{}
		}
		tb.cache.bubbleHeights[bubbleKey{rowNum: it.rowNum, width: innerWidth}] = out
	}
	return out
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

	xIdx := tb.cellIdx(cellMessageDelete, it.rowNum, -1)
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

// padRight pads s with spaces on the right to exactly w visual cells.
// If s is already wider than w, it is truncated.
func padRight(s string, w int) string {
	vw := visualWidth(s)
	if vw >= w {
		return truncate(s, w)
	}
	return s + strings.Repeat(" ", w-vw)
}

// renderEditExpand renders a 2-column diff view for the Edit expand area.
// Left column shows old_string in red, right column shows new_string in green,
// separated by "│". Capped at 5 rows each.
func renderEditExpand(oldStr, newStr string, innerWidth int) []string {
	const indent = 3
	avail := innerWidth - indent
	if avail < 3 {
		avail = 3
	}
	// col width = (avail - 1) / 2  (subtract 1 for "│" separator)
	colW := (avail - 1) / 2

	splitCap := func(s string) []string {
		if s == "" {
			return []string{""}
		}
		parts := strings.Split(strings.TrimRight(s, "\n"), "\n")
		if len(parts) > 5 {
			parts = parts[:5]
		}
		return parts
	}

	oldLines := splitCap(oldStr)
	newLines := splitCap(newStr)
	n := len(oldLines)
	if len(newLines) > n {
		n = len(newLines)
	}
	if n > 5 {
		n = 5
	}

	prefix := strings.Repeat(" ", indent)
	var out []string
	for i := 0; i < n; i++ {
		var o, nw string
		if i < len(oldLines) {
			o = oldLines[i]
		}
		if i < len(newLines) {
			nw = newLines[i]
		}
		oldCell := "\x1b[31m" + padRight(o, colW) + "\x1b[0m"
		newCell := "\x1b[32m" + padRight(nw, colW) + "\x1b[0m"
		out = append(out, prefix+oldCell+"│"+newCell)
	}
	return out
}

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
