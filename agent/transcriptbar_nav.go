package agent

import (
	"sort"

	"github.com/ejumper/aunic/transcript"
)

// This file holds the focusable-cell definitions, the cells() builder that
// turns transcript state into a flat focus list, and the keyboard-navigation
// geometry helpers (line/column math) used by handleKey to move the cursor.

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

	// Todo summary row cells (rowNum=0 sentinel; real rows start at 1).
	cellTodoExpand     // [↓]/[↑] expand/collapse on summary row
	cellTodoClear      // [x] clear-all on summary row
	cellTodoItemToggle // [✔]/[ ] per-expanded-item (hitIdx = todo.ID)
	cellTodoItemDelete // [x] per-expanded-item (hitIdx = todo.ID)

	cellTasksButton // [tasks] — opens task overlay
)

type cell struct {
	kind   cellKind
	rowNum int // 0 for top bar; row.Num for row/hit cells
	hitIdx int // -1 unless a hit cell
}

// cells returns the linear list of focusable cells in current rendering order.
//
// Memoized via tb.cache — every mutator (SetRows/SetTodos/activate) invalidates
// before its side effects so the next call rebuilds. The collapsed fast path is
// not cached (it's a single allocation and the cache would just add overhead).
func (tb TranscriptBar) cells() []cell {
	if tb.collapsed {
		return []cell{{kind: cellToggleOpen, hitIdx: -1}}
	}
	if tb.cache != nil && tb.cache.cellsValid {
		return tb.cache.cells
	}
	out := []cell{
		{kind: cellToggleOpen, hitIdx: -1},
		{kind: cellToggleHeight, hitIdx: -1},
		{kind: cellFilterAll, hitIdx: -1},
		{kind: cellFilterChat, hitIdx: -1},
		{kind: cellFilterSearch, hitIdx: -1},
		{kind: cellFilterTools, hitIdx: -1},
		{kind: cellTasksButton, hitIdx: -1},
	}
	// Todo summary row sits between the top bar and the first transcript item
	// when there is at least one active todo. The sentinel rowNum=0 keys both
	// the cell list and the `expanded` map (real rows start at 1).
	if len(tb.todos) > 0 {
		out = append(out,
			cell{kind: cellTodoExpand, rowNum: 0, hitIdx: -1},
			cell{kind: cellTodoClear, rowNum: 0, hitIdx: -1},
		)
		if tb.expanded[0] {
			for _, t := range tb.todos {
				out = append(out,
					cell{kind: cellTodoItemToggle, rowNum: 0, hitIdx: t.ID},
					cell{kind: cellTodoItemDelete, rowNum: 0, hitIdx: t.ID},
				)
			}
		}
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
				hits := decodeWarn(p.resultContent, "search_result", transcript.DecodeSearchResult)
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
	if tb.cache != nil {
		tb.cache.cells = out
		tb.cache.cellsValid = true
	}
	return out
}

// ── Cell-index lookup ───────────────────────────────────────────────────────

func indexOfCell(cells []cell, kind cellKind, rowNum, hitIdx int) int {
	for i, c := range cells {
		if c.kind == kind && c.rowNum == rowNum && c.hitIdx == hitIdx {
			return i
		}
	}
	return -1
}

// cellIdx is the O(1) form of indexOfCell. It builds tb.cache.cellsIndex
// lazily on first call after the cache is invalidated, then serves further
// lookups in this render pass from the map. Callers must have already invoked
// tb.cells() so the cached cells list is current; the renderers do this at
// their top.
func (tb TranscriptBar) cellIdx(kind cellKind, rowNum, hitIdx int) int {
	if tb.cache == nil {
		return indexOfCell(tb.cells(), kind, rowNum, hitIdx)
	}
	if tb.cache.cellsIndex == nil {
		cells := tb.cells()
		idx := make(map[cell]int, len(cells))
		for i, c := range cells {
			idx[c] = i
		}
		tb.cache.cellsIndex = idx
	}
	if i, ok := tb.cache.cellsIndex[cell{kind: kind, rowNum: rowNum, hitIdx: hitIdx}]; ok {
		return i
	}
	return -1
}

// ── Navigation geometry (line/column math) ──────────────────────────────────

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
