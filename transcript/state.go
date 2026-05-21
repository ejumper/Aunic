package transcript

import "strings"

// State is the persistent per-file UI state stored as a single-line HTML
// comment at the bottom of a note file. Each field is a raw string for
// permissive parsing; callers validate values and fall back to defaults on
// unrecognized input rather than rejecting the line.
type State struct {
	Mode       string // "note" | "chat"
	Agent      string // "off" | "read" | "work"
	Model      string // "<provider>/<model>" (matches llm config keys)
	Transcript string // "closed" | "open:partial" | "open:full"
}

const (
	stateLineVersion = "1"
	stateLineMarker  = "<!-- aunic-state"
)

// ExtractState peels all aunic-state lines off text. Returns the remainder
// (with one trailing newline normalized), the parsed state from the LAST
// such line (later wins), and whether any state line was found. Strips
// every match so stray orphans don't accumulate across rewrites.
// Unrecognized keys are silently ignored.
func ExtractState(text string) (rest string, s State, found bool) {
	lines := strings.Split(text, "\n")
	out := make([]string, 0, len(lines))
	lastBody := ""
	for _, line := range lines {
		if strings.HasPrefix(strings.TrimSpace(line), stateLineMarker) {
			lastBody = strings.TrimSpace(line)
			found = true
			continue
		}
		out = append(out, line)
	}
	if !found {
		return text, State{}, false
	}
	body := strings.TrimPrefix(lastBody, "<!--")
	body = strings.TrimSuffix(body, "-->")
	body = strings.TrimSpace(body)
	body = strings.TrimPrefix(body, "aunic-state")
	for _, kv := range strings.Fields(body) {
		eq := strings.Index(kv, "=")
		if eq <= 0 {
			continue
		}
		key, val := kv[:eq], kv[eq+1:]
		switch key {
		case "mode":
			s.Mode = val
		case "agent":
			s.Agent = val
		case "model":
			s.Model = val
		case "transcript":
			s.Transcript = val
		}
	}
	rest = strings.Join(out, "\n")
	rest = strings.TrimRight(rest, " \t\n")
	if rest != "" {
		rest += "\n"
	}
	return rest, s, true
}

// RenderState formats a State as the single-line HTML comment. Empty fields
// are omitted so a minimal state still produces a compact line.
func RenderState(s State) string {
	var b strings.Builder
	b.WriteString(stateLineMarker)
	b.WriteString(" v=")
	b.WriteString(stateLineVersion)
	if s.Mode != "" {
		b.WriteString(" mode=")
		b.WriteString(s.Mode)
	}
	if s.Agent != "" {
		b.WriteString(" agent=")
		b.WriteString(s.Agent)
	}
	if s.Model != "" {
		b.WriteString(" model=")
		b.WriteString(s.Model)
	}
	if s.Transcript != "" {
		b.WriteString(" transcript=")
		b.WriteString(s.Transcript)
	}
	b.WriteString(" -->")
	return b.String()
}

// AppendStateLine returns text with a state line appended at the end.
// Ensures exactly one newline before the line and one terminating newline.
func AppendStateLine(text string, s State) string {
	var b strings.Builder
	b.WriteString(text)
	if !strings.HasSuffix(text, "\n") {
		b.WriteByte('\n')
	}
	b.WriteString(RenderState(s))
	b.WriteByte('\n')
	return b.String()
}
