package runner

import (
	"context"
	_ "embed"
	"encoding/json"
	"fmt"

	"github.com/ejumper/aunic/web"
)

//go:embed desc_web_search.md
var webSearchDesc string

//go:embed desc_web_fetch.md
var webFetchDesc string

const fetchTruncate = 8000

// web_search ──────────────────────────────────────────────────────────────────

type webSearchArgs struct {
	Query string `json:"query"`
	N     int    `json:"n,omitempty"`
}

type webSearchTool struct{}

func (webSearchTool) Name() string { return "web_search" }

func (webSearchTool) Description() string { return webSearchDesc }

func (webSearchTool) Schema() map[string]any {
	return map[string]any{
		"type": "object",
		"properties": map[string]any{
			"query": map[string]any{"type": "string", "description": "Search query."},
			"n":     map[string]any{"type": "integer", "description": "Number of results (1-25, default 5).", "minimum": 1, "maximum": 25},
		},
		"required":             []string{"query"},
		"additionalProperties": false,
	}
}

func (webSearchTool) Execute(ctx context.Context, rc *RunContext, argsJSON string) Result {
	var args webSearchArgs
	if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
		return errorResult("invalid_arguments", err.Error())
	}
	if args.Query == "" {
		return errorResult("empty_query", "query must not be empty.")
	}
	n := args.N
	if n == 0 {
		n = 5
	}

	results, err := web.Search(ctx, args.Query, n)
	if err != nil {
		return errorResult("search_failed", err.Error())
	}

	payload := make([]map[string]string, 0, len(results))
	for _, r := range results {
		payload = append(payload, map[string]string{
			"title":    r.Title,
			"url":      r.URL,
			"domain":   r.Domain,
			"abstract": r.Abstract,
		})
	}
	b, _ := json.Marshal(payload)
	return Result{
		JSON:    string(b),
		Summary: fmt.Sprintf("%d results", len(results)),
	}
}

// web_fetch ───────────────────────────────────────────────────────────────────

type webFetchArgs struct {
	URL string `json:"url"`
}

type webFetchTool struct{}

func (webFetchTool) Name() string { return "web_fetch" }

func (webFetchTool) Description() string { return webFetchDesc }

func (webFetchTool) Schema() map[string]any {
	return map[string]any{
		"type": "object",
		"properties": map[string]any{
			"url": map[string]any{"type": "string", "description": "URL to fetch."},
		},
		"required":             []string{"url"},
		"additionalProperties": false,
	}
}

func (webFetchTool) Execute(ctx context.Context, rc *RunContext, argsJSON string) Result {
	var args webFetchArgs
	if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
		return errorResult("invalid_arguments", err.Error())
	}
	if args.URL == "" {
		return errorResult("empty_url", "url must not be empty.")
	}

	page, err := web.Fetch(ctx, args.URL)
	if err != nil {
		return errorResult("fetch_failed", err.Error())
	}

	md := page.Markdown
	truncated := false
	if len(md) > fetchTruncate {
		md = md[:fetchTruncate] + "\n\n[content truncated at 8000 chars]"
		truncated = true
	}

	b, _ := json.Marshal(map[string]string{
		"title":    page.Title,
		"url":      page.URL,
		"markdown": md,
	})
	summary := fmt.Sprintf("%d chars", len(md))
	if truncated {
		summary += " (truncated)"
	}
	return Result{
		JSON:    string(b),
		Summary: summary,
	}
}
