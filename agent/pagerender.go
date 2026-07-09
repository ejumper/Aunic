package agent

import (
	"regexp"
	"strings"
	"unicode"

	"github.com/ejumper/aunic/editor"
	"github.com/mattn/go-runewidth"
)

// pageLine is one rendered row of the web page pager.
//
// Most non-table source lines map to 1..N pageLines (N>1 when word-wrapped).
// All wrapped rows from the same source line share the same srcLine index;
// source holds the entire original markdown line so Phase 2 copy can grab the
// raw text without needing to reconstruct wrap-point byte offsets.
//
// Table source lines collapse into a different shape: a 4-line markdown table
// (header + separator + 2 data rows) produces 4 pageLines (header + box-drawn
// separator + 2 data rows). All carry inTable=true and the same
// tableStart/tableEnd range so Phase 2 can copy the full original pipe-syntax
// block as a unit.
type pageLine struct {
	display    string
	source     string
	srcLine    int
	inTable    bool
	tableStart int
	tableEnd   int
	// linkSpans lists rendered markdown link regions on this line, in display
	// order. startCol/endCol are rune offsets into the stripped (no-ANSI)
	// display. Table-cell links are NOT tracked (deferred).
	linkSpans []linkSpan
}

// tableSepRe matches a markdown table separator row. Examples:
//   |---|---|
//   | :--- | ---: | :---: |
//   ---|---
var tableSepRe = regexp.MustCompile(`^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$`)

// Link rendering uses two sentinel characters (Start-of-Heading and
// Start-of-Text in ASCII) instead of injecting ANSI codes directly:
//
//   1. preprocessLinks: scans the source with a real parser (handles balanced
//      parens in URLs, empty alt text, nested image-in-link markup) and wraps
//      the visible portion of each link in linkOpenSentinel / linkCloseSentinel.
//   2. editor.HighlightLine then sees only sentinels — no `[` chars to confuse
//      its own link regex, no `\x1b` chars to bait other inline rules.
//   3. wordWrap passes sentinels through untouched (RuneWidth treats control
//      chars as width 0).
//   4. finalizeLinkSentinels translates sentinels to ANSI as a final pass,
//      tracking depth across the soft-wrapped lines so a link that crosses a
//      wrap point is closed on the left line and reopened on the right.
//
// The close uses the targeted reset `\x1b[39m\x1b[24m` (fg default + underline
// off) rather than a full reset, so an ambient header background or bold
// attribute survives a link embedded inside it.
const (
	linkOpenSentinel  = ''
	linkCloseSentinel = ''
	// URL metadata sits between linkURLStartSentinel and linkURLEndSentinel
	// just inside the open sentinel:
	//   linkOpenSentinel + linkURLStartSentinel + <URL> + linkURLEndSentinel + text + linkCloseSentinel
	// wordWrap treats this metadata as zero-width. finalizeLinkSentinels
	// strips the URL out and records it as the link's span URL.
	linkURLStartSentinel = ''
	linkURLEndSentinel   = ''
	linkAnsiOpen      = "\x1b[35;4m"
	linkAnsiClose     = "\x1b[39m\x1b[24m"
)

// linkSpan locates a rendered link within a pageLine's display: rune offsets
// in the stripped (no-ANSI) text and the URL to navigate on Enter.
type linkSpan struct {
	startCol int // rune offset, inclusive
	endCol   int // rune offset, exclusive
	url      string
}

// preprocessLinks walks s and replaces every `[text](url)` and `![alt](url)`
// with `text` (empty-text links are removed entirely). Nested
// links are handled by recursing on the captured text.
func preprocessLinks(s string) string {
	var b strings.Builder
	i := 0
	for i < len(s) {
		c := s[i]
		if c == '[' || (c == '!' && i+1 < len(s) && s[i+1] == '[') {
			if end, text, url, ok := parseLink(s, i); ok {
				if processed := preprocessLinks(text); processed != "" {
					b.WriteRune(linkOpenSentinel)
					b.WriteRune(linkURLStartSentinel)
					b.WriteString(url)
					b.WriteRune(linkURLEndSentinel)
					b.WriteString(processed)
					b.WriteRune(linkCloseSentinel)
				}
				i = end
				continue
			}
		}
		b.WriteByte(c)
		i++
	}
	return b.String()
}

