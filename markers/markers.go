// Package markers parses Aunic's edit-command markers (@>><<@, %>><<%,
// !>><<!, $>><<$) from raw note text and produces the model-visible snapshot
// (marker tokens replaced by HTML-comment annotations, hidden regions elided),
// nesting validation, and the editor highlight overlay ranges.
//
// Mechanical write enforcement (WritePolicy, protected-range checks,
// ApplyEdits/ResolveEdit/ResolveWrite) was removed along with the built-in
// note_write/note_edit tools it served — see git history. Marker boundaries
// are currently honored at the prompt level only (the harness system prompts
// explain the snapshot annotations to the model); re-adding enforcement for
// the generic file-edit tools the harnesses use is still an open problem.
package markers

import (
	"fmt"
	"sort"
	"strings"
)

type Kind int

const (
	KindWriteScope  Kind = iota // @>><<@
	KindExclude                 // %>><<%
	KindIncludeOnly             // !>><<!
	KindReadOnly                // $>><<$
)

func (k Kind) String() string {
	switch k {
	case KindWriteScope:
		return "@>> <<@"
	case KindExclude:
		return "%>> <<%"
	case KindIncludeOnly:
		return "!>> <<!"
	case KindReadOnly:
		return "$>> <<$"
	}
	return "?"
}

type Span struct {
	Kind       Kind
	OpenStart  int
	OpenEnd    int
	CloseStart int
	CloseEnd   int
	ParentIdx  int // -1 if top-level
}

func (s Span) BodyStart() int { return s.OpenEnd }
func (s Span) BodyEnd() int   { return s.CloseStart }

type Warning struct {
	Code    string
	Message string
	Offset  int
}

type ValidationError struct {
	Code    string
	Message string
	Offset  int
}

func (e *ValidationError) Error() string { return e.Message }

// Slot is a single @>><<@ wrap mapped to its 1-based slot number used in the
// model-visible snapshot's <!--Write/Rewrite #N--> annotations. Hidden wraps
// (those outside !>><<! or inside %>><<%) never become slots.
type Slot struct {
	SpanIdx int
	Number  int
	Empty   bool
}

// Snapshot is the model-visible rendering of a parsed note.
type Snapshot struct {
	Raw     string
	Visible string
	// HasShaping is true when at least one !>><<!, %>><<%, or $>><<$ span
	// exists. Callers use this to decide whether the injected note context
	// needs the marker-annotation explanation.
	HasShaping bool
}

type Parse struct {
	Text     string
	Spans    []Span
	Warnings []Warning
}

type tokenDef struct {
	kind Kind
	tok  string
}

var openerDefs = []tokenDef{
	{KindWriteScope, "@>>"},
	{KindExclude, "%>>"},
	{KindIncludeOnly, "!>>"},
	{KindReadOnly, "$>>"},
}

var closerDefs = []tokenDef{
	{KindWriteScope, "<<@"},
	{KindExclude, "<<%"},
	{KindIncludeOnly, "<<!"},
	{KindReadOnly, "<<$"},
}

const elidedPlaceholder = "<!-- elided -->"

