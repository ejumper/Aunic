package claude

import (
	"fmt"
	"strings"

	"github.com/ejumper/aunic/transcript"
)

// maxHistoryRows caps how many trailing transcript rows get summarized, so a
// long-running note's history doesn't balloon the first prompt of every fresh
// Claude session.
const maxHistoryRows = 40

// SummarizePriorTranscript renders Aunic transcript rows as a compact,
// plain-text recap wrapped in a <prior-conversation-summary> tag, to prepend
// to the first prompt of a fresh Claude session that has no --resume history.
// Deliberately not a wire-format translation — Claude Code's own session
// JSONL format is documented as internal/unstable (Anthropic's docs
// explicitly say not to hand-construct it); this is just a readable recap,
// not a session restore.
func SummarizePriorTranscript(rows []transcript.Row) string {
	if len(rows) == 0 {
		return ""
	}
	start := 0
	if len(rows) > maxHistoryRows {
		start = len(rows) - maxHistoryRows
	}

	var b strings.Builder
	b.WriteString("<prior-conversation-summary>\n")
	b.WriteString("Recap of earlier conversation on this note, from before this session started:\n\n")
	for _, r := range rows[start:] {
		if line := summarizeRow(r); line != "" {
			b.WriteString(line)
			b.WriteString("\n")
		}
	}
	b.WriteString("</prior-conversation-summary>")
	return b.String()
}

func summarizeRow(r transcript.Row) string {
	switch r.Type {
	case transcript.TypeMessage:
		c, err := transcript.DecodeMessage(r.Content)
		if err != nil {
			return ""
		}
		switch r.Role {
		case transcript.RoleUser:
			return "User: " + c.Text
		case transcript.RoleAssistant:
			return "Assistant: " + c.Text
		}
		return ""
	case transcript.TypeToolCall:
		return summarizeToolCall(r)
	case transcript.TypeToolResult:
		return summarizeToolResult(r)
	}
	return ""
}

func summarizeToolCall(r transcript.Row) string {
	switch r.Tool {
	case transcript.ToolRead, transcript.ToolWrite, transcript.ToolEdit:
		c, err := transcript.DecodeAgentFileCall(r.Content)
		if err != nil {
			return ""
		}
		return fmt.Sprintf("Assistant used %s on %s", r.Tool, c.FilePath)
	case transcript.ToolBash:
		c, err := transcript.DecodeAgentCmdCall(r.Content)
		if err != nil {
			return ""
		}
		return "Assistant ran: " + c.Command
	case transcript.ToolGrep, transcript.ToolGlob:
		c, err := transcript.DecodeAgentPatternCall(r.Content)
		if err != nil {
			return ""
		}
		return fmt.Sprintf("Assistant searched with %s: %s", r.Tool, c.Pattern)
	case transcript.ToolWebSearch:
		c, err := transcript.DecodeSearchCall(r.Content)
		if err != nil {
			return ""
		}
		return "Assistant searched the web: " + c.Query
	case transcript.ToolWebFetch:
		c, err := transcript.DecodeFetchCall(r.Content)
		if err != nil {
			return ""
		}
		return "Assistant fetched: " + c.URL
	}
	return ""
}

func summarizeToolResult(r transcript.Row) string {
	switch r.Tool {
	case transcript.ToolRead, transcript.ToolWrite, transcript.ToolGrep, transcript.ToolGlob:
		c, err := transcript.DecodeAgentPreviewResult(r.Content)
		if err != nil {
			return ""
		}
		return "  -> " + strings.Join(c.Lines, " / ")
	case transcript.ToolBash:
		c, err := transcript.DecodeAgentOutputResult(r.Content)
		if err != nil {
			return ""
		}
		return "  -> " + c.Output
	}
	// Results for other tools (web_search/web_fetch) are already reflected in
	// the corresponding call summary above.
	return ""
}