// parseLink tries to parse a markdown link at s[i]. Returns the byte index
// just past the closing `)`, the text inside `[]`, the URL between `()`, and
// whether parsing succeeded. Handles `![alt](url)` images and balanced parens
// inside the URL. Nested `[...](...)` inside the text is skipped past so the
// outer ] is found at the correct depth.
func parseLink(s string, i int) (int, string, string, bool) {
	n := len(s)
	if i < n && s[i] == '!' {
		i++
	}
	if i >= n || s[i] != '[' {
		return 0, "", "", false
	}
	i++ // skip [
	textStart := i
	for i < n && s[i] != ']' && s[i] != '\n' {
		if s[i] == '[' || (s[i] == '!' && i+1 < n && s[i+1] == '[') {
			if nend, _, _, nok := parseLink(s, i); nok {
				i = nend
				continue
			}
		}
		i++
	}
	if i >= n || s[i] != ']' {
		return 0, "", "", false
	}
	text := s[textStart:i]
	i++ // skip ]
	if i >= n || s[i] != '(' {
		return 0, "", "", false
	}
	i++ // skip (
	urlStart := i
	depth := 1
	for i < n {
		c := s[i]
		i++
		switch c {
		case '(':
			depth++
		case ')':
			depth--
			if depth == 0 {
				return i, text, s[urlStart : i-1], true
			}
		case '\n':
			return 0, "", "", false
		}
	}
	return 0, "", "", false
}

// finalizeLinkSentinels converts sentinel pairs to ANSI styling, closing and
// reopening the styling across soft-wrap line breaks. depth is tracked across
// lines so a nested or wrap-crossing link doesn't leak its styling onto the
// box's right border.
//
// Also extracts the URL metadata embedded between linkURLStartSentinel /
// linkURLEndSentinel just inside each linkOpenSentinel, and returns per-output-
// line link spans (rune offsets in stripped display + URL). Nested links use
// the outermost URL for the entire span.
func finalizeLinkSentinels(s string) (string, [][]linkSpan) {
	lines := strings.Split(s, "\n")
	spans := make([][]linkSpan, len(lines))
	if !strings.ContainsRune(s, linkOpenSentinel) && !strings.ContainsRune(s, linkCloseSentinel) {
		return s, spans
	}
	depth := 0
	var urlStack []string
	for li, line := range lines {
		var b strings.Builder
		col := 0
		spanStart := -1
		spanURL := ""
		if depth > 0 {
			b.WriteString(linkAnsiOpen)
			spanStart = 0
			if len(urlStack) > 0 {
				spanURL = urlStack[0]
			}
		}
		runes := []rune(line)
		for i := 0; i < len(runes); i++ {
			r := runes[i]
			switch r {
			case linkOpenSentinel:
				url := ""
				if i+1 < len(runes) && runes[i+1] == linkURLStartSentinel {
					j := i + 2
					for j < len(runes) && runes[j] != linkURLEndSentinel {
						j++
					}
					url = string(runes[i+2 : j])
					if j < len(runes) {
						i = j // loop's i++ skips past linkURLEndSentinel
					} else {
						i = j - 1
					}
				}
				if depth == 0 {
					b.WriteString(linkAnsiOpen)
					spanStart = col
					spanURL = url
				}
				urlStack = append(urlStack, url)
				depth++
			case linkCloseSentinel:
				if depth > 0 {
					depth--
					if len(urlStack) > 0 {
						urlStack = urlStack[:len(urlStack)-1]
					}
					if depth == 0 {
						b.WriteString(linkAnsiClose)
						if spanStart >= 0 {
							spans[li] = append(spans[li], linkSpan{startCol: spanStart, endCol: col, url: spanURL})
							spanStart = -1
							spanURL = ""
						}
					}
				}
			case linkURLStartSentinel, linkURLEndSentinel:
				// stray sentinels — drop
			default:
				b.WriteRune(r)
				col++
			}
		}
		if depth > 0 {
			b.WriteString(linkAnsiClose)
			if spanStart >= 0 {
				spans[li] = append(spans[li], linkSpan{startCol: spanStart, endCol: col, url: spanURL})
			}
		}
		lines[li] = b.String()
	}
	return strings.Join(lines, "\n"), spans
}

