package pi

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/ejumper/aunic/transcript"
)

// BuildSessionFile translates Aunic transcript rows into a Pi v3 session JSONL
// file and returns its path. This is used when a new Pi session has no history
// but Aunic's transcript table has prior conversation rows — the synthesized
// file is passed to Pi via --session so the model has context of prior turns.
//
// The caller is responsible for deleting the file when it is no longer needed
// (typically after Pi confirms it loaded the session).
func BuildSessionFile(rows []transcript.Row, cwd, dir, sessionID string) (string, error) {
	if err := os.MkdirAll(dir, 0755); err != nil {
		return "", fmt.Errorf("pi session: mkdir: %w", err)
	}
	path := filepath.Join(dir, sessionID+"-backup.jsonl")
	f, err := os.Create(path)
	if err != nil {
		return "", fmt.Errorf("pi session: create: %w", err)
	}
	defer f.Close()

	enc := json.NewEncoder(f)
	now := time.Now().UTC()

	// Session header — first line, no id/parentId.
	header := map[string]any{
		"type":      "session",
		"version":   3,
		"id":        sessionID,
		"timestamp": now.Format(time.RFC3339Nano),
		"cwd":       cwd,
	}
	if err := enc.Encode(header); err != nil {
		return "", err
	}

	// Convert rows to Pi session message entries.
	entries := rowsToEntries(rows, now)
	prevID := ""
	for i, entry := range entries {
		id := fmt.Sprintf("%08x", i+1)
		entry["id"] = id
		if prevID == "" {
			entry["parentId"] = nil
		} else {
			entry["parentId"] = prevID
		}
		// Stagger timestamps slightly so Pi's tree is well-ordered.
		entry["timestamp"] = now.Add(time.Duration(i) * time.Millisecond).Format(time.RFC3339Nano)
		prevID = id
		if err := enc.Encode(entry); err != nil {
			return "", err
		}
	}

	return path, nil
}

// rowsToEntries converts transcript rows to Pi session message entry maps.
// Each entry is of type {"type":"message","message":{...}}.
// Web search / web fetch rows are collapsed into a single user message block.
func rowsToEntries(rows []transcript.Row, _ time.Time) []map[string]any {
	var out []map[string]any

	// Collapse consecutive web rows into a single context block before the
	// user message that follows them.
	type pendingWeb struct{ query, result string }
	var webBuf []pendingWeb

	flushWeb := func() {
		if len(webBuf) == 0 {
			return
		}
		var sb strings.Builder
		for _, w := range webBuf {
			sb.WriteString("[Web search: ")
			sb.WriteString(w.query)
			sb.WriteString("]\n")
			if w.result != "" {
				sb.WriteString(w.result)
				sb.WriteByte('\n')
			}
		}
		webBuf = nil
		out = append(out, messageEntry("user", []any{
			map[string]any{"type": "text", "text": sb.String()},
		}))
	}

	for _, row := range rows {
		switch {
		case row.Tool == transcript.ToolWebSearch && row.Type == transcript.TypeToolCall:
			c, err := transcript.DecodeSearchCall(row.Content)
			if err == nil {
				webBuf = append(webBuf, pendingWeb{query: c.Query})
			}

		case row.Tool == transcript.ToolWebSearch && row.Type == transcript.TypeToolResult:
			hits, err := transcript.DecodeSearchResult(row.Content)
			if err == nil && len(webBuf) > 0 {
				var sb strings.Builder
				for i, h := range hits {
					if i >= 5 {
						break
					}
					fmt.Fprintf(&sb, "%d. %s <%s>\n   %s\n", i+1, h.Title, h.URL, h.Snippet)
				}
				webBuf[len(webBuf)-1].result = sb.String()
			}

		case row.Tool == transcript.ToolWebFetch:
			// Skip web fetch rows — they're noise for context reconstruction.

		case row.Role == transcript.RoleUser && row.Type == transcript.TypeMessage:
			flushWeb()
			c, err := transcript.DecodeMessage(row.Content)
			if err != nil {
				continue
			}
			out = append(out, messageEntry("user", c.Text))

		case row.Role == transcript.RoleAssistant && row.Type == transcript.TypeMessage:
			flushWeb()
			c, err := transcript.DecodeMessage(row.Content)
			if err != nil {
				continue
			}
			out = append(out, messageEntry("assistant", []any{
				map[string]any{"type": "text", "text": c.Text},
			}))

		case row.Role == transcript.RoleAssistant && row.Type == transcript.TypeToolCall:
			flushWeb()
			entry := assistantToolCallEntry(row)
			if entry != nil {
				out = append(out, entry)
			}

		case row.Role == transcript.RoleTool && row.Type == transcript.TypeToolResult:
			entry := toolResultEntry(row)
			if entry != nil {
				out = append(out, entry)
			}
		}
	}
	flushWeb()
	return out
}

