package editor

import (
	"strings"
	"testing"
)

func TestHighlightLineFastPaths(t *testing.T) {
	tests := []struct {
		name   string
		input  string
		prefix string
		empty  bool
	}{
		{"empty", "", "", true},
		{"h1", "# Hello World", "\x1b[1m\x1b[97m", false},
		{"h2", "## Subheading", "\x1b[1m\x1b[4m", false},
		{"h3", "### Three", "\x1b[1m\x1b[4m", false},
		{"blockquote", "> a quote", "\x1b[90m", false},
		{"horizontal rule ---", "---", "\x1b[36m", false},
		{"horizontal rule ***", "***", "\x1b[36m", false},
		{"checked list", "- [x] done", "\x1b[32m", false},
		{"checked list capital X", "- [X] done", "\x1b[32m", false},
		{"unchecked list", "- [ ] todo", "\x1b[90m", false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := highlightLine(tt.input)
			if tt.empty {
				if result != "" {
					t.Errorf("expected empty output, got %q", result)
				}
				return
			}
			if !strings.HasPrefix(result, tt.prefix) {
				t.Errorf("highlightLine(%q)\n  got    %q\n  want prefix %q", tt.input, result, tt.prefix)
			}
			if !strings.HasSuffix(result, "\x1b[0m") {
				t.Errorf("highlightLine(%q) does not end with reset: %q", tt.input, result)
			}
		})
	}
}

func TestHighlightLineInlineTokens(t *testing.T) {
	tests := []struct {
		name     string
		input    string
		contains string
	}{
		// Mid-line (leading space) cases.
		{"bold mid-line", "word **bold** text", "\x1b[1m"},
		{"bold underscore mid-line", "word __bold__ text", "\x1b[1m"},
		{"italic mid-line", "word *italic* text", "\x1b[3m"},
		{"italic underscore mid-line", "word _italic_ text", "\x1b[3m"},
		{"strikethrough mid-line", "word ~~strike~~ text", "\x1b[9m"},
		{"link", "[text](url)", "\x1b[35;4m"},
		{"inline code", "word `code` word", "\x1b[34m"},

		// Issue 3 regression: line-start emphasis. chroma's lexer required a
		// literal whitespace char before the marker, so these were silently
		// rendered as plain text. The new tokenizer uses (^|\s) and handles
		// both positions.
		{"bold line-start", "**bold** text", "\x1b[1m"},
		{"bold underscore line-start", "__bold__ text", "\x1b[1m"},
		{"italic line-start", "*italic* text", "\x1b[3m"},
		{"italic underscore line-start", "_italic_ text", "\x1b[3m"},
		{"strikethrough line-start", "~~strike~~ text", "\x1b[9m"},

		// Whole-line emphasis (no trailing text after the closing marker).
		{"bold whole line", "**bold**", "\x1b[1m"},
		{"italic whole line", "*italic*", "\x1b[3m"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := highlightLine(tt.input)
			if !strings.Contains(result, tt.contains) {
				t.Errorf("highlightLine(%q)\n  got  %q\n  want to contain %q", tt.input, result, tt.contains)
			}
		})
	}
}

func TestHighlightLineInlineTokensNoFalsePositives(t *testing.T) {
	// Things that look emphasis-adjacent but shouldn't render styled.
	tests := []struct {
		name  string
		input string
	}{
		{"intraword asterisk", "foo*bar*baz"},   // no \W around markers
		{"empty bold", "** **"},                  // content can't be empty/space
		{"single asterisk", "alone *"},           // no closing marker
		{"unmatched bold", "**unclosed"},         // no closing marker
		{"asterisk with leading space inside", "*  bad*"}, // content starts with space
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := highlightLine(tt.input)
			stripped := stripANSI(result)
			if stripped != tt.input {
				t.Errorf("input was modified by highlight: %q -> %q", tt.input, stripped)
			}
			// Must not contain bold/italic/strike open codes.
			for _, code := range []string{"\x1b[1m", "\x1b[3m", "\x1b[9m"} {
				if strings.Contains(result, code) {
					t.Errorf("unexpected style %q applied to %q: got %q", code, tt.input, result)
				}
			}
		})
	}
}