// renderMarkdownPage splits markdown into source lines, detects table blocks,
// and renders each segment to pageLines with ANSI styling via
// editor.HighlightLine for non-table lines and a custom column-aligned
// renderer for tables. Lines inside fenced code blocks use Chroma highlighting
// instead of HighlightLine, so `# comment` lines are not styled as H1 headings.
func renderMarkdownPage(markdown string, innerWidth int) []pageLine {
	if innerWidth < 1 {
		innerWidth = 1
	}
	srcLines := strings.Split(markdown, "\n")

	// Pre-scan for fenced code blocks. Lines in the map bypass renderTextLine
	// (which applies markdown heading/emphasis rules that are incorrect inside
	// code blocks — e.g. `# comment` would be styled as an H1).
	codeLines := editor.ParseCodeBlockRanges(srcLines, nil)

	var out []pageLine
	i := 0
	for i < len(srcLines) {
		if tStart, tEnd := detectTableAt(srcLines, i); tStart >= 0 {
			out = append(out, renderTable(srcLines[tStart:tEnd], tStart, tEnd, innerWidth)...)
			i = tEnd
			continue
		}
		if hl, ok := codeLines[i]; ok {
			out = append(out, pageLine{
				display: padTo(hl, innerWidth),
				source:  srcLines[i],
				srcLine: i,
			})
			i++
			continue
		}
		out = append(out, renderTextLine(srcLines[i], i, innerWidth)...)
		i++
	}
	return out
}

// detectTableAt checks whether a markdown table begins at srcLines[i]. Returns
// (start, end) where end is exclusive. Returns (-1, -1) if no table here.
func detectTableAt(srcLines []string, i int) (start, end int) {
	if i+1 >= len(srcLines) {
		return -1, -1
	}
	if !strings.Contains(srcLines[i], "|") {
		return -1, -1
	}
	if !tableSepRe.MatchString(srcLines[i+1]) {
		return -1, -1
	}
	end = i + 2
	for end < len(srcLines) && strings.Contains(srcLines[end], "|") && strings.TrimSpace(srcLines[end]) != "" {
		end++
	}
	return i, end
}

// renderTable parses a table block (header + separator + data rows) and
// returns pageLines with column-aligned cells separated by `│` and a `─/┼`
// separator below the header.
func renderTable(tableLines []string, tStart, tEnd, innerWidth int) []pageLine {
	if len(tableLines) < 2 {
		return nil
	}
	header := splitTableRow(tableLines[0])
	dataRows := make([][]string, 0, len(tableLines)-2)
	for _, line := range tableLines[2:] {
		dataRows = append(dataRows, splitTableRow(line))
	}

	ncols := len(header)
	for _, row := range dataRows {
		if len(row) > ncols {
			ncols = len(row)
		}
	}
	if ncols == 0 {
		return nil
	}

	normalize := func(row []string) []string {
		fixed := make([]string, ncols)
		for i := 0; i < ncols && i < len(row); i++ {
			fixed[i] = row[i]
		}
		return fixed
	}
	header = normalize(header)
	for i := range dataRows {
		dataRows[i] = normalize(dataRows[i])
	}

	colW := make([]int, ncols)
	measure := func(row []string) {
		for i, c := range row {
			if w := visualWidth(c); w > colW[i] {
				colW[i] = w
			}
		}
	}
	measure(header)
	for _, row := range dataRows {
		measure(row)
	}
	for i := range colW {
		if colW[i] < 1 {
			colW[i] = 1
		}
	}

	// `cell │ cell │ cell` — separator is 3 visual cells per gap.
	sepW := 3 * (ncols - 1)
	total := sepW
	for _, w := range colW {
		total += w
	}
	if total > innerWidth {
		avail := innerWidth - sepW
		if avail < ncols {
			avail = ncols
		}
		contentSum := 0
		for _, w := range colW {
			contentSum += w
		}
		if contentSum > 0 {
			for i := range colW {
				nw := colW[i] * avail / contentSum
				if nw < 3 {
					nw = 3
				}
				colW[i] = nw
			}
		}
	}

	out := renderTableRow(header, colW, true, tStart, tEnd)
	out = append(out, renderTableSeparator(colW, tStart, tEnd))
	for _, row := range dataRows {
		out = append(out, renderTableRow(row, colW, false, tStart, tEnd)...)
	}
	return out
}