// messageEntry builds a Pi session message entry for user or assistant text.
func messageEntry(role string, content any) map[string]any {
	return map[string]any{
		"type": "message",
		"message": map[string]any{
			"role":      role,
			"content":   content,
			"timestamp": time.Now().UnixMilli(),
		},
	}
}

// assistantToolCallEntry builds a Pi session message entry for a tool call row.
// Returns nil for unrecognised row types.
func assistantToolCallEntry(row transcript.Row) map[string]any {
	args := toolCallArgs(row)
	if args == nil {
		return nil
	}
	piToolName := aunicToPiTool(row.Tool)
	return map[string]any{
		"type": "message",
		"message": map[string]any{
			"role": "assistant",
			"content": []any{
				map[string]any{
					"type":      "toolCall",
					"id":        row.ToolID,
					"name":      piToolName,
					"arguments": args,
				},
			},
			"timestamp": time.Now().UnixMilli(),
			// Minimal required fields for a valid AssistantMessage.
			"api":        "unknown",
			"provider":   "unknown",
			"model":      "unknown",
			"stopReason": "toolUse",
			"usage":      map[string]any{"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
		},
	}
}

// toolResultEntry builds a Pi session message entry for a tool result row.
func toolResultEntry(row transcript.Row) map[string]any {
	text := toolResultText(row)
	piToolName := aunicToPiTool(row.Tool)
	return map[string]any{
		"type": "message",
		"message": map[string]any{
			"role":       "toolResult",
			"toolCallId": row.ToolID,
			"toolName":   piToolName,
			"content":    []any{map[string]any{"type": "text", "text": text}},
			"isError":    false,
			"timestamp":  time.Now().UnixMilli(),
		},
	}
}

// toolCallArgs extracts Pi-formatted argument maps from Aunic content types.
func toolCallArgs(row transcript.Row) map[string]any {
	switch row.Tool {
	case transcript.ToolEdit:
		c, err := transcript.DecodeAgentFileCall(row.Content)
		if err != nil {
			return nil
		}
		return map[string]any{
			"file_path":  c.FilePath,
			"old_string": c.OldString,
			"new_string": c.NewString,
		}
	case transcript.ToolWrite:
		c, err := transcript.DecodeAgentFileCall(row.Content)
		if err != nil {
			return nil
		}
		return map[string]any{
			"file_path": c.FilePath,
			"content":   c.NewString,
		}
	case transcript.ToolRead:
		c, err := transcript.DecodeAgentFileCall(row.Content)
		if err != nil {
			return nil
		}
		return map[string]any{"file_path": c.FilePath}
	case transcript.ToolBash:
		c, err := transcript.DecodeAgentCmdCall(row.Content)
		if err != nil {
			return nil
		}
		return map[string]any{"command": c.Command}
	case transcript.ToolGrep, transcript.ToolGlob:
		c, err := transcript.DecodeAgentPatternCall(row.Content)
		if err != nil {
			return nil
		}
		return map[string]any{"pattern": c.Pattern}
	}
	// Unknown tool — include raw content as a string arg.
	return map[string]any{"raw": string(row.Content)}
}

// toolResultText extracts a text summary from a tool result row.
func toolResultText(row transcript.Row) string {
	switch row.Tool {
	case transcript.ToolRead, transcript.ToolGrep, transcript.ToolGlob:
		c, err := transcript.DecodeAgentPreviewResult(row.Content)
		if err == nil {
			return strings.Join(c.Lines, "\n")
		}
	}
	c, err := transcript.DecodeAgentOutputResult(row.Content)
	if err == nil {
		return c.Output
	}
	return string(row.Content)
}

// aunicToPiTool maps Aunic transcript tool name constants to Pi's lowercase names.
func aunicToPiTool(name string) string {
	switch name {
	case transcript.ToolEdit:
		return "edit"
	case transcript.ToolWrite:
		return "write"
	case transcript.ToolRead:
		return "read"
	case transcript.ToolBash:
		return "bash"
	case transcript.ToolGrep:
		return "grep"
	case transcript.ToolGlob:
		return "glob"
	}
	return strings.ToLower(name)
}
