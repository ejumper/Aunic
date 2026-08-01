package main

import (
	"testing"

	pi "github.com/ejumper/aunic/harness/pi"
	"github.com/ejumper/aunic/transcript"
)

// These tests pin Aunic's parsing to Pi's actual tool-argument schema
// (path / edits[].oldText / edits[].newText), which drifted from the
// Claude-style names Aunic originally used (file_path / old_string /
// new_string) and left transcript rows blank.

func TestPiEditTexts_EditsArray(t *testing.T) {
	args := map[string]any{
		"path": "/notes/x.md",
		"edits": []any{
			map[string]any{"oldText": "a", "newText": "b"},
			map[string]any{"oldText": "c", "newText": "d"},
		},
	}
	oldText, newText := piEditTexts(args)
	if oldText != "a\nc" || newText != "b\nd" {
		t.Fatalf("got old=%q new=%q, want old=%q new=%q", oldText, newText, "a\nc", "b\nd")
	}
}

func TestPiEditTexts_LegacyForm(t *testing.T) {
	oldText, newText := piEditTexts(map[string]any{"oldText": "x", "newText": "y"})
	if oldText != "x" || newText != "y" {
		t.Fatalf("got old=%q new=%q, want x/y", oldText, newText)
	}
}

func TestAppendToolCallRow_PiEditArgs(t *testing.T) {
	m := appModel{piActiveToolRows: map[string]int{}}
	tc := pi.ToolCallObj{
		ID:   "call_1",
		Name: "edit",
		Arguments: map[string]any{
			"path":  "/notes/x.md",
			"edits": []any{map[string]any{"oldText": "a", "newText": "b"}},
		},
	}
	m = m.appendToolCallRow(tc)
	if len(m.transcriptRows) != 1 {
		t.Fatalf("want 1 row, got %d", len(m.transcriptRows))
	}
	c, err := transcript.DecodeAgentFileCall(m.transcriptRows[0].Content)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if c.FilePath != "/notes/x.md" {
		t.Errorf("FilePath = %q, want /notes/x.md (path key not read)", c.FilePath)
	}
	if c.OldString != "a" || c.NewString != "b" {
		t.Errorf("old/new = %q/%q, want a/b (edits array not read)", c.OldString, c.NewString)
	}
}

func TestAppendToolCallRow_PiReadArgs(t *testing.T) {
	m := appModel{piActiveToolRows: map[string]int{}}
	tc := pi.ToolCallObj{ID: "c", Name: "read", Arguments: map[string]any{"path": "/notes/y.md"}}
	m = m.appendToolCallRow(tc)
	c, err := transcript.DecodeAgentFileCall(m.transcriptRows[0].Content)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if c.FilePath != "/notes/y.md" {
		t.Errorf("read FilePath = %q, want /notes/y.md", c.FilePath)
	}
}

func TestAppendToolCallRow_PiWriteArgs(t *testing.T) {
	m := appModel{piActiveToolRows: map[string]int{}}
	tc := pi.ToolCallObj{ID: "c", Name: "write", Arguments: map[string]any{
		"path": "/notes/z.md", "content": "hello",
	}}
	m = m.appendToolCallRow(tc)
	c, err := transcript.DecodeAgentFileCall(m.transcriptRows[0].Content)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if c.FilePath != "/notes/z.md" {
		t.Errorf("write FilePath = %q, want /notes/z.md", c.FilePath)
	}
}
