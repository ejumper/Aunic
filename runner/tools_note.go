package runner

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
)

//go:embed desc_note_edit.md
var noteEditDesc string

//go:embed desc_note_write.md
var noteWriteDesc string

// note_edit ────────────────────────────────────────────────────────────────────

// noteEditOp is one find/replace operation. note_edit accepts either a single
// op at the top level (old_string / new_string / replace_all) or a batch via
// the edits array.
type noteEditOp struct {
	OldString  string `json:"old_string"`
	NewString  string `json:"new_string"`
	ReplaceAll bool   `json:"replace_all,omitempty"`
}

type noteEditArgs struct {
	OldString  string       `json:"old_string,omitempty"`
	NewString  string       `json:"new_string,omitempty"`
	ReplaceAll bool         `json:"replace_all,omitempty"`
	Edits      []noteEditOp `json:"edits,omitempty"`
}

// noteEditOutcome reports the per-op result of a (possibly batched) note_edit
// call. Index is 1-based to match the way edits are described to the model.
type noteEditOutcome struct {
	Index   int    `json:"index"`
	Applied bool   `json:"applied"`
	Count   int    `json:"count,omitempty"`
	Error   string `json:"error,omitempty"`
	Message string `json:"message,omitempty"`
}

type noteEditTool struct{}

func (noteEditTool) Name() string { return "note_edit" }

func (noteEditTool) Description() string { return noteEditDesc }

func (noteEditTool) Schema() map[string]any {
	editItem := map[string]any{
		"type": "object",
		"properties": map[string]any{
			"old_string":  map[string]any{"type": "string", "description": "Exact text in the note to replace."},
			"new_string":  map[string]any{"type": "string", "description": "Replacement text."},
			"replace_all": map[string]any{"type": "boolean", "description": "If true, replace every occurrence; otherwise old_string must be unique."},
		},
		"required":             []string{"old_string", "new_string"},
		"additionalProperties": false,
	}
	return map[string]any{
		"type": "object",
		"properties": map[string]any{
			"old_string":  map[string]any{"type": "string", "description": "Single-edit form: exact text in the note to replace."},
			"new_string":  map[string]any{"type": "string", "description": "Single-edit form: replacement text."},
			"replace_all": map[string]any{"type": "boolean", "description": "Single-edit form: if true, replace every occurrence; otherwise old_string must be unique."},
			"edits": map[string]any{
				"type":        "array",
				"description": "Multi-edit form: ops applied sequentially. Provide this OR top-level old_string/new_string, not both.",
				"minItems":    1,
				"items":       editItem,
			},
		},
		"additionalProperties": false,
	}
}

func (noteEditTool) Execute(ctx context.Context, rc *RunContext, argsJSON string) Result {
	var args noteEditArgs
	if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
		return errorResult("invalid_arguments", err.Error())
	}

	// Normalize to a single ops slice. Accept either form; reject mixing or
	// omission. The multi-edit path is just the single-edit path with N>1.
	hasTopLevel := args.OldString != "" || args.NewString != ""
	hasEdits := len(args.Edits) > 0
	if hasTopLevel && hasEdits {
		return errorResult("invalid_arguments", "provide either top-level old_string/new_string OR an edits array, not both.")
	}
	if !hasTopLevel && !hasEdits {
		return errorResult("invalid_arguments", "must provide either old_string/new_string or a non-empty edits array.")
	}

	var ops []noteEditOp
	if hasTopLevel {
		ops = []noteEditOp{{OldString: args.OldString, NewString: args.NewString, ReplaceAll: args.ReplaceAll}}
	} else {
		ops = args.Edits
	}

	// Validate every op upfront. If any is malformed, reject the whole batch
	// without applying anything — the model must fix args and resubmit.
	for i, op := range ops {
		if op.OldString == "" {
			return errorResult("empty_old_string", fmt.Sprintf("edit %d: old_string must not be empty.", i+1))
		}
		if op.OldString == op.NewString {
			return errorResult("no_op", fmt.Sprintf("edit %d: old_string equals new_string; nothing to change.", i+1))
		}
	}

	if rc.ApplyNoteEdit == nil {
		return errorResult("apply_unavailable", "no edit apply callback wired on RunContext.")
	}

	// Apply each op sequentially against the live editor. Cascading failures
	// are intentional: a later op that depends on an earlier failed op will
	// itself fail (old_string_not_found) and is reported alongside the trigger.
	outcomes := make([]noteEditOutcome, 0, len(ops))
	for i, op := range ops {
		reply, err := rc.ApplyNoteEdit(ctx, op.OldString, op.NewString, op.ReplaceAll)
		if err != nil {
			if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
				return errorResult("cancelled", "run cancelled before edit could be applied.")
			}
			return errorResult("apply_failed", err.Error())
		}

		out := noteEditOutcome{Index: i + 1, Count: reply.Count}
		switch {
		case reply.Applied:
			out.Applied = true
		case reply.ConflictProtected:
			out.Error = "protected_range"
			out.Message = "edit overlaps a $>> <<$ protected range; choose an old_string outside the protected area."
		case reply.ConflictAmbiguous:
			out.Error = "multiple_matches"
			out.Message = fmt.Sprintf("old_string occurs %d times; set replace_all=true or add more surrounding context.", reply.Count)
		case reply.ConflictNotFound:
			out.Error = "old_string_not_found"
			out.Message = "old_string not present in the current note. If an earlier edit in this batch failed, this edit may have been waiting on its result."
		default:
			out.Error = "apply_failed"
			out.Message = "edit was not applied."
		}
		outcomes = append(outcomes, out)
	}

	applied := 0
	var failed []noteEditOutcome
	for _, o := range outcomes {
		if o.Applied {
			applied++
		} else {
			failed = append(failed, o)
		}
	}

	// All applied: run ends.
	if len(failed) == 0 {
		payloadM := map[string]any{
			"type":    "note_content_edit",
			"applied": applied,
		}
		if len(ops) == 1 {
			payloadM["old_string"] = ops[0].OldString
			payloadM["new_string"] = ops[0].NewString
			payloadM["replace_all"] = ops[0].ReplaceAll
		}
		payload, _ := json.Marshal(payloadM)
		summary := "note edited"
		switch {
		case len(ops) > 1:
			summary = fmt.Sprintf("note edited (%d edits)", applied)
		case ops[0].ReplaceAll && outcomes[0].Count > 1:
			summary = fmt.Sprintf("note edited (%d replacements)", outcomes[0].Count)
		}
		return Result{
			JSON:    string(payload),
			EndsRun: true,
			Summary: summary,
		}
	}

	// Some edits failed: keep the applied ones, hand the failed ones back so
	// the model can retry only those. Do NOT end the run.
	payloadM := map[string]any{
		"type":    "note_content_edit",
		"applied": applied,
		"total":   len(ops),
		"failed":  failed,
	}
	payload, _ := json.Marshal(payloadM)
	var summary string
	switch {
	case len(ops) == 1:
		summary = failed[0].Error
	case applied == 0:
		summary = fmt.Sprintf("note edits failed (0 of %d)", len(ops))
	default:
		summary = fmt.Sprintf("note edited partially (%d of %d, %d failed)", applied, len(ops), len(failed))
	}
	return Result{
		JSON:    string(payload),
		IsError: true,
		Summary: summary,
	}
}

// note_write ──────────────────────────────────────────────────────────────────

type noteWriteArgs struct {
	Content string `json:"content"`
}

type noteWriteTool struct{}

func (noteWriteTool) Name() string { return "note_write" }

func (noteWriteTool) Description() string { return noteWriteDesc }

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