func TestCodeSpanPrecedence(t *testing.T) {
	tests := []struct {
		name        string
		input       string
		wantCode    bool // styled code present
		wantNoEmph  bool // no bold/italic/strike styling
		description string
	}{
		{
			name:        "italic wrapping code",
			input:       "*foo `code` bar*",
			wantCode:    true,
			wantNoEmph:  true,
			description: "code wins; asterisks stay literal",
		},
		{
			name:        "bold wrapping code",
			input:       "**foo `code` bar**",
			wantCode:    true,
			wantNoEmph:  true,
			description: "code wins over strong too",
		},
		{
			name:        "code adjacent to italic",
			input:       "`code` and *italic*",
			wantCode:    true,
			wantNoEmph:  false,
			description: "non-overlapping; both styled",
		},
		{
			name:        "italic before code",
			input:       "*italic* `code`",
			wantCode:    true,
			wantNoEmph:  false,
			description: "non-overlapping; both styled",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := highlightLine(tt.input)
			hasCode := strings.Contains(result, "\x1b[34m")
			if hasCode != tt.wantCode {
				t.Errorf("%s: code styling present=%v, want %v\n  result: %q", tt.description, hasCode, tt.wantCode, result)
			}
			hasEmph := false
			for _, code := range []string{"\x1b[1m", "\x1b[3m", "\x1b[9m"} {
				if strings.Contains(result, code) {
					hasEmph = true
					break
				}
			}
			if tt.wantNoEmph && hasEmph {
				t.Errorf("%s: emphasis styling leaked into code-precedence case\n  result: %q", tt.description, result)
			}
		})
	}
}

func TestHighlightLinePlainTextNoCorruption(t *testing.T) {
	input := "plain text without formatting"
	result := highlightLine(input)

	stripped := stripANSI(result)
	if stripped != input {
		t.Errorf("plain text not preserved after highlight:\n  input: %q\n  output: %q", input, stripped)
	}
}

func TestCachedHighlightHits(t *testing.T) {
	cache := make(map[string]string)

	first := cachedHighlight("**bold** text", cache)
	second := cachedHighlight("**bold** text", cache)

	if first != second {
		t.Errorf("cache not hit: first=%q second=%q", first, second)
	}
	if len(cache) != 1 {
		t.Errorf("cache should have 1 entry, got %d", len(cache))
	}
}

func TestCachedHighlightMisses(t *testing.T) {
	cache := make(map[string]string)

	a := cachedHighlight("**bold** text", cache)
	b := cachedHighlight("*italic* text", cache)

	if a == b {
		t.Error("different content should produce different highlighted output")
	}
	if len(cache) != 2 {
		t.Errorf("cache should have 2 entries, got %d", len(cache))
	}
}

func TestCachedHighlightEmpty(t *testing.T) {
	cache := make(map[string]string)

	result := cachedHighlight("", cache)
	if result != "" {
		t.Errorf("empty input: got %q, want empty", result)
	}
	if len(cache) != 0 {
		t.Errorf("empty input should not be cached, got %d entries", len(cache))
	}
}

func TestCachedHighlightNilCache(t *testing.T) {
	result := cachedHighlight("**bold** text", nil)
	if !strings.Contains(result, "\x1b[1m") {
		t.Errorf("nil cache should still highlight: got %q", result)
	}
}

// stripANSI removes ANSI escape sequences from a string.
func stripANSI(s string) string {
	var b strings.Builder
	inEscape := false
	for i := 0; i < len(s); i++ {
		if s[i] == '\x1b' {
			inEscape = true
			continue
		}
		if inEscape {
			if (s[i] >= 'A' && s[i] <= 'Z') || (s[i] >= 'a' && s[i] <= 'z') {
				inEscape = false
			}
			continue
		}
		b.WriteByte(s[i])
	}
	return b.String()
}
