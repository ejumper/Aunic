package runner

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
)

// RunContext carries the per-run state the tools need: the active note's path
// and the snapshot taken at run start (for conflict detection on note_write),
// plus callbacks that route note_edit / note_write through the main loop so
// edits land on the live editor buffer rather than disk.
type RunContext struct {
	ActivePath      string
	SnapshotContent string
	SnapshotHash    string

	// ApplyNoteEdit asks the main loop to apply a find/replace to the live
	// editor buffer. Returns the reply, or ctx.Err() if cancelled.
	ApplyNoteEdit func(ctx context.Context, old, new string, replaceAll bool) (NoteEditApplyReply, error)

	// ApplyNoteWrite asks the main loop to replace the entire editor buffer.
	// Returns the reply, or ctx.Err() if cancelled.
	ApplyNoteWrite func(ctx context.Context, content string) (NoteWriteApplyReply, error)
}

func HashContent(content string) string {
	sum := sha256.Sum256([]byte(content))
	return hex.EncodeToString(sum[:])
}
