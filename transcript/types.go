package transcript

import "encoding/json"

// Role and Type are string enums kept loose so the markdown table renders
// readable string values rather than opaque integers.
type Role string
type Type string

const (
	RoleUser      Role = "user"
	RoleAssistant Role = "assistant"
	RoleTool      Role = "tool"

	TypeMessage    Type = "message"
	TypeToolCall   Type = "tool_call"
	TypeToolResult Type = "tool_result"
)

// Tool names.
const (
	ToolWebSearch = "web_search"
	ToolWebFetch  = "web_fetch"
)

// Row is one entry in the transcript table. The schema is fixed; the Content
// JSON varies by (Tool, Type). Use the helpers in content.go to encode/decode.
type Row struct {
	Num     int
	Role    Role
	Type    Type
	Tool    string
	ToolID  string
	Content json.RawMessage
}

// SearchCallContent is the content of a Tool=web_search, Type=tool_call row.
type SearchCallContent struct {
	Query string `json:"query"`
}

// SearchResultHit is one item inside a SearchResultContent slice.
type SearchResultHit struct {
	Title   string `json:"title"`
	URL     string `json:"url"`
	Domain  string `json:"domain"`
	Snippet string `json:"snippet"`
}

// SearchResultContent is the content of a Tool=web_search, Type=tool_result row.
type SearchResultContent []SearchResultHit

// FetchCallContent is the content of a Tool=web_fetch, Type=tool_call row.
type FetchCallContent struct {
	URL string `json:"url"`
}

// FetchResultContent is the content of a Tool=web_fetch, Type=tool_result row.
// Snippet is the first ~300 chars of the fetched markdown — the full body is
// intentionally not retained (per project design decision).
type FetchResultContent struct {
	Title   string `json:"title"`
	URL     string `json:"url"`
	Snippet string `json:"snippet"`
}

// MessageContent is the content of a Type=message row (chat). Used for both
// Role=user and Role=assistant message rows.
type MessageContent struct {
	Text string `json:"text"`
}
