package runner

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
)

// RunContext carries the per-run state the tools need: the active note's path
// and the snapshot taken at run start (for conflict detection on note_write),
// plus callbacks that route note_edit / note_write / todo_* through the main
// loop so edits land on the live editor buffer rather than disk.
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

	// ApplyTodoWrite asks the main loop to replace the persistent todo list
	// with the provided texts. The reply contains the post-write items (with
	// auto-assigned IDs).
	ApplyTodoWrite func(ctx context.Context, texts []string) (TodoWriteApplyReply, error)

	// ApplyTodoDone asks the main loop to mark the todo with the given ID
	// as done. NotFound is set on the reply when no such todo exists.
	ApplyTodoDone func(ctx context.Context, id int) (TodoDoneApplyReply, error)

	// ApplyNoteEditAt asks the main loop to apply a scoped-edit form to the
	// live editor buffer. The map keys are slot numbers (as strings) and
	// values are the new body for each slot.
	ApplyNoteEditAt func(ctx context.Context, edits map[string]string) (NoteEditAtApplyReply, error)
}

func HashContent(content string) string {
	sum := sha256.Sum256([]byte(content))
	return hex.EncodeToString(sum[:])
}