// Scan walks text and produces matched marker spans. Unclosed/unmatched
// tokens are surfaced as warnings; only properly-matched spans appear in
// Spans. Tokens are skipped (treated as literal text) when preceded by '\'
// or fully enclosed in matching '"' / '\” quotes.
func Scan(text string) Parse {
	p := Parse{Text: text}
	var stack []int

	for i := 0; i < len(text); {
		if def, ok := matchToken(text, i, openerDefs); ok {
			if tokenIsEscaped(text, i, len(def.tok)) {
				i++
				continue
			}
			sp := Span{
				Kind:       def.kind,
				OpenStart:  i,
				OpenEnd:    i + len(def.tok),
				CloseStart: -1,
				CloseEnd:   -1,
				ParentIdx:  -1,
			}
			if len(stack) > 0 {
				sp.ParentIdx = stack[len(stack)-1]
			}
			p.Spans = append(p.Spans, sp)
			stack = append(stack, len(p.Spans)-1)
			i += len(def.tok)
			continue
		}
		if def, ok := matchToken(text, i, closerDefs); ok {
			if tokenIsEscaped(text, i, len(def.tok)) {
				i++
				continue
			}
			if len(stack) > 0 && p.Spans[stack[len(stack)-1]].Kind == def.kind {
				idx := stack[len(stack)-1]
				stack = stack[:len(stack)-1]
				p.Spans[idx].CloseStart = i
				p.Spans[idx].CloseEnd = i + len(def.tok)
				i += len(def.tok)
				continue
			}
			p.Warnings = append(p.Warnings, Warning{
				Code:    "unmatched_close",
				Message: "unmatched closing marker " + def.tok,
				Offset:  i,
			})
			i += len(def.tok)
			continue
		}
		i++
	}
	for _, idx := range stack {
		p.Warnings = append(p.Warnings, Warning{
			Code:    "unmatched_open",
			Message: "unmatched opening marker " + p.Spans[idx].Kind.String()[:3],
			Offset:  p.Spans[idx].OpenStart,
		})
	}
	matched := make([]Span, 0, len(p.Spans))
	for _, s := range p.Spans {
		if s.CloseStart >= 0 {
			matched = append(matched, s)
		}
	}
	p.Spans = matched
	return p
}

func matchToken(text string, i int, defs []tokenDef) (tokenDef, bool) {
	for _, d := range defs {
		if strings.HasPrefix(text[i:], d.tok) {
			return d, true
		}
	}
	return tokenDef{}, false
}

func tokenIsEscaped(text string, i, tokLen int) bool {
	if i > 0 && text[i-1] == '\\' {
		return true
	}
	if i > 0 && i+tokLen < len(text) {
		b, a := text[i-1], text[i+tokLen]
		if (b == '"' && a == '"') || (b == '\'' && a == '\'') {
			return true
		}
	}
	return false
}

// Validate enforces the nesting rules for @>><<@. Anything inside a write-
// scope span is contradictory; @>><<@ inside a read-only span is
// nonsensical. Write-scope spans inside %>><<% are silently dropped (see
// Slots). Returns nil when no violation is found.
func (p Parse) Validate() *ValidationError {
	for i := range p.Spans {
		s := p.Spans[i]
		if s.Kind != KindWriteScope {
			continue
		}
		if p.chainContainsKind(s.ParentIdx, KindReadOnly) {
			return &ValidationError{
				Code:    "scope_inside_readonly",
				Message: "@>> <<@ cannot be nested inside $>> <<$",
				Offset:  s.OpenStart,
			}
		}
		if p.chainContainsKind(s.ParentIdx, KindExclude) {
			continue
		}
		if p.hasDescendant(i) {
			return &ValidationError{
				Code:    "marker_inside_scope",
				Message: "Edit Markers can not be nested inside @>> <<@",
				Offset:  s.OpenStart,
			}
		}
	}
	return nil
}

func (p Parse) chainContainsKind(startIdx int, kind Kind) bool {
	for cur := startIdx; cur >= 0; cur = p.Spans[cur].ParentIdx {
		if p.Spans[cur].Kind == kind {
			return true
		}
	}
	return false
}

func (p Parse) hasDescendant(spanIdx int) bool {
	for j, c := range p.Spans {
		if j == spanIdx {
			continue
		}
		for cur := c.ParentIdx; cur >= 0; cur = p.Spans[cur].ParentIdx {
			if cur == spanIdx {
				return true
			}
		}
	}
	return false
}

