package runner

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
)

//go:embed desc_note_edit_at.md
var noteEditAtDesc string

// note_edit_at — scoped edit tool, registered in place of note_edit/note_write
// whenever the active note contains at least one @>> <<@ wrap.

type noteEditAtArgs struct {
	Edits map[string]string `json:"edits"`
}

type noteEditAtTool struct{}

func (noteEditAtTool) Name() string { return "note_edit_at" }

func (noteEditAtTool) Description() string { return noteEditAtDesc }

func (noteEditAtTool) Schema() map[string]any {
	return map[string]any{
		"type": "object",
		"properties": map[string]any{
			"edits": map[string]any{
				"type":        "object",
				"description": "Map of slot number (as a string key like \"1\") to new content for that slot. Omitted slots are left unchanged. An empty string deletes the slot's body but preserves the markers.",
				"additionalProperties": map[string]any{
					"type": "string",
				},
			},
		},
		"required":             []string{"edits"},
		"additionalProperties": false,
	}
}

func (noteEditAtTool) Execute(ctx context.Context, rc *RunContext, argsJSON string) Result {
	var args noteEditAtArgs
	if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
		return errorResult("invalid_arguments", err.Error())
	}
	if rc.ApplyNoteEditAt == nil {
		return errorResult("apply_unavailable", "no scoped-edit apply callback wired on RunContext.")
	}

	reply, err := rc.ApplyNoteEditAt(ctx, args.Edits)
	if err != nil {
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return errorResult("cancelled", "run cancelled before scoped edit could be applied.")
		}
		return errorResult("apply_failed", err.Error())
	}
	if reply.ValidationError != "" {
		return errorResult("invalid_slot", reply.ValidationError)
	}
	if reply.HashMismatch {
		return errorResult("live_note_conflict", "the note changed since the run started; aborting to avoid clobbering edits.")
	}
	if !reply.Applied {
		return errorResult("apply_failed", "scoped edit was not applied.")
	}

	summary := "no slots edited"
	switch len(reply.AppliedSlots) {
	case 0:
		// keep default
	case 1:
		summary = fmt.Sprintf("slot #%d edited", reply.AppliedSlots[0])
	default:
		summary = fmt.Sprintf("%d slots edited", len(reply.AppliedSlots))
	}

	payload, _ := json.Marshal(map[string]any{
		"type":          "note_scoped_edit",
		"applied_slots": reply.AppliedSlots,
	})
	return Result{
		JSON:    string(payload),
		EndsRun: true,
		Summary: summary,
	}
}
