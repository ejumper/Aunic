package claude

import "encoding/json"

// BaseEvent holds just the type discriminator for initial top-level dispatch.
// Top-level types seen: "system", "stream_event", "assistant", "user", "result",
// "rate_limit_event" (confirmed via live capture).
type BaseEvent struct {
	Type string `json:"type"`
}

// SystemInitEvent is the first event of every session (Subtype "init"), or a
// periodic status ping (Subtype "status", most fields empty).
type SystemInitEvent struct {
	Type       string   `json:"type"`
	Subtype    string   `json:"subtype"`
	Model      string   `json:"model,omitempty"`
	Cwd        string   `json:"cwd,omitempty"`
	SessionID  string   `json:"session_id,omitempty"`
	Tools      []string `json:"tools,omitempty"`
	McpServers []struct {
		Name   string `json:"name"`
		Status string `json:"status"`
	} `json:"mcp_servers,omitempty"`
}

// StreamEventEnvelope wraps every message_start/content_block_*/message_delta/
// message_stop event. Confirmed one level deeper than the raw Anthropic
// Messages API shape: unmarshal into this first, then re-unmarshal Event into
// the concrete inner type once its own "type" field is known.
type StreamEventEnvelope struct {
	Type      string          `json:"type"` // "stream_event"
	Event     json.RawMessage `json:"event"`
	SessionID string          `json:"session_id"`
}

// InnerEventBase holds the inner event's type discriminator for dispatch.
// Inner types seen: message_start, content_block_start, content_block_delta,
// content_block_stop, message_delta, message_stop.
type InnerEventBase struct {
	Type string `json:"type"`
}

// ContentBlockStartEvent fires when a new content block begins. ContentBlock
// is a "text" block (Text starts empty, filled via deltas) or a "tool_use"
// block (ID/Name set immediately, Input filled incrementally via
// input_json_delta chunks in ContentBlockDeltaEvent).
type ContentBlockStartEvent struct {
	Type         string `json:"type"`
	Index        int    `json:"index"`
	ContentBlock struct {
		Type  string         `json:"type"`
		Text  string         `json:"text,omitempty"`
		ID    string         `json:"id,omitempty"`
		Name  string         `json:"name,omitempty"`
		Input map[string]any `json:"input,omitempty"`
	} `json:"content_block"`
}

// ContentBlockDeltaEvent carries either a text delta (Delta.Type
// "text_delta", Delta.Text is the chunk) or a tool-input delta (Delta.Type
// "input_json_delta", Delta.PartialJSON accumulates into the complete
// arguments JSON once ContentBlockStopEvent arrives for that index).
type ContentBlockDeltaEvent struct {
	Type  string `json:"type"`
	Index int    `json:"index"`
	Delta struct {
		Type        string `json:"type"`
		Text        string `json:"text,omitempty"`
		PartialJSON string `json:"partial_json,omitempty"`
	} `json:"delta"`
}

// ContentBlockStopEvent fires when a content block (text or tool_use) is complete.
type ContentBlockStopEvent struct {
	Type  string `json:"type"`
	Index int    `json:"index"`
}

// MessageStopEvent fires when the assistant's turn is fully done.
type MessageStopEvent struct {
	Type string `json:"type"`
}

// ToolResultMessage is the top-level "user"-role message Claude Code emits
// carrying a tool_result content block, plus its own bonus stdout/stderr
// fields not part of the standard content block.
type ToolResultMessage struct {
	Type    string `json:"type"` // "user"
	Message struct {
		Role    string `json:"role"`
		Content []struct {
			Type       string `json:"type"` // "tool_result"
			ToolUseID  string `json:"tool_use_id"`
			Content    any    `json:"content"`
			IsError    bool   `json:"is_error"`
		} `json:"content"`
	} `json:"message"`
	ToolUseResult struct {
		Stdout      string `json:"stdout"`
		Stderr      string `json:"stderr"`
		Interrupted bool   `json:"interrupted"`
	} `json:"tool_use_result"`
}

// ResultEvent is the final event of a turn: Result is the plain-text answer,
// TotalCostUSD is what this turn cost.
type ResultEvent struct {
	Type         string  `json:"type"` // "result"
	Subtype      string  `json:"subtype"`
	IsError      bool    `json:"is_error"`
	Result       string  `json:"result"`
	TotalCostUSD float64 `json:"total_cost_usd"`
}
