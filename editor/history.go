package editor

// Delta-based undo/redo with 1-second time bucketing, modeled after the micro
// editor. Each edit produces an editEvent capturing only the changed runes
// (not a full snapshot) plus the cursor positions before and after the edit.
// On ctrl+z we pop all events sharing the top event's 1-second wall-clock
// bucket and apply their inverses in reverse order — so typing a word fast
// undoes as a single group.

const (
	historyCap      = 1000 // maximum number of events retained
	historyBucketMs = 1000 // grouping window in milliseconds
)

// editEvent is one contiguous change to the buffer. removed is the text that
// was there before; inserted is the text that's there after. runeOffset is the
// rune index into the *pre-edit* buffer where the change starts.
type editEvent struct {
	runeOffset   int
	removed      []rune
	inserted     []rune
	cursorBefore position
	cursorAfter  position
	timestamp    int64 // unix millis
}

// history is the undo + redo stack pair. Undo holds individual events;
// redo holds pre-grouped slices so we don't have to re-bucket on redo.
type history struct {
	undo []editEvent
	redo [][]editEvent
}

// bucketOf rounds t down to the nearest 1-second wall-clock boundary.
func bucketOf(t int64) int64 {
	return t - (t % historyBucketMs)
}

// diffRunes returns the single contiguous change between prev and cur as an
// editEvent. The bool is false when prev == cur (no change to record).
//
// The algorithm finds the longest common prefix and longest common suffix
// at the rune level (so multibyte UTF-8 codepoints stay intact), and the
// remaining slices in the middle are what was removed and inserted.
func diffRunes(prev, cur string) (editEvent, bool) {
	if prev == cur {
		return editEvent{}, false
	}
	a := []rune(prev)
	b := []rune(cur)

	// Common prefix.
	i := 0
	for i < len(a) && i < len(b) && a[i] == b[i] {
		i++
	}

	// Common suffix, bounded so it doesn't overlap the prefix on either side.
	ja, jb := len(a), len(b)
	for ja > i && jb > i && a[ja-1] == b[jb-1] {
		ja--
		jb--
	}

	return editEvent{
		runeOffset: i,
		removed:    append([]rune(nil), a[i:ja]...),
		inserted:   append([]rune(nil), b[i:jb]...),
	}, true
}

// applyForward returns buf with ev's removed slice replaced by ev's inserted
// slice. Used on redo.
func applyForward(buf string, ev editEvent) string {
	r := []rune(buf)
	head := r[:ev.runeOffset]
	tail := r[ev.runeOffset+len(ev.removed):]
	out := make([]rune, 0, len(head)+len(ev.inserted)+len(tail))
	out = append(out, head...)
	out = append(out, ev.inserted...)
	out = append(out, tail...)
	return string(out)
}

// applyInverse returns buf with ev's inserted slice replaced by ev's removed
// slice. Used on undo.
func applyInverse(buf string, ev editEvent) string {
	r := []rune(buf)
	head := r[:ev.runeOffset]
	tail := r[ev.runeOffset+len(ev.inserted):]
	out := make([]rune, 0, len(head)+len(ev.removed)+len(tail))
	out = append(out, head...)
	out = append(out, ev.removed...)
	out = append(out, tail...)
	return string(out)
}

// push appends an event to the undo stack and clears the redo stack. The
// oldest event is dropped if the stack exceeds historyCap.
func (h *history) push(ev editEvent) {
	h.undo = append(h.undo, ev)
	if len(h.undo) > historyCap {
		h.undo = h.undo[len(h.undo)-historyCap:]
	}
	h.redo = nil
}

// popUndoGroup pops all events sharing the top event's 1-second bucket and
// returns them in pop order (most-recent first). Returns nil if the stack is
// empty.
func (h *history) popUndoGroup() []editEvent {
	if len(h.undo) == 0 {
		return nil
	}
	bucket := bucketOf(h.undo[len(h.undo)-1].timestamp)
	var group []editEvent
	for len(h.undo) > 0 {
		top := h.undo[len(h.undo)-1]
		if bucketOf(top.timestamp) != bucket {
			break
		}
		h.undo = h.undo[:len(h.undo)-1]
		group = append(group, top)
	}
	return group
}

// popRedoGroup pops the most recent redo group as a slice. Returns nil if the
// redo stack is empty. The slice is in pop order from the original undo (i.e.
// most-recent event first), so redo must iterate it in reverse to apply
// oldest-first.
func (h *history) popRedoGroup() []editEvent {
	if len(h.redo) == 0 {
		return nil
	}
	group := h.redo[len(h.redo)-1]
	h.redo = h.redo[:len(h.redo)-1]
	return group
}

// pushRedoGroup pushes a group onto the redo stack. Used during undo.
func (h *history) pushRedoGroup(group []editEvent) {
	h.redo = append(h.redo, group)
}

// pushUndoGroup pushes a group's events onto the undo stack. Used during redo
// to restore the events so a subsequent ctrl+z undoes them again.
func (h *history) pushUndoGroup(group []editEvent) {
	// group is in pop order (most-recent first); append in reverse so the
	// resulting stack order matches the original chronological order.
	for i := len(group) - 1; i >= 0; i-- {
		h.undo = append(h.undo, group[i])
	}
	if len(h.undo) > historyCap {
		h.undo = h.undo[len(h.undo)-historyCap:]
	}
}
