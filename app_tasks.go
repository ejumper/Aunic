package main

import (
	"path/filepath"
	"strings"
	"unicode/utf8"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/ejumper/aunic/agent"
	"github.com/ejumper/aunic/tasks"
)

// taskOverlayMode controls what the task overlay is displaying.
type taskOverlayMode int

const (
	taskModePicker taskOverlayMode = iota // list-of-lists picker
	taskModeList                          // showing tasks in one list
	taskModeAdd                           // inline add-task input
)

// taskOverlayState holds all overlay display state.
type taskOverlayState struct {
	open    bool
	mode    taskOverlayMode
	lists   []tasks.ListConfig // all configured lists
	sorted  []tasks.ListConfig // sorted by mtime for picker
	list    tasks.ListConfig   // active list (taskModeList / taskModeAdd)
	entries []tasks.TaskEntry  // tasks for active list
	cursor  int                // current row index
	input   string             // new task text (taskModeAdd)
	index   *tasks.TaskIndex   // shared index
	err     string             // last write error message
}

// openTaskOverlay initialises and opens the task overlay for the given note
// file. It picks the most-specific configured list for the note's directory;
// if none matches it shows the list picker instead.
func (m appModel) openTaskOverlay() appModel {
	lists := tasks.LoadListConfigs()
	if len(lists) == 0 {
		return m
	}
	idx := tasks.LoadIndex()
	idx.Refresh(lists)
	idx.Save()

	sorted := idx.ListsSortedByMtime(lists)

	// Find most specific list for the current note.
	dir := filepath.Dir(m.filepath)
	best := tasks.BestListForDir(dir, lists)

	ov := taskOverlayState{
		open:   true,
		lists:  lists,
		sorted: sorted,
		index:  idx,
	}

	if best != nil {
		ov.mode = taskModeList
		ov.list = *best
		ov.entries = idx.TasksForList(*best)
		ov.cursor = 0
	} else {
		ov.mode = taskModePicker
		ov.cursor = 0
	}

	m.taskOverlay = ov
	return m
}

// handleTaskOverlayKey routes keyboard events when the task overlay is open.
// Returns (model, cmd, handled).
func (m appModel) handleTaskOverlayKey(msg tea.KeyMsg) (appModel, tea.Cmd, bool) {
	ov := &m.taskOverlay
	if !ov.open {
		return m, nil, false
	}

	switch ov.mode {
	case taskModePicker:
		return m.handlePickerKey(msg)
	case taskModeList:
		return m.handleListKey(msg)
	case taskModeAdd:
		return m.handleAddKey(msg)
	}
	return m, nil, false
}

func (m appModel) handlePickerKey(msg tea.KeyMsg) (appModel, tea.Cmd, bool) {
	ov := &m.taskOverlay
	n := len(ov.sorted)
	switch msg.String() {
	case "esc", "q":
		ov.open = false
	case "up", "k":
		if ov.cursor > 0 {
			ov.cursor--
		}
	case "down", "j":
		if ov.cursor < n-1 {
			ov.cursor++
		}
	case "enter", " ":
		if n > 0 && ov.cursor < n {
			ov.mode = taskModeList
			ov.list = ov.sorted[ov.cursor]
			ov.entries = ov.index.TasksForList(ov.list)
			ov.cursor = 0
			ov.err = ""
		}
	}
	return m, nil, true
}

