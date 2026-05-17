package runner

import (
	"context"
	"encoding/json"
)

type Tool interface {
	Name() string
	Description() string
	Schema() map[string]any
	Execute(ctx context.Context, rc *RunContext, argsJSON string) Result
}

type Result struct {
	JSON    string
	IsError bool
	EndsRun bool
	Summary string
}

func errorResult(code, msg string) Result {
	b, _ := json.Marshal(map[string]string{
		"error":   code,
		"message": msg,
	})
	return Result{
		JSON:    string(b),
		IsError: true,
		Summary: code,
	}
}

func registry() map[string]Tool {
	tools := []Tool{
		noteEditTool{},
		noteWriteTool{},
		webSearchTool{},
		webFetchTool{},
	}
	m := make(map[string]Tool, len(tools))
	for _, t := range tools {
		m[t.Name()] = t
	}
	return m
}

// AllTools returns the registered tools in a stable order. Used by the runner
// to build the openai-go tool param list.
func AllTools() []Tool {
	return []Tool{
		noteEditTool{},
		noteWriteTool{},
		webSearchTool{},
		webFetchTool{},
	}
}

// Lookup returns the tool registered under name, or nil if not found.
func Lookup(name string) Tool {
	return registry()[name]
}
