package editor

import (
	"reflect"
	"testing"
)

func TestDiffRunes_PureInsert(t *testing.T) {
	ev, ok := diffRunes("abc", "abXc")
	if !ok {
		t.Fatal("expected change, got none")
	}
	if ev.runeOffset != 2 {
		t.Errorf("runeOffset = %d, want 2", ev.runeOffset)
	}
	if len(ev.removed) != 0 {
		t.Errorf("removed = %q, want empty", string(ev.removed))
	}
	if string(ev.inserted) != "X" {
		t.Errorf("inserted = %q, want %q", string(ev.inserted), "X")
	}
}

func TestDiffRunes_PureDelete(t *testing.T) {
	ev, ok := diffRunes("abXc", "abc")
	if !ok {
		t.Fatal("expected change, got none")
	}
	if ev.runeOffset != 2 {
		t.Errorf("runeOffset = %d, want 2", ev.runeOffset)
	}
	if string(ev.removed) != "X" {
		t.Errorf("removed = %q, want %q", string(ev.removed), "X")
	}
	if len(ev.inserted) != 0 {
		t.Errorf("inserted = %q, want empty", string(ev.inserted))
	}
}

func TestDiffRunes_Replace(t *testing.T) {
	ev, ok := diffRunes("abc", "aXc")
	if !ok {
		t.Fatal("expected change, got none")
	}
	if ev.runeOffset != 1 {
		t.Errorf("runeOffset = %d, want 1", ev.runeOffset)
	}
	if string(ev.removed) != "b" {
		t.Errorf("removed = %q, want %q", string(ev.removed), "b")
	}
	if string(ev.inserted) != "X" {
		t.Errorf("inserted = %q, want %q", string(ev.inserted), "X")
	}
}

func TestDiffRunes_Empty(t *testing.T) {
	if _, ok := diffRunes("abc", "abc"); ok {
		t.Error("expected no change for identical strings")
	}
}

func TestDiffRunes_UnicodeBoundary(t *testing.T) {
	// "🙂" is a 4-byte rune; the diff must be rune-aligned, not byte-aligned.
	ev, ok := diffRunes("ab", "a🙂b")
	if !ok {
		t.Fatal("expected change, got none")
	}
	if ev.runeOffset != 1 {
		t.Errorf("runeOffset = %d, want 1", ev.runeOffset)
	}
	if string(ev.inserted) != "🙂" {
		t.Errorf("inserted = %q, want %q", string(ev.inserted), "🙂")
	}
	if len(ev.removed) != 0 {
		t.Errorf("removed = %q, want empty", string(ev.removed))
	}
}

func TestApplyForward_Insert(t *testing.T) {
	ev := editEvent{runeOffset: 2, inserted: []rune("X")}
	got := applyForward("abc", ev)
	if got != "abXc" {
		t.Errorf("applyForward = %q, want %q", got, "abXc")
	}
}

func TestApplyInverse_Insert(t *testing.T) {
	ev := editEvent{runeOffset: 2, inserted: []rune("X")}
	got := applyInverse("abXc", ev)
	if got != "abc" {
		t.Errorf("applyInverse = %q, want %q", got, "abc")
	}
}

func TestApplyRoundTrip(t *testing.T) {
	prev := "hello world"
	cur := "hello cruel world"
	ev, ok := diffRunes(prev, cur)
	if !ok {
		t.Fatal("expected change")
	}
	if got := applyForward(prev, ev); got != cur {
		t.Errorf("forward: got %q, want %q", got, cur)
	}
	if got := applyInverse(cur, ev); got != prev {
		t.Errorf("inverse: got %q, want %q", got, prev)
	}
}

func TestHistoryPushClearsRedo(t *testing.T) {
	h := &history{}
	h.push(editEvent{timestamp: 1000})
	h.pushRedoGroup([]editEvent{{timestamp: 1000}})
	if len(h.redo) != 1 {
		t.Fatalf("setup: redo len = %d, want 1", len(h.redo))
	}
	h.push(editEvent{timestamp: 2000})
	if len(h.redo) != 0 {
		t.Errorf("redo not cleared after push: len = %d", len(h.redo))
	}
}

func TestBucketGrouping_SameSecond(t *testing.T) {
	h := &history{}
	// Three events all in the [1000, 2000) bucket.
	h.push(editEvent{timestamp: 1100})
	h.push(editEvent{timestamp: 1500})
	h.push(editEvent{timestamp: 1900})
	group := h.popUndoGroup()
	if len(group) != 3 {
		t.Errorf("group len = %d, want 3", len(group))
	}
	if len(h.undo) != 0 {
		t.Errorf("undo stack not empty: len = %d", len(h.undo))
	}
}

func TestBucketGrouping_CrossSecond(t *testing.T) {
	h := &history{}
	// Two distinct buckets: [1000,2000) and [3000,4000).
	h.push(editEvent{timestamp: 1500})
	h.push(editEvent{timestamp: 3500})
	group := h.popUndoGroup()
	if len(group) != 1 {
		t.Errorf("first group len = %d, want 1", len(group))
	}
	if group[0].timestamp != 3500 {
		t.Errorf("first group ts = %d, want 3500", group[0].timestamp)
	}
	group2 := h.popUndoGroup()
	if len(group2) != 1 {
		t.Errorf("second group len = %d, want 1", len(group2))
	}
	if group2[0].timestamp != 1500 {
		t.Errorf("second group ts = %d, want 1500", group2[0].timestamp)
	}
}

