package agent

import (
	"strconv"

	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
)

// GotoLineMsg is emitted when the user confirms a line number.
type GotoLineMsg struct{ Line int }

// GotoClosedMsg is emitted when the goto bar is dismissed without navigating.
type GotoClosedMsg struct{}

// GotoBar is the go-to-line UI that replaces the prompt box when ctrl+g is pressed.
type GotoBar struct {
	input      textinput.Model
	innerWidth int
}

func NewGotoBar() GotoBar {
	ti := textinput.New()
	ti.Prompt = ""
	ti.Focus()
	return GotoBar{input: ti}
}

func (gb GotoBar) Update(msg tea.Msg) (GotoBar, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.String() {
		case "esc", "ctrl+g":
			return gb, func() tea.Msg { return GotoClosedMsg{} }
		case "enter":
			n, err := strconv.Atoi(gb.input.Value())
			if err != nil || n < 1 {
				return gb, nil
			}
			return gb, func() tea.Msg { return GotoLineMsg{Line: n} }
		}
		// Only allow digit keys through; swallow all other printable input.
		if msg.Type == tea.KeyRunes {
			for _, r := range msg.Runes {
				if r < '0' || r > '9' {
					return gb, nil
				}
			}
		}
		var cmd tea.Cmd
		gb.input, cmd = gb.input.Update(msg)
		return gb, cmd
	case tea.MouseMsg:
		if msg.Action == tea.MouseActionPress && msg.Button == tea.MouseButtonLeft {
			// Click on the close button (rightmost 3 cols of content row).
			contentCol := msg.X - 1 // subtract left border
			if msg.Y == 2 && contentCol >= gb.innerWidth-closeButtonW {
				return gb, func() tea.Msg { return GotoClosedMsg{} }
			}
		}
	}
	return gb, nil
}

// Height returns the number of content rows this bar occupies.
func (gb GotoBar) Height() int { return 1 }

// View renders Height() lines each exactly innerWidth cells wide.
func (gb GotoBar) View(innerWidth int) []string {
	gb.innerWidth = innerWidth
	label := "Go to line: "
	gb.input.Width = innerWidth - visualWidth(label) - closeButtonW
	if gb.input.Width < 1 {
		gb.input.Width = 1
	}
	line := label + gb.input.View() + closeButton(false)
	return []string{padTo(line, innerWidth)}
}
