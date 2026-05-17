package agent

import (
	"strconv"
	"strings"
)

// AtCmdKind identifies which @ command was parsed.
type AtCmdKind int

const (
	AtWeb AtCmdKind = iota
)

// AtCmdResult holds the parsed result of a recognized @ command.
type AtCmdResult struct {
	Kind  AtCmdKind
	Query string
	N     int // number of results; default 10, range 1–25
}

// ParseAtCmd checks whether s is a recognized @ command and returns the parsed
// result. Returns nil if no pattern matches.
//
// Recognized patterns:
//
//	@web <query>          → AtWeb, N=10
//	@web -<n> <query>     → AtWeb, N=n (1≤n≤25)
func ParseAtCmd(s string) *AtCmdResult {
	s = strings.TrimSpace(s)
	if !strings.HasPrefix(s, "@web ") {
		return nil
	}
	rest := strings.TrimSpace(s[len("@web "):])
	if rest == "" {
		return nil
	}

	n := 10
	if strings.HasPrefix(rest, "-") {
		parts := strings.SplitN(rest[1:], " ", 2)
		if len(parts) == 2 {
			if num, err := strconv.Atoi(parts[0]); err == nil && num >= 1 && num <= 25 {
				n = num
				rest = strings.TrimSpace(parts[1])
				if rest == "" {
					return nil
				}
			}
		}
	}

	return &AtCmdResult{Kind: AtWeb, Query: rest, N: n}
}

// ColorAtCmd injects ANSI blue (color 4) around the "@web" token at the start
// of s. All original bytes are preserved so visual positions stay identical to
// the plain-text string.
func ColorAtCmd(s string) string {
	trimmed := strings.TrimSpace(s)
	leadWS := len(s) - len(strings.TrimLeft(s, " \t\n"))
	const token = "@web"
	if !strings.HasPrefix(trimmed, token) {
		return s
	}
	var b strings.Builder
	b.WriteString(s[:leadWS])
	b.WriteString("\x1b[34m")
	b.WriteString(token)
	b.WriteString("\x1b[39m")
	b.WriteString(trimmed[len(token):])
	b.WriteString(s[leadWS+len(trimmed):])
	return b.String()
}
