package editor

import (
	"strings"
	"testing"
)

// TestReplaceAllMatches_LargeBuffer locks in the strings.Builder rewrite of
// ReplaceAllMatches. The pre-fix version concatenated full-buffer slices in a
// loop (O(N·L)); this test only verifies correctness — the speedup is observable
// but not asserted (it's an implementation detail).
func TestReplaceAllMatches_LargeBuffer(t *testing.T) {
	// Build a 1000-line buffer with one match per line.
	var sb strings.Builder
	for i := 0; i < 1000; i++ {
		sb.WriteString("line FOO and more text\n")
	}
	content := sb.String()

	m := New("test.md", content)
	m.SetSearch("FOO", true)
	if got := m.SearchMatchCount(); got != 1000 {
		t.Fatalf("SearchMatchCount = %d, want 1000", got)
	}

	res := m.ReplaceAllMatches("BAR")
	if res.Count != 0 {
		t.Fatalf("after replace, Count = %d, want 0 remaining matches of FOO", res.Count)
	}
	if strings.Contains(m.Value(), "FOO") {
		t.Fatalf("FOO still present in buffer after ReplaceAllMatches")
	}
	wantBars := strings.Count(m.Value(), "BAR")
	if wantBars != 1000 {
		t.Fatalf("BAR count = %d, want 1000", wantBars)
	}
}

// TestReplaceAllMatches_PreservesSurrounding verifies that ReplaceAllMatches
// does not corrupt content between matches (regression guard for the
// strings.Builder rewrite — easy to get prev/byteFrom slicing wrong).
func TestReplaceAllMatches_PreservesSurrounding(t *testing.T) {
	m := New("t.md", "alpha XX beta XX gamma XX delta")
	m.SetSearch("XX", true)
	m.ReplaceAllMatches("YY")
	got := m.Value()
	want := "alpha YY beta YY gamma YY delta"
	if got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

// TestReplaceAllMatches_DifferentLengths covers the case where replacement
// length differs from match length — the Builder.Grow estimate is approximate
// and must not affect correctness.
func TestReplaceAllMatches_DifferentLengths(t *testing.T) {
	m := New("t.md", "a X b X c X d")
	m.SetSearch("X", true)
	m.ReplaceAllMatches("LONGER")
	got := m.Value()
	want := "a LONGER b LONGER c LONGER d"
	if got != want {
		t.Fatalf("got %q, want %q", got, want)
	}

	m2 := New("t.md", "a LONGER b LONGER c LONGER d")
	m2.SetSearch("LONGER", true)
	m2.ReplaceAllMatches("X")
	got2 := m2.Value()
	want2 := "a X b X c X d"
	if got2 != want2 {
		t.Fatalf("got %q, want %q", got2, want2)
	}
}
