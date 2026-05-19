package transcript

import "encoding/json"

func mustEncode(v any) json.RawMessage {
	b, err := json.Marshal(v)
	if err != nil {
		// All call sites pass typed structs with serializable fields, so this
		// is a programming error — surface immediately.
		panic(err)
	}
	return b
}

// EncodeSearchCall builds a tool_call content blob for web_search.
func EncodeSearchCall(query string) json.RawMessage {
	return mustEncode(SearchCallContent{Query: query})
}

// EncodeSearchResult builds a tool_result content blob for web_search.
func EncodeSearchResult(hits []SearchResultHit) json.RawMessage {
	if hits == nil {
		hits = []SearchResultHit{}
	}
	return mustEncode(SearchResultContent(hits))
}

// EncodeFetchCall builds a tool_call content blob for web_fetch.
func EncodeFetchCall(url string) json.RawMessage {
	return mustEncode(FetchCallContent{URL: url})
}

// EncodeFetchResult builds a tool_result content blob for web_fetch. snippet
// should already be truncated by the caller.
func EncodeFetchResult(title, url, snippet string) json.RawMessage {
	return mustEncode(FetchResultContent{Title: title, URL: url, Snippet: snippet})
}

// DecodeSearchCall reads a tool_call content blob for web_search.
func DecodeSearchCall(raw json.RawMessage) (SearchCallContent, error) {
	var c SearchCallContent
	err := json.Unmarshal(raw, &c)
	return c, err
}

// DecodeSearchResult reads a tool_result content blob for web_search.
func DecodeSearchResult(raw json.RawMessage) (SearchResultContent, error) {
	var c SearchResultContent
	err := json.Unmarshal(raw, &c)
	return c, err
}

// DecodeFetchCall reads a tool_call content blob for web_fetch.
func DecodeFetchCall(raw json.RawMessage) (FetchCallContent, error) {
	var c FetchCallContent
	err := json.Unmarshal(raw, &c)
	return c, err
}

// DecodeFetchResult reads a tool_result content blob for web_fetch.
func DecodeFetchResult(raw json.RawMessage) (FetchResultContent, error) {
	var c FetchResultContent
	err := json.Unmarshal(raw, &c)
	return c, err
}

// AgentFileCallContent is the content of a tool_call row for Read/Write/Edit.
type AgentFileCallContent struct {
	FilePath  string `json:"file_path"`
	OldString string `json:"old,omitempty"` // Edit only
	NewString string `json:"new,omitempty"` // Edit only
}

// AgentCmdCallContent is the content of a tool_call row for Bash.
type AgentCmdCallContent struct {
	Command string `json:"command"`
}

// AgentPatternCallContent is the content of a tool_call row for Grep/Glob.
type AgentPatternCallContent struct {
	Pattern string `json:"pattern"`
}

// AgentPreviewResultContent is the content of a tool_result row for
// Read/Write/Grep/Glob — stores up to 5 preview lines.
type AgentPreviewResultContent struct {
	Lines []string `json:"lines"`
}

// AgentOutputResultContent is the content of a tool_result row for Bash.
type AgentOutputResultContent struct {
	Output string `json:"output"`
}

// EncodeAgentFileCall builds a tool_call content blob for Read/Write/Edit.
func EncodeAgentFileCall(filePath, oldStr, newStr string) json.RawMessage {
	return mustEncode(AgentFileCallContent{FilePath: filePath, OldString: oldStr, NewString: newStr})
}

// EncodeAgentCmdCall builds a tool_call content blob for Bash.
func EncodeAgentCmdCall(command string) json.RawMessage {
	return mustEncode(AgentCmdCallContent{Command: command})
}

// EncodeAgentPatternCall builds a tool_call content blob for Grep/Glob.
func EncodeAgentPatternCall(pattern string) json.RawMessage {
	return mustEncode(AgentPatternCallContent{Pattern: pattern})
}

// EncodeAgentPreviewResult builds a tool_result content blob for Read/Write/Grep/Glob.
func EncodeAgentPreviewResult(lines []string) json.RawMessage {
	if lines == nil {
		lines = []string{}
	}
	return mustEncode(AgentPreviewResultContent{Lines: lines})
}

// EncodeAgentOutputResult builds a tool_result content blob for Bash.
func EncodeAgentOutputResult(output string) json.RawMessage {
	return mustEncode(AgentOutputResultContent{Output: output})
}

// DecodeAgentFileCall reads a tool_call content blob for Read/Write/Edit.
func DecodeAgentFileCall(raw json.RawMessage) (AgentFileCallContent, error) {
	var c AgentFileCallContent
	err := json.Unmarshal(raw, &c)
	return c, err
}

// DecodeAgentCmdCall reads a tool_call content blob for Bash.
func DecodeAgentCmdCall(raw json.RawMessage) (AgentCmdCallContent, error) {
	var c AgentCmdCallContent
	err := json.Unmarshal(raw, &c)
	return c, err
}

// DecodeAgentPatternCall reads a tool_call content blob for Grep/Glob.
func DecodeAgentPatternCall(raw json.RawMessage) (AgentPatternCallContent, error) {
	var c AgentPatternCallContent
	err := json.Unmarshal(raw, &c)
	return c, err
}

// DecodeAgentPreviewResult reads a tool_result content blob for Read/Write/Grep/Glob.
func DecodeAgentPreviewResult(raw json.RawMessage) (AgentPreviewResultContent, error) {
	var c AgentPreviewResultContent
	err := json.Unmarshal(raw, &c)
	return c, err
}

// DecodeAgentOutputResult reads a tool_result content blob for Bash.
func DecodeAgentOutputResult(raw json.RawMessage) (AgentOutputResultContent, error) {
	var c AgentOutputResultContent
	err := json.Unmarshal(raw, &c)
	return c, err
}

// EncodeMessage builds a message content blob (chat row body).
func EncodeMessage(text string) json.RawMessage {
	return mustEncode(MessageContent{Text: text})
}

// DecodeMessage reads a message content blob.
func DecodeMessage(raw json.RawMessage) (MessageContent, error) {
	var c MessageContent
	err := json.Unmarshal(raw, &c)
	return c, err
}
