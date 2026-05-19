package logs

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"sync"
	"time"
)

var logFile *os.File

// Init opens (or creates) the log file at path and installs a pretty-printing
// JSON handler as the default slog logger. Call Close() on program exit.
func Init(path string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("logs: mkdir: %w", err)
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return fmt.Errorf("logs: open: %w", err)
	}
	logFile = f
	slog.SetDefault(slog.New(&prettyHandler{
		mu:    &sync.Mutex{},
		w:     f,
		level: slog.LevelDebug,
	}))
	return nil
}

// Close flushes and closes the log file.
func Close() {
	if logFile != nil {
		_ = logFile.Sync()
		_ = logFile.Close()
	}
}

// DefaultPath returns ~/.local/share/aunic/aunic.log.
func DefaultPath() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".local", "share", "aunic", "aunic.log")
}

// prettyHandler writes each log record as indented JSON followed by a blank line.
type prettyHandler struct {
	mu    *sync.Mutex
	w     io.Writer
	level slog.Level
	attrs []slog.Attr
}

func (h *prettyHandler) Enabled(_ context.Context, level slog.Level) bool {
	return level >= h.level
}

func (h *prettyHandler) Handle(_ context.Context, r slog.Record) error {
	m := map[string]any{
		"level": r.Level.String(),
		"msg":   r.Message,
		"time":  r.Time.UTC().Format(time.RFC3339Nano),
	}

	for _, a := range h.attrs {
		insertAttr(m, a)
	}
	r.Attrs(func(a slog.Attr) bool {
		insertAttr(m, a)
		return true
	})

	b, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return fmt.Errorf("logs: marshal: %w", err)
	}

	h.mu.Lock()
	defer h.mu.Unlock()
	_, err = fmt.Fprintf(h.w, "%s\n\n", b)
	return err
}

func (h *prettyHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	merged := make([]slog.Attr, len(h.attrs)+len(attrs))
	copy(merged, h.attrs)
	copy(merged[len(h.attrs):], attrs)
	return &prettyHandler{mu: h.mu, w: h.w, level: h.level, attrs: merged}
}

func (h *prettyHandler) WithGroup(name string) slog.Handler {
	// Groups not used in aunic; satisfy the interface.
	return h
}

// insertAttr converts a slog.Attr into a JSON-friendly value and stores it in m.
func insertAttr(m map[string]any, a slog.Attr) {
	v := a.Value.Resolve()
	switch v.Kind() {
	case slog.KindGroup:
		sub := map[string]any{}
		for _, ga := range v.Group() {
			insertAttr(sub, ga)
		}
		m[a.Key] = sub
	case slog.KindDuration:
		m[a.Key] = v.Duration().String()
	case slog.KindTime:
		m[a.Key] = v.Time().UTC().Format(time.RFC3339Nano)
	default:
		m[a.Key] = v.Any()
	}
}
