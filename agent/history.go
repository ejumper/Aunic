package agent

// Delta-based undo/redo with 1-second time bucketing. Copied from the editor
// package so the promptbox has its own independent history stack.

const (
	historyCap      = 1000
	historyBucketMs = 1000
)

type editEvent struct {
	runeOffset   int
	removed      []rune
	inserted     []rune
	cursorBefore position
	cursorAfter  position
	timestamp    int64 // unix millis
}

type history struct {
	undo []editEvent
	redo [][]editEvent
}

func bucketOf(t int64) int64 {
	return t - (t % historyBucketMs)
}

func diffRunes(prev, cur string) (editEvent, bool) {
	if prev == cur {
		return editEvent{}, false
	}
	a := []rune(prev)
	b := []rune(cur)

	i := 0
	for i < len(a) && i < len(b) && a[i] == b[i] {
		i++
	}

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

func (h *history) push(ev editEvent) {
	h.undo = append(h.undo, ev)
	if len(h.undo) > historyCap {
		h.undo = h.undo[len(h.undo)-historyCap:]
	}
	h.redo = nil
}

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

func (h *history) popRedoGroup() []editEvent {
	if len(h.redo) == 0 {
		return nil
	}
	group := h.redo[len(h.redo)-1]
	h.redo = h.redo[:len(h.redo)-1]
	return group
}

func (h *history) pushRedoGroup(group []editEvent) {
	h.redo = append(h.redo, group)
}

func (h *history) pushUndoGroup(group []editEvent) {
	for i := len(group) - 1; i >= 0; i-- {
		h.undo = append(h.undo, group[i])
	}
	if len(h.undo) > historyCap {
		h.undo = h.undo[len(h.undo)-historyCap:]
	}
}
