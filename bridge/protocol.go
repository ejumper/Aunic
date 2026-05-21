// Package bridge defines the wire protocol between Aunic (Go) and the
// TypeScript bridge subprocess that drives a Claude Agent SDK session.
//
// The bridge is launched per run. The first thing Aunic writes to its stdin
// is a StartConfig JSON document terminated by a newline. From that point on
// both sides exchange newline-delimited JSON messages in either direction.
//
// Direction conventions:
//   Aunic → bridge (stdin):  StartConfig (once), then ToolResult / Abort
//   bridge → Aunic (stdout): Event stream (Started, Text, Thinking, ToolCall,
//                            ToolResult, ToolCallBuiltin, ToolResultBuiltin,
//                            Usage, End)
package bridge

import "encoding/json"

// ── Aunic → bridge messages ───────────────────────────────────────────────────

// StartConfig is sent once on bridge startup. It carries everything the bridge
// needs to construct the Agent SDK session and the in-process MCP server.
type StartConfig struct {
	// Model is the canonical model ID (e.g. "claude-opus-4-7").
	Model string `json:"model"`
	// Effort is one of "low", "medium", "high", "xhigh", "max". Empty → "medium".
	Effort string `json:"effort,omitempty"`
	// MaxTurns caps the SDK's agent loop (mirrors Aunic's maxSteps).
	MaxTurns int `json:"maxTurns"`

	// SystemPrompt is the Aunic-side system prompt. Sent as a literal string;
	// the bridge does not append the Claude Code default preset.
	SystemPrompt string `json:"systemPrompt"`

	// UserPrompt is the formatted user message. Aunic composes this server-side
	// from the snapshot, prior transcript, todos block, and the user's prompt.
	UserPrompt string `json:"userPrompt"`

	// BuiltinTools is the list of Claude Code built-in tool names to allow
	// (e.g. ["Read", "Glob", "Grep", "LS", "WebFetch", "WebSearch"]). Empty
	// disables all built-in tools.
	BuiltinTools []string `json:"builtinTools"`

	// AunicTools is the list of Aunic-specific tools to register with the
	// in-process MCP server. The bridge creates stub handlers that forward
	// invocations to Aunic over stdio.
	AunicTools []ToolDef `json:"aunicTools"`
}

// ToolDef is a tool definition forwarded to the bridge. The bridge registers
// each as an MCP tool whose handler forwards args to Aunic.
type ToolDef struct {
	Name        string          `json:"name"`
	Description string          `json:"description"`
	Schema      json.RawMessage `json:"schema"`
}

// ToolResult is Aunic's response to a ToolCall event. The bridge correlates
// by ID. JSON is the raw tool result; IsError signals tool-level failure.
type ToolResult struct {
	Type    string `json:"type"` // always "tool_result"
	ID      string `json:"id"`
	JSON    string `json:"json"`
	IsError bool   `json:"isError"`
}

// Abort tells the bridge to cancel the in-flight query and exit.
type Abort struct {
	Type string `json:"type"` // always "abort"
}

// ── bridge → Aunic events ─────────────────────────────────────────────────────

// Event is the wire envelope sent by the bridge on stdout. Only one of the
// payload fields is populated per message; the Type field selects which.
type Event struct {
	Type string `json:"type"`

	// Common payloads
	Text    string `json:"text,omitempty"`
	Name    string `json:"name,omitempty"`
	ID      string `json:"id,omitempty"`
	Summary string `json:"summary,omitempty"`
	Args    string `json:"args,omitempty"`    // tool args as JSON string
	Result  string `json:"result,omitempty"`  // tool result as JSON string
	IsError bool   `json:"isError,omitempty"` // for tool_result events

	// Usage payload
	InputTokens  int `json:"inputTokens,omitempty"`
	OutputTokens int `json:"outputTokens,omitempty"`

	// End payload
	Reason  string `json:"reason,omitempty"`  // "stop" | "max_turns" | "error" | "cancelled"
	Message string `json:"message,omitempty"` // present on error/cancelled
}

// Event type constants. Strings live in one place so both sides agree.
const (
	EventStarted           = "started"             // bridge ready, SDK initialized
	EventThinking          = "thinking"            // model thinking block
	EventText              = "text"                // model text block
	EventToolCall          = "tool_call"           // Aunic MCP tool invoked — awaits ToolResult
	EventToolResult        = "tool_result"         // Aunic MCP tool result (for display only — Aunic already provided it)
	EventToolCallBuiltin   = "tool_call_builtin"   // SDK-handled tool invoked
	EventToolResultBuiltin = "tool_result_builtin" // SDK-handled tool returned
	EventUsage             = "usage"               // token usage tick
	EventEnd               = "end"                 // run is over
)

// End reasons
const (
	EndStop      = "stop"
	EndMaxTurns  = "max_turns"
	EndError     = "error"
	EndCancelled = "cancelled"
)