// splitTableRow splits a markdown table row on '|' and trims surrounding
// whitespace from each cell. Outer empties (from leading/trailing '|') are
// dropped.
func splitTableRow(line string) []string {
	s := strings.TrimSpace(line)
	s = strings.TrimPrefix(s, "|")
	s = strings.TrimSuffix(s, "|")
	parts := strings.Split(s, "|")
	for i := range parts {
		parts[i] = strings.TrimSpace(parts[i])
	}
	return parts
}

// renderTableRow renders one logical table row (header or data) as 1..N
// pageLines, wrapping each cell to its column width and padding shorter cells
// with blanks to match the tallest cell.
func renderTableRow(cells []string, colW []int, isHeader bool, tStart, tEnd int) []pageLine {
	wrapped := make([][]string, len(cells))
	maxRows := 1
	for i, c := range cells {
		styled := editor.HighlightLine(preprocessLinks(c))
		w, _ := finalizeLinkSentinels(wordWrap(styled, colW[i]))
		rows := strings.Split(w, "\n")
		wrapped[i] = rows
		if len(rows) > maxRows {
			maxRows = len(rows)
		}
	}

	out := make([]pageLine, 0, maxRows)
	for r := 0; r < maxRows; r++ {
		parts := make([]string, len(cells))
		for i := range cells {
			var cell string
			if r < len(wrapped[i]) {
				cell = wrapped[i][r]
			}
			parts[i] = padTo(cell, colW[i])
		}
		display := strings.Join(parts, " │ ")
		if isHeader {
			display = "\x1b[1m" + display + "\x1b[22m"
		}
		out = append(out, pageLine{
			display:    display,
			srcLine:    tStart,
			inTable:    true,
			tableStart: tStart,
			tableEnd:   tEnd,
		})
	}
	return out
}

// renderTableSeparator renders the `─/┼` row that sits between the header and
// data rows.
func renderTableSeparator(colW []int, tStart, tEnd int) pageLine {
	parts := make([]string, len(colW))
	for i, w := range colW {
		parts[i] = strings.Repeat("─", w)
	}
	display := "\x1b[90m" + strings.Join(parts, "─┼─") + "\x1b[0m"
	return pageLine{
		display:    display,
		srcLine:    tStart + 1,
		inTable:    true,
		tableStart: tStart,
		tableEnd:   tEnd,
	}
}

// renderTextLine highlights a single non-table source line via
// editor.HighlightLine and word-wraps the result, prepending the original
// indent to every wrapped row. H1 lines get a full-width blue background.
func renderTextLine(srcLine string, srcIdx, innerWidth int) []pageLine {
	if srcLine == "" {
		return []pageLine{{srcLine: srcIdx}}
	}
	indent, content := extractIndent(srcLine)
	isH1 := strings.HasPrefix(content, "# ")

	highlighted := editor.HighlightLine(preprocessLinks(content))

	indentW := visualWidth(indent)
	wrapLimit := innerWidth - indentW
	if wrapLimit < 10 {
		wrapLimit = innerWidth
		indent = ""
	}

	wrapped, lineSpans := finalizeLinkSentinels(wordWrap(highlighted, wrapLimit))
	rows := strings.Split(wrapped, "\n")
	indentRunes := len([]rune(indent))

	out := make([]pageLine, 0, len(rows))
	for i, row := range rows {
		display := indent + row
		if isH1 {
			display = fillH1Line(display, innerWidth)
		}
		var spans []linkSpan
		if i < len(lineSpans) && len(lineSpans[i]) > 0 {
			spans = make([]linkSpan, len(lineSpans[i]))
			for j, sp := range lineSpans[i] {
				sp.startCol += indentRunes
				sp.endCol += indentRunes
				spans[j] = sp
			}
		}
		out = append(out, pageLine{
			display:   display,
			source:    srcLine,
			srcLine:   srcIdx,
			linkSpans: spans,
		})
	}
	return out
}

