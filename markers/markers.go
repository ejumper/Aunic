// Package markers parses Aunic's edit-command markers (@>><<@, %>><<%,
// !>><<!, $>><<$) from raw note text and produces the artifacts the runner
// needs: validation, the model-visible snapshot, the source map back to raw
// offsets, and the policy hints that decide which note tools stay registered.
//
// $>><<$ is recognized so nesting rules can be enforced, but is not yet
// semantically wired (read-only enforcement, comment substitution) — its
// tokens are stripped from the visible snapshot and its body is left as
// regular content.
package markers

import (
	"fmt"
	"regexp"
	"sort"
	"strconv"
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
// model-visible snapshot and the note_edit_at form. Hidden wraps (those
// outside !>><<! or inside %>><<%) never become slots.
type Slot struct {
	SpanIdx int
	Number  int
	Empty   bool
}

// WritePolicy describes how note_write is allowed to operate given the
// current marker configuration.
type WritePolicy int

const (
	// WritePolicyFull lets note_write replace the entire note body.
	WritePolicyFull WritePolicy = iota
	// WritePolicyScoped restricts note_write to a specific raw range
	// (Snapshot.NoteWriteRange) — the include's body, or the writable region
	// between edge-only excludes.
	WritePolicyScoped
	// WritePolicyForbidden removes note_write from the tool list entirely.
	// Triggered by multiple !>><<! spans, by a %>><<% in the middle of
	// writable content, or whenever @>><<@ scoped edits are active.
	WritePolicyForbidden
)

// Range is a half-open [Start, End) raw byte range.
type Range struct {
	Start int
	End   int
}

// SourceSegment maps a span of visible (model-seen) text back to a span of
// raw note text. Visible offsets are within Snapshot.Visible, raw offsets
// are within Snapshot.Raw. The two spans always have equal length.
type SourceSegment struct {
	VisibleStart int
	VisibleEnd   int
	RawStart     int
	RawEnd       int
}

// Snapshot is the full set of artifacts produced from a parsed note that the
// runner and tools need: the visible text the model sees, the source map
// back to raw offsets, the surviving @>><<@ slots in numbering order, and
// the note_write policy.
type Snapshot struct {
	Raw            string
	Visible        string
	SourceMap      []SourceSegment
	Slots          []Slot
	WritePolicy    WritePolicy
	NoteWriteRange Range
	// Protected lists raw byte ranges covered by visible $>> <<$ spans.
	// ResolveEdit rejects any edit whose raw range overlaps one of these.
	Protected []Range
	// HasShaping is true when at least one !>><<!, %>><<%, or $>><<$ span
	// exists. Callers use this to decide whether note_edit must go through
	// the source map (and protection check) rather than a plain string
	// find/replace.
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

// BuildSnapshot produces the artifacts needed by the runner and the
// note-edit/note-write apply paths: the model-visible text, a source map
// back to the raw note, the surviving @>><<@ slots, the protected ranges
// from $>><<$, and the note_write policy. Call Validate first if you need
// nesting violations rejected.
//
// The walk uses two indexed maps — leadingByOpenStart and
// trailingByCloseStart — so nested marker bodies are emitted naturally:
// the opener's leading comment is written at OpenStart, the body content
// is walked through normally (potentially encountering nested markers),
// and the closer's trailing comment is written at CloseStart.
func (p Parse) BuildSnapshot() Snapshot {
	text := p.Text
	vis := p.computeVisibility()
	wrap := p.wrapperMask()
	slots := p.Slots()

	leading := make(map[int]string)
	trailing := make(map[int]string)
	var protected []Range

	for _, sl := range slots {
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
		protected = append(protected, Range{Start: sp.BodyStart(), End: sp.BodyEnd()})
	}

	var b strings.Builder
	var segs []SourceSegment

	chunkRawStart := -1
	chunkVisStart := 0
	pendingElision := false

	flushChunk := func(rawEnd int) {
		if chunkRawStart >= 0 {
			segs = append(segs, SourceSegment{
				VisibleStart: chunkVisStart,
				VisibleEnd:   b.Len(),
				RawStart:     chunkRawStart,
				RawEnd:       rawEnd,
			})
			chunkRawStart = -1
		}
	}
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
			flushChunk(i)
			emitElisionIfPending()
			b.WriteString(lead)
		}
		if tail, ok := trailing[i]; ok {
			flushChunk(i)
			b.WriteString(tail)
		}

		if wrap[i] {
			flushChunk(i)
			i++
			continue
		}

		if !vis[i] {
			flushChunk(i)
			pendingElision = true
			i++
			continue
		}

		if chunkRawStart < 0 {
			emitElisionIfPending()
			chunkRawStart = i
			chunkVisStart = b.Len()
		}
		b.WriteByte(text[i])
		i++
	}
	flushChunk(n)
	if pendingElision {
		b.WriteString(elidedPlaceholder)
	}

	policy, rng := computeWritePolicy(p, vis, wrap)

	return Snapshot{
		Raw:            text,
		Visible:        b.String(),
		SourceMap:      segs,
		Slots:          slots,
		WritePolicy:    policy,
		NoteWriteRange: rng,
		Protected:      protected,
		HasShaping:     hasShaping(p),
	}
}

