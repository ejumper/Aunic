package transcript

import (
	"strings"
	"testing"
)

func TestSplit_NoDelimiter(t *testing.T) {
	in := "# Just a note\n\nsome body\n"
	note, tx := Split(in)
	if note != in {
		t.Errorf("note: got %q, want %q", note, in)
	}
	if tx != "" {
		t.Errorf("transcript: got %q, want empty", tx)
	}
}

func TestSplit_WithDelimiter(t *testing.T) {
	in := "# Note\n\nbody line\n***\n# Transcript\n\n| # | role | type | tool | tool_id | content |\n"
	note, tx := Split(in)
	if note != "# Note\n\nbody line" {
		t.Errorf("note: got %q", note)
	}
	if !strings.HasPrefix(tx, "\n| # | role") {
		t.Errorf("transcript prefix unexpected: %q", tx)
	}
}

func TestJoin_NoRows(t *testing.T) {
	got := Join("# hi", nil, "")
	if got != "# hi" {
		t.Errorf("got %q, want %q", got, "# hi")
	}
}

func TestJoin_WithRows(t *testing.T) {
	rows := []Row{
		{Num: 1, Role: RoleAssistant, Type: TypeToolCall, Tool: ToolWebSearch, ToolID: "call_1", Content: EncodeSearchCall("hello world")},
	}
	got := Join("body", rows, "")
	if !strings.Contains(got, "body\n***\n# Transcript\n") {
		t.Errorf("missing delimiter: %q", got)
	}
	if !strings.Contains(got, `{"query":"hello world"}`) {
		t.Errorf("missing content: %q", got)
	}
}

func TestRoundTrip(t *testing.T) {
	rows := []Row{
		{Num: 1, Role: RoleAssistant, Type: TypeToolCall, Tool: ToolWebSearch, ToolID: "call_1", Content: EncodeSearchCall("rust lifetimes")},
		{Num: 2, Role: RoleTool, Type: TypeToolResult, Tool: ToolWebSearch, ToolID: "call_1", Content: EncodeSearchResult([]SearchResultHit{
			{Title: "Title A", URL: "https://a.example/x", Domain: "a.example", Snippet: "Abstract A"},
			{Title: "Title with pipe | inside", URL: "https://b.example/y", Domain: "b.example", Snippet: "Snip B"},
		})},
		{Num: 3, Role: RoleAssistant, Type: TypeToolCall, Tool: ToolWebFetch, ToolID: "call_2", Content: EncodeFetchCall("https://a.example/x")},
		{Num: 4, Role: RoleTool, Type: TypeToolResult, Tool: ToolWebFetch, ToolID: "call_2", Content: EncodeFetchResult("Title A", "https://a.example/x", "first 300 chars…")},
	}
	full := Join("note body\n", rows, "")
	_, tx := Split(full)
	got, err := Parse(tx)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(got) != len(rows) {
		t.Fatalf("row count: got %d, want %d", len(got), len(rows))
	}
	for i, r := range got {
		if r.Num != rows[i].Num || r.Role != rows[i].Role || r.Type != rows[i].Type ||
			r.Tool != rows[i].Tool || r.ToolID != rows[i].ToolID {
			t.Errorf("row %d header mismatch: got %+v", i, r)
		}
		if string(r.Content) != string(rows[i].Content) {
			t.Errorf("row %d content: got %s, want %s", i, r.Content, rows[i].Content)
		}
	}
}

func TestSnippet_Truncates(t *testing.T) {
	long := strings.Repeat("abcdefghij ", 100) // 1100 chars
	got := Snippet(long)
	if len(got) > snippetMaxChars+5 {
		t.Errorf("snippet too long: %d", len(got))
	}
	if !strings.HasSuffix(got, "…") {
		t.Errorf("expected ellipsis: %q", got[len(got)-10:])
	}
}

func TestSnippet_SkipsBlankLines(t *testing.T) {
	in := "\n\n\nFirst real content line.\n\nSecond line."
	got := Snippet(in)
	if !strings.HasPrefix(got, "First real content line.") {
		t.Errorf("got %q", got)
	}
}