// computeVisibility returns a per-byte map: true when the content at that
// byte should appear in the model-visible snapshot, false when it's hidden
// by content-shaping markers. Wrapper tokens themselves are not considered
// here — see wrapperMask.
func (p Parse) computeVisibility() []bool {
	n := len(p.Text)
	vis := make([]bool, n)
	hasInclude := false
	for _, s := range p.Spans {
		if s.Kind == KindIncludeOnly {
			hasInclude = true
			break
		}
	}
	if !hasInclude {
		for i := range vis {
			vis[i] = true
		}
	} else {
		for _, s := range p.Spans {
			if s.Kind == KindIncludeOnly {
				for i := s.BodyStart(); i < s.BodyEnd(); i++ {
					vis[i] = true
				}
			}
		}
	}
	for _, s := range p.Spans {
		if s.Kind == KindExclude {
			for i := s.BodyStart(); i < s.BodyEnd(); i++ {
				vis[i] = false
			}
		}
	}
	return vis
}

// wrapperMask marks bytes that belong to a marker open or close token.
// These are never emitted verbatim in the snapshot — they're either
// stripped (!>>, <<!, %>>, <<%, $>>, <<$) or replaced with HTML comments
// (visible @>><<@ wraps).
func (p Parse) wrapperMask() []bool {
	n := len(p.Text)
	w := make([]bool, n)
	for _, s := range p.Spans {
		for i := s.OpenStart; i < s.OpenEnd; i++ {
			w[i] = true
		}
		for i := s.CloseStart; i < s.CloseEnd; i++ {
			w[i] = true
		}
	}
	return w
}

// Slots returns the write-scope wraps as numbered slots in document order.
// Wraps that live inside a %>><<% body (transitively) or outside any
// !>><<! body when !>><<! is in play are dropped — they're invisible to
// the model and therefore can't be slot targets.
func (p Parse) Slots() []Slot {
	vis := p.computeVisibility()
	type indexed struct {
		idx int
		pos int
	}
	var collected []indexed
	for i, s := range p.Spans {
		if s.Kind != KindWriteScope {
			continue
		}
		bodyHidden := false
		body := func() (start, end int) { return s.BodyStart(), s.BodyEnd() }
		bs, be := body()
		if bs == be {
			// Empty wrap — use the position just after the opener as a
			// visibility probe. If that byte is hidden (or beyond text),
			// the whole wrap is in a hidden region.
			if s.OpenEnd < len(vis) && !vis[s.OpenEnd] {
				bodyHidden = true
			}
			if s.OpenEnd >= len(vis) {
				// Wrap at very end of text. Compute visibility from a
				// previous in-body byte; if there is none, assume hidden
				// when content shaping is active.
				if hasShaping(p) {
					bodyHidden = true
				}
			}
		} else {
			if !vis[bs] {
				bodyHidden = true
			}
		}
		if bodyHidden {
			continue
		}
		collected = append(collected, indexed{idx: i, pos: s.OpenStart})
	}
	sort.Slice(collected, func(a, b int) bool { return collected[a].pos < collected[b].pos })

	out := make([]Slot, 0, len(collected))
	for n, c := range collected {
		sp := p.Spans[c.idx]
		out = append(out, Slot{
			SpanIdx: c.idx,
			Number:  n + 1,
			Empty:   sp.BodyStart() == sp.BodyEnd(),
		})
	}
	return out
}

func hasShaping(p Parse) bool {
	for _, s := range p.Spans {
		switch s.Kind {
		case KindIncludeOnly, KindExclude, KindReadOnly:
			return true
		}
	}
	return false
}

// protectedRender describes a $>><<$ span that survives content shaping
// (i.e., is not buried inside a %>><<% body) so BuildSnapshot can render
// its boundary HTML comments and track its body as a Protected range.
type protectedRender struct {
	spanIdx int
	number  int
	empty   bool
}

func (p Parse) protectedRenders() []protectedRender {
	vis := p.computeVisibility()
	var out []protectedRender
	n := 0
	for i, s := range p.Spans {
		if s.Kind != KindReadOnly {
			continue
		}
		empty := s.BodyStart() == s.BodyEnd()
		probe := s.OpenEnd
		if !empty {
			probe = s.BodyStart()
		}
		if probe < len(vis) && !vis[probe] {
			continue
		}
		n++
		out = append(out, protectedRender{spanIdx: i, number: n, empty: empty})
	}
	return out
}