// computeWritePolicy applies the policy table: any $>><<$ → forbidden;
// multiple includes → forbidden; single include → scoped to the include
// body; middle exclude → forbidden; edge-only excludes → scoped between
// them; else full.
func computeWritePolicy(p Parse, vis, wrap []bool) (WritePolicy, Range) {
	var includes, excludes []int
	hasReadOnly := false
	for i, s := range p.Spans {
		switch s.Kind {
		case KindIncludeOnly:
			includes = append(includes, i)
		case KindExclude:
			excludes = append(excludes, i)
		case KindReadOnly:
			hasReadOnly = true
		}
	}

	// A visible $>><<$ anywhere makes a full-note write unsafe: the model
	// can't reliably reproduce protected content byte-for-byte from the
	// commented snapshot. %>><<%-buried $>><<$ doesn't count — the model
	// never sees those, so they don't constrain writability.
	if hasReadOnly && anyVisibleReadOnly(p) {
		return WritePolicyForbidden, Range{}
	}

	if len(includes) > 1 {
		return WritePolicyForbidden, Range{}
	}
	if len(includes) == 1 {
		s := p.Spans[includes[0]]
		return WritePolicyScoped, Range{Start: s.BodyStart(), End: s.BodyEnd()}
	}
	if len(excludes) == 0 {
		return WritePolicyFull, Range{Start: 0, End: len(p.Text)}
	}

	firstVisible, lastVisible := -1, -1
	for i := range vis {
		if vis[i] && !wrap[i] {
			if firstVisible < 0 {
				firstVisible = i
			}
			lastVisible = i
		}
	}

	topEnd := 0
	botStart := len(p.Text)
	hasTop, hasBot := false, false
	for _, idx := range excludes {
		s := p.Spans[idx]
		isTop := firstVisible < 0 || s.CloseEnd <= firstVisible
		isBot := lastVisible < 0 || s.OpenStart > lastVisible
		switch {
		case isTop:
			hasTop = true
			if s.CloseEnd > topEnd {
				topEnd = s.CloseEnd
			}
		case isBot:
			hasBot = true
			if s.OpenStart < botStart {
				botStart = s.OpenStart
			}
		default:
			return WritePolicyForbidden, Range{}
		}
	}
	if !hasTop {
		topEnd = 0
	}
	if !hasBot {
		botStart = len(p.Text)
	}
	return WritePolicyScoped, Range{Start: topEnd, End: botStart}
}

// ApplyEdits returns text with each requested slot's body replaced by the
// supplied content. Slot keys may be numeric strings ("1"). The @>> and
// <<@ markers themselves are preserved. Submitting an empty string deletes
// the body (the markers remain so the wrap stays available for the next
// turn).
func (p Parse) ApplyEdits(text string, edits map[string]string) (newText string, applied []int, err error) {
	slots := p.Slots()
	if len(slots) == 0 && len(edits) > 0 {
		return text, nil, fmt.Errorf("no @>> <<@ slots exist in the current note")
	}
	byNum := make(map[int]Slot, len(slots))
	valid := make([]int, 0, len(slots))
	for _, s := range slots {
		byNum[s.Number] = s
		valid = append(valid, s.Number)
	}
	sort.Ints(valid)

	type op struct {
		start, end int
		content    string
		slot       int
	}
	ops := make([]op, 0, len(edits))
	for key, content := range edits {
		num, convErr := strconv.Atoi(strings.TrimPrefix(strings.TrimSpace(key), "#"))
		if convErr != nil {
			return text, nil, fmt.Errorf("invalid slot key %q (expected an integer like \"1\")", key)
		}
		sl, ok := byNum[num]
		if !ok {
			return text, nil, fmt.Errorf("slot #%d does not exist; valid slots are %s", num, formatSlotList(valid))
		}
		sp := p.Spans[sl.SpanIdx]
		ops = append(ops, op{
			start:   sp.BodyStart(),
			end:     sp.BodyEnd(),
			content: stripScopeComments(content),
			slot:    num,
		})
	}
	sort.Slice(ops, func(a, b int) bool { return ops[a].start > ops[b].start })

	out := text
	done := make([]int, 0, len(ops))
	for _, o := range ops {
		out = out[:o.start] + o.content + out[o.end:]
		done = append(done, o.slot)
	}
	sort.Ints(done)
	return out, done, nil
}

