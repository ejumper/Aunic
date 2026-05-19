package markers

import (
	"strings"
	"testing"
)

// ── @>><<@ basics ─────────────────────────────────────────────────────────

func TestScanWriteScope_EmptyWrap(t *testing.T) {
	text := "before @>><<@ after"
	p := Scan(text)
	if len(p.Spans) != 1 {
		t.Fatalf("want 1 span, got %d", len(p.Spans))
	}
	if p.Spans[0].Kind != KindWriteScope {
		t.Errorf("want KindWriteScope, got %v", p.Spans[0].Kind)
	}
	slots := p.Slots()
	if len(slots) != 1 || !slots[0].Empty || slots[0].Number != 1 {
		t.Errorf("unexpected slots: %+v", slots)
	}
}

func TestBuildSnapshot_EmptyWrap(t *testing.T) {
	text := "before @>><<@ after"
	snap := Scan(text).BuildSnapshot()
	want := "before <!--Write #1 location--> after"
	if snap.Visible != want {
		t.Errorf("got %q, want %q", snap.Visible, want)
	}
	if snap.HasShaping {
		t.Errorf("HasShaping should be false")
	}
	if snap.WritePolicy != WritePolicyFull {
		t.Errorf("want WritePolicyFull, got %v", snap.WritePolicy)
	}
}

func TestBuildSnapshot_Rewrite(t *testing.T) {
	text := "before @>>hello<<@ after"
	snap := Scan(text).BuildSnapshot()
	want := "before <!--Rewrite #1 start-->hello<!--Rewrite #1 end--> after"
	if snap.Visible != want {
		t.Errorf("got %q, want %q", snap.Visible, want)
	}
}

func TestBuildSnapshot_MultipleSlots(t *testing.T) {
	text := "a @>><<@ b @>>x<<@ c"
	snap := Scan(text).BuildSnapshot()
	want := "a <!--Write #1 location--> b <!--Rewrite #2 start-->x<!--Rewrite #2 end--> c"
	if snap.Visible != want {
		t.Errorf("got %q, want %q", snap.Visible, want)
	}
}

// ── Validation ───────────────────────────────────────────────────────────

func TestValidate_MarkerInsideScope(t *testing.T) {
	text := "@>> hi $>>protected<<$ bye <<@"
	err := Scan(text).Validate()
	if err == nil {
		t.Fatal("want validation error, got nil")
	}
	if !strings.Contains(err.Message, "Edit Markers can not be nested") {
		t.Errorf("unexpected message: %q", err.Message)
	}
}

func TestValidate_ScopeInsideReadOnly(t *testing.T) {
	text := "$>> some @>><<@ stuff <<$"
	err := Scan(text).Validate()
	if err == nil {
		t.Fatal("want validation error, got nil")
	}
	if !strings.Contains(err.Message, "$>> <<$") {
		t.Errorf("unexpected message: %q", err.Message)
	}
}

func TestValidate_ScopeInsideIncludeOnly_OK(t *testing.T) {
	text := "!>> some @>><<@ stuff <<!"
	if err := Scan(text).Validate(); err != nil {
		t.Errorf("unexpected error: %v", err)
	}
}

// ── %>><<% (exclude) ─────────────────────────────────────────────────────

func TestBuildSnapshot_Exclude_Middle(t *testing.T) {
	text := "before %>>hidden<<% after"
	snap := Scan(text).BuildSnapshot()
	want := "before <!-- elided --> after"
	if snap.Visible != want {
		t.Errorf("got %q, want %q", snap.Visible, want)
	}
	if !snap.HasShaping {
		t.Errorf("HasShaping should be true")
	}
	if snap.WritePolicy != WritePolicyForbidden {
		t.Errorf("middle exclude → forbidden, got %v", snap.WritePolicy)
	}
}

func TestBuildSnapshot_Exclude_TopOnly(t *testing.T) {
	text := "%>>top<<% writable body"
	snap := Scan(text).BuildSnapshot()
	if !strings.HasPrefix(snap.Visible, "<!-- elided -->") {
		t.Errorf("missing leading elision: %q", snap.Visible)
	}
	if snap.WritePolicy != WritePolicyScoped {
		t.Errorf("edge-only exclude → scoped, got %v", snap.WritePolicy)
	}
	if snap.NoteWriteRange.Start == 0 {
		t.Errorf("scoped range should start after the top exclude close, got %+v", snap.NoteWriteRange)
	}
}

