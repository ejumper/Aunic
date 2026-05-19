package todos

// AssignIDs takes a list of text items the model wants to track and assigns
// sequential IDs starting from 1. Used when todo_write replaces the list
// wholesale.
func AssignIDs(texts []string) []Todo {
	out := make([]Todo, 0, len(texts))
	for i, t := range texts {
		out = append(out, Todo{
			ID:   i + 1,
			Text: t,
			Done: false,
		})
	}
	return out
}

// AllDone reports whether every item has Done=true. Returns false for an empty
// list so an empty list never triggers auto-clear.
func AllDone(items []Todo) bool {
	if len(items) == 0 {
		return false
	}
	for _, t := range items {
		if !t.Done {
			return false
		}
	}
	return true
}

// MarkDone returns a new slice with the item matching id set to Done=true.
// The second return is true if an item with that ID existed.
func MarkDone(items []Todo, id int) ([]Todo, bool) {
	out := make([]Todo, len(items))
	found := false
	for i, t := range items {
		if t.ID == id {
			t.Done = true
			found = true
		}
		out[i] = t
	}
	return out, found
}
