package editor

import (
	"regexp"
	"strings"

	"github.com/mattn/go-runewidth"
)

type tableAlign int

const (
	alignLeft tableAlign = iota
	alignCenter
	alignRight
)

type tableBlock struct {
	start, end int // [start, end) line indices
	header     []string
	separator  []tableAlign
	body       [][]string
}

var sepCellRe = regexp.MustCompile(`^\s*:?-{3,}:?\s*$`)

// NormalizeMarkdownTables returns text with every markdown table column-aligned.
// Tables inside fenced code blocks are left untouched.
func NormalizeMarkdownTables(text string) string {
	lines := strings.Split(text, "\n")
	blocks := detectTableBlocks(lines)
	if len(blocks) == 0 {
		return text
	}
	// Process back-to-front so earlier indices remain valid.
	for i := len(blocks) - 1; i >= 0; i-- {
		rendered := renderBlock(blocks[i])
		newLines := make([]string, 0, len(lines)-(blocks[i].end-blocks[i].start)+len(rendered))
		newLines = append(newLines, lines[:blocks[i].start]...)
		newLines = append(newLines, rendered...)
		newLines = append(newLines, lines[blocks[i].end:]...)
		lines = newLines
	}
	return strings.Join(lines, "\n")
}

// NormalizeMarkdownTablesInRange normalizes only table blocks that overlap the
// line range [selStart, selEnd]. A wiggle margin of 2 lines is applied on each
// side so that slightly over- or under-selected tables still register.
func NormalizeMarkdownTablesInRange(text string, selStart, selEnd int) string {
	const wiggle = 2
	lines := strings.Split(text, "\n")
	blocks := detectTableBlocks(lines)
	if len(blocks) == 0 {
		return text
	}
	for i := len(blocks) - 1; i >= 0; i-- {
		b := blocks[i]
		// Block overlaps the wiggle-expanded selection window.
		if b.start >= selEnd+wiggle+1 || b.end <= selStart-wiggle {
			continue
		}
		rendered := renderBlock(b)
		newLines := make([]string, 0, len(lines)-(b.end-b.start)+len(rendered))
		newLines = append(newLines, lines[:b.start]...)
		newLines = append(newLines, rendered...)
		newLines = append(newLines, lines[b.end:]...)
		lines = newLines
	}
	return strings.Join(lines, "\n")
}

func detectTableBlocks(lines []string) []tableBlock {
	var blocks []tableBlock
	inFence := false
	i := 0
	for i < len(lines) {
		t := strings.TrimSpace(lines[i])
		if strings.HasPrefix(t, "```") || strings.HasPrefix(t, "~~~") {
			inFence = !inFence
			i++
			continue
		}
		if inFence || !isPipeRow(t) {
			i++
			continue
		}
		// Need at least a header + separator.
		if i+1 >= len(lines) {
			i++
			continue
		}
		sepLine := strings.TrimSpace(lines[i+1])
		if !isSeparatorRow(sepLine) {
			i++
			continue
		}
		header := parseRow(t)
		aligns := parseSeparator(sepLine, len(header))
		ncols := len(header)
		j := i + 2
		var body [][]string
		for j < len(lines) {
			rt := strings.TrimSpace(lines[j])
			if !isPipeRow(rt) {
				break
			}
			row := parseRow(rt)
			if len(row) != ncols {
				break
			}
			body = append(body, row)
			j++
		}
		blocks = append(blocks, tableBlock{
			start:     i,
			end:       j,
			header:    header,
			separator: aligns,
			body:      body,
		})
		i = j
	}
	return blocks
}

func isPipeRow(s string) bool {
	return strings.Contains(s, "|")
}

func isSeparatorRow(s string) bool {
	cells := splitCells(s)
	if len(cells) == 0 {
		return false
	}
	for _, c := range cells {
		if !sepCellRe.MatchString(c) {
			return false
		}
	}
	return true
}

// parseRow splits a pipe-delimited row into trimmed cell strings.
func parseRow(s string) []string {
	return splitCells(s)
}

func splitCells(s string) []string {
	s = strings.TrimSpace(s)
	if strings.HasPrefix(s, "|") {
		s = s[1:]
	}
	if strings.HasSuffix(s, "|") {
		s = s[:len(s)-1]
	}
	parts := strings.Split(s, "|")
	cells := make([]string, len(parts))
	for i, p := range parts {
		cells[i] = strings.TrimSpace(p)
	}
	return cells
}

func parseSeparator(s string, ncols int) []tableAlign {
	aligns := make([]tableAlign, ncols)
	cells := splitCells(s)
	for i := 0; i < ncols && i < len(cells); i++ {
		c := strings.TrimSpace(cells[i])
		left := strings.HasPrefix(c, ":")
		right := strings.HasSuffix(c, ":")
		switch {
		case left && right:
			aligns[i] = alignCenter
		case right:
			aligns[i] = alignRight
		default:
			aligns[i] = alignLeft
		}
	}
	return aligns
}

func renderBlock(b tableBlock) []string {
	ncols := len(b.header)
	widths := make([]int, ncols)
	for i, h := range b.header {
		w := runewidth.StringWidth(h)
		if w > widths[i] {
			widths[i] = w
		}
	}
	for _, row := range b.body {
		for i, cell := range row {
			if i >= ncols {
				break
			}
			w := runewidth.StringWidth(cell)
			if w > widths[i] {
				widths[i] = w
			}
		}
	}
	for i := range widths {
		if widths[i] < 3 {
			widths[i] = 3
		}
	}

	out := make([]string, 0, 2+len(b.body))
	out = append(out, renderRow(b.header, widths, b.separator))
	out = append(out, renderSeparator(b.separator, widths))
	for _, row := range b.body {
		out = append(out, renderRow(row, widths, b.separator))
	}
	return out
}

func renderRow(cells []string, widths []int, aligns []tableAlign) string {
	var b strings.Builder
	b.WriteByte('|')
	for i, cell := range cells {
		if i >= len(widths) {
			break
		}
		al := alignLeft
		if i < len(aligns) {
			al = aligns[i]
		}
		b.WriteByte(' ')
		b.WriteString(alignCell(cell, widths[i], al))
		b.WriteString(" |")
	}
	return b.String()
}

func renderSeparator(aligns []tableAlign, widths []int) string {
	var b strings.Builder
	b.WriteByte('|')
	for i, w := range widths {
		al := alignLeft
		if i < len(aligns) {
			al = aligns[i]
		}
		b.WriteByte(' ')
		switch al {
		case alignCenter:
			b.WriteByte(':')
			b.WriteString(strings.Repeat("-", w-2))
			b.WriteByte(':')
		case alignRight:
			b.WriteString(strings.Repeat("-", w-1))
			b.WriteByte(':')
		default:
			b.WriteString(strings.Repeat("-", w))
		}
		b.WriteString(" |")
	}
	return b.String()
}

func alignCell(text string, width int, al tableAlign) string {
	w := runewidth.StringWidth(text)
	pad := width - w
	if pad <= 0 {
		return text
	}
	switch al {
	case alignRight:
		return strings.Repeat(" ", pad) + text
	case alignCenter:
		left := pad / 2
		right := pad - left
		return strings.Repeat(" ", left) + text + strings.Repeat(" ", right)
	default:
		return text + strings.Repeat(" ", pad)
	}
}
