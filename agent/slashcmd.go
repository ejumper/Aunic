package agent

import (
	"strconv"
	"strings"
)

// span holds a [start, end) byte range within a string.
type span struct{ start, end int }

// ColorKeywords injects ANSI blue (color 4) around the slash command keyword
// tokens in s (e.g. /find, /replace, /go), leaving argument text in the
// default color. For /model <name>, validModels (lowercase keys) determines
// whether to color the name green (ANSI color 2) or leave it default.
func ColorKeywords(s string, sc *SlashCmdResult, validModels map[string]bool) string {
	trimmed := strings.TrimSpace(s)
	leadWS := len(s) - len(strings.TrimLeft(s, " \t\n"))
	trailWS := s[leadWS+len(trimmed):]

	// /model <name> gets two-color treatment: keyword blue, name green if valid.
	if sc.Kind == SlashModel && sc.ModelName != "" {
		var b strings.Builder
		b.WriteString(s[:leadWS])
		b.WriteString("\x1b[34m/model\x1b[39m ")
		if validModels != nil && validModels[strings.ToLower(sc.ModelName)] {
			b.WriteString("\x1b[32m")
			b.WriteString(sc.ModelName)
			b.WriteString("\x1b[39m")
		} else {
			b.WriteString(sc.ModelName)
		}
		b.WriteString(trailWS)
		return b.String()
	}

	var spans []span

	switch sc.Kind {
	case SlashFind:
		spans = []span{{0, 5}} // "/find"
	case SlashFindReplaceOpen, SlashFindReplace:
		spans = append(spans, span{0, 5}) // "/find"
		// " /replace" starts at 6 + findQuery-length; skip the leading space.
		if idx := strings.Index(trimmed[6:], " /replace"); idx >= 0 {
			start := 6 + idx + 1
			spans = append(spans, span{start, start + 8}) // "/replace"
		}
	case SlashGotoOpen, SlashGoto:
		spans = []span{{0, 3}} // "/go"
	case SlashCopy:
		spans = inlineTokenSpan(trimmed, "/cp")
	case SlashBg:
		spans = inlineTokenSpan(trimmed, "/bg")
	case SlashModel:
		spans = inlineTokenSpan(trimmed, "/model")
	case SlashFixTables:
		spans = []span{{0, 11}} // "/fix-tables"
	case SlashNote:
		spans = inlineTokenSpan(trimmed, "/note")
	case SlashChat:
		spans = inlineTokenSpan(trimmed, "/chat")
	case SlashWork:
		spans = inlineTokenSpan(trimmed, "/work")
	case SlashRead:
		spans = inlineTokenSpan(trimmed, "/read")
	case SlashAgentOff:
		spans = inlineTokenSpan(trimmed, "/off")
	case SlashWeb:
		spans = inlineTokenSpan(trimmed, "/web")
	case SlashTodo:
		spans = inlineTokenSpan(trimmed, "/todo")
	case SlashClear:
		spans = inlineTokenSpan(trimmed, "/clear")
	}

	var b strings.Builder
	b.WriteString(s[:leadWS]) // leading whitespace unchanged
	prev := 0
	for _, sp := range spans {
		b.WriteString(trimmed[prev:sp.start])
		b.WriteString("\x1b[34m")
		b.WriteString(trimmed[sp.start:sp.end])
		b.WriteString("\x1b[39m")
		prev = sp.end
	}
	b.WriteString(trimmed[prev:])
	b.WriteString(trailWS)
	return b.String()
}

// SlashCmdKind identifies what kind of slash command was parsed.
type SlashCmdKind int