// BuildSnapshot produces the model-visible text of a parsed note: marker
// tokens are replaced by HTML-comment annotations and hidden regions are
// elided. Call Validate first if you need nesting violations rejected.
//
// The walk uses two indexed maps — leading and trailing — so nested marker
// bodies are emitted naturally: the opener's comment is written at OpenStart,
// the body content is walked through normally (potentially encountering
// nested markers), and the closer's comment is written at CloseStart.
func (p Parse) BuildSnapshot() Snapshot {
	text := p.Text
	vis := p.computeVisibility()
	wrap := p.wrapperMask()

	leading := make(map[int]string)
	trailing := make(map[int]string)

	for _, sl := range p.Slots() {
		sp := p.Spans[sl.SpanIdx]
		if sl.Empty {
			leading[sp.OpenStart] = fmt.Sprintf("<!--Write #%d location-->", sl.Number)
			continue
		}
		leading[sp.OpenStart] = fmt.Sprintf("<!--Rewrite #%d start-->", sl.Number)
		trailing[sp.CloseStart] = fmt.Sprintf("<!--Rewrite #%d end-->", sl.Number)
	}

	for _, pr := range p.protectedRenders() {
		sp := p.Spans[pr.spanIdx]
		if pr.empty {
			leading[sp.OpenStart] = fmt.Sprintf("<!--PROTECTED #%d: NO EDITS HERE-->", pr.number)
			continue
		}
		leading[sp.OpenStart] = fmt.Sprintf("<!--PROTECTED #%d start: NO EDITS-->", pr.number)
		trailing[sp.CloseStart] = fmt.Sprintf("<!--PROTECTED #%d end-->", pr.number)
	}

	var b strings.Builder
	inChunk := false
	pendingElision := false

	flushChunk := func() { inChunk = false }
	emitElisionIfPending := func() {
		if pendingElision {
			b.WriteString(elidedPlaceholder)
			pendingElision = false
		}
	}

	i := 0
	n := len(text)
	for i < n {
		if lead, ok := leading[i]; ok {
			flushChunk()
			emitElisionIfPending()
			b.WriteString(lead)
		}
		if tail, ok := trailing[i]; ok {
			flushChunk()
			b.WriteString(tail)
		}

		if wrap[i] {
			flushChunk()
			i++
			continue
		}

		if !vis[i] {
			flushChunk()
			pendingElision = true
			i++
			continue
		}

		if !inChunk {
			emitElisionIfPending()
			inChunk = true
		}
		b.WriteByte(text[i])
		i++
	}
	if pendingElision {
		b.WriteString(elidedPlaceholder)
	}

	return Snapshot{
		Raw:        text,
		Visible:    b.String(),
		HasShaping: hasShaping(p),
	}
}

// StripMarkers removes the opener and closer tokens of all matched spans
// whose Kind is in kinds. Body content is left in place. Orphan/unmatched
// tokens and escaped tokens are not touched (they don't appear in Spans).
// Returns text unchanged when no spans of the requested kinds exist.
func StripMarkers(text string, kinds ...Kind) string {
	if len(kinds) == 0 {
		return text
	}
	keep := make(map[Kind]bool, len(kinds))
	for _, k := range kinds {
		keep[k] = true
	}
	p := Scan(text)
	if len(p.Spans) == 0 {
		return text
	}
	type tokenRange struct{ start, end int }
	var ranges []tokenRange
	for _, s := range p.Spans {
		if !keep[s.Kind] {
			continue
		}
		ranges = append(ranges, tokenRange{s.OpenStart, s.OpenEnd})
		ranges = append(ranges, tokenRange{s.CloseStart, s.CloseEnd})
	}
	if len(ranges) == 0 {
		return text
	}
	sort.Slice(ranges, func(i, j int) bool { return ranges[i].start > ranges[j].start })
	out := text
	for _, r := range ranges {
		out = out[:r.start] + out[r.end:]
	}
	return out
}

