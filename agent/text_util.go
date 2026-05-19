package agent

import (
	"strings"

	"github.com/mattn/go-runewidth"
)

// padTo pads s with spaces on the right (or truncates) so the rendered width
// is exactly `width` visual cells. Counts visual cells via visualWidth, so
// ANSI escapes don't contribute and wide unicode runes are counted correctly.
// Used package-wide by every bar and pane renderer.
//
// Previously lived at the bottom of findbar.go with a duplicate ANSI-stripping
// helper and a stale "import cycle with render.go" comment (same package, no
// cycle possible). Moved here so it has an obvious home and uses the unicode-
// aware visualWidth instead of the older ASCII-assuming variant.
func padTo(s string, width int) string {
	w := visualWidth(s)
	if w < width {
		return s + strings.Repeat(" ", width-w)
	}
	if w > width {
		// Truncate — walk runes counting visual width, preserving ANSI escapes.
		var b strings.Builder
		vis := 0
		inEsc := false
		for _, r := range s {
			if r == '\x1b' {
				inEsc = true
				b.WriteRune(r)
				continue
			}
			if inEsc {
				b.WriteRune(r)
				if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') {
					inEsc = false
				}
				continue
			}
			rw := runewidth.RuneWidth(r)
			if r == '\t' {
				rw = tabWidth
			}
			if vis+rw > width {
				break
			}
			b.WriteRune(r)
			vis += rw
		}
		if vis < width {
			b.WriteString(strings.Repeat(" ", width-vis))
		}
		return b.String()
	}
	return s
}
