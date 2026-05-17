package agent

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"
)

// ModelItem is one entry in the model picker.
type ModelItem struct {
	ProviderKey string
	ModelKey    string
	Name        string // display name from aunic.json
}

// ModelSelectedMsg is emitted when the user selects a model from the picker.
type ModelSelectedMsg struct {
	ProviderKey string
	ModelKey    string
}

// ModelBarClosedMsg is emitted when the model picker is dismissed without a selection.
type ModelBarClosedMsg struct{}

// modelLayoutItem records the rendered position of one item, for mouse hit-testing.
type modelLayoutItem struct {
	idx      int // index into ModelBar.items
	row      int
	startCol int
	endCol   int
}

// ModelBar is the model-selection UI that replaces the prompt box.
// Items are laid out horizontally, wrapping to new rows as needed.
type ModelBar struct {
	items      []ModelItem
	cursor     int
	innerWidth int
}

// NewModelBar creates a ModelBar pre-populated with items.
func NewModelBar(items []ModelItem, innerWidth int) ModelBar {
	return ModelBar{items: items, innerWidth: innerWidth}
}

// computeLayout assigns (row, startCol, endCol) to each item given innerWidth.
func (mb ModelBar) computeLayout(innerWidth int) []modelLayoutItem {
	layout := make([]modelLayoutItem, len(mb.items))
	col, row := 0, 0
	for i, item := range mb.items {
		itemW := visualWidth(item.Name) + 2 // "[" + name + "]"
		if i > 0 {
			if col+1+itemW > innerWidth {
				row++
				col = 0
			} else {
				col++ // space separator
			}
		}
		layout[i] = modelLayoutItem{
			idx:      i,
			row:      row,
			startCol: col,
			endCol:   col + itemW,
		}
		col += itemW
	}
	return layout
}

// Height returns the number of content rows this bar occupies.
func (mb ModelBar) Height() int {
	if len(mb.items) == 0 {
		return 1
	}
	layout := mb.computeLayout(mb.innerWidth)
	return layout[len(layout)-1].row + 1
}

// Update handles keyboard and mouse events.
func (mb ModelBar) Update(msg tea.Msg) (ModelBar, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.String() {
		case "esc":
			return mb, func() tea.Msg { return ModelBarClosedMsg{} }
		case "enter":
			if mb.cursor >= 0 && mb.cursor < len(mb.items) {
				item := mb.items[mb.cursor]
				return mb, func() tea.Msg {
					return ModelSelectedMsg{ProviderKey: item.ProviderKey, ModelKey: item.ModelKey}
				}
			}
		case "left", "up":
			if mb.cursor > 0 {
				mb.cursor--
			}
		case "right", "down":
			if mb.cursor < len(mb.items)-1 {
				mb.cursor++
			}
		}
	case tea.MouseMsg:
		if msg.Action != tea.MouseActionPress || msg.Button != tea.MouseButtonLeft {
			return mb, nil
		}
		// Within the pane: Y=0 indicator, Y=1 top border, Y=2+ content.
		contentRow := msg.Y - 2
		contentCol := msg.X - 1 // subtract left border
		if contentRow < 0 || contentCol < 0 {
			return mb, nil
		}
		// Close button: last row, rightmost 3 cols.
		layout := mb.computeLayout(mb.innerWidth)
		lastRow := 0
		if len(layout) > 0 {
			lastRow = layout[len(layout)-1].row
		}
		if contentRow == lastRow && contentCol >= mb.innerWidth-closeButtonW {
			return mb, func() tea.Msg { return ModelBarClosedMsg{} }
		}
		for _, li := range layout {
			if li.row == contentRow && contentCol >= li.startCol && contentCol < li.endCol {
				item := mb.items[li.idx]
				return mb, func() tea.Msg {
					return ModelSelectedMsg{ProviderKey: item.ProviderKey, ModelKey: item.ModelKey}
				}
			}
		}
	}
	return mb, nil
}

// View renders Height() lines each exactly innerWidth cells wide.
func (mb ModelBar) View(innerWidth int) []string {
	layout := mb.computeLayout(innerWidth)
	h := 1
	if len(layout) > 0 {
		h = layout[len(layout)-1].row + 1
	}

	type rowState struct {
		b   strings.Builder
		col int
	}
	bufs := make([]*rowState, h)
	for i := range bufs {
		bufs[i] = &rowState{}
	}

	for _, li := range layout {
		rb := bufs[li.row]
		// Pad up to startCol.
		if li.startCol > rb.col {
			rb.b.WriteString(strings.Repeat(" ", li.startCol-rb.col))
			rb.col = li.startCol
		}
		name := mb.items[li.idx].Name
		itemVisW := visualWidth(name) + 2
		if li.idx == mb.cursor {
			rb.b.WriteString("\x1b[7m[" + name + "]\x1b[0m")
		} else {
			rb.b.WriteString("[" + name + "]")
		}
		rb.col += itemVisW
	}

	rows := make([]string, h)
	for r := 0; r < h; r++ {
		if r == h-1 {
			// Last row: truncate to leave room, then append close button.
			rows[r] = padTo(bufs[r].b.String(), innerWidth-closeButtonW) + closeButton(false)
		} else {
			rows[r] = padTo(bufs[r].b.String(), innerWidth)
		}
	}
	return rows
}
