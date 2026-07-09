package editor

import (
	"regexp"
	"strings"
)

// SearchResultMsg carries match count and current index back to app.go so the
// indicator line can be updated without the agent package importing editor.
type SearchResultMsg struct{ Count, Current int }

type searchMatch struct {
	start    position
	end      position
	byteFrom int // byte offset of match start in the full content string
	byteTo   int // byte offset of match end
}

// findAllMatches returns all non-overlapping literal matches of query in
// content. regexp.QuoteMeta ensures the user's string is always treated as
// literal text. Case-insensitive when caseSensitive is false.
func findAllMatches(content, query string, caseSensitive bool) []searchMatch {
	if query == "" {
		return nil
	}
	pattern := regexp.QuoteMeta(query)
	if !caseSensitive {
		pattern = "(?i)" + pattern
	}
	re, err := regexp.Compile(pattern)
	if err != nil {
		return nil
	}
	pairs := re.FindAllStringIndex(content, -1)
	if len(pairs) == 0 {
		return nil
	}
	matches := make([]searchMatch, len(pairs))
	for i, p := range pairs {
		matches[i] = searchMatch{
			start:    byteOffsetToPos(content, p[0]),
			end:      byteOffsetToPos(content, p[1]),
			byteFrom: p[0],
			byteTo:   p[1],
		}
	}
	return matches
}

// byteOffsetToPos maps a byte offset within the flat content string (newline-
// delimited) to a {row, col} buffer position. col is a rune count, not bytes.
func byteOffsetToPos(content string, byteOffset int) position {
	prefix := content[:byteOffset]
	row := strings.Count(prefix, "\n")
	lastNL := strings.LastIndex(prefix, "\n")
	var colStr string
	if lastNL < 0 {
		colStr = prefix
	} else {
		colStr = prefix[lastNL+1:]
	}
	return position{row: row, col: len([]rune(colStr))}
}

func (m *Model) SetSearch(query string, caseSensitive bool) SearchResultMsg {
	m.searchQuery = query
	m.searchCaseSensitive = caseSensitive
	m.searchMatches = findAllMatches(m.textarea.Value(), query, caseSensitive)
	if len(m.searchMatches) == 0 {
		m.searchCurrent = -1
	} else if m.searchCurrent >= len(m.searchMatches) {
		m.searchCurrent = 0
	} else if m.searchCurrent < 0 {
		m.searchCurrent = 0
	}
	m.scrollToCurrentMatch()
	return SearchResultMsg{Count: len(m.searchMatches), Current: m.searchCurrent}
}

func (m *Model) SearchNext() SearchResultMsg {
	if len(m.searchMatches) == 0 {
		return SearchResultMsg{Count: 0, Current: -1}
	}
	m.searchCurrent = (m.searchCurrent + 1) % len(m.searchMatches)
	m.scrollToCurrentMatch()
	return SearchResultMsg{Count: len(m.searchMatches), Current: m.searchCurrent}
}

func (m *Model) SearchPrev() SearchResultMsg {
	if len(m.searchMatches) == 0 {
		return SearchResultMsg{Count: 0, Current: -1}
	}
	m.searchCurrent = (m.searchCurrent - 1 + len(m.searchMatches)) % len(m.searchMatches)
	m.scrollToCurrentMatch()
	return SearchResultMsg{Count: len(m.searchMatches), Current: m.searchCurrent}
}

func (m *Model) ReplaceCurrentMatch(replacement string) SearchResultMsg {
	if len(m.searchMatches) == 0 || m.searchCurrent < 0 {
		return SearchResultMsg{Count: 0, Current: -1}
	}
	match := m.searchMatches[m.searchCurrent]
	content := m.textarea.Value()
	newContent := content[:match.byteFrom] + replacement + content[match.byteTo:]
	m.textarea.SetValue(newContent)
	m.moveCursorTo(match.start.row, match.start.col)
	m.refreshAfterChange()
	m.searchMatches = findAllMatches(newContent, m.searchQuery, m.searchCaseSensitive)
	if len(m.searchMatches) == 0 {
		m.searchCurrent = -1
	} else if m.searchCurrent >= len(m.searchMatches) {
		m.searchCurrent = 0
	}
	m.scrollToCurrentMatch()
	return SearchResultMsg{Count: len(m.searchMatches), Current: m.searchCurrent}
}

func (m *Model) ReplaceAllMatches(replacement string) SearchResultMsg {
	if len(m.searchMatches) == 0 {
		return SearchResultMsg{Count: 0, Current: -1}
	}
	savedLine := m.textarea.Line()
	savedCol := m.cursorCol()
	content := m.textarea.Value()
	// findAllMatches returns matches in forward order; build the new content
	// in one pass with strings.Builder to avoid O(N·L) repeated reallocation.
	var b strings.Builder
	b.Grow(len(content) + len(m.searchMatches)*len(replacement))
	prev := 0
	for _, match := range m.searchMatches {
		b.WriteString(content[prev:match.byteFrom])
		b.WriteString(replacement)
		prev = match.byteTo
	}
	b.WriteString(content[prev:])
	content = b.String()
	m.textarea.SetValue(content)
	lines := strings.Split(content, "\n")
	if savedLine >= len(lines) {
		savedLine = len(lines) - 1
	}
	if savedLine < 0 {
		savedLine = 0
	}
	if savedCol > len([]rune(lines[savedLine])) {
		savedCol = len([]rune(lines[savedLine]))
	}
	m.moveCursorTo(savedLine, savedCol)
	m.refreshAfterChange()
	m.searchMatches = findAllMatches(content, m.searchQuery, m.searchCaseSensitive)
	if len(m.searchMatches) > 0 {
		m.searchCurrent = 0
	} else {
		m.searchCurrent = -1
	}
	return SearchResultMsg{Count: len(m.searchMatches), Current: m.searchCurrent}
}

func (m *Model) ClearSearch() {
	m.searchQuery = ""
	m.searchMatches = nil
	m.searchCurrent = -1
}

func (m Model) SearchMatchCount() int { return len(m.searchMatches) }

func (m *Model) scrollToCurrentMatch() {
	if m.searchCurrent < 0 || m.searchCurrent >= len(m.searchMatches) {
		return
	}
	match := m.searchMatches[m.searchCurrent]
	absRow, _ := m.bufferPosToVisual(match.start.row, match.start.col)
	if absRow < m.viewport.YOffset {
		m.viewport.YOffset = absRow
	} else if absRow >= m.viewport.YOffset+m.viewport.Height {
		m.viewport.YOffset = absRow - m.viewport.Height + 1
	}
	if m.viewport.YOffset < 0 {
		m.viewport.YOffset = 0
	}
}
