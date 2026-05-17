package editor

import (
	"strings"
	"testing"
)

func TestExtractIndent(t *testing.T) {
	tests := []struct {
		input       string
		wantIndent  string
		wantContent string
	}{
		{"no indent", "", "no indent"},
		{"\tone tab", "\t", "one tab"},
		{"\t\ttwo tabs", "\t\t", "two tabs"},
		{"    four spaces", "    ", "four spaces"},
		{"\t space after tab", "\t ", "space after tab"},
		{"", "", ""},
		{"   ", "   ", ""},
	}

	for _, tt := range tests {
		indent, content := extractIndent(tt.input)
		if indent != tt.wantIndent || content != tt.wantContent {
			t.Errorf("extractIndent(%q) = (%q, %q), want (%q, %q)",
				tt.input, indent, content, tt.wantIndent, tt.wantContent)
		}
	}
}

func TestWrapWithIndent(t *testing.T) {
	// Test that wrapping preserves indent on continuation lines
	indent := "\t\t"
	content := "This is a very long line that should wrap to multiple lines in the output"
	limit := 40

	result := wrapWithIndent(content, indent, limit)
	lines := strings.Split(result, "\n")

	if len(lines) < 2 {
		t.Fatalf("expected multiple wrapped lines, got %d: %q", len(lines), result)
	}

	for _, line := range lines {
		if !strings.HasPrefix(line, indent) {
			t.Errorf("wrapped line missing indent prefix %q: %q", indent, line)
		}
	}
}

func TestWrapWithIndentShortContent(t *testing.T) {
	// Short content should stay on one line
	indent := "\t"
	content := "short"
	limit := 40

	result := wrapWithIndent(content, indent, limit)
	lines := strings.Split(result, "\n")

	if len(lines) != 1 {
		t.Errorf("expected 1 line, got %d: %q", len(lines), result)
	}
	if lines[0] != indent+content {
		t.Errorf("expected %q, got %q", indent+content, lines[0])
	}
}

func TestGutterWidth(t *testing.T) {
	tests := []struct {
		lineCount int
		want      int
	}{
		{1, 2},
		{9, 2},
		{10, 3},
		{99, 3},
		{100, 4},
		{999, 4},
		{1000, 5},
	}

	for _, tt := range tests {
		got := gutterWidth(tt.lineCount)
		if got != tt.want {
			t.Errorf("gutterWidth(%d) = %d, want %d", tt.lineCount, got, tt.want)
		}
	}
}

func TestCountLines(t *testing.T) {
	tests := []struct {
		input string
		want  int
	}{
		{"", 1},
		{"one line", 1},
		{"two\nlines", 2},
		{"three\nseparate\nlines", 3},
	}

	for _, tt := range tests {
		got := countLines(tt.input)
		if got != tt.want {
			t.Errorf("countLines(%q) = %d, want %d", tt.input, got, tt.want)
		}
	}
}

func TestWordWrapPreservesTrailingSpaces(t *testing.T) {
	tests := []struct {
		name  string
		input string
		width int
		want  string
	}{
		{"single trailing space", "foo ", 80, "foo "},
		{"two trailing spaces", "foo  ", 80, "foo  "},
		{"only spaces", "    ", 80, "    "},
		{"trailing after wrap", "hello world ", 7, "hello \nworld "},
	}
	for _, tt := range tests {
		got := wordWrap(tt.input, tt.width)
		if got != tt.want {
			t.Errorf("%s: wordWrap(%q, %d) = %q, want %q", tt.name, tt.input, tt.width, got, tt.want)
		}
	}
}

func TestWordWrapBreaksAtWordBoundary(t *testing.T) {
	// Breaks at the last space that fits within width; the space itself stays
	// at the end of the previous line so continuation lines start cleanly with
	// the next word (matters when wrapWithIndent prepends an indent).
	got := wordWrap("hello world foo bar", 11)
	want := "hello \nworld foo \nbar"
	if got != want {
		t.Errorf("wordWrap = %q, want %q", got, want)
	}
}

func TestWordWrapHardBreaksLongWord(t *testing.T) {
	got := wordWrap("abcdefghij", 5)
	want := "abcde\nfghij"
	if got != want {
		t.Errorf("wordWrap = %q, want %q", got, want)
	}
}

func TestWordWrapPreservesAnsiAndWidth(t *testing.T) {
	in := "\x1b[1mhello\x1b[22m world"
	got := wordWrap(in, 7)
	want := "\x1b[1mhello\x1b[22m \nworld"
	if got != want {
		t.Errorf("wordWrap = %q, want %q", got, want)
	}
}

func TestWordWrapEdgeCases(t *testing.T) {
	tests := []struct {
		name  string
		input string
		width int
		want  string
	}{
		{"empty", "", 10, ""},
		{"width zero", "anything goes", 0, "anything goes"},
		{"width negative", "anything goes", -5, "anything goes"},
		{"fits exactly", "hello", 5, "hello"},
		{"single space input", " ", 80, " "},
		{"tab width", "ab\tcd", 80, "ab\tcd"},
	}
	for _, tt := range tests {
		got := wordWrap(tt.input, tt.width)
		if got != tt.want {
			t.Errorf("%s: wordWrap(%q, %d) = %q, want %q", tt.name, tt.input, tt.width, got, tt.want)
		}
	}
}

func TestWrapWithIndentSoftwrapsAtIndentLevel(t *testing.T) {
	// User's stated requirement: continuation lines start at the same indent
	// level as the original line, with no extra leading space from the
	// between-word break.
	indent := "    "
	content := "indented line that wraps on words"
	got := wrapWithIndent(content, indent, 22) // indent(4) + content limit(18)
	lines := strings.Split(got, "\n")
	for i, ln := range lines {
		if !strings.HasPrefix(ln, indent) {
			t.Fatalf("line %d missing indent: %q", i, ln)
		}
		// No DOUBLED indent — the between-word space must not become leading
		// whitespace on the continuation line.
		if strings.HasPrefix(ln, indent+" ") {
			t.Errorf("line %d has extra leading space (indent doubled): %q", i, ln)
		}
	}
}

func TestWrapWithIndentPreservesTrailingSpaces(t *testing.T) {
	// Regression for Issue 1: muesli/reflow/wordwrap stripped trailing spaces,
	// causing the cursor to fall behind on space input.
	got := wrapWithIndent("foo  ", "", 80)
	if got != "foo  " {
		t.Errorf("wrapWithIndent(%q, %q, 80) = %q, want %q", "foo  ", "", got, "foo  ")
	}
}

func TestVisualWidth(t *testing.T) {
	tests := []struct {
		input string
		want  int
	}{
		{"", 0},
		{"hello", 5},
		{"\t", tabWidth},
		{"\t\t", tabWidth * 2},
		{"\ttext", tabWidth + 4},
	}

	for _, tt := range tests {
		got := visualWidth(tt.input)
		if got != tt.want {
			t.Errorf("visualWidth(%q) = %d, want %d", tt.input, got, tt.want)
		}
	}
}