// fillH1Line applies the H1 full-line blue background, padding to innerWidth.
// Re-applies the background after every internal full-reset so it persists
// through any styled tokens emitted by HighlightLine.
func fillH1Line(line string, innerWidth int) string {
	line = strings.ReplaceAll(line, "\x1b[0m", "\x1b[0m\x1b[44m")
	pad := innerWidth - visualWidth(line)
	if pad < 0 {
		pad = 0
	}
	return "\x1b[44m" + line + strings.Repeat(" ", pad) + "\x1b[0m"
}

// ── Cursor / selection helpers ────────────────────────────────────────────────
//
// Cursor positions in the pager live in DISPLAY-RUNE space:
//   line = index into WebBar.pageLines
//   col  = rune offset into the ANSI-stripped display string for that line.
//
// Rune offset (not visual column) is the canonical form because line length is
// "number of visible runes" which is unambiguous for both ASCII and wide
// characters. Conversions to visual columns happen at the boundary with
// rendering and mouse coordinates.

// stripANSI returns s with all ANSI escape sequences removed.
func stripANSI(s string) string {
	var b strings.Builder
	inEsc := false
	for _, r := range s {
		if r == '\x1b' {
			inEsc = true
			continue
		}
		if inEsc {
			if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') {
				inEsc = false
			}
			continue
		}
		b.WriteRune(r)
	}
	return b.String()
}

// displayRuneCount returns the number of visible runes in display (ANSI codes
// excluded). Used for end-of-line cursor positioning.
func displayRuneCount(display string) int {
	n := 0
	inEsc := false
	for _, r := range display {
		if r == '\x1b' {
			inEsc = true
			continue
		}
		if inEsc {
			if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') {
				inEsc = false
			}
			continue
		}
		n++
	}
	return n
}

// runeOffsetToVisualCol returns the visual column at the given rune offset
// within display. Visual width per rune comes from runewidth (wide chars = 2).
func runeOffsetToVisualCol(display string, runeOffset int) int {
	col := 0
	seen := 0
	inEsc := false
	for _, r := range display {
		if r == '\x1b' {
			inEsc = true
			continue
		}
		if inEsc {
			if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') {
				inEsc = false
			}
			continue
		}
		if seen >= runeOffset {
			return col
		}
		w := runewidth.RuneWidth(r)
		if r == '\t' {
			w = tabWidth
		}
		col += w
		seen++
	}
	return col
}

// visualColToRuneOffset returns the rune offset closest to visualCol in
// display's stripped form. Clamps to [0, displayRuneCount].
func visualColToRuneOffset(display string, visualCol int) int {
	if visualCol <= 0 {
		return 0
	}
	col := 0
	seen := 0
	inEsc := false
	for _, r := range display {
		if r == '\x1b' {
			inEsc = true
			continue
		}
		if inEsc {
			if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') {
				inEsc = false
			}
			continue
		}
		w := runewidth.RuneWidth(r)
		if r == '\t' {
			w = tabWidth
		}
		if col+w > visualCol {
			return seen
		}
		col += w
		seen++
	}
	return seen
}

// injectCursorAtRuneRange wraps the runes in [fromRune, toRune) with reverse-
// video. Used to highlight an entire link span as the cursor "atom" when the
// cursor sits inside a markdown link.
func injectCursorAtRuneRange(display string, fromRune, toRune int) string {
	const (
		curOpen  = "\x1b[7m"
		curClose = "\x1b[27m"
	)
	if fromRune >= toRune {
		return display
	}
	var b strings.Builder
	seen := 0
	inEsc := false
	inCur := false
	for _, r := range display {
		if r == '\x1b' {
			b.WriteRune(r)
			inEsc = true
			continue
		}
		if inEsc {
			b.WriteRune(r)
			if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') {
				inEsc = false
				if inCur {
					b.WriteString(curOpen)
				}
			}
			continue
		}
		shouldCur := seen >= fromRune && seen < toRune
		if shouldCur && !inCur {
			b.WriteString(curOpen)
			inCur = true
		} else if !shouldCur && inCur {
			b.WriteString(curClose)
			inCur = false
		}
		b.WriteRune(r)
		seen++
	}
	if inCur {
		b.WriteString(curClose)
	}
	return b.String()
}

