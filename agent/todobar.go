package agent

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/bubbles/textarea"
	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"

	"github.com/ejumper/aunic/todos"
)

// TodoSubmitMsg is emitted when the user confirms a prompt + todo list. The
// Todos slice is the final, non-empty, trimmed list of task texts in order.
type TodoSubmitMsg struct {
	Prompt string
	Todos  []string
}

// TodoBarClosedMsg is emitted when the user dismisses the bar without
// submitting (esc or clicking the [x] in the title row).
type TodoBarClosedMsg struct{}

type todoZone int

const (
	todoZonePrompt todoZone = iota
	todoZoneTodos
	todoZoneButtons
)

// TodoBar is the multi-section authoring modal that replaces the prompt box
// while /todo is active. It contains a multi-line prompt textarea, a slice of
// single-line todo inputs, and a button row with [+] and [x] to add or
// remove todo rows.
type TodoBar struct {
	prompt     textarea.Model
	todoInputs []textinput.Model

	zone   todoZone
	rowIdx int // 0..len(todoInputs)-1 when zone == todoZoneTodos
	btnIdx int // 0=[+], 1=[x] when zone == todoZoneButtons

}

// NewTodoBar constructs a TodoBar pre-populated from existing. When existing
// is empty, a single blank todo input is created. Focus starts on the prompt
// textarea.
func NewTodoBar(existing []todos.Todo) TodoBar {
	ta := textarea.New()
	ta.MaxHeight = 0
	ta.ShowLineNumbers = false
	ta.SetHeight(3)
	_ = ta.Focus()

	var inputs []textinput.Model
	if len(existing) == 0 {
		ti := newTodoInput()
		inputs = append(inputs, ti)
	} else {
		for _, t := range existing {
			ti := newTodoInput()
			ti.SetValue(t.Text)
			inputs = append(inputs, ti)
		}
	}
	return TodoBar{
		prompt:     ta,
		todoInputs: inputs,
		zone:       todoZonePrompt,
	}
}

func newTodoInput() textinput.Model {
	ti := textinput.New()
	ti.Prompt = ""
	return ti
}

// Height returns the number of content rows this bar occupies in its current
// state. Layout: prompt label (1) + textarea height + separator (1) + N todo
// rows + button row (1).
func (tb TodoBar) Height() int {
	return 1 + tb.prompt.Height() + 1 + len(tb.todoInputs) + 1
}

// Update handles key and mouse events for the bar. Returns the (possibly
// mutated) bar and a tea.Cmd for any emitted message.
func (tb TodoBar) Update(msg tea.Msg) (TodoBar, tea.Cmd) {
	switch m := msg.(type) {
	case tea.KeyMsg:
		key := m.String()
		switch key {
		case "esc":
			return tb, func() tea.Msg { return TodoBarClosedMsg{} }
		case "enter":
			return tb.handleEnter()
		case "up", "shift+tab":
			return tb.moveUp(), nil
		case "down", "tab":
			return tb.moveDown(), nil
		case "left":
			if tb.zone == todoZoneButtons {
				if tb.btnIdx > 0 {
					tb.btnIdx--
				}
				return tb, nil
			}
		case "right":
			if tb.zone == todoZoneButtons {
				if tb.btnIdx < 1 {
					tb.btnIdx++
				}
				return tb, nil
			}
		}
		// Forward the key to whichever input owns focus.
		return tb.forwardKey(m)
	case tea.MouseMsg:
		// Mouse routing — left for app to wire up later if needed.
		return tb, nil
	}
	return tb, nil
}

func (tb TodoBar) handleEnter() (TodoBar, tea.Cmd) {
	switch tb.zone {
	case todoZonePrompt:
		// Forward to textarea to insert a newline.
		var cmd tea.Cmd
		tb.prompt, cmd = tb.prompt.Update(tea.KeyMsg{Type: tea.KeyEnter})
		return tb, cmd
	case todoZoneTodos:
		return tb, tb.submitCmd()
	case todoZoneButtons:
		if tb.btnIdx == 0 {
			return tb.addTodo(), nil
		}
		return tb.removeLastTodo(), nil
	}
	return tb, nil
}

func (tb TodoBar) submitCmd() tea.Cmd {
	prompt := strings.TrimSpace(tb.prompt.Value())
	var out []string
	for _, in := range tb.todoInputs {
		s := strings.TrimSpace(in.Value())
		if s != "" {
			out = append(out, s)
		}
	}
	return func() tea.Msg { return TodoSubmitMsg{Prompt: prompt, Todos: out} }
}

func (tb TodoBar) addTodo() TodoBar {
	ti := newTodoInput()
	tb.todoInputs = append(tb.todoInputs, ti)
	// Move focus to the new row.
	tb.zone = todoZoneTodos
	tb.rowIdx = len(tb.todoInputs) - 1
	tb.refocus()
	return tb
}

