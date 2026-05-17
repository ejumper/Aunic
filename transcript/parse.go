package transcript

import (
	"encoding/json"
	"errors"
	"strconv"
	"strings"
)

// Parse reads a transcript section (the text *after* the "***\n# Transcript"
// delimiter) and returns the rows it contains. Returns nil and no error when
// the section is empty or has no table. Returns an error only on a malformed
// table header; individual unrecognized rows are silently skipped.
func Parse(text string) ([]Row, error) {
	lines := strings.Split(text, "\n")

	// Find the first line that looks like a table header row (has at least
	// the expected six pipe-delimited cells).
	headerIdx := -1
	for i, ln := range lines {
		if isHeaderRow(ln) {
			headerIdx = i
			break
		}
	}
	if headerIdx < 0 {
		return nil, nil
	}
	if headerIdx+1 >= len(lines) || !isSeparatorRow(lines[headerIdx+1]) {
		return nil, errors.New("transcript table header not followed by separator row")
	}

	var rows []Row
	for i := headerIdx + 2; i < len(lines); i++ {
		ln := strings.TrimSpace(lines[i])
		if ln == "" {
			break
		}
		if !strings.HasPrefix(ln, "|") {
			break
		}
		cells := splitRow(ln)
		if len(cells) < 6 {
			continue
		}
		num, err := strconv.Atoi(strings.TrimSpace(cells[0]))
		if err != nil {
			continue
		}
		role := Role(strings.TrimSpace(cells[1]))
		typ := Type(strings.TrimSpace(cells[2]))
		tool := strings.TrimSpace(cells[3])
		toolID := strings.TrimSpace(cells[4])
		contentStr := strings.TrimSpace(cells[5])
		contentStr = strings.ReplaceAll(contentStr, `\|`, "|")
		var content json.RawMessage
		if contentStr != "" {
			content = json.RawMessage(contentStr)
		}
		rows = append(rows, Row{
			Num:     num,
			Role:    role,
			Type:    typ,
			Tool:    tool,
			ToolID:  toolID,
			Content: content,
		})
	}
	return rows, nil
}

func isHeaderRow(ln string) bool {
	ln = strings.TrimSpace(ln)
	if !strings.HasPrefix(ln, "|") {
		return false
	}
	low := strings.ToLower(ln)
	return strings.Contains(low, "| #") && strings.Contains(low, "role") &&
		strings.Contains(low, "type") && strings.Contains(low, "tool") &&
		strings.Contains(low, "content")
}

func isSeparatorRow(ln string) bool {
	ln = strings.TrimSpace(ln)
	if !strings.HasPrefix(ln, "|") {
		return false
	}
	for _, c := range ln {
		if c != '|' && c != '-' && c != ':' && c != ' ' {
			return false
		}
	}
	return true
}

// splitRow splits a pipe-delimited markdown row, honoring `\|` as a literal
// pipe inside cells.
func splitRow(ln string) []string {
	ln = strings.TrimSpace(ln)
	ln = strings.TrimPrefix(ln, "|")
	ln = strings.TrimSuffix(ln, "|")

	var cells []string
	var cur strings.Builder
	for i := 0; i < len(ln); i++ {
		c := ln[i]
		if c == '\\' && i+1 < len(ln) && ln[i+1] == '|' {
			cur.WriteByte('|')
			i++
			continue
		}
		if c == '|' {
			cells = append(cells, cur.String())
			cur.Reset()
			continue
		}
		cur.WriteByte(c)
	}
	cells = append(cells, cur.String())
	return cells
}
