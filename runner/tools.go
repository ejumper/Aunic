package runner

import (
	"context"
	"encoding/json"
	"sync"

	"github.com/ejumper/aunic/todos"
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
	// TodosAfter is set by tools that mutate the active todo list (todo_write,
	// todo_done) to the post-call state. The runner uses it to re-render the
	// todos block in the user message before the next API call. Leave nil for
	// tools that don't touch todos.
	TodosAfter []todos.Todo
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

// registry is the full tool map, built once at first use. The tool list is
// static after process start, so building this on every Lookup call (as the
// previous implementation did) was wasted work on every tool invocation.
var registry = sync.OnceValue(func() map[string]Tool {
	all := append(AllTools(), AgentWorkTools()...)
	all = append(all, noteEditAtTool{})
	m := make(map[string]Tool, len(all))
	for _, t := range all {
		m[t.Name()] = t
	}
	return m
})

// AllTools returns the base tool set (always available, regardless of agent mode).
func AllTools() []Tool {
	return []Tool{
		noteEditTool{},
		noteWriteTool{},
		webSearchTool{},
		webFetchTool{},
	}
}

// AgentReadTools returns the additional tools available in agent read mode.
func AgentReadTools() []Tool {
	return []Tool{
		readTool{},
		grepTool{},
		globTool{},
		todoWriteTool{},
		todoDoneTool{},
	}
}

// AgentWorkTools returns the additional tools available in agent work mode
// (all read tools plus write, edit, bash).
func AgentWorkTools() []Tool {
	return []Tool{
		readTool{},
		writeTool{},
		editTool{},
		grepTool{},
		globTool{},
		bashTool{},
		todoWriteTool{},
		todoDoneTool{},
	}
}

// Lookup returns the tool registered under name, or nil if not found.
func Lookup(name string) Tool {
	return registry()[name]
}
