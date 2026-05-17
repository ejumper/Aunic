package runner

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
)

// note_edit ────────────────────────────────────────────────────────────────────

type noteEditArgs struct {
	OldString  string `json:"old_string"`
	NewString  string `json:"new_string"`
	ReplaceAll bool   `json:"replace_all,omitempty"`
}

type noteEditTool struct{}

func (noteEditTool) Name() string { return "note_edit" }

func (noteEditTool) Description() string {
	return "Edit the current active markdown note using exact old_string/new_string replacement. old_string must come from the current note content exactly as it appears."
}

func (noteEditTool) Schema() map[string]any {
	return map[string]any{
		"type": "object",
		"properties": map[string]any{
			"old_string":  map[string]any{"type": "string", "description": "Exact text in the note to replace."},
			"new_string":  map[string]any{"type": "string", "description": "Replacement text."},
			"replace_all": map[string]any{"type": "boolean", "description": "If true, replace every occurrence; otherwise old_string must be unique."},
		},
		"required":             []string{"old_string", "new_string"},
		"additionalProperties": false,
	}
}

func (noteEditTool) Execute(ctx context.Context, rc *RunContext, argsJSON string) Result {
	var args noteEditArgs
	if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
		return errorResult("invalid_arguments", err.Error())
	}

	if args.OldString == "" {
		return errorResult("empty_old_string", "old_string must not be empty.")
	}
	if args.OldString == args.NewString {
		return errorResult("no_op", "old_string equals new_string; nothing to change.")
	}

	if rc.ApplyNoteEdit == nil {
		return errorResult("apply_unavailable", "no edit apply callback wired on RunContext.")
	}

	reply, err := rc.ApplyNoteEdit(ctx, args.OldString, args.NewString, args.ReplaceAll)
	if err != nil {
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return errorResult("cancelled", "run cancelled before edit could be applied.")
		}
		return errorResult("apply_failed", err.Error())
	}
	switch {
	case reply.ConflictNotFound:
		return errorResult("old_string_not_found", "old_string not present in the current note.")
	case reply.ConflictAmbiguous:
		return errorResult("multiple_matches", fmt.Sprintf("old_string occurs %d times; set replace_all=true or disambiguate.", reply.Count))
	case !reply.Applied:
		return errorResult("apply_failed", "edit was not applied.")
	}

	payload, _ := json.Marshal(map[string]any{
		"type":        "note_content_edit",
		"old_string":  args.OldString,
		"new_string":  args.NewString,
		"replace_all": args.ReplaceAll,
	})
	summary := "note edited"
	if args.ReplaceAll {
		summary = fmt.Sprintf("note edited (%d replacements)", reply.Count)
	}
	return Result{
		JSON:    string(payload),
		EndsRun: true,
		Summary: summary,
	}
}

// note_write ──────────────────────────────────────────────────────────────────

type noteWriteArgs struct {
	Content string `json:"content"`
}

type noteWriteTool struct{}

func (noteWriteTool) Name() string { return "note_write" }

func (noteWriteTool) Description() string {
	return "Replace the entire content of the current active markdown note with the provided content."
}

func (noteWriteTool) Schema() map[string]any {
	return map[string]any{
		"type": "object",
		"properties": map[string]any{
			"content": map[string]any{"type": "string", "description": "New full content for the note."},
		},
		"required":             []string{"content"},
		"additionalProperties": false,
	}
}

func (noteWriteTool) Execute(ctx context.Context, rc *RunContext, argsJSON string) Result {
	var args noteWriteArgs
	if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
		return errorResult("invalid_arguments", err.Error())
	}

	if rc.ApplyNoteWrite == nil {
		return errorResult("apply_unavailable", "no write apply callback wired on RunContext.")
	}

	reply, err := rc.ApplyNoteWrite(ctx, args.Content)
	if err != nil {
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return errorResult("cancelled", "run cancelled before write could be applied.")
		}
		return errorResult("apply_failed", err.Error())
	}
	if reply.HashMismatch {
		return errorResult("live_note_conflict", "the note changed since the run started; aborting to avoid clobbering edits.")
	}
	if !reply.Applied {
		return errorResult("apply_failed", "write was not applied.")
	}

	payload, _ := json.Marshal(map[string]any{
		"type":  "note_content_write",
		"bytes": len(args.Content),
	})
	return Result{
		JSON:    string(payload),
		EndsRun: true,
		Summary: fmt.Sprintf("note rewritten (%d bytes)", len(args.Content)),
	}
}