func TestBuildSnapshot_Exclude_BottomOnly(t *testing.T) {
	text := "writable body %>>bot<<%"
	snap := Scan(text).BuildSnapshot()
	if !strings.HasSuffix(snap.Visible, "<!-- elided -->") {
		t.Errorf("missing trailing elision: %q", snap.Visible)
	}
	if snap.WritePolicy != WritePolicyScoped {
		t.Errorf("edge-only exclude → scoped, got %v", snap.WritePolicy)
	}
}

func TestBuildSnapshot_Exclude_BothEdges(t *testing.T) {
	text := "%>>top<<% middle %>>bot<<%"
	snap := Scan(text).BuildSnapshot()
	want := "<!-- elided --> middle <!-- elided -->"
	if snap.Visible != want {
		t.Errorf("got %q, want %q", snap.Visible, want)
	}
	if snap.WritePolicy != WritePolicyScoped {
		t.Errorf("edge-only → scoped, got %v", snap.WritePolicy)
	}
}

func TestBuildSnapshot_Exclude_MiddleAndEdge_Forbidden(t *testing.T) {
	text := "%>>top<<% middle %>>mid<<% more %>>bot<<%"
	snap := Scan(text).BuildSnapshot()
	if snap.WritePolicy != WritePolicyForbidden {
		t.Errorf("middle exclude with surrounding writable → forbidden, got %v", snap.WritePolicy)
	}
}

// ── !>><<! (include-only) ────────────────────────────────────────────────

func TestBuildSnapshot_IncludeOnly_Single(t *testing.T) {
	text := "before !>>shown<<! after"
	snap := Scan(text).BuildSnapshot()
	want := "<!-- elided -->shown<!-- elided -->"
	if snap.Visible != want {
		t.Errorf("got %q, want %q", snap.Visible, want)
	}
	if snap.WritePolicy != WritePolicyScoped {
		t.Errorf("single include → scoped, got %v", snap.WritePolicy)
	}
}

func TestBuildSnapshot_IncludeOnly_Multiple_Forbidden(t *testing.T) {
	text := "!>>a<<! middle !>>b<<!"
	snap := Scan(text).BuildSnapshot()
	if snap.WritePolicy != WritePolicyForbidden {
		t.Errorf("multiple include → forbidden, got %v", snap.WritePolicy)
	}
	want := "a<!-- elided -->b"
	if snap.Visible != want {
		t.Errorf("got %q, want %q", snap.Visible, want)
	}
}

// ── @>><<@ inside content shaping ────────────────────────────────────────

func TestBuildSnapshot_Scope_InsideIncludeOnly(t *testing.T) {
	text := "before !>> @>><<@ <<! after"
	snap := Scan(text).BuildSnapshot()
	if !strings.Contains(snap.Visible, "<!--Write #1 location-->") {
		t.Errorf("slot should be visible inside !>><<!: %q", snap.Visible)
	}
	if len(snap.Slots) != 1 {
		t.Errorf("want 1 slot, got %d", len(snap.Slots))
	}
}

func TestBuildSnapshot_Scope_OutsideIncludeOnly_NotASlot(t *testing.T) {
	text := "@>><<@ before !>>shown<<! @>><<@"
	snap := Scan(text).BuildSnapshot()
	if len(snap.Slots) != 0 {
		t.Errorf("wraps outside !>><<! should not be slots, got %d", len(snap.Slots))
	}
	if strings.Contains(snap.Visible, "Write #") {
		t.Errorf("no slot comments expected in visible: %q", snap.Visible)
	}
}

func TestBuildSnapshot_Scope_InsideExclude_Swallowed(t *testing.T) {
	text := "%>> ignore @>><<@ this <<% @>>real<<@"
	snap := Scan(text).BuildSnapshot()
	if len(snap.Slots) != 1 {
		t.Fatalf("want 1 surviving slot, got %d", len(snap.Slots))
	}
	if snap.Slots[0].Empty {
		t.Errorf("surviving slot should be Rewrite")
	}
}

