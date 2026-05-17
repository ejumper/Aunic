package transcript

import (
	"strings"
)

// Render emits the transcript as a markdown table with one row per entry.
// JSON content is single-lined; any pipe `|` inside JSON strings is escaped
// as `\|` so the markdown table parser can recover the original cells.
func Render(rows []Row) string {
	var b strings.Builder
	b.WriteString("| # | role | type | tool | tool_id | content |\n")
	b.WriteString("|---|------|------|------|---------|---------|\n")
	for _, r := range rows {
		content := string(r.Content)
		content = strings.ReplaceAll(content, "\n", " ")
		content = strings.ReplaceAll(content, "|", `\|`)
		b.WriteString("| ")
		b.WriteString(itoa(r.Num))
		b.WriteString(" | ")
		b.WriteString(string(r.Role))
		b.WriteString(" | ")
		b.WriteString(string(r.Type))
		b.WriteString(" | ")
		b.WriteString(r.Tool)
		b.WriteString(" | ")
		b.WriteString(r.ToolID)
		b.WriteString(" | ")
		b.WriteString(content)
		b.WriteString(" |\n")
	}
	return b.String()
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}