// HighlightRange describes a contiguous raw-byte range that should receive a
// specific ANSI color in the editor overlay. Color is one of 1 (%), 2 (@),
// 5 ($), or 6 (!).
type HighlightRange struct {
	Start int
	End   int
	Color int
}

// markerANSIColor returns the ANSI color number for a marker kind.
//   - KindIncludeOnly (!): 6 (cyan)
//   - KindExclude     (%): 1 (red)
//   - KindReadOnly    ($): 5 (magenta)
//   - KindWriteScope  (@): 2 (green)
func markerANSIColor(k Kind) int {
	switch k {
	case KindIncludeOnly:
		return 6
	case KindExclude:
		return 1
	case KindReadOnly:
		return 5
	case KindWriteScope:
		return 2
	}
	return 0
}

// spanDepth returns the nesting depth of the span at idx (0 = top-level).
func (p Parse) spanDepth(idx int) int {
	d := 0
	for cur := p.Spans[idx].ParentIdx; cur >= 0; cur = p.Spans[cur].ParentIdx {
		d++
	}
	return d
}

// HighlightRanges returns two slices for the editor marker overlay:
//   - bgRanges: token bytes (the bracket sequences @>>, <<@, etc.) that should
//     receive background+foreground styling.
//   - ulRanges: body bytes that should receive underline-color styling.
//
// Only semantically active spans contribute: @>><<@ if in Slots(), $>><<$ if
// in protectedRenders(), and %>><<% / !>><<! always. When spans overlap,
// the innermost active span's color wins — achieved by processing outermost
// spans first (lowest depth) and letting inner spans overwrite.
func (p Parse) HighlightRanges() (bgRanges, ulRanges []HighlightRange) {
	if len(p.Spans) == 0 {
		return
	}

	// Build set of active span indices.
	active := make(map[int]bool, len(p.Spans))
	for i, s := range p.Spans {
		switch s.Kind {
		case KindIncludeOnly, KindExclude:
			active[i] = true
		}
	}
	for _, sl := range p.Slots() {
		active[sl.SpanIdx] = true
	}
	for _, pr := range p.protectedRenders() {
		active[pr.spanIdx] = true
	}

	if len(active) == 0 {
		return
	}

	// Sort active spans by depth ascending (outermost first) so inner spans
	// overwrite when painting.
	order := make([]int, 0, len(active))
	for idx := range active {
		order = append(order, idx)
	}
	sort.Slice(order, func(a, b int) bool {
		da, db := p.spanDepth(order[a]), p.spanDepth(order[b])
		if da != db {
			return da < db
		}
		return p.Spans[order[a]].OpenStart < p.Spans[order[b]].OpenStart
	})

	n := len(p.Text)
	bgMap := make([]int, n)
	ulMap := make([]int, n)

	for _, idx := range order {
		s := p.Spans[idx]
		color := markerANSIColor(s.Kind)
		for i := s.OpenStart; i < s.OpenEnd && i < n; i++ {
			bgMap[i] = color
		}
		for i := s.BodyStart(); i < s.BodyEnd() && i < n; i++ {
			ulMap[i] = color
		}
		for i := s.CloseStart; i < s.CloseEnd && i < n; i++ {
			bgMap[i] = color
		}
	}

	// Compress maps into range slices.
	bgRanges = compressColorMap(bgMap)
	ulRanges = compressColorMap(ulMap)
	return
}

// compressColorMap converts a per-byte color map into a slice of contiguous
// non-zero ranges. Zero entries are skipped.
func compressColorMap(m []int) []HighlightRange {
	var out []HighlightRange
	i := 0
	for i < len(m) {
		if m[i] == 0 {
			i++
			continue
		}
		color := m[i]
		j := i + 1
		for j < len(m) && m[j] == color {
			j++
		}
		out = append(out, HighlightRange{Start: i, End: j, Color: color})
		i = j
	}
	return out
}
