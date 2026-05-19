package todos

import "strings"

// PromptBlock renders the active todos as a text block appended to the user
// prompt at run start. Returns "" for an empty list. The trailing instruction
// is intentionally short — the tool descriptions carry the detail; this is
// just orientation in the user message itself.
func PromptBlock(items []Todo) string {
	if len(items) == 0 {
		return ""
	}
	var b strings.Builder
	b.WriteString("--- Active Todos ---\n")
	b.WriteString(Render(items))
	b.WriteString("\nUse the todo_done tool with the todo's ID number to check off items as you complete them.")
	return b.String()
}
