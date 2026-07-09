package transcript

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
)

// State is the persistent UI state shared across all open files. Fields are
// raw strings for permissive parsing; callers validate values and fall back
// to defaults on unrecognized input.
type State struct {
	Mode       string `json:"mode"`            // "note" | "chat"
	Agent      string `json:"agent"`           // "off" | "read" | "work"
	Model      string `json:"model"`           // "<provider>/<model>" (matches llm config keys)
	Transcript string `json:"transcript"`      // "closed" | "open:partial" | "open:full"
	Voice      string `json:"voice,omitempty"` // "on" | "off" (omitted when off)
}

const (
	stateLineMarker = "<!-- aunic-state"
)

// globalStatePath returns ~/.local/share/aunic/state.json, respecting
// XDG_DATA_HOME when set.
func globalStatePath() (string, error) {
	base := os.Getenv("XDG_DATA_HOME")
	if base == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		base = filepath.Join(home, ".local", "share")
	}
	return filepath.Join(base, "aunic", "state.json"), nil
}

// LoadGlobalState reads ~/.local/share/aunic/state.json. Returns a zero State
// (and no error) when the file does not exist yet.
func LoadGlobalState() (State, error) {
	path, err := globalStatePath()
	if err != nil {
		return State{}, err
	}
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return State{}, nil
	}
	if err != nil {
		return State{}, err
	}
	var s State
	if err := json.Unmarshal(data, &s); err != nil {
		return State{}, err
	}
	return s, nil
}

// SaveGlobalState writes s to ~/.local/share/aunic/state.json, creating the
// directory if needed. Errors are non-fatal; callers may ignore the return.
func SaveGlobalState(s State) error {
	path, err := globalStatePath()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	data, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(data, '\n'), 0o644)
}

// ExtractState peels all aunic-state lines off text. Returns the remainder
// (with one trailing newline normalized), the parsed state from the LAST such
// line (later wins), and whether any state line was found. Used for backward-
// compatible migration: existing files with embedded state lines are stripped
// on first read; the state is migrated to the global file instead.
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
		case "voice":
			s.Voice = val
		}
	}
	rest = strings.Join(out, "\n")
	rest = strings.TrimRight(rest, " \t\n")
	if rest != "" {
		rest += "\n"
	}
	return rest, s, true
}
