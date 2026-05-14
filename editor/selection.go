package editor

// position is a textarea-buffer coordinate: row is the logical line index,
// col is the rune offset within that line.
type position struct {
	row int
	col int
}

// before reports whether p comes before q in document order.
func (p position) before(q position) bool {
	if p.row != q.row {
		return p.row < q.row
	}
	return p.col < q.col
}

// selection tracks an active selection range layered over the textarea. The
// anchor is the fixed endpoint set when shift-extension or mouse drag begins;
// the head is always the textarea's current cursor and is read on demand. A
// zero selection (active=false) means no selection.
type selection struct {
	active bool
	anchor position
}

// ordered returns the selection as (start, end) with start <= end in document
// order. head is the current head position (typically the textarea cursor).
func (s selection) ordered(head position) (start, end position) {
	if s.anchor.before(head) || s.anchor == head {
		return s.anchor, head
	}
	return head, s.anchor
}

// isEmpty reports whether anchor and head sit on the same buffer position
// (selection has zero width even though active is true — e.g. immediately
// after starting extension before any motion).
func (s selection) isEmpty(head position) bool {
	return s.anchor == head
}