// ── Source map for ResolveEdit ───────────────────────────────────────────

func TestResolveEdit_AcrossElision_NoMatch(t *testing.T) {
	text := "before %>>hidden<<% after"
	snap := Scan(text).BuildSnapshot()
	// Visible: "before <!-- elided --> after"
	// Try to replace across the elision boundary
	_, _, conflict := snap.ResolveEdit("before  after", "X", false)
	if conflict != EditConflictNotFound {
		t.Errorf("expected NotFound across elision, got %v", conflict)
	}
}

func TestResolveEdit_VisibleMatch_AppliesToRaw(t *testing.T) {
	text := "before %>>hidden<<% after"
	snap := Scan(text).BuildSnapshot()
	updated, count, conflict := snap.ResolveEdit("after", "AFTER", false)
	if conflict != EditConflictNone || count != 1 {
		t.Fatalf("expected single clean match, got count=%d conflict=%v", count, conflict)
	}
	if updated != "before %>>hidden<<% AFTER" {
		t.Errorf("got %q", updated)
	}
}

func TestResolveEdit_OnlyHiddenMatch_NotFound(t *testing.T) {
	text := "%>>secret<<% public"
	snap := Scan(text).BuildSnapshot()
	_, _, conflict := snap.ResolveEdit("secret", "X", false)
	if conflict != EditConflictNotFound {
		t.Errorf("hidden-only match should be NotFound, got %v", conflict)
	}
}

func TestResolveEdit_AmbiguousVisible(t *testing.T) {
	text := "foo bar foo"
	snap := Scan(text).BuildSnapshot()
	_, count, conflict := snap.ResolveEdit("foo", "X", false)
	if conflict != EditConflictAmbiguous || count != 2 {
		t.Errorf("expected Ambiguous (count=2), got count=%d conflict=%v", count, conflict)
	}
}

func TestResolveEdit_ReplaceAll(t *testing.T) {
	text := "foo bar %>>foo<<% foo"
	snap := Scan(text).BuildSnapshot()
	updated, count, conflict := snap.ResolveEdit("foo", "X", true)
	if conflict != EditConflictNone || count != 2 {
		t.Fatalf("expected 2 visible matches replaced, got count=%d conflict=%v", count, conflict)
	}
	// Hidden foo stays untouched
	if updated != "X bar %>>foo<<% X" {
		t.Errorf("got %q", updated)
	}
}

// ── ResolveWrite ─────────────────────────────────────────────────────────

func TestResolveWrite_Full(t *testing.T) {
	text := "anything"
	snap := Scan(text).BuildSnapshot()
	out, ok := snap.ResolveWrite("brand new")
	if !ok || out != "brand new" {
		t.Errorf("got ok=%v out=%q", ok, out)
	}
}

func TestResolveWrite_ScopedInclude(t *testing.T) {
	text := "before !>>old<<! after"
	snap := Scan(text).BuildSnapshot()
	out, ok := snap.ResolveWrite("NEW")
	if !ok {
		t.Fatal("scoped write should be allowed")
	}
	if out != "before !>>NEW<<! after" {
		t.Errorf("got %q", out)
	}
}

func TestResolveWrite_ScopedExcludeEdges(t *testing.T) {
	text := "%>>top<<% middle %>>bot<<%"
	snap := Scan(text).BuildSnapshot()
	out, ok := snap.ResolveWrite("NEW")
	if !ok {
		t.Fatal("scoped write should be allowed")
	}
	if out != "%>>top<<%NEW%>>bot<<%" {
		t.Errorf("got %q", out)
	}
}

func TestResolveWrite_Forbidden(t *testing.T) {
	text := "!>>a<<! middle !>>b<<!"
	snap := Scan(text).BuildSnapshot()
	_, ok := snap.ResolveWrite("X")
	if ok {
		t.Error("forbidden policy should refuse write")
	}
}

// ── Escape handling ──────────────────────────────────────────────────────

func TestEscape_Backslash(t *testing.T) {
	text := `talk about \@>> and \<<@ as text`
	p := Scan(text)
	if len(p.Spans) != 0 {
		t.Errorf("want 0 spans for escaped markers, got %d", len(p.Spans))
	}
}

