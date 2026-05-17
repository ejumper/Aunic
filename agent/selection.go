package agent

type position struct {
	row int
	col int
}

func (p position) before(q position) bool {
	if p.row != q.row {
		return p.row < q.row
	}
	return p.col < q.col
}

type selection struct {
	active bool
	anchor position
}

func (s selection) ordered(head position) (start, end position) {
	if s.anchor.before(head) || s.anchor == head {
		return s.anchor, head
	}
	return head, s.anchor
}

func (s selection) isEmpty(head position) bool {
	return s.anchor == head
}