func (tb TodoBar) removeLastTodo() TodoBar {
	if len(tb.todoInputs) <= 1 {
		return tb
	}
	tb.todoInputs = tb.todoInputs[:len(tb.todoInputs)-1]
	if tb.zone == todoZoneTodos && tb.rowIdx >= len(tb.todoInputs) {
		tb.rowIdx = len(tb.todoInputs) - 1
	}
	tb.refocus()
	return tb
}

// moveUp transitions focus toward the prompt section.
func (tb TodoBar) moveUp() TodoBar {
	switch tb.zone {
	case todoZonePrompt:
		// Let the textarea consume — caller already invoked moveUp via key, so
		// just forward the key to the textarea so its internal cursor moves.
		var cmd tea.Cmd
		tb.prompt, cmd = tb.prompt.Update(tea.KeyMsg{Type: tea.KeyUp})
		_ = cmd
	case todoZoneTodos:
		if tb.rowIdx > 0 {
			tb.rowIdx--
		} else {
			tb.zone = todoZonePrompt
		}
		tb.refocus()
	case todoZoneButtons:
		tb.zone = todoZoneTodos
		tb.rowIdx = len(tb.todoInputs) - 1
		tb.refocus()
	}
	return tb
}

// moveDown transitions focus away from the prompt section.
func (tb TodoBar) moveDown() TodoBar {
	switch tb.zone {
	case todoZonePrompt:
		// Move to first todo when at last line; otherwise let textarea handle.
		if tb.prompt.Line() == tb.prompt.LineCount()-1 {
			tb.zone = todoZoneTodos
			tb.rowIdx = 0
			tb.refocus()
		} else {
			var cmd tea.Cmd
			tb.prompt, cmd = tb.prompt.Update(tea.KeyMsg{Type: tea.KeyDown})
			_ = cmd
		}
	case todoZoneTodos:
		if tb.rowIdx < len(tb.todoInputs)-1 {
			tb.rowIdx++
		} else {
			tb.zone = todoZoneButtons
			tb.btnIdx = 0
		}
		tb.refocus()
	case todoZoneButtons:
		// Already at the bottom — no further movement.
	}
	return tb
}

func (tb *TodoBar) refocus() {
	for i := range tb.todoInputs {
		if tb.zone == todoZoneTodos && i == tb.rowIdx {
			tb.todoInputs[i].Focus()
		} else {
			tb.todoInputs[i].Blur()
		}
	}
	if tb.zone == todoZonePrompt {
		_ = tb.prompt.Focus()
	} else {
		tb.prompt.Blur()
	}
}

func (tb TodoBar) forwardKey(m tea.KeyMsg) (TodoBar, tea.Cmd) {
	switch tb.zone {
	case todoZonePrompt:
		var cmd tea.Cmd
		tb.prompt, cmd = tb.prompt.Update(m)
		return tb, cmd
	case todoZoneTodos:
		if tb.rowIdx < 0 || tb.rowIdx >= len(tb.todoInputs) {
			return tb, nil
		}
		var cmd tea.Cmd
		tb.todoInputs[tb.rowIdx], cmd = tb.todoInputs[tb.rowIdx].Update(m)
		return tb, cmd
	}
	return tb, nil
}

// View renders Height() lines each exactly innerWidth cells wide.
func (tb TodoBar) View(innerWidth int) []string {
	if innerWidth < 1 {
		innerWidth = 1
	}

	// Layout the textarea width.
	tb.prompt.SetWidth(innerWidth)

	var lines []string

	// Prompt label.
	lines = append(lines, padTo("\x1b[4mPrompt\x1b[0m", innerWidth))

	// Textarea — split its View() into lines and pad each.
	ptaLines := strings.Split(tb.prompt.View(), "\n")
	for _, ln := range ptaLines {
		lines = append(lines, padTo(ln, innerWidth))
	}

	// Separator (a row of '─' the full inner width).
	lines = append(lines, padTo(strings.Repeat("─", innerWidth), innerWidth))

	// Todo rows: "todo N: <input>"
	for i := range tb.todoInputs {
		label := fmt.Sprintf("todo %d: ", i+1)
		inputW := innerWidth - visualWidth(label)
		if inputW < 1 {
			inputW = 1
		}
		tb.todoInputs[i].Width = inputW
		line := label + tb.todoInputs[i].View()
		if tb.zone == todoZoneTodos && i == tb.rowIdx {
			line = "\x1b[7m" + line + "\x1b[0m"
		}
		lines = append(lines, padTo(line, innerWidth))
	}

	// Button row: [+][x]
	addBtn := "[+]"
	delBtn := "[x]"
	if tb.zone == todoZoneButtons && tb.btnIdx == 0 {
		addBtn = "\x1b[7m" + addBtn + "\x1b[0m"
	}
	if tb.zone == todoZoneButtons && tb.btnIdx == 1 {
		delBtn = "\x1b[7m" + delBtn + "\x1b[0m"
	}
	lines = append(lines, padTo(addBtn+delBtn, innerWidth))

	return lines
}