func (m appModel) handleListKey(msg tea.KeyMsg) (appModel, tea.Cmd, bool) {
	ov := &m.taskOverlay
	n := len(ov.entries)
	switch msg.String() {
	case "esc":
		// Back to picker if multiple lists; close if only one.
		if len(ov.lists) > 1 {
			ov.mode = taskModePicker
			ov.cursor = 0
			ov.err = ""
		} else {
			ov.open = false
		}
	case "q":
		ov.open = false
	case "up", "k":
		if ov.cursor > 0 {
			ov.cursor--
		}
	case "down", "j":
		if ov.cursor < n-1 {
			ov.cursor++
		}
	case "n":
		// Toggle checked/unchecked on current task.
		if n > 0 && ov.cursor < n {
			t := ov.entries[ov.cursor]
			if err := tasks.ToggleTask(t.FilePath, t.LineNumber, !t.Checked); err != nil {
				ov.err = err.Error()
			} else {
				ov.err = ""
				m = m.refreshTaskList()
			}
		}
	case "d":
		// Delete current task.
		if n > 0 && ov.cursor < n {
			t := ov.entries[ov.cursor]
			if err := tasks.DeleteTask(t.FilePath, t.LineNumber); err != nil {
				ov.err = err.Error()
			} else {
				ov.err = ""
				m = m.refreshTaskList()
				if ov.cursor >= len(ov.entries) && ov.cursor > 0 {
					ov.cursor--
				}
			}
		}
	case "a":
		ov.mode = taskModeAdd
		ov.input = ""
		ov.err = ""
	}
	return m, nil, true
}

func (m appModel) handleAddKey(msg tea.KeyMsg) (appModel, tea.Cmd, bool) {
	ov := &m.taskOverlay
	switch msg.String() {
	case "esc":
		ov.mode = taskModeList
		ov.input = ""
		ov.err = ""
	case "enter":
		text := strings.TrimSpace(ov.input)
		if text != "" {
			if err := tasks.AddTask(ov.list, text); err != nil {
				ov.err = err.Error()
			} else {
				ov.err = ""
				ov.mode = taskModeList
				ov.input = ""
				m = m.refreshTaskList()
				// Move cursor to last entry (newly added task).
				if len(ov.entries) > 0 {
					ov.cursor = len(ov.entries) - 1
				}
			}
		} else {
			ov.mode = taskModeList
			ov.input = ""
		}
	case "backspace", "ctrl+h":
		if len(ov.input) > 0 {
			_, size := utf8.DecodeLastRuneInString(ov.input)
			ov.input = ov.input[:len(ov.input)-size]
		}
	default:
		// Append printable characters.
		if len(msg.Runes) > 0 {
			ov.input += string(msg.Runes)
		}
	}
	return m, nil, true
}

// refreshTaskList re-queries the index for the current list (after a write).
func (m appModel) refreshTaskList() appModel {
	ov := &m.taskOverlay
	ov.index.Refresh(ov.lists)
	ov.index.Save()
	ov.entries = ov.index.TasksForList(ov.list)
	ov.sorted = ov.index.ListsSortedByMtime(ov.lists)
	return m
}

// viewTaskOverlay renders the overlay into exactly height lines of width cells.
func (m appModel) viewTaskOverlay(width, height int) []string {
	ov := m.taskOverlay
	switch ov.mode {
	case taskModePicker:
		return renderTaskPicker(ov, width, height)
	case taskModeList:
		return renderTaskList(ov, width, height)
	case taskModeAdd:
		return renderTaskAdd(ov, width, height)
	}
	return padLines(nil, width, height)
}

// ── Renderers ──────────────────────────────────────────────────────────────

func renderTaskPicker(ov taskOverlayState, width, height int) []string {
	var lines []string

	// Header
	title := "Task Lists"
	lines = append(lines, taskHeader(title, width))

	// List entries
	for i, lc := range ov.sorted {
		prefix := "  "
		attr := "\x1b[0m"
		if i == ov.cursor {
			prefix = "▶ "
			attr = "\x1b[7m"
		}
		text := prefix + lc.Title + " (" + lc.Name + ")"
		lines = append(lines, taskPadLine(attr+text+"\x1b[0m", width))
	}

	// Footer help
	if ov.err != "" {
		lines = append(lines, taskPadLine("\x1b[31m"+ov.err+"\x1b[0m", width))
	}
	lines = append(lines, taskFooter("[↑↓] navigate  [Enter] open  [Esc/q] close", width))

	return padLines(lines, width, height)
}