// injectCursorAtRune wraps the rune at runeOffset in display with reverse-video.
// If runeOffset is past the last visible rune, appends a reverse-video space.
func injectCursorAtRune(display string, runeOffset int) string {
	var out strings.Builder
	seen := 0
	inEsc := false
	injected := false
	for _, r := range display {
		if injected {
			out.WriteRune(r)
			continue
		}
		if r == '\x1b' {
			inEsc = true
			out.WriteRune(r)
			continue
		}
		if inEsc {
			out.WriteRune(r)
			if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') {
				inEsc = false
			}
			continue
		}
		if seen == runeOffset {
			out.WriteString("\x1b[7m")
			out.WriteRune(r)
			out.WriteString("\x1b[27m")
			injected = true
			seen++
			continue
		}
		out.WriteRune(r)
		seen++
	}
	if !injected {
		out.WriteString("\x1b[7m \x1b[27m")
	}
	return out.String()
}

// applySelectionRuneRange wraps display runes in [fromRune, toRune) with a
// bright-blue background selection highlight. toRune of -1 extends to end of
// line.
//
// Re-applies the selection background after every ANSI escape that ends while
// we're inside the selection — otherwise an embedded `\x1b[0m` (used by link
// styling, etc.) would silently strip the selection bg mid-range.
func applySelectionRuneRange(display string, fromRune, toRune int) string {
	const (
		selOpen  = "\x1b[103m"
		selClose = "\x1b[49m"
	)
	var b strings.Builder
	seen := 0
	inEsc := false
	inSel := false
	for _, r := range display {
		if r == '\x1b' {
			b.WriteRune(r)
			inEsc = true
			continue
		}
		if inEsc {
			b.WriteRune(r)
			if (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') {
				inEsc = false
				if inSel {
					b.WriteString(selOpen)
				}
			}
			continue
		}
		shouldSel := seen >= fromRune && (toRune < 0 || seen < toRune)
		if shouldSel && !inSel {
			b.WriteString(selOpen)
			inSel = true
		} else if !shouldSel && inSel {
			b.WriteString(selClose)
			inSel = false
		}
		b.WriteRune(r)
		seen++
	}
	if inSel {
		b.WriteString(selClose)
	}
	return b.String()
}

// strippedSubstring returns the runes [fromRune, toRune) from display's
// stripped form. toRune of -1 means end of stripped string.
func strippedSubstring(display string, fromRune, toRune int) string {
	stripped := stripANSI(display)
	runes := []rune(stripped)
	if fromRune < 0 {
		fromRune = 0
	}
	if fromRune > len(runes) {
		fromRune = len(runes)
	}
	if toRune < 0 || toRune > len(runes) {
		toRune = len(runes)
	}
	if fromRune >= toRune {
		return ""
	}
	return string(runes[fromRune:toRune])
}

// wordRight returns the rune offset of the next word boundary after start,
// stepping forward through display runes (stripped).
func wordRight(stripped string, start int) int {
	runes := []rune(stripped)
	n := len(runes)
	i := start
	if i < 0 {
		i = 0
	}
	for i < n && !isWordRune(runes[i]) {
		i++
	}
	for i < n && isWordRune(runes[i]) {
		i++
	}
	return i
}

// wordLeft returns the rune offset of the previous word boundary before start.
func wordLeft(stripped string, start int) int {
	runes := []rune(stripped)
	i := start
	if i > len(runes) {
		i = len(runes)
	}
	if i > 0 {
		i--
	}
	for i > 0 && !isWordRune(runes[i]) {
		i--
	}
	for i > 0 && isWordRune(runes[i-1]) {
		i--
	}
	return i
}

func isWordRune(r rune) bool {
	return unicode.IsLetter(r) || unicode.IsDigit(r) || r == '_'
}
