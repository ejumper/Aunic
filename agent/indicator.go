package agent

import (
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

// IndicatorStaleMsg is fired 10 s after a message is set. Seq must match the
// indicator's current sequence number or the event is ignored (a newer message
// superseded this timer).
type IndicatorStaleMsg struct{ Seq int }

// Indicator is a single-line ephemeral status display rendered above the
// prompt box. Messages start italic and fade to faint+italic after 10 s.
// Error messages render in red and do not fade.
type Indicator struct {
	message string
	stale   bool
	isError bool
	seq     int
}

// Set replaces the current indicator message and resets stale/error state.
func (ind *Indicator) Set(msg string) {
	ind.message = msg
	ind.stale = false
	ind.isError = false
	ind.seq++
}

// SetError sets the indicator to an error message rendered in red.
// Error messages do not fade stale.
func (ind *Indicator) SetError(msg string) {
	ind.message = msg
	ind.stale = false
	ind.isError = true
	ind.seq++
}

// StaleCmd returns a one-shot cmd that fires an IndicatorStaleMsg after 10 s.
// Capture the seq at call time so a superseded timer does nothing.
func (ind Indicator) StaleCmd() tea.Cmd {
	seq := ind.seq
	return func() tea.Msg {
		time.Sleep(10 * time.Second)
		return IndicatorStaleMsg{Seq: seq}
	}
}

// MarkStale sets the indicator to its faint style if seq matches the current
// sequence number.
func (ind *Indicator) MarkStale(seq int) {
	if seq == ind.seq {
		ind.stale = true
	}
}

// View renders the indicator as a single terminal line padded to width.
func (ind Indicator) View(width int) string {
	msg := ind.message
	vis := indicatorVisibleLen(msg)
	switch {
	case vis < width:
		msg = msg + strings.Repeat(" ", width-vis)
	case vis > width:
		msg = indicatorTruncate(msg, width)
	}
	if ind.isError {
		return "\x1b[3m\x1b[31m" + msg + ansiReset
	}
	if ind.stale {
		return "\x1b[2m\x1b[3m" + msg + ansiReset
	}
	return "\x1b[3m" + msg + ansiReset
}

// indicatorVisibleLen counts printable runes, skipping ANSI CSI sequences.
func indicatorVisibleLen(s string) int {
	n := 0
	inEsc := false
	for _, r := range s {
		if r == '\x1b' {
			inEsc = true
			continue
		}
		if inEsc {
			if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') {
				inEsc = false
			}
			continue
		}
		n++
	}
	return n
}

// indicatorTruncate cuts s to at most maxVis visible characters, preserving
// ANSI escape sequences that appear before the cut point.
func indicatorTruncate(s string, maxVis int) string {
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
		if vis >= maxVis {
			break
		}
		b.WriteRune(r)
		vis++
	}
	return b.String()
}