func renderTaskList(ov taskOverlayState, width, height int) []string {
	var lines []string

	// Header
	lines = append(lines, taskHeader(ov.list.Title, width))

	// Task entries — leave 2 rows for footer (+ optional error)
	maxRows := height - 2
	if ov.err != "" {
		maxRows--
	}
	if maxRows < 1 {
		maxRows = 1
	}

	// Scroll window around cursor
	start := 0
	if len(ov.entries) > maxRows && ov.cursor >= maxRows {
		start = ov.cursor - maxRows + 1
	}

	for i := start; i < len(ov.entries) && len(lines)-1 < maxRows; i++ {
		t := ov.entries[i]
		mark := " "
		if t.Checked {
			mark = "x"
		}
		indent := strings.Repeat("  ", t.IndentLevel)
		display := tasks.DisplayText(t.Text)

		suffix := ""
		if t.TimerMinutes > 0 {
			h, mm := t.TimerMinutes/60, t.TimerMinutes%60
			suffix += " \x1b[35m{&" + itoa(h) + "h" + pad2(mm) + "m}\x1b[0m"
		}
		if t.DueRaw != "" {
			suffix += " \x1b[33m{@" + t.DueRaw + "}\x1b[0m"
		}

		raw := indent + "- [" + mark + "] " + display + suffix
		attr := "\x1b[0m"
		if t.Checked {
			attr = "\x1b[2m"
		}
		if i == ov.cursor {
			attr = "\x1b[7m"
			if t.Checked {
				attr = "\x1b[2m\x1b[7m"
			}
		}

		// Strip suffix ANSI for reverse-video selected lines to avoid colour artifacts
		if i == ov.cursor {
			raw = indent + "- [" + mark + "] " + display
			if t.TimerMinutes > 0 {
				h, mm := t.TimerMinutes/60, t.TimerMinutes%60
				raw += " {&" + itoa(h) + "h" + pad2(mm) + "m}"
			}
			if t.DueRaw != "" {
				raw += " {@" + t.DueRaw + "}"
			}
		}

		lines = append(lines, taskPadLine(attr+raw+"\x1b[0m", width))
	}

	if len(ov.entries) == 0 {
		lines = append(lines, taskPadLine("\x1b[2m(no tasks)\x1b[0m", width))
	}

	if ov.err != "" {
		lines = append(lines, taskPadLine("\x1b[31m"+ov.err+"\x1b[0m", width))
	}
	help := "[↑↓] nav  [n] toggle  [d] delete  [a] add  [Esc] back  [q] close"
	lines = append(lines, taskFooter(help, width))

	return padLines(lines, width, height)
}

func renderTaskAdd(ov taskOverlayState, width, height int) []string {
	var lines []string
	lines = append(lines, taskHeader(ov.list.Title+": Add Task", width))
	prompt := "New task: " + ov.input + "▌"
	lines = append(lines, taskPadLine(prompt, width))
	if ov.err != "" {
		lines = append(lines, taskPadLine("\x1b[31m"+ov.err+"\x1b[0m", width))
	}
	lines = append(lines, taskFooter("[Enter] save  [Esc] cancel", width))
	return padLines(lines, width, height)
}

// ── Helpers ───────────────────────────────────────────────────────────────

func taskHeader(title string, width int) string {
	label := "━━━ " + title + " "
	rem := width - agent.VisualWidth(label)
	if rem < 0 {
		rem = 0
	}
	return "\x1b[1m" + label + strings.Repeat("━", rem) + "\x1b[0m"
}

func taskFooter(help string, width int) string {
	return "\x1b[2m" + agent.PadTo(help, width) + "\x1b[0m"
}

func taskPadLine(content string, width int) string {
	return agent.PadTo(content, width)
}

func padLines(lines []string, width, height int) []string {
	for len(lines) < height {
		lines = append(lines, agent.PadTo("", width))
	}
	if len(lines) > height {
		lines = lines[:height]
	}
	return lines
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	s := ""
	for n > 0 {
		s = string(rune('0'+n%10)) + s
		n /= 10
	}
	return s
}

func pad2(n int) string {
	if n < 10 {
		return "0" + itoa(n)
	}
	return itoa(n)
}
