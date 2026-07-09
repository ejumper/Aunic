package agent

import (
	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
)

// WebQuerySubmitMsg is emitted when the user confirms a web search query.
type WebQuerySubmitMsg struct{ Query string }

// WebQueryClosedMsg is emitted when the web query bar is dismissed without searching.
type WebQueryClosedMsg struct{}

// WebQueryBar is the web-search input UI that replaces the prompt box.
type WebQueryBar struct {
	input      textinput.Model
	innerWidth int
}

// NewWebQueryBar creates a WebQueryBar sized to the pane's inner width. The
// width is stored on the struct (and refreshed by Pane.SetWidth) because
// Update's close-button hit test needs it — View's value receiver can't
// persist it.
func NewWebQueryBar(innerWidth int) WebQueryBar {
	ti := textinput.New()
	ti.Prompt = ""
	ti.Focus()
	return WebQueryBar{input: ti, innerWidth: innerWidth}
}

func (wb WebQueryBar) Update(msg tea.Msg) (WebQueryBar, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.String() {
		case "esc":
			return wb, func() tea.Msg { return WebQueryClosedMsg{} }
		case "enter":
			q := wb.input.Value()
			if q == "" {
				return wb, nil
			}
			return wb, func() tea.Msg { return WebQuerySubmitMsg{Query: q} }
		}
		var cmd tea.Cmd
		wb.input, cmd = wb.input.Update(msg)
		return wb, cmd
	case tea.MouseMsg:
		if msg.Action == tea.MouseActionPress && msg.Button == tea.MouseButtonLeft {
			contentCol := msg.X - 1 // subtract left border
			if msg.Y == 2 && contentCol >= wb.innerWidth-closeButtonW {
				return wb, func() tea.Msg { return WebQueryClosedMsg{} }
			}
		}
	}
	return wb, nil
}

// Height returns the number of content rows this bar occupies.
func (wb WebQueryBar) Height() int { return 1 }

// View renders Height() lines each exactly innerWidth cells wide.
func (wb WebQueryBar) View(innerWidth int) []string {
	label := "web search: "
	wb.input.Width = innerWidth - visualWidth(label) - closeButtonW
	if wb.input.Width < 1 {
		wb.input.Width = 1
	}
	return []string{padTo(label+wb.input.View()+closeButton(false), innerWidth)}
}
