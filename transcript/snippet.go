package transcript

import (
	"strings"
	"unicode/utf8"
)

// snippetMaxChars caps the stored snippet length for web_fetch results so the
// transcript stays compact.
const snippetMaxChars = 300

// Snippet extracts a short preview from fetched markdown. It skips leading
// blank/heading lines, joins the next visible lines with a space, and truncates
// to snippetMaxChars (UTF-8 safe). Trailing whitespace is stripped.
func Snippet(markdown string) string {
	lines := strings.Split(markdown, "\n")
	var parts []string
	total := 0
	for _, ln := range lines {
		t := strings.TrimSpace(ln)
		if t == "" {
			continue
		}
		if len(parts) > 0 {
			total++ // joiner space
		}
		total += utf8.RuneCountInString(t)
		parts = append(parts, t)
		if total >= snippetMaxChars {
			break
		}
	}
	out := strings.Join(parts, " ")
	if utf8.RuneCountInString(out) <= snippetMaxChars {
		return out
	}
	// i is a byte offset at a rune boundary (range over string yields those).
	count := 0
	for i := range out {
		if count == snippetMaxChars {
			return strings.TrimRight(out[:i], " ") + "…"
		}
		count++
	}
	return out
}
