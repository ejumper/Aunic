package agent

import "github.com/ejumper/aunic/transcript"

// This file holds the derivations from the raw transcript rows that the bar's
// cells/render/height code consumes: top-level (tool_call, tool_result) pairs,
// the combined (pair + chat message) item stream, and the filter predicates.

// ── Top-level pairs ─────────────────────────────────────────────────────────

type pair struct {
	callNum       int
	tool          string
	callContent   []byte
	resultContent []byte
}

// topLevelPairs walks rows and groups each tool_call with its matching
// tool_result by ToolID. tool_results without a paired call are dropped.
//
// Memoized via tb.cache — invalidated by SetRows (the only mutator of tb.rows).
func (tb TranscriptBar) topLevelPairs() []pair {
	if tb.cache != nil && tb.cache.pairsValid {
		return tb.cache.pairs
	}
	results := map[string]int{} // ToolID → index in tb.rows
	for i, r := range tb.rows {
		if r.Type == transcript.TypeToolResult {
			results[r.ToolID] = i
		}
	}
	var out []pair
	for _, r := range tb.rows {
		if r.Type != transcript.TypeToolCall {
			continue
		}
		p := pair{
			callNum:     r.Num,
			tool:        r.Tool,
			callContent: r.Content,
		}
		if ri, ok := results[r.ToolID]; ok {
			p.resultContent = tb.rows[ri].Content
		}
		out = append(out, p)
	}
	if tb.cache != nil {
		tb.cache.pairs = out
		tb.cache.pairsValid = true
	}
	return out
}

// ── Top-level items (pairs + chat messages, in source order) ────────────────

type itemKind int

const (
	itemPair itemKind = iota
	itemMessage
)

type item struct {
	kind itemKind

	// common
	rowNum int

	// itemMessage:
	role transcript.Role
	text string

	// itemPair:
	pair pair
}

// items walks tb.rows in source order, yielding either pair items (tool_call
// rows joined with their tool_result) or message items (chat). tool_result
// rows are absorbed into their paired call; orphan tool_results are skipped.
func (tb TranscriptBar) items() []item {
	results := map[string]int{} // ToolID → index in tb.rows
	for i, r := range tb.rows {
		if r.Type == transcript.TypeToolResult {
			results[r.ToolID] = i
		}
	}
	var out []item
	for _, r := range tb.rows {
		switch r.Type {
		case transcript.TypeMessage:
			mc := decodeWarn(r.Content, "message", transcript.DecodeMessage)
			out = append(out, item{
				kind:   itemMessage,
				rowNum: r.Num,
				role:   r.Role,
				text:   mc.Text,
			})
		case transcript.TypeToolCall:
			p := pair{
				callNum:     r.Num,
				tool:        r.Tool,
				callContent: r.Content,
			}
			if ri, ok := results[r.ToolID]; ok {
				p.resultContent = tb.rows[ri].Content
			}
			out = append(out, item{
				kind:   itemPair,
				rowNum: r.Num,
				pair:   p,
			})
		}
	}
	return out
}

func (tb TranscriptBar) passesFilter(p pair) bool {
	switch tb.filter {
	case FilterAll:
		return true
	case FilterChat:
		return false // chat rows aren't pairs
	case FilterSearch:
		return p.tool == transcript.ToolWebSearch || p.tool == transcript.ToolWebFetch
	case FilterTools:
		return p.tool != transcript.ToolWebSearch && p.tool != transcript.ToolWebFetch
	}
	return true
}

func (tb TranscriptBar) passesFilterItem(it item) bool {
	switch tb.filter {
	case FilterAll:
		return true
	case FilterChat:
		return it.kind == itemMessage
	case FilterSearch:
		return it.kind == itemPair && (it.pair.tool == transcript.ToolWebSearch || it.pair.tool == transcript.ToolWebFetch)
	case FilterTools:
		return it.kind == itemPair && it.pair.tool != transcript.ToolWebSearch && it.pair.tool != transcript.ToolWebFetch
	}
	return true
}
