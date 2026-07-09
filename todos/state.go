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
