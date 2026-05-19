package agent

import (
	"encoding/json"
	"log/slog"
	"sync"
)

// decodeWarn wraps a transcript.Decode* call so a decode failure is logged
// (rather than silently producing a zero value). Used by the transcript-bar
// renderers and event handlers, where the prior code carried ~30 sites of
// `x, _ := transcript.DecodeFoo(...)` — a transcript-format change that
// breaks decoding would render rows as blank with no diagnostic.
//
// Decoded values still flow through on the error path (zero value of T), so
// callers don't need a separate error path — the warning is observability,
// not enforcement.
//
// The raw input type matches the transcript package's Decode* signatures
// (json.RawMessage, which is a named []byte). Row content slices satisfy
// this directly via Go's named-slice-type rules.
func decodeWarn[T any](data json.RawMessage, label string, fn func(json.RawMessage) (T, error)) T {
	v, err := fn(data)
	if err != nil {
		warnDecodeOnce(label, err)
	}
	return v
}

// warnDecodeOnce logs a decode failure at most once per label so a single
// bad row in a long-lived transcript doesn't spam the log. Using sync.Map
// keeps the once-per-label guard concurrency-safe even though TranscriptBar
// itself is single-threaded — slog.Warn is cheap but the format string
// includes the original error, so we want to truly de-dup.
var decodeWarnedLabels sync.Map

func warnDecodeOnce(label string, err error) {
	if _, loaded := decodeWarnedLabels.LoadOrStore(label, struct{}{}); loaded {
		return
	}
	slog.Warn("transcript decode failed", "label", label, "err", err)
}
