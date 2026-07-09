// Package todos holds the persistent todo list that lives in the "## Todos"
// section at the bottom of a note file's transcript area. Todos are rendered
// as a markdown checkbox list (e.g. "- [ ] #1 Write tests") so the file stays
// human-readable and portable. The model interacts with this list via the
// todo_write and todo_done tools in the runner package; the list itself is
// surfaced to the model by appending it to the user prompt text at run start.
package todos

import (
	"fmt"
	"strconv"
	"strings"
)

type Todo struct {
	ID   int    `json:"id"`
	Text string `json:"text"`
	Done bool   `json:"done"`
}

// Parse reads the body of a "## Todos" section (without the heading line) and
// returns the todo list. Recognizes lines of the form:
//
//   - [ ] #N text
//   - [x] #N text
//
// Lines that don't match are skipped silently. Returns nil if no items found.
func Parse(text string) []Todo {
	var items []Todo
	for _, raw := range strings.Split(text, "\n") {
		ln := strings.TrimSpace(raw)
		if !strings.HasPrefix(ln, "- [") {
			continue
		}
		if len(ln) < len("- [ ] #1 x") {
			continue
		}
		// Mark character at index 3 ("- [X]" → 'X' or ' ').
		mark := ln[3]
		if mark != ' ' && mark != 'x' && mark != 'X' {
			continue
		}
		rest := strings.TrimPrefix(ln[4:], "]")
		rest = strings.TrimLeft(rest, " ")
		if !strings.HasPrefix(rest, "#") {
			continue
		}
		rest = rest[1:]
		// Read digits for the ID.
		i := 0
		for i < len(rest) && rest[i] >= '0' && rest[i] <= '9' {
			i++
		}
		if i == 0 {
			continue
		}
		id, err := strconv.Atoi(rest[:i])
		if err != nil {
			continue
		}
		body := strings.TrimSpace(rest[i:])
		items = append(items, Todo{
			ID:   id,
			Text: body,
			Done: mark == 'x' || mark == 'X',
		})
	}
	return items
}

// Render emits the todos as a markdown checkbox list. Returns "" for an empty
// list so callers can omit the section entirely.
func Render(items []Todo) string {
	if len(items) == 0 {
		return ""
	}
	var b strings.Builder
	for i, t := range items {
		mark := " "
		if t.Done {
			mark = "x"
		}
		fmt.Fprintf(&b, "- [%s] #%d %s", mark, t.ID, t.Text)
		if i < len(items)-1 {
			b.WriteByte('\n')
		}
	}
	return b.String()
}
