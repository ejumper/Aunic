package runner

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/ejumper/aunic/todos"
)

//go:embed desc_todo_write.md
var todoWriteDesc string

//go:embed desc_todo_done.md
var todoDoneDesc string

// todo_write ──────────────────────────────────────────────────────────────────

type todoWriteArgs struct {
	Todos []string `json:"todos"`
}

type todoWriteTool struct{}

func (todoWriteTool) Name() string { return "todo_write" }

func (todoWriteTool) Description() string { return todoWriteDesc }

func (todoWriteTool) Schema() map[string]any {
	return map[string]any{
		"type": "object",
		"properties": map[string]any{
			"todos": map[string]any{
				"type":        "array",
				"description": "Ordered list of tasks to track.",
				"items": map[string]any{
					"type":        "string",
					"description": "Imperative task description (e.g. 'Write tests').",
				},
			},
		},
		"required":             []string{"todos"},
		"additionalProperties": false,
	}
}

func (todoWriteTool) Execute(ctx context.Context, rc *RunContext, argsJSON string) Result {
	var args todoWriteArgs
	if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
		return errorResult("invalid_arguments", err.Error())
	}
	if len(args.Todos) == 0 {
		return errorResult("empty_list", "todos array must contain at least one item; call note_edit instead if you have nothing to plan.")
	}
	for i, t := range args.Todos {
		if t == "" {
			return errorResult("empty_item", fmt.Sprintf("todos[%d] is empty.", i))
		}
	}

	if rc.ApplyTodoWrite == nil {
		return errorResult("apply_unavailable", "no todo_write apply callback wired on RunContext.")
	}

	reply, err := rc.ApplyTodoWrite(ctx, args.Todos)
	if err != nil {
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return errorResult("cancelled", "run cancelled before todos could be written.")
		}
		return errorResult("apply_failed", err.Error())
	}
	if !reply.Applied {
		return errorResult("apply_failed", "todo write was not applied.")
	}

	payload, _ := json.Marshal(map[string]any{
		"type":     "todos_written",
		"items":    reply.Items,
		"rendered": todos.Render(reply.Items),
	})
	return Result{
		JSON:       string(payload),
		Summary:    fmt.Sprintf("%d todos written", len(reply.Items)),
		TodosAfter: reply.Items,
	}
}

// todo_done ───────────────────────────────────────────────────────────────────

type todoDoneArgs struct {
	ID int `json:"id"`
}

type todoDoneTool struct{}

func (todoDoneTool) Name() string { return "todo_done" }

func (todoDoneTool) Description() string { return todoDoneDesc }

func (todoDoneTool) Schema() map[string]any {
	return map[string]any{
		"type": "object",
		"properties": map[string]any{
			"id": map[string]any{
				"type":        "integer",
				"description": "The todo's ID number (the N in #N).",
			},
		},
		"required":             []string{"id"},
		"additionalProperties": false,
	}
}

func (todoDoneTool) Execute(ctx context.Context, rc *RunContext, argsJSON string) Result {
	var args todoDoneArgs
	if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
		return errorResult("invalid_arguments", err.Error())
	}

	if rc.ApplyTodoDone == nil {
		return errorResult("apply_unavailable", "no todo_done apply callback wired on RunContext.")
	}

	reply, err := rc.ApplyTodoDone(ctx, args.ID)
	if err != nil {
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return errorResult("cancelled", "run cancelled before todo could be marked done.")
		}
		return errorResult("apply_failed", err.Error())
	}
	if reply.NotFound {
		return errorResult("todo_not_found", fmt.Sprintf("no todo with id %d in the active list.", args.ID))
	}
	if !reply.Applied {
		return errorResult("apply_failed", "todo_done was not applied.")
	}

	var completedText string
	doneCount := 0
	remaining := make([]string, 0, len(reply.Items))
	for _, t := range reply.Items {
		if t.ID == args.ID {
			completedText = t.Text
		}
		if t.Done {
			doneCount++
		} else {
			remaining = append(remaining, fmt.Sprintf("#%d %s", t.ID, t.Text))
		}
	}

	payload, _ := json.Marshal(map[string]any{
		"type":           "todo_done",
		"id":             args.ID,
		"completed_text": completedText,
		"items":          reply.Items,
		"rendered":       todos.Render(reply.Items),
		"remaining":      remaining,
	})
	return Result{
		JSON:       string(payload),
		Summary:    fmt.Sprintf("✓ #%d (%d/%d done)", args.ID, doneCount, len(reply.Items)),
		TodosAfter: reply.Items,
	}
}
