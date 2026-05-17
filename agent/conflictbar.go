package agent

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"
)

// ConflictBar replaces the prompt box area when a note_edit or note_write tool
// call cannot be applied to the live editor buffer cleanly. The user picks
// whose version wins: their own edits, or the model's proposed change.
type ConflictBar struct {
	focus      int // 0 = user wins, 1 = model wins
	innerWidth int
}

// ConflictUserWinsMsg is emitted when the user presses [user wins]. The model's
// proposed text is copied to the clipboard and the edit is not applied.
type ConflictUserWinsMsg struct{}

// ConflictModelWinsMsg is emitted when the user presses [model wins]. The
// buffer is reverted to the run snapshot and the model's edit is applied.
type ConflictModelWinsMsg struct{}

// NewConflictBar creates a ConflictBar for the given inner width. Focus starts
// on [user wins] (index 0) as the safe/conservative default.
func NewConflictBar(innerWidth int) ConflictBar {
	return ConflictBar{innerWidth: innerWidth}
}

// Height returns the number of content rows this bar occupies (always 1).
func (cb ConflictBar) Height() int { return 1 }

// layout returns the column ranges of each button within innerWidth for
// mouse hit-testing. Columns are 0-indexed relative to the content area.
func (cb ConflictBar) layout(innerWidth int) (userStart, userEnd, modelStart, modelEnd int) {
	uw := bracketWidth("user wins")
	mw := bracketWidth("model wins")
	total := uw + 2 + mw // 2-space gap between buttons
	leftPad := (innerWidth - total) / 2
	if leftPad < 0 {
		leftPad = 0
	}
	userStart = leftPad
	userEnd = leftPad + uw
	modelStart = leftPad + uw + 2
	modelEnd = modelStart + mw
	return
}

// View renders one content line exactly innerWidth cells wide.
func (cb ConflictBar) View(innerWidth int) []string {
	userStart, _, _, _ := cb.layout(innerWidth)

	var b strings.Builder
	b.WriteString(strings.Repeat(" ", userStart))
	b.WriteString(bracketButton("user wins", cb.focus == 0))
	b.WriteString("  ")
	b.WriteString(bracketButton("model wins", cb.focus == 1))

	return []string{padTo(b.String(), innerWidth)}
}

// Update handles keyboard and mouse events.
func (cb ConflictBar) Update(msg tea.Msg) (ConflictBar, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.String() {
		case "left":
			cb.focus = 0
		case "right":
			cb.focus = 1
		case "enter":
			if cb.focus == 0 {
				return cb, func() tea.Msg { return ConflictUserWinsMsg{} }
			}
			return cb, func() tea.Msg { return ConflictModelWinsMsg{} }
		}

	case tea.MouseMsg:
		if msg.Action != tea.MouseActionPress || msg.Button != tea.MouseButtonLeft {
			return cb, nil
		}
		// Y=0 indicator, Y=1 top border, Y=2 first content row.
		contentRow := msg.Y - 2
		contentCol := msg.X - 1 // subtract left border
		if contentRow != 0 || contentCol < 0 {
			return cb, nil
		}
		userStart, userEnd, modelStart, modelEnd := cb.layout(cb.innerWidth)
		if contentCol >= userStart && contentCol < userEnd {
			return cb, func() tea.Msg { return ConflictUserWinsMsg{} }
		}
		if contentCol >= modelStart && contentCol < modelEnd {
			return cb, func() tea.Msg { return ConflictModelWinsMsg{} }
		}
	}
	return cb, nil
}
