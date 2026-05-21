package modellogs

import (
	"crypto/rand"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/ejumper/aunic/logs"
)

const (
	divider  = "════════════════════════════════════════════════════════════════\n"
	stepFmt  = "── Step %d ──────────────────────────────────────────────────────\n\n"
)

// Session writes a human-readable model interaction log for one run.
// All methods are nil-safe so callers can proceed without a session when the
// log file cannot be opened.
type Session struct {
	f        *os.File
	step     int
	totalIn  int
	totalOut int
}

// Start opens a new session log file under <binary-dir>/aunic-logging/model-logs/.
// File name: <yy-mm-dd>_<notename>_<id>.log
// Returns nil and an error if the file cannot be created — callers should
// log the error and continue without model logging.
func Start(notePath string) (*Session, error) {
	logDir := filepath.Join(logs.BaseLogDir(), "model-logs")
	if err := os.MkdirAll(logDir, 0o755); err != nil {
		return nil, fmt.Errorf("modellogs: mkdir: %w", err)
	}

	dateStr := time.Now().Format("06-01-02")
	base := filepath.Base(notePath)
	noteName := strings.TrimSuffix(base, filepath.Ext(base))
	var buf [2]byte
	_, _ = rand.Read(buf[:])
	id := fmt.Sprintf("%x", buf)

	filename := fmt.Sprintf("%s_%s_%s.log", dateStr, noteName, id)
	f, err := os.Create(filepath.Join(logDir, filename))
	if err != nil {
		return nil, fmt.Errorf("modellogs: create: %w", err)
	}
	return &Session{f: f}, nil
}

// Close flushes and closes the session log file.
func (s *Session) Close() {
	if s == nil {
		return
	}
	_ = s.f.Sync()
	_ = s.f.Close()
}

func (s *Session) writef(format string, args ...any) {
	if s == nil {
		return
	}
	fmt.Fprintf(s.f, format, args...)
}

// LogRunHeader writes the opening divider with timestamp, model, and mode.
func (s *Session) LogRunHeader(model, mode string) {
	ts := time.Now().Format("2006-01-02 15:04:05")
	s.writef(divider)
	s.writef("Run  %s  |  %s  |  %s\n", ts, model, mode)
	s.writef(divider)
	s.writef("\n")
}

// LogUserPrompt writes the user's prompt.
func (s *Session) LogUserPrompt(prompt string) {
	s.writef("<USER>\n")
	writeIndented(s.f, prompt, "  ")
	s.writef("</USER>\n\n")
}

// NextStep increments the step counter and writes the step header.
func (s *Session) NextStep() {
	if s == nil {
		return
	}
	s.step++
	s.writef(stepFmt, s.step)
}

// LogThinking writes a thinking block. No-ops if text is empty.
func (s *Session) LogThinking(text string) {
	if s == nil || text == "" {
		return
	}
	s.writef("  <THINKING>\n")
	writeIndented(s.f, text, "    ")
	s.writef("  </THINKING>\n\n")
}

// LogPlainText logs a plain-text model response in note mode (where the model
// should have called a tool instead). Useful for spotting model confusion.
func (s *Session) LogPlainText(text string) {
	s.writef("  <PLAIN_TEXT>\n")
	writeIndented(s.f, text, "    ")
	s.writef("  </PLAIN_TEXT>\n\n")
}

// LogToolCall writes the tool invocation with its full JSON arguments.
func (s *Session) LogToolCall(name, argsJSON string) {
	s.writef("  <TOOL %s>\n", name)
	var m map[string]any
	if json.Unmarshal([]byte(argsJSON), &m) == nil {
		b, _ := json.MarshalIndent(m, "", "  ")
		writeIndented(s.f, string(b), "    ")
	} else {
		writeIndented(s.f, argsJSON, "    ")
	}
	s.writef("  </TOOL %s>\n\n", name)
}

// LogToolResult writes the outcome of a tool call.
func (s *Session) LogToolResult(name, summary string, isError bool) {
	status := "ok"
	if isError {
		status = "error"
	}
	s.writef("  <RESULT %s>\n", name)
	s.writef("    %s: %s\n", status, summary)
	s.writef("  </RESULT %s>\n\n", name)
}

// LogChatResponse writes the model's final plain-text reply in chat mode.
func (s *Session) LogChatResponse(text string) {
	s.writef("  <RESPONSE>\n")
	writeIndented(s.f, text, "    ")
	s.writef("  </RESPONSE>\n\n")
}

// AddTokens accumulates token counts for the run summary.
func (s *Session) AddTokens(in, out int) {
	if s == nil {
		return
	}
	s.totalIn += in
	s.totalOut += out
}

// LogRunEnd writes the closing divider with run statistics.
func (s *Session) LogRunEnd(elapsed time.Duration, reason string) {
	if s == nil {
		return
	}
	s.writef(divider)
	s.writef("Run end  |  %d steps  |  %d in  %d out  |  %.1fs  |  %s\n",
		s.step, s.totalIn, s.totalOut, elapsed.Seconds(), reason)
	s.writef(divider)
	s.writef("\n")
}

// writeIndented writes text to w with each line prefixed by indent.
// A trailing newline is always added.
func writeIndented(w *os.File, text, indent string) {
	if w == nil {
		return
	}
	lines := strings.Split(strings.TrimRight(text, "\n"), "\n")
	for _, line := range lines {
		fmt.Fprintf(w, "%s%s\n", indent, line)
	}
}