// EditConflict classifies the outcome of ResolveEdit when the requested
// edit cannot be applied. Matches editor.ConflictKind so the runner
// surfaces the same diagnostics as the non-shaped path, plus
// EditConflictProtected for $>><<$ hits.
type EditConflict int

const (
	EditConflictNone EditConflict = iota
	EditConflictNotFound
	EditConflictAmbiguous
	EditConflictProtected
)

// anyVisibleReadOnly reports whether at least one $>><<$ span survives
// content shaping — i.e., its body is not entirely buried inside a
// %>><<% region.
func anyVisibleReadOnly(p Parse) bool {
	vis := p.computeVisibility()
	for _, s := range p.Spans {
		if s.Kind != KindReadOnly {
			continue
		}
		probe := s.OpenEnd
		if s.BodyStart() != s.BodyEnd() {
			probe = s.BodyStart()
		}
		if probe >= len(vis) || vis[probe] {
			return true
		}
	}
	return false
}

func overlapsAny(start, end int, ranges []Range) bool {
	for _, r := range ranges {
		if start < r.End && end > r.Start {
			return true
		}
	}
	return false
}

// ResolveEdit performs a snapshot-aware note_edit: it finds old in the
// visible text, maps the match back to raw offsets through the source
// map, and applies the replacement on the raw text. Matches that fall on
// the placeholder/HTML comment text (no source mapping) are ignored. If
// no valid match remains the result reports NotFound; if more than one
// match exists without replace_all the result reports Ambiguous.
func (s Snapshot) ResolveEdit(old, new string, replaceAll bool) (updated string, count int, conflict EditConflict) {
	if old == "" {
		return s.Raw, 0, EditConflictNotFound
	}
	var matches []match
	protectedHits := 0
	from := 0
	for from < len(s.Visible) {
		idx := strings.Index(s.Visible[from:], old)
		if idx < 0 {
			break
		}
		visStart := from + idx
		if rawStart, ok := visibleToRaw(s.SourceMap, visStart, len(old)); ok {
			if overlapsAny(rawStart, rawStart+len(old), s.Protected) {
				protectedHits++
			} else {
				matches = append(matches, match{rawStart: rawStart, length: len(old)})
			}
		}
		from = visStart + 1
	}
	if len(matches) == 0 {
		if protectedHits > 0 {
			return s.Raw, protectedHits, EditConflictProtected
		}
		return s.Raw, 0, EditConflictNotFound
	}
	if len(matches) > 1 && !replaceAll {
		return s.Raw, len(matches), EditConflictAmbiguous
	}
	sort.Slice(matches, func(a, b int) bool { return matches[a].rawStart > matches[b].rawStart })
	out := s.Raw
	for _, m := range matches {
		out = out[:m.rawStart] + new + out[m.rawStart+m.length:]
	}
	return out, len(matches), EditConflictNone
}

type match struct {
	rawStart int
	length   int
}

func visibleToRaw(segs []SourceSegment, visStart, length int) (int, bool) {
	visEnd := visStart + length
	for _, seg := range segs {
		if visStart >= seg.VisibleStart && visEnd <= seg.VisibleEnd {
			return seg.RawStart + (visStart - seg.VisibleStart), true
		}
	}
	return 0, false
}

// ResolveWrite produces the raw text after a note_write call given the
// snapshot's policy. For WritePolicyScoped, the content is spliced into
// NoteWriteRange (the include body or the between-edges region) while
// preserving the surrounding markers and untouched content. Returns
// (rawText, false) when the policy is forbidden — callers should never
// reach this path because the tool is dropped from the API list, but the
// guard keeps the contract explicit.
func (s Snapshot) ResolveWrite(content string) (string, bool) {
	switch s.WritePolicy {
	case WritePolicyFull:
		return content, true
	case WritePolicyScoped:
		return s.Raw[:s.NoteWriteRange.Start] + content + s.Raw[s.NoteWriteRange.End:], true
	}
	return s.Raw, false
}

// stripScopeComments removes any <!--Write #N location--> or
// <!--Rewrite #N start--> / <!--Rewrite #N end--> markers the model may
// have accidentally echoed back inside its submitted content. They're
// tool-machinery, not part of the user's note.
var scopeCommentRe = regexp.MustCompile(`<!--(?:Write #\d+ location|Rewrite #\d+ (?:start|end))-->`)

func stripScopeComments(s string) string {
	return scopeCommentRe.ReplaceAllString(s, "")
}

func formatSlotList(nums []int) string {
	parts := make([]string, 0, len(nums))
	for _, n := range nums {
		parts = append(parts, fmt.Sprintf("#%d", n))
	}
	return strings.Join(parts, ", ")
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