const (
	// SlashFind opens the find UI with an optional pre-filled query.
	SlashFind SlashCmdKind = iota
	// SlashFindReplaceOpen opens find+replace with the find field filled;
	// cursor starts in the replace input.
	SlashFindReplaceOpen
	// SlashFindReplace opens find+replace with both fields filled;
	// cursor starts on the ↓ next button.
	SlashFindReplace
	// SlashGotoOpen opens the goto bar with no pre-filled line number.
	SlashGotoOpen
	// SlashGoto jumps directly to a line without showing the goto bar.
	SlashGoto
	// SlashCopy copies text to the OS clipboard and clears the prompt.
	SlashCopy
	// SlashBg suspends the aunic process (equivalent to shell ctrl+z / SIGTSTP).
	SlashBg
	// SlashModel opens the model picker, or switches directly if a valid name follows.
	SlashModel
	// SlashFixTables normalizes all markdown tables in the active note.
	SlashFixTables
	// SlashNote switches the agent to note mode (model ends runs with note_edit).
	SlashNote
	// SlashChat switches the agent to chat mode (model ends runs with plain text).
	SlashChat
	// SlashWork enables agent work mode (read/write/edit/grep/glob/bash tools).
	SlashWork
	// SlashRead enables agent read mode (read/grep/glob tools).
	SlashRead
	// SlashAgentOff disables agent tools (off mode).
	SlashAgentOff
	// SlashWeb opens the web search bar, or searches directly if a query follows.
	SlashWeb
	// SlashTodo opens the todo authoring modal (prompt + list of todo inputs).
	// Rejected in agent: off mode since todo_write / todo_done are only
	// registered in read / work.
	SlashTodo
	// SlashClear removes transcript rows. ClearTarget selects what to clear:
	// "trans" (all), "chat" (message rows), "tool" (non-web tool pairs), or
	// "search" (web_search + web_fetch pairs). Empty ClearTarget is a no-op
	// for safety.
	SlashClear
	// SlashMarkerInclude / SlashMarkerScope / SlashMarkerReadOnly /
	// SlashMarkerExclude wrap the editor's active selection with the
	// corresponding edit-command marker tokens. Picker-only entries — there
	// is no text form to parse.
	SlashMarkerInclude
	SlashMarkerScope
	SlashMarkerReadOnly
	SlashMarkerExclude
)

// SlashCmdResult holds the parsed result of a recognized slash command.
type SlashCmdResult struct {
	Kind         SlashCmdKind
	FindQuery    string
	ReplaceQuery string
	Line         int
	CopyText     string
	ModelName    string // non-empty for /model <name>
	WebQuery     string // non-empty for /web <query>
	ClearTarget string // "trans"|"chat"|"tool"|"search" for /clear; "" for the bare /clear no-op
}

// ParseSlashCmd checks whether s is a recognized slash command and returns
// the parsed result. Returns nil if no pattern matches.
//
// Recognized patterns:
//
//	/find                          → SlashFind (empty query)
//	/find <query>                  → SlashFind
//	/find <query> /replace         → SlashFindReplaceOpen
//	/find <query> /replace <repl>  → SlashFindReplace
//	/go                            → SlashGotoOpen
//	/go <n>                        → SlashGoto (n must be a positive integer)
func ParseSlashCmd(s string) *SlashCmdResult {
	s = strings.TrimSpace(s)

	switch {
	case s == "/find":
		return &SlashCmdResult{Kind: SlashFind}

	case strings.HasPrefix(s, "/find "):
		rest := s[len("/find "):]
		if idx := strings.Index(rest, " /replace"); idx >= 0 {
			findQ := rest[:idx]
			replQ := strings.TrimSpace(rest[idx+len(" /replace"):])
			if replQ == "" {
				return &SlashCmdResult{Kind: SlashFindReplaceOpen, FindQuery: findQ}
			}
			return &SlashCmdResult{Kind: SlashFindReplace, FindQuery: findQ, ReplaceQuery: replQ}
		}
		return &SlashCmdResult{Kind: SlashFind, FindQuery: rest}

	case s == "/go":
		return &SlashCmdResult{Kind: SlashGotoOpen}

	case strings.HasPrefix(s, "/go "):
		numStr := strings.TrimSpace(s[len("/go "):])
		n, err := strconv.Atoi(numStr)
		if err != nil || n < 1 {
			return nil
		}
		return &SlashCmdResult{Kind: SlashGoto, Line: n}

	case s == "/cp":
		return &SlashCmdResult{Kind: SlashCopy}

	case strings.HasPrefix(s, "/cp "):
		return &SlashCmdResult{Kind: SlashCopy, CopyText: s[len("/cp "):]}

	case s == "/bg":
		return &SlashCmdResult{Kind: SlashBg}

	case s == "/model":
		return &SlashCmdResult{Kind: SlashModel}

	case strings.HasPrefix(s, "/model "):
		return &SlashCmdResult{Kind: SlashModel, ModelName: strings.TrimSpace(s[len("/model "):])}

	case s == "/fix-tables":
		return &SlashCmdResult{Kind: SlashFixTables}

	case s == "/note":
		return &SlashCmdResult{Kind: SlashNote}

	case s == "/chat":
		return &SlashCmdResult{Kind: SlashChat}

	case s == "/work":
		return &SlashCmdResult{Kind: SlashWork}

	case s == "/read":
		return &SlashCmdResult{Kind: SlashRead}

	case s == "/off":
		return &SlashCmdResult{Kind: SlashAgentOff}

	case s == "/web":
		return &SlashCmdResult{Kind: SlashWeb}

	case strings.HasPrefix(s, "/web "):
		return &SlashCmdResult{Kind: SlashWeb, WebQuery: strings.TrimSpace(s[5:])}

	case s == "/todo":
		return &SlashCmdResult{Kind: SlashTodo}

	case s == "/clear":
		return &SlashCmdResult{Kind: SlashClear}

	case strings.HasPrefix(s, "/clear "):
		target := strings.TrimSpace(s[len("/clear "):])
		switch target {
		case "trans", "chat", "tool", "search", "markers":
			return &SlashCmdResult{Kind: SlashClear, ClearTarget: target}
		}
		// /clear followed by some combination of @ ! $ % strips those marker
		// kinds from the editor body (see app.executeClearMarkers).
		if isMarkerCharSet(target) {
			return &SlashCmdResult{Kind: SlashClear, ClearTarget: target}
		}
		// Unknown target — still recognize as /clear so the app can show a
		// usage hint rather than sending the literal text to the model.
		return &SlashCmdResult{Kind: SlashClear}
	}

	return nil
}

