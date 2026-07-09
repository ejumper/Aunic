package agent

import (
	"net/url"
	"path/filepath"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/ejumper/aunic/transcript"
)

// This file holds the bar's input handling: keyboard navigation, mouse
// clicks/wheel, and the activate() dispatch that turns a focused cell into a
// state mutation or outgoing tea.Cmd. Update() in transcriptbar.go is the
// public entry point and routes here.

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

func (tb TranscriptBar) activate(c cell) (TranscriptBar, tea.Cmd) {
	// Some cases mutate state that cells()/topLevelPairs() depend on
	// (collapsed/filter/expanded). The cases that only emit a tea.Cmd don't
	// need invalidation, but doing it unconditionally is one line and keeps
	// the invariant "every activate invalidates" easy to verify by inspection.
	tb.invalidateCache()
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
	case cellTodoExpand:
		tb.expanded[0] = !tb.expanded[0]
	case cellTasksButton:
		return tb, func() tea.Msg { return TasksOpenMsg{} }
	case cellTodoClear:
		return tb, func() tea.Msg { return TodoSummaryClearAllMsg{} }
	case cellTodoItemToggle:
		id := c.hitIdx
		return tb, func() tea.Msg { return TodoItemToggleMsg{ID: id} }
	case cellTodoItemDelete:
		id := c.hitIdx
		return tb, func() tea.Msg { return TodoItemDeleteMsg{ID: id} }
	}
	return tb, nil
}

func (tb TranscriptBar) openRowURL(rowNum int, browser bool) tea.Cmd {
	for _, p := range tb.topLevelPairs() {
		if p.callNum != rowNum {
			continue
		}
		switch p.tool {
		case transcript.ToolWebSearch:
			c := decodeWarn(p.callContent, "search_call", transcript.DecodeSearchCall)
			url := "https://duckduckgo.com/?q=" + queryEscape(c.Query)
			if browser {
				return func() tea.Msg { return TranscriptOpenURLMsg{URL: url} }
			}
			return func() tea.Msg { return TranscriptOpenInPagerMsg{URL: url} }
		case transcript.ToolWebFetch:
			c := decodeWarn(p.callContent, "fetch_call", transcript.DecodeFetchCall)
			url := c.URL
			if url == "" {
				return nil
			}
			if browser {
				return func() tea.Msg { return TranscriptOpenURLMsg{URL: url} }
			}
			return func() tea.Msg { return TranscriptOpenInPagerMsg{URL: url} }
		case transcript.ToolRead, transcript.ToolWrite, transcript.ToolEdit,
			transcript.ToolNoteEdit, transcript.ToolNoteWrite:
			c := decodeWarn(p.callContent, "file_call", transcript.DecodeAgentFileCall)
			if c.FilePath == "" {
				return nil
			}
			if browser {
				return nil // local files don't open in browser
			}
			path := c.FilePath
			title := filepath.Base(path)
			return func() tea.Msg { return TranscriptOpenFileMsg{Title: title, Path: path} }
		case transcript.ToolBash:
			c2 := decodeWarn(p.callContent, "cmd_call", transcript.DecodeAgentCmdCall)
			r2 := decodeWarn(p.resultContent, "output_result", transcript.DecodeAgentOutputResult)
			if browser {
				return nil
			}
			cmd := c2.Command
			if len(cmd) > 40 {
				cmd = cmd[:40] + "…"
			}
			content := "```\n" + c2.Command + "\n```\n\n" + r2.Output
			return func() tea.Msg {
				return TranscriptOpenFileMsg{Title: "bash: " + cmd, Content: content}
			}
		}
		return nil
	}
	return nil
}

func (tb TranscriptBar) openHitURL(rowNum, hitIdx int, browser bool) tea.Cmd {
	for _, p := range tb.topLevelPairs() {
		if p.callNum != rowNum || p.tool != transcript.ToolWebSearch {
			continue
		}
		hits := decodeWarn(p.resultContent, "search_result", transcript.DecodeSearchResult)
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
				sc := decodeWarn(p.callContent, "search_call", transcript.DecodeSearchCall)
				return sc.Query
			case transcript.ToolWebFetch:
				fc := decodeWarn(p.callContent, "fetch_call", transcript.DecodeFetchCall)
				return fc.URL
			case transcript.ToolRead, transcript.ToolWrite, transcript.ToolEdit,
				transcript.ToolNoteEdit, transcript.ToolNoteWrite:
				fc := decodeWarn(p.callContent, "file_call", transcript.DecodeAgentFileCall)
				return fc.FilePath
			case transcript.ToolBash:
				bc := decodeWarn(p.callContent, "cmd_call", transcript.DecodeAgentCmdCall)
				return bc.Command
			case transcript.ToolGrep, transcript.ToolGlob:
				pc := decodeWarn(p.callContent, "pattern_call", transcript.DecodeAgentPatternCall)
				return pc.Pattern
			}
		}
	case cellHitExpand, cellHitURL, cellHitDelete:
		for _, p := range tb.topLevelPairs() {
			if p.callNum != c.rowNum || p.tool != transcript.ToolWebSearch {
				continue
			}
			hits := decodeWarn(p.resultContent, "search_result", transcript.DecodeSearchResult)
			if c.hitIdx >= 0 && c.hitIdx < len(hits) {
				return hits[c.hitIdx].URL
			}
		}
	case cellMessageDelete:
		for _, r := range tb.rows {
			if r.Num == c.rowNum {
				mc := decodeWarn(r.Content, "message", transcript.DecodeMessage)
				return mc.Text
			}
		}
	}
	return ""
}

// queryEscape produces an application/x-www-form-urlencoded query value for a
// search URL (spaces → '+', unreserved chars kept, everything else %-encoded as
// UTF-8). url.QueryEscape implements exactly this — same behavior as the prior
// hand-rolled loop.
func queryEscape(q string) string { return url.QueryEscape(q) }

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
