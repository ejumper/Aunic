package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/ejumper/aunic/editor"
)

type appModel struct {
	editor   editor.Model
	filepath string
	width    int
	ready    bool

	savedValue string

	// Exit-confirmation dialog state.
	showExitDialog bool
	dialogFocus    int // 0=save, 1=exit, 2=cancel
}

func newApp(fp, content string) appModel {
	return appModel{
		editor:     editor.New(fp, content),
		filepath:   fp,
		savedValue: content,
	}
}

func (m appModel) Init() tea.Cmd {
	return m.editor.Init()
}

func (m appModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.ready = true

		editorHeight := msg.Height - 2
		if editorHeight < 1 {
			editorHeight = 1
		}

		resized := tea.WindowSizeMsg{Width: msg.Width, Height: editorHeight}
		em, cmd := m.editor.Update(resized)
		m.editor = em.(editor.Model)
		return m, cmd

	case tea.MouseMsg:
		// Title bar click while dialog is open.
		if m.showExitDialog && msg.Y == 0 &&
			msg.Action == tea.MouseActionPress && msg.Button == tea.MouseButtonLeft {
			return m.handleDialogClick(msg.X)
		}
		if msg.Y < 2 {
			return m, nil
		}
		msg.Y -= 2
		em, cmd := m.editor.Update(msg)
		m.editor = em.(editor.Model)
		return m, cmd

	case tea.KeyMsg:
		return m.handleAppKey(msg)
	}

	em, cmd := m.editor.Update(msg)
	m.editor = em.(editor.Model)
	return m, cmd
}

func (m appModel) handleAppKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	if m.showExitDialog {
		return m.handleDialogKey(msg)
	}

	switch msg.String() {
	case "esc":
		if !m.editor.HasActiveSelection() {
			return m.maybeQuit()
		}
	case "ctrl+s":
		em, cmd := m.editor.Update(msg)
		m.editor = em.(editor.Model)
		m.savedValue = m.editor.Value()
		return m, cmd
	}

	em, cmd := m.editor.Update(msg)
	m.editor = em.(editor.Model)
	return m, cmd
}

func (m appModel) maybeQuit() (tea.Model, tea.Cmd) {
	if m.editor.Value() != m.savedValue {
		m.showExitDialog = true
		m.dialogFocus = 0
		return m, nil
	}
	return m, tea.Quit
}

func (m appModel) handleDialogKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "left":
		if m.dialogFocus > 0 {
			m.dialogFocus--
		}
	case "right":
		if m.dialogFocus < 2 {
			m.dialogFocus++
		}
	case "enter":
		return m.executeDialog()
	case "esc":
		m.showExitDialog = false
	}
	return m, nil
}

// dialogOptionCols returns the visual [start, end) column ranges of each
// dialog option within the title bar. Used by both renderDialogBar and
// handleDialogClick so mouse hit-testing stays in sync with rendering.
func dialogOptionCols(termWidth int) (prefix string, starts, ends [3]int) {
	prefix = "Unsaved Changes! "
	labels := [3]string{"[save]", "[exit]", "[cancel]"}
	total := len(prefix)
	for _, l := range labels {
		total += len(l)
	}
	leftPad := (termWidth - total) / 2
	if leftPad < 0 {
		leftPad = 0
	}
	pos := leftPad + len(prefix)
	for i, l := range labels {
		starts[i] = pos
		ends[i] = pos + len(l)
		pos = ends[i]
	}
	return
}

func (m appModel) handleDialogClick(x int) (tea.Model, tea.Cmd) {
	_, starts, ends := dialogOptionCols(m.width)
	for i := range starts {
		if x >= starts[i] && x < ends[i] {
			m.dialogFocus = i
			return m.executeDialog()
		}
	}
	return m, nil
}

func (m appModel) executeDialog() (tea.Model, tea.Cmd) {
	switch m.dialogFocus {
	case 0: // save
		os.WriteFile(m.filepath, []byte(m.editor.Value()), 0644)
		return m, tea.Quit
	case 1: // exit without saving
		return m, tea.Quit
	case 2: // cancel
		m.showExitDialog = false
	}
	return m, nil
}

func (m appModel) View() string {
	if !m.ready {
		return m.editor.View()
	}

	unsaved := m.editor.Value() != m.savedValue
	parts := []string{
		renderTitleBar(m.width, m.filepath, unsaved, m.showExitDialog, m.dialogFocus),
		"",
		m.editor.View(),
	}
	return strings.Join(parts, "\n")
}

func renderTitleBar(width int, filename string, unsaved, showDialog bool, dialogFocus int) string {
	if showDialog {
		return renderDialogBar(width, dialogFocus)
	}

	name := filepath.Base(filename)
	if name == "" {
		name = "Untitled"
	}
	if unsaved {
		name = "* " + name + " *"
	}

	label := fmt.Sprintf(" %s ", name)
	if len(label) > width {
		label = label[:width]
	}

	leftPad := (width - len(label)) / 2
	rightPad := width - leftPad - len(label)
	if rightPad < 0 {
		rightPad = 0
	}

	style := "\x1b[34;4m"
	rst := "\x1b[0m"

	return style + strings.Repeat(" ", leftPad) + label + strings.Repeat(" ", rightPad) + rst
}

func renderDialogBar(width, dialogFocus int) string {
	const (
		base       = "\x1b[4m\x1b[34m"  // underline + ANSI 4 (blue) text
		focusOpen  = "\x1b[44m\x1b[97m" // ANSI bg 4 (blue) + fg 15 (bright white)
		focusClose = "\x1b[0m\x1b[4m\x1b[34m" // reset then re-apply base style
		rst        = "\x1b[0m"
	)

	prefix, _, _ := dialogOptionCols(width)
	labels := [3]string{"[save]", "[exit]", "[cancel]"}

	total := len(prefix)
	for _, l := range labels {
		total += len(l)
	}
	leftPad := (width - total) / 2
	if leftPad < 0 {
		leftPad = 0
	}
	rightPad := width - leftPad - total
	if rightPad < 0 {
		rightPad = 0
	}

	// prefix is "Unsaved Changes! " — render the label italic, trailing space plain.
	italicLabel := "\x1b[3mUnsaved Changes!\x1b[23m "

	var b strings.Builder
	b.WriteString(base)
	b.WriteString(strings.Repeat(" ", leftPad))
	b.WriteString(italicLabel)
	for i, label := range labels {
		if i == dialogFocus {
			b.WriteString(focusOpen)
			b.WriteString(label)
			b.WriteString(focusClose)
		} else {
			b.WriteString(label)
		}
	}
	b.WriteString(strings.Repeat(" ", rightPad))
	b.WriteString(rst)
	return b.String()
}