func TestEscape_Quotes(t *testing.T) {
	text := `talk about "@>>" and "<<@" as text`
	p := Scan(text)
	if len(p.Spans) != 0 {
		t.Errorf("want 0 spans for quoted markers, got %d", len(p.Spans))
	}
}

// ── $>><<$ (protection) ──────────────────────────────────────────────────

func TestBuildSnapshot_ReadOnly_Empty(t *testing.T) {
	text := "before $>><<$ after"
	snap := Scan(text).BuildSnapshot()
	want := "before <!--PROTECTED #1: NO EDITS HERE--> after"
	if snap.Visible != want {
		t.Errorf("got %q, want %q", snap.Visible, want)
	}
	if len(snap.Protected) != 0 {
		t.Errorf("empty $>><<$ should produce no Protected range, got %+v", snap.Protected)
	}
	if !snap.HasShaping {
		t.Errorf("$>><<$ should set HasShaping")
	}
	if snap.WritePolicy != WritePolicyForbidden {
		t.Errorf("$>><<$ should forbid note_write, got %v", snap.WritePolicy)
	}
}

func TestBuildSnapshot_ReadOnly_NonEmpty(t *testing.T) {
	text := "before $>>locked<<$ after"
	snap := Scan(text).BuildSnapshot()
	want := "before <!--PROTECTED #1 start: NO EDITS-->locked<!--PROTECTED #1 end--> after"
	if snap.Visible != want {
		t.Errorf("got %q, want %q", snap.Visible, want)
	}
	if len(snap.Protected) != 1 {
		t.Fatalf("expected 1 Protected range, got %d", len(snap.Protected))
	}
}

func TestResolveEdit_HitsProtected_ReturnsProtected(t *testing.T) {
	text := "before $>>locked<<$ after"
	snap := Scan(text).BuildSnapshot()
	_, count, conflict := snap.ResolveEdit("locked", "X", false)
	if conflict != EditConflictProtected {
		t.Errorf("expected Protected, got %v", conflict)
	}
	if count != 1 {
		t.Errorf("expected 1 protected hit count, got %d", count)
	}
}

func TestResolveEdit_EditOutsideProtected_OK(t *testing.T) {
	text := "before $>>locked<<$ after"
	snap := Scan(text).BuildSnapshot()
	updated, count, conflict := snap.ResolveEdit("after", "AFTER", false)
	if conflict != EditConflictNone || count != 1 {
		t.Fatalf("expected single clean match, got count=%d conflict=%v", count, conflict)
	}
	if updated != "before $>>locked<<$ AFTER" {
		t.Errorf("got %q", updated)
	}
}

func TestResolveEdit_PartialOverlapProtected_ReturnsProtected(t *testing.T) {
	text := "abc$>>locked<<$xyz"
	snap := Scan(text).BuildSnapshot()
	// "locke" is entirely inside protected → blocked.
	_, _, conflict := snap.ResolveEdit("locke", "X", false)
	if conflict != EditConflictProtected {
		t.Errorf("expected Protected, got %v", conflict)
	}
}

func TestResolveEdit_VisibleCommentNotMappable(t *testing.T) {
	text := "$>>x<<$"
	snap := Scan(text).BuildSnapshot()
	// The PROTECTED comment text isn't in any source segment, so any
	// old_string that includes it should fail (not even valid in source map).
	_, _, conflict := snap.ResolveEdit("<!--PROTECTED #1 start: NO EDITS-->x", "y", false)
	if conflict != EditConflictNotFound {
		t.Errorf("expected NotFound for old_string crossing comment text, got %v", conflict)
	}
}

func TestResolveEdit_ReplaceAll_SkipsProtected(t *testing.T) {
	text := "foo $>>foo<<$ foo"
	snap := Scan(text).BuildSnapshot()
	updated, count, conflict := snap.ResolveEdit("foo", "X", true)
	if conflict != EditConflictNone || count != 2 {
		t.Fatalf("expected 2 replacements outside protected, got count=%d conflict=%v", count, conflict)
	}
	if updated != "X $>>foo<<$ X" {
		t.Errorf("got %q", updated)
	}
}

