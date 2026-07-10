package editor

import (
	"bytes"
	"regexp"
	"sort"
	"strings"

	"github.com/alecthomas/chroma/v2"
	"github.com/alecthomas/chroma/v2/formatters"
	"github.com/alecthomas/chroma/v2/lexers"
	"github.com/alecthomas/chroma/v2/styles"
)

var (
	hrRe        = regexp.MustCompile(`^(\*{3,}|-{3,}|_{3,})\s*$`)
	checkedRe   = regexp.MustCompile(`(?i)^\s*[-*+] \[[xX]\]`)
	uncheckedRe = regexp.MustCompile(`^\s*[-*+] \[ \]`)
)

func isH1(s string) bool {
	return strings.HasPrefix(s, "# ")
}

func isH2(s string) bool {
	return strings.HasPrefix(s, "## ") && !strings.HasPrefix(s, "###")
}

func isH3toH6(s string) bool {
	if !strings.HasPrefix(s, "##") {
		return false
	}
	n := 0
	for n < len(s) && s[n] == '#' {
		n++
	}
	return n >= 2 && n <= 6 && n < len(s) && s[n] == ' '
}

func isBlockquote(s string) bool {
	return strings.HasPrefix(s, ">")
}

func isHorizontalRule(s string) bool {
	return hrRe.MatchString(strings.TrimSpace(s))
}

func isCheckedList(s string) bool {
	return checkedRe.MatchString(s)
}

func isUncheckedList(s string) bool {
	return uncheckedRe.MatchString(s)
}

// inlineRule is an inline markdown construct (bold, italic, code, link, …).
// A rule's regex matches a single span of the construct in line content; the
// open/close ANSI pair wraps the styled portion of the match.
//
// `prefixed` rules use `(^|\s)` as their first group so they match either at
// line start OR after whitespace, and capture the styled body as group 2.
// Rules that match unambiguously without a prefix (code spans, links) set
// this to false and style the whole match.
//
// `claims` marks the rule as taking precedence over overlapping matches from
// non-claiming rules. Used for code spans, which per CommonMark bind tighter
// than emphasis — `*foo \x60code\x60 bar*` should render as plain asterisks
// wrapping a styled code span, not as italic with backticks visible inside.
type inlineRule struct {
	re       *regexp.Regexp
	open     string
	close    string
	prefixed bool
	claims   bool
}

// inlineRules are scanned for every line that doesn't hit a block-level
// fast-path in HighlightLine. Order matters as a tiebreaker when two rules
// match at the same start position: strong precedes italic so `**` is read
// as bold, not as two adjacent `*` italic markers.
//
// RE2 (Go's regexp engine) supports neither lookahead nor backreferences:
//   - The trailing context (`\W` or end-of-line) is captured as an explicit
//     group instead of using `(?=...)`. The consumer advances past the body
//     only, leaving the trailing char available as plain text for the next
//     match's prefix or for the post-loop tail emit.
//   - `*..*` / `_.._` (and the strong equivalents) need separate patterns
//     rather than a `\1`/`\2` backreference to enforce matching markers.
var inlineRules = []inlineRule{
	{regexp.MustCompile(`(^|\s)(\*\*[^*\s].*?\*\*)(\W|$)`), "\x1b[1m", "\x1b[22m", true, false},
	{regexp.MustCompile(`(^|\s)(__[^_\s].*?__)(\W|$)`), "\x1b[1m", "\x1b[22m", true, false},
	{regexp.MustCompile(`(^|\s)(\*[^*\s][^*]*\*)(\W|$)`), "\x1b[3m", "\x1b[23m", true, false},
	{regexp.MustCompile(`(^|\s)(_[^_\s][^_]*_)(\W|$)`), "\x1b[3m", "\x1b[23m", true, false},
	{regexp.MustCompile(`(^|\s)(~~[^~\s][^~]*~~)(\W|$)`), "\x1b[9m", "\x1b[29m", true, false},
	{regexp.MustCompile("`[^`]+`"), "\x1b[34m", "\x1b[39m", false, true},
	{regexp.MustCompile(`!?\[[^\]]+\]\([^)]+\)`), "\x1b[35;4m", ansiReset, false, false},
}

// inlineMatch is one regex hit inside a line. matchStart is the absolute
// position the match began at (used for sort and overlap checks). bodyStart
// and bodyEnd bracket the portion that gets ANSI-styled; for prefixed rules
// that's the marker-plus-content captured as group 2, for non-prefixed rules
// it's the whole match. A future live-renderer can reuse this representation
// directly — it carries position info, not styling decisions.
type inlineMatch struct {
	ruleIdx    int
	matchStart int
	bodyStart  int
	bodyEnd    int
}