func TestBucketGrouping_AlignedBoundary(t *testing.T) {
	h := &history{}
	// 1000 and 1999 are both in bucket [1000,2000). 2000 is in [2000,3000).
	h.push(editEvent{timestamp: 1000})
	h.push(editEvent{timestamp: 1999})
	h.push(editEvent{timestamp: 2000})
	g1 := h.popUndoGroup()
	if len(g1) != 1 || g1[0].timestamp != 2000 {
		t.Errorf("g1 = %+v, want single 2000", g1)
	}
	g2 := h.popUndoGroup()
	if len(g2) != 2 {
		t.Errorf("g2 len = %d, want 2", len(g2))
	}
}

func TestStackCap(t *testing.T) {
	h := &history{}
	for i := 0; i < historyCap+500; i++ {
		h.push(editEvent{timestamp: int64(i * 10)})
	}
	if len(h.undo) != historyCap {
		t.Errorf("undo len = %d, want %d", len(h.undo), historyCap)
	}
	// Oldest entries dropped — the top should still be the newest.
	if h.undo[len(h.undo)-1].timestamp != int64((historyCap+500-1)*10) {
		t.Errorf("top entry not preserved")
	}
}

func TestPopEmpty(t *testing.T) {
	h := &history{}
	if g := h.popUndoGroup(); g != nil {
		t.Errorf("popUndoGroup on empty: got %+v, want nil", g)
	}
	if g := h.popRedoGroup(); g != nil {
		t.Errorf("popRedoGroup on empty: got %+v, want nil", g)
	}
}

func TestPushUndoGroupReversesPopOrder(t *testing.T) {
	// pushUndoGroup must restore chronological order on the undo stack —
	// group is in pop order (most-recent first), so the oldest event should
	// end up deepest in the stack.
	h := &history{}
	group := []editEvent{
		{timestamp: 3000}, // most recent (pop order)
		{timestamp: 2000},
		{timestamp: 1000}, // oldest
	}
	h.pushUndoGroup(group)

	// Top of stack should be 3000 again.
	if got := h.undo[len(h.undo)-1].timestamp; got != 3000 {
		t.Errorf("top = %d, want 3000", got)
	}
	// Deepest should be 1000.
	if got := h.undo[0].timestamp; got != 1000 {
		t.Errorf("bottom = %d, want 1000", got)
	}
}

func TestUndoRedoRoundTrip(t *testing.T) {
	// Simulate three sequential edits at different timestamps; verify that
	// applying the inverse of the popped group, then the forward of the
	// pushed group, gets back to the original.
	original := "the quick brown fox"
	buf := original

	// edit 1: "the quick brown fox" -> "the quick BROWN fox"
	cur := "the quick BROWN fox"
	ev1, _ := diffRunes(buf, cur)
	ev1.timestamp = 1000
	buf = cur

	// edit 2: -> "the quick BROWN foxes"
	cur = "the quick BROWN foxes"
	ev2, _ := diffRunes(buf, cur)
	ev2.timestamp = 2500
	buf = cur

	h := &history{}
	h.push(ev1)
	h.push(ev2)

	// Undo once → back to "the quick BROWN fox".
	g := h.popUndoGroup()
	undone := buf
	for _, ev := range g {
		undone = applyInverse(undone, ev)
	}
	if undone != "the quick BROWN fox" {
		t.Errorf("after one undo: %q", undone)
	}
	h.pushRedoGroup(g)

	// Undo again → back to original.
	g = h.popUndoGroup()
	for _, ev := range g {
		undone = applyInverse(undone, ev)
	}
	if undone != original {
		t.Errorf("after two undos: %q, want %q", undone, original)
	}
	h.pushRedoGroup(g)

	// Redo both → back to final.
	redone := undone
	for k := 0; k < 2; k++ {
		g := h.popRedoGroup()
		for i := len(g) - 1; i >= 0; i-- {
			redone = applyForward(redone, g[i])
		}
		h.pushUndoGroup(g)
	}
	if redone != buf {
		t.Errorf("after redo: %q, want %q", redone, buf)
	}
}

func TestEditEventStored_FullCopy(t *testing.T) {
	// Ensure diffRunes returns slices that are not aliased into the input —
	// otherwise subsequent buffer mutations could corrupt history.
	prev := "abcdef"
	cur := "aXcdef"
	ev, _ := diffRunes(prev, cur)
	got := string(ev.removed)
	want := "b"
	if got != want {
		t.Fatalf("removed = %q, want %q", got, want)
	}
	// Mutate ev.removed and ensure no panic / weirdness — independent slice.
	ev.removed[0] = 'Z'
	if !reflect.DeepEqual(ev.inserted, []rune{'X'}) {
		t.Errorf("inserted corrupted after mutating removed")
	}
}
