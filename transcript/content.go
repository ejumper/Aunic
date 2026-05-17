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