// findInlineMatches scans content with every inline rule and returns the
// matches in left-to-right order, ties broken by rule priority. Matches from
// non-claiming rules that overlap a claiming-rule match are dropped — that's
// how code spans get to bind tighter than emphasis. The remaining matches may
// still overlap each other; styleInline drops later overlaps as they appear.
func findInlineMatches(content string) []inlineMatch {
	var matches []inlineMatch
	for i, rule := range inlineRules {
		for _, idx := range rule.re.FindAllStringSubmatchIndex(content, -1) {
			m := inlineMatch{ruleIdx: i, matchStart: idx[0]}
			if rule.prefixed {
				// idx layout: [whole, group1(prefix), group2(body), group3(trailing)]
				m.bodyStart = idx[4]
				m.bodyEnd = idx[5]
			} else {
				m.bodyStart = idx[0]
				m.bodyEnd = idx[1]
			}
			matches = append(matches, m)
		}
	}

	// Drop non-claiming matches that overlap a claiming match. A non-claiming
	// match m overlaps a claim c iff their byte ranges intersect at all —
	// either m starts inside c, ends inside c, or fully contains it.
	var claims []inlineMatch
	for _, m := range matches {
		if inlineRules[m.ruleIdx].claims {
			claims = append(claims, m)
		}
	}
	if len(claims) > 0 {
		filtered := make([]inlineMatch, 0, len(matches))
		for _, m := range matches {
			if !inlineRules[m.ruleIdx].claims {
				overlaps := false
				for _, c := range claims {
					if m.matchStart < c.bodyEnd && m.bodyEnd > c.matchStart {
						overlaps = true
						break
					}
				}
				if overlaps {
					continue
				}
			}
			filtered = append(filtered, m)
		}
		matches = filtered
	}

	sort.Slice(matches, func(i, j int) bool {
		if matches[i].matchStart != matches[j].matchStart {
			return matches[i].matchStart < matches[j].matchStart
		}
		return matches[i].ruleIdx < matches[j].ruleIdx
	})
	return matches
}

// styleInline applies ANSI styling to markdown inline constructs in content.
// content must not contain newlines — it's a single logical line with the
// indent already stripped by extractIndent.
//
// Detection (findInlineMatches) is intentionally kept separate from emission,
// so a future live-renderer can reuse the matcher with a different output
// strategy.
func styleInline(content string) string {
	if content == "" {
		return ""
	}
	matches := findInlineMatches(content)
	var b strings.Builder
	pos := 0
	for _, m := range matches {
		if m.matchStart < pos {
			// Already inside an earlier-emitted span.
			continue
		}
		b.WriteString(content[pos:m.matchStart])
		if m.bodyStart > m.matchStart {
			// Prefix char (the captured (^|\s)) gets emitted plain so the
			// styling starts at the marker, not at the preceding whitespace.
			b.WriteString(content[m.matchStart:m.bodyStart])
		}
		rule := inlineRules[m.ruleIdx]
		b.WriteString(rule.open)
		b.WriteString(content[m.bodyStart:m.bodyEnd])
		b.WriteString(rule.close)
		pos = m.bodyEnd
	}
	b.WriteString(content[pos:])
	return b.String()
}

// HighlightLine applies markdown syntax highlighting to content (indent
// already stripped). Returns ANSI 0-15 escape codes that respect the user's
// terminal color scheme.
func HighlightLine(content string) string {
	switch {
	case content == "":
		return ""
	case isH1(content), isH2(content), isH3toH6(content):
		return "\x1b[1;34;4m" + content + ansiReset
	case isHorizontalRule(content):
		return "\x1b[36m" + content + ansiReset
	case isCheckedList(content):
		return "\x1b[32m" + content + ansiReset
	case isUncheckedList(content):
		return ansiGray + content + ansiReset
	case isBlockquote(content):
		return ansiGray + content + ansiReset
	}
	return styleInline(content)
}

func cachedHighlight(content string, cache map[string]string) string {
	if content == "" {
		return ""
	}
	if hl, ok := cache[content]; ok {
		return hl
	}
	hl := HighlightLine(content)
	if cache != nil {
		cache[content] = hl
	}
	return hl
}

// chromaStyle is the Chroma style used for code block syntax highlighting.
var chromaStyle = styles.Get("modus-vivendi")