func TestBuildSnapshot_ReadOnly_NestedInsideIncludeOnly(t *testing.T) {
	text := "!>> visible $>>locked<<$ end <<!"
	snap := Scan(text).BuildSnapshot()
	if !strings.Contains(snap.Visible, "<!--PROTECTED #1 start: NO EDITS-->locked<!--PROTECTED #1 end-->") {
		t.Errorf("nested $>><<$ inside !>><<! should render: %q", snap.Visible)
	}
	if len(snap.Protected) != 1 {
		t.Errorf("nested $>><<$ should be tracked, got %d ranges", len(snap.Protected))
	}
}

func TestBuildSnapshot_ReadOnly_BuriedInExclude_Skipped(t *testing.T) {
	text := "%>> $>>buried<<$ <<% visible"
	snap := Scan(text).BuildSnapshot()
	if strings.Contains(snap.Visible, "PROTECTED") {
		t.Errorf("$>><<$ buried in %%>><<%% should not render: %q", snap.Visible)
	}
	if len(snap.Protected) != 0 {
		t.Errorf("buried $>><<$ should not track protection, got %d ranges", len(snap.Protected))
	}
}

func TestBuildSnapshot_NestedReadOnly(t *testing.T) {
	text := "$>>outer $>>inner<<$ end<<$"
	snap := Scan(text).BuildSnapshot()
	if !strings.Contains(snap.Visible, "<!--PROTECTED #1 start: NO EDITS-->") {
		t.Errorf("outer $>><<$ should render: %q", snap.Visible)
	}
	if !strings.Contains(snap.Visible, "<!--PROTECTED #2 start: NO EDITS-->") {
		t.Errorf("inner $>><<$ should render: %q", snap.Visible)
	}
	if len(snap.Protected) != 2 {
		t.Errorf("both nested $>><<$ should be tracked, got %d", len(snap.Protected))
	}
}

func TestResolveWrite_ReadOnly_Forbidden(t *testing.T) {
	text := "before $>>locked<<$ after"
	snap := Scan(text).BuildSnapshot()
	_, ok := snap.ResolveWrite("anything")
	if ok {
		t.Error("note_write should be forbidden when $>><<$ is present")
	}
}

// ── ApplyEdits (note_edit_at slot apply) ─────────────────────────────────

