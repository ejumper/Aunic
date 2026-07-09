package pi

// BaseEvent holds just the type discriminator for initial dispatch.
type BaseEvent struct {
	Type string `json:"type"`
}

// MessageUpdateEvent is emitted during streaming of an assistant message.
type MessageUpdateEvent struct {
	Type                  string           `json:"type"`
	AssistantMessageEvent AssistantMsgEvent `json:"assistantMessageEvent"`
}

// AssistantMsgEvent is the inner delta event inside a MessageUpdateEvent.
type AssistantMsgEvent struct {
	// Type is one of: start, text_start, text_delta, text_end,
	// thinking_start, thinking_delta, thinking_end,
	// toolcall_start, toolcall_delta, toolcall_end, done, error.
	Type         string       `json:"type"`
	ContentIndex int          `json:"contentIndex"`
	Delta        string       `json:"delta"`
	// ToolCall is populated on toolcall_end.
	ToolCall *ToolCallObj `json:"toolCall,omitempty"`
}

// ToolCallObj holds a completed tool call from the model.
type ToolCallObj struct {
	ID        string                 `json:"id"`
	Name      string                 `json:"name"`
	Arguments map[string]any `json:"arguments"`
}

// ToolExecStartEvent is emitted when a tool begins execution.
type ToolExecStartEvent struct {
	Type       string                 `json:"type"`
	ToolCallID string                 `json:"toolCallId"`
	ToolName   string                 `json:"toolName"`
	Args       map[string]any `json:"args"`
}

// ToolExecUpdateEvent is emitted with partial streaming tool output.
type ToolExecUpdateEvent struct {
	Type         string        `json:"type"`
	ToolCallID   string        `json:"toolCallId"`
	ToolName     string        `json:"toolName"`
	PartialResult *ToolResult  `json:"partialResult,omitempty"`
}

// ToolExecEndEvent is emitted when a tool finishes.
type ToolExecEndEvent struct {
	Type       string     `json:"type"`
	ToolCallID string     `json:"toolCallId"`
	ToolName   string     `json:"toolName"`
	Result     ToolResult `json:"result"`
	IsError    bool       `json:"isError"`
}

// ToolResult wraps the content blocks returned by a tool.
type ToolResult struct {
	Content []ContentBlock `json:"content"`
}

// ContentBlock is a typed content block (text or image).
type ContentBlock struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

// AgentEndEvent is emitted when the agent finishes processing.
type AgentEndEvent struct {
	Type string `json:"type"`
}

// CompactionStartEvent is emitted when context compaction begins.
type CompactionStartEvent struct {
	Type   string `json:"type"`
	Reason string `json:"reason"`
}

// CompactionEndEvent is emitted when compaction completes.
type CompactionEndEvent struct {
	Type    string `json:"type"`
	Reason  string `json:"reason"`
	Aborted bool   `json:"aborted"`
}

// AutoRetryStartEvent is emitted when a transient error triggers automatic retry.
type AutoRetryStartEvent struct {
	Type         string `json:"type"`
	Attempt      int    `json:"attempt"`
	MaxAttempts  int    `json:"maxAttempts"`
	ErrorMessage string `json:"errorMessage"`
}

// ExtensionUIRequestEvent is emitted by extensions that need UI interaction.
// Dialog methods (select/confirm/input/editor) block until a response is sent.
// Fire-and-forget methods (notify/setStatus/setWidget/setTitle) need no response.
type ExtensionUIRequestEvent struct {
	Type       string `json:"type"`
	ID         string `json:"id"`
	Method     string `json:"method"`
	// setStatus fields
	StatusKey  string `json:"statusKey,omitempty"`
	StatusText string `json:"statusText,omitempty"`
	// notify fields
	Message    string `json:"message,omitempty"`
	// select fields
	Title   string   `json:"title,omitempty"`
	Options []string `json:"options,omitempty"`
}

// RpcResponse is emitted in response to any command sent to Pi.
type RpcResponse struct {
	Type    string      `json:"type"`    // always "response"
	ID      string      `json:"id,omitempty"`
	Command string      `json:"command"`
	Success bool        `json:"success"`
	Error   string      `json:"error,omitempty"`
	Data    any `json:"data,omitempty"`
}

// StateData is the data payload in a get_state response.
type StateData struct {
	IsStreaming   bool   `json:"isStreaming"`
	IsCompacting  bool   `json:"isCompacting"`
	MessageCount  int    `json:"messageCount"`
	SessionFile   string `json:"sessionFile,omitempty"`
	SessionID     string `json:"sessionId,omitempty"`
}

// ExtensionErrorEvent is emitted when an extension throws.
type ExtensionErrorEvent struct {
	Type          string `json:"type"`
	ExtensionPath string `json:"extensionPath,omitempty"`
	Event         string `json:"event,omitempty"`
	Error         string `json:"error"`
}