// highlightCodeBlock runs Chroma syntax highlighting on a multi-line code body
// using the terminal16 formatter. Returns the output split into per-line
// strings (without trailing newlines). Falls back to plain text on error.
func highlightCodeBlock(lang, body string) []string {
	var lexer chroma.Lexer
	if lang != "" {
		lexer = lexers.Get(lang)
	}
	if lexer == nil {
		lexer = lexers.Analyse(body)
	}
	if lexer == nil {
		lexer = lexers.Fallback
	}
	lexer = chroma.Coalesce(lexer)

	iterator, err := lexer.Tokenise(nil, body)
	if err != nil {
		return strings.Split(body, "\n")
	}

	var buf bytes.Buffer
	if err := formatters.TTY16m.Format(&buf, chromaStyle, iterator); err != nil {
		return strings.Split(body, "\n")
	}

	// Chroma emits a trailing newline; strip it before splitting.
	out := strings.TrimSuffix(buf.String(), "\n")
	return strings.Split(out, "\n")
}

// highlightWholeFile runs Chroma syntax highlighting on the entire file,
// detecting the lexer from the file extension. Returns a map[lineIdx]highlighted
// for every line, or nil if Chroma has no lexer for the extension (plain text).
func highlightWholeFile(filepath string, lines []string, cache map[string]string) map[int]string {
	lexer := lexers.Match(filepath)
	if lexer == nil {
		return nil
	}
	body := strings.Join(lines, "\n")
	cacheKey := "\x00whole\x00" + body

	var hlLines []string
	if cached, ok := cache[cacheKey]; ok {
		hlLines = strings.Split(cached, "\n")
	} else {
		lexer = chroma.Coalesce(lexer)
		iterator, err := lexer.Tokenise(nil, body)
		if err != nil {
			return nil
		}
		var buf bytes.Buffer
		if err := formatters.TTY16m.Format(&buf, chromaStyle, iterator); err != nil {
			return nil
		}
		out := strings.TrimSuffix(buf.String(), "\n")
		hlLines = strings.Split(out, "\n")
		if cache != nil {
			cache[cacheKey] = strings.Join(hlLines, "\n")
		}
	}

	result := make(map[int]string, len(lines))
	for i, hl := range hlLines {
		if i < len(lines) {
			result[i] = hl
		}
	}
	return result
}

// ParseCodeBlockRanges scans lines for fenced code blocks (``` delimited) and
// returns a map[lineIndex]highlightedContent for every line that belongs to a
// code block. Fence lines themselves are mapped to a dimmed rendition of their
// raw content. Body lines are mapped to Chroma-highlighted content.
//
// Lines absent from the returned map should be highlighted with the normal
// per-line markdown rules. cache may be nil (disables Chroma result caching).
func ParseCodeBlockRanges(lines []string, cache map[string]string) map[int]string {
	result := make(map[int]string)
	inBlock := false
	var lang string
	var bodyStart int
	var bodyLines []string

	flushBlock := func(closeIdx int) {
		cacheKey := "\x00" + lang + "\x00" + strings.Join(bodyLines, "\n")
		var hlLines []string
		if cached, ok := cache[cacheKey]; ok {
			hlLines = strings.Split(cached, "\n")
		} else {
			hlLines = highlightCodeBlock(lang, strings.Join(bodyLines, "\n"))
			if cache != nil {
				cache[cacheKey] = strings.Join(hlLines, "\n")
			}
		}
		for k, hl := range hlLines {
			lineIdx := bodyStart + k
			if lineIdx < closeIdx {
				result[lineIdx] = hl
			}
		}
	}

	for i, line := range lines {
		trimmed := strings.TrimLeft(line, " \t")
		if !inBlock {
			if strings.HasPrefix(trimmed, "```") {
				inBlock = true
				lang = strings.TrimSpace(trimmed[3:])
				bodyStart = i + 1
				bodyLines = nil
				result[i] = ansiGray + line + ansiReset
			}
			continue
		}
		// Inside a block.
		if strings.TrimSpace(trimmed) == "```" {
			flushBlock(i)
			result[i] = ansiGray + line + ansiReset
			inBlock = false
			lang = ""
			bodyLines = nil
		} else {
			bodyLines = append(bodyLines, line)
		}
	}
	// Unclosed fence: highlight whatever body we have.
	if inBlock && len(bodyLines) > 0 {
		flushBlock(len(lines))
	}
	return result
}