func TestApplyEdits_EmptyToFilled(t *testing.T) {
	text := "before @>><<@ after"
	p := Scan(text)
	out, applied, err := p.ApplyEdits(text, map[string]string{"1": "filled"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if out != "before @>>filled<<@ after" {
		t.Errorf("got %q", out)
	}
	if len(applied) != 1 || applied[0] != 1 {
		t.Errorf("want applied=[1], got %v", applied)
	}
}

func TestApplyEdits_MultipleOrder(t *testing.T) {
	text := "@>><<@ X @>><<@ Y @>><<@"
	p := Scan(text)
	out, _, err := p.ApplyEdits(text, map[string]string{"1": "one", "2": "two", "3": "three"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if out != "@>>one<<@ X @>>two<<@ Y @>>three<<@" {
		t.Errorf("got %q", out)
	}
}

func TestApplyEdits_BadSlot(t *testing.T) {
	text := "@>><<@"
	p := Scan(text)
	_, _, err := p.ApplyEdits(text, map[string]string{"7": "x"})
	if err == nil || !strings.Contains(err.Error(), "#7") {
		t.Errorf("expected error naming #7, got %v", err)
	}
}

func TestApplyEdits_StripsScopeComments(t *testing.T) {
	text := "@>><<@"
	p := Scan(text)
	out, _, err := p.ApplyEdits(text, map[string]string{
		"1": "<!--Write #1 location-->hello<!--Rewrite #1 start-->world<!--Rewrite #1 end-->",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if out != "@>>helloworld<<@" {
		t.Errorf("got %q", out)
	}
}

// ── StripMarkers ──────────────────────────────────────────────────────────────

func TestStripMarkers_SingleKind(t *testing.T) {
	in := "before @>>scoped<<@ after"
	out := StripMarkers(in, KindWriteScope)
	want := "before scoped after"
	if out != want {
		t.Errorf("got %q, want %q", out, want)
	}
}

func TestStripMarkers_MultipleKinds(t *testing.T) {
	in := "a @>>w<<@ b !>>i<<! c %>>e<<% d $>>r<<$ e"
	out := StripMarkers(in, KindWriteScope, KindIncludeOnly)
	want := "a w b i c %>>e<<% d $>>r<<$ e"
	if out != want {
		t.Errorf("got %q, want %q", out, want)
	}
}

func TestStripMarkers_AllKinds(t *testing.T) {
	in := "@>>w<<@ %>>e<<% !>>i<<! $>>r<<$"
	out := StripMarkers(in, KindWriteScope, KindExclude, KindIncludeOnly, KindReadOnly)
	want := "w e i r"
	if out != want {
		t.Errorf("got %q, want %q", out, want)
	}
}

func TestStripMarkers_Nested(t *testing.T) {
	// Clearing @>><<@ from text where @>><<@ is nested inside %>><<%.
	in := "%>>outer @>>inner<<@ tail<<%"
	out := StripMarkers(in, KindWriteScope)
	want := "%>>outer inner tail<<%"
	if out != want {
		t.Errorf("got %q, want %q", out, want)
	}
}

func TestStripMarkers_EmptyWrap(t *testing.T) {
	in := "before @>><<@ after"
	out := StripMarkers(in, KindWriteScope)
	want := "before  after"
	if out != want {
		t.Errorf("got %q, want %q", out, want)
	}
}

func TestStripMarkers_NoMatch(t *testing.T) {
	in := "@>>scoped<<@"
	out := StripMarkers(in, KindReadOnly)
	if out != in {
		t.Errorf("expected unchanged, got %q", out)
	}
}

func TestStripMarkers_OrphanTokenSurvives(t *testing.T) {
	// Unmatched opener is not in Spans, so it survives the strip.
	in := "stray @>> with no close"
	out := StripMarkers(in, KindWriteScope)
	if out != in {
		t.Errorf("expected orphan to survive, got %q", out)
	}
}

func TestStripMarkers_EscapedTokenSurvives(t *testing.T) {
	in := `escaped \@>>body\<<@ stays`
	out := StripMarkers(in, KindWriteScope)
	if out != in {
		t.Errorf("expected escaped tokens to survive, got %q", out)
	}
}

// ── HighlightRanges ───────────────────────────────────────────────────────────

// colorAt returns the color in m at byte offset i, or 0 if not present.
func colorAt(ranges []HighlightRange, i int) int {
	for _, r := range ranges {
		if i >= r.Start && i < r.End {
			return r.Color
		}
	}
	return 0
}

func TestHighlightRanges_NoMarkers(t *testing.T) {
	p := Scan("plain text")
	bg, ul := p.HighlightRanges()
	if len(bg) != 0 || len(ul) != 0 {
		t.Errorf("expected empty ranges for plain text")
	}
}

func TestHighlightRanges_SingleInclude(t *testing.T) {
	// !>>body<<!  — tokens get color 6 bg, body gets color 6 ul.
	text := "!>>body<<!!"
	// text bytes: !>>  (0-2), body (3-6), <<! (7-9), ! (10) — the last ! is not a closer
	// Actually: "!>>" is bytes 0,1,2; "body" is 3,4,5,6; "<<!" is 7,8,9; "!" is 10
	// Wait let me re-check the token lengths: "!>>" = 3 bytes, "<<!" = 3 bytes
	text = "!>>body<<!"
	p := Scan(text)
	if len(p.Spans) != 1 {
		t.Fatalf("want 1 span, got %d", len(p.Spans))
	}
	bg, ul := p.HighlightRanges()

	// Opener "!>>" at 0-2 → bg color 6
	for i := 0; i < 3; i++ {
		if colorAt(bg, i) != 6 {
			t.Errorf("bg[%d] want 6, got %d", i, colorAt(bg, i))
		}
		if colorAt(ul, i) != 0 {
			t.Errorf("ul[%d] want 0 (token, not body), got %d", i, colorAt(ul, i))
		}
	}
	// Body "body" at 3-6 → ul color 6
	for i := 3; i < 7; i++ {
		if colorAt(ul, i) != 6 {
			t.Errorf("ul[%d] want 6, got %d", i, colorAt(ul, i))
		}
		if colorAt(bg, i) != 0 {
			t.Errorf("bg[%d] want 0 (body, not token), got %d", i, colorAt(bg, i))
		}
	}
	// Closer "<<!" at 7-9 → bg color 6
	for i := 7; i < 10; i++ {
		if colorAt(bg, i) != 6 {
			t.Errorf("bg[%d] want 6, got %d", i, colorAt(bg, i))
		}
	}
}

// TestHighlightRanges_ExcludeOverridesReadOnly verifies the user's example:
// %>> text $>> text <<$ <<% — entire interior underlined as ANSI 1 because
// $>><<$ is nullified by %>><<%`.
func TestHighlightRanges_ExcludeOverridesReadOnly(t *testing.T) {
	text := "%>>outer $>>inner<<$ outer<<%"
	// Byte layout:
	//   %>>  = 0,1,2
	//   outer  = 3-7
	//   " "  = 8
	//   $>>  = 9,10,11
	//   inner = 12-16
	//   <<$  = 17,18,19
	//   " outer" = 20-25
	//   <<%  = 26,27,28
	p := Scan(text)
	if len(p.Spans) != 2 {
		t.Fatalf("want 2 spans, got %d — text: %q", len(p.Spans), text)
	}
	bg, ul := p.HighlightRanges()

	// %>> opener → bg color 1
	if colorAt(bg, 0) != 1 {
		t.Errorf("bg[0] (%%>> opener) want 1, got %d", colorAt(bg, 0))
	}
	// "inner" bytes inside $>><<$ which is inside %>><<% — body of % → ul color 1
	inner := strings.Index(text, "inner")
	if inner < 0 {
		t.Fatal("inner not found")
	}
	for i := inner; i < inner+5; i++ {
		if colorAt(ul, i) != 1 {
			t.Errorf("ul[%d] (inner, inside %%>>%%) want 1, got %d", i, colorAt(ul, i))
		}
	}
	// $>> token bytes — $>><<$ is nullified by %>><<%, so these chars are just
	// body bytes of %, underlined with color 1 (not bg color 5).
	dollar := strings.Index(text, "$>>")
	if dollar < 0 {
		t.Fatal("$>> not found")
	}
	if colorAt(ul, dollar) != 1 {
		t.Errorf("ul[%d] ($>> inside %%>>) want 1 (body of %%), got %d", dollar, colorAt(ul, dollar))
	}
	if colorAt(bg, dollar) != 0 {
		t.Errorf("bg[%d] ($>> inside %%>>) want 0 ($span inactive), got %d", dollar, colorAt(bg, dollar))
	}
}

// TestHighlightRanges_IncludeWithWriteScope verifies: !>> text @>> inner <<@ text <<!
// outer text → ul color 6, inner text → ul color 2 (both active, innermost wins).
func TestHighlightRanges_IncludeWithWriteScope(t *testing.T) {
	text := "!>>outer @>>inner<<@ outer<<!"
	p := Scan(text)
	if len(p.Spans) != 2 {
		t.Fatalf("want 2 spans, got %d", len(p.Spans))
	}
	bg, ul := p.HighlightRanges()

	outerIdx := strings.Index(text, "outer @>>")
	innerIdx := strings.Index(text, "inner")
	outer2Idx := strings.Index(text, " outer<<!") + 1 // skip the leading space

	if outerIdx < 0 || innerIdx < 0 || outer2Idx < 0 {
		t.Fatalf("could not locate test substrings in %q", text)
	}

	// "outer" body of !>><<! (before @>>) → ul color 6
	if colorAt(ul, outerIdx) != 6 {
		t.Errorf("ul[outer] want 6, got %d", colorAt(ul, outerIdx))
	}
	// "inner" body of @>><<@ → ul color 2 (@ wins over !)
	if colorAt(ul, innerIdx) != 2 {
		t.Errorf("ul[inner] want 2, got %d", colorAt(ul, innerIdx))
	}
	// "outer" after <<@ → ul color 6 again
	if colorAt(ul, outer2Idx) != 6 {
		t.Errorf("ul[outer2] want 6, got %d", colorAt(ul, outer2Idx))
	}
	// @>> token bytes → bg color 2
	atIdx := strings.Index(text, "@>>")
	if colorAt(bg, atIdx) != 2 {
		t.Errorf("bg[@>>] want 2, got %d", colorAt(bg, atIdx))
	}
}