// FindInlineCmd searches s for a /cp or /bg token that appears as a standalone
// word — preceded by start-of-string or a space, and followed by end-of-string
// or a space. CopyText is set to everything in s except the token and its
// adjacent separator space(s). Returns nil if not found.
func FindInlineCmd(s string) *SlashCmdResult {
	for _, entry := range []struct {
		token string
		kind  SlashCmdKind
	}{
		{"/cp", SlashCopy},
		{"/bg", SlashBg},
		{"/model", SlashModel},
		{"/note", SlashNote},
		{"/chat", SlashChat},
		{"/work", SlashWork},
		{"/read", SlashRead},
		{"/off", SlashAgentOff},
		{"/web", SlashWeb},
		{"/todo", SlashTodo},
		{"/clear", SlashClear},
	} {
		if r := findInlineToken(s, entry.token, entry.kind); r != nil {
			return r
		}
	}
	return nil
}

func findInlineToken(s, token string, kind SlashCmdKind) *SlashCmdResult {
	tlen := len(token)
	for i := 0; i <= len(s)-tlen; i++ {
		if s[i:i+tlen] != token {
			continue
		}
		if i > 0 && s[i-1] != ' ' {
			continue
		}
		end := i + tlen
		if end < len(s) && s[end] != ' ' {
			continue
		}
		before := strings.TrimRight(s[:i], " ")
		after := ""
		if end < len(s) {
			after = strings.TrimLeft(s[end:], " ")
		}
		copyText := before
		if before != "" && after != "" {
			copyText = before + " " + after
		} else if after != "" {
			copyText = after
		}
		return &SlashCmdResult{Kind: kind, CopyText: copyText}
	}
	return nil
}

// isMarkerCharSet reports whether s is a non-empty string composed only of
// edit-marker symbol characters (@, !, $, %). Used to recognize forms like
// "/clear @!" or "/clear %@$".
func isMarkerCharSet(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		switch r {
		case '@', '!', '$', '%':
		default:
			return false
		}
	}
	return true
}

// inlineTokenSpan returns the byte-offset span of token within s as a
// standalone word, for use by ColorKeywords. Returns nil if not found.
func inlineTokenSpan(s, token string) []span {
	tlen := len(token)
	for i := 0; i <= len(s)-tlen; i++ {
		if s[i:i+tlen] != token {
			continue
		}
		if i > 0 && s[i-1] != ' ' {
			continue
		}
		end := i + tlen
		if end < len(s) && s[end] != ' ' {
			continue
		}
		return []span{{i, end}}
	}
	return nil
}
