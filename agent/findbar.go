package agent

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
)

// Messages produced by FindBar and handled by app.go to coordinate with the editor.
type FindQueryMsg struct {
	Query         string
	CaseSensitive bool
}
type FindNavMsg struct{ Direction int } // +1 = next, -1 = prev
type FindReplaceMsg struct {
	Replacement string
	All         bool
}
type FindClosedMsg struct{}
type FindModeMsg struct{ ReplaceMode bool }

// focusZone tracks which part of the find bar has keyboard focus.
type focusZone int

const (
	zoneFindInput focusZone = iota
	zoneReplaceInput
	zoneButtons
)

// FindBar is the find/find-and-replace UI that lives inside the agent pane
// bordered box when find mode is active.
type FindBar struct {
	findInput     textinput.Model
	replaceInput  textinput.Model
	replaceMode   bool
	caseSensitive bool
	focus         focusZone
	focusedBtn    int
}

// find-only button indices (left to right: left group, then right close)
const (
	fbBtnCase = iota
	fbBtnNext
	fbBtnPrev
	fbBtnToggle // [F→R]
	fbBtnClose  // right-justified close
	fbBtnCountFindOnly
)

// find+replace button indices
const (
	fbReplBtnCase = iota
	fbReplBtnNext
	fbReplBtnPrev
	fbReplBtnReplace
	fbReplBtnReplaceAll
	fbReplBtnToggle // [R→F]
	fbReplBtnClose  // right-justified close
	fbBtnCountRepl
)

func NewFindBar(replaceMode bool, query string) FindBar {
	fi := textinput.New()
	fi.Prompt = ""
	fi.SetValue(query)
	fi.Focus()

	ri := textinput.New()
	ri.Prompt = ""

	return FindBar{
		findInput:    fi,
		replaceInput: ri,
		replaceMode:  replaceMode,
		focus:        zoneFindInput,
		focusedBtn:   0,
	}
}

func (fb FindBar) buttonCount() int {
	if fb.replaceMode {
		return fbBtnCountRepl
	}
	return fbBtnCountFindOnly
}

// findBtnClose returns the close button index for the current mode.
func (fb FindBar) findBtnClose() int {
	if fb.replaceMode {
		return fbReplBtnClose
	}
	return fbBtnClose
}

// FindQuery returns the current find input value.
func (fb FindBar) FindQuery() string { return fb.findInput.Value() }

func (fb FindBar) Update(msg tea.Msg) (FindBar, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		key := msg.String()

		// Global intercepts regardless of focus zone.
		switch key {
		case "esc":
			return fb, func() tea.Msg { return FindClosedMsg{} }
		case "ctrl+f":
			return fb, func() tea.Msg { return FindModeMsg{ReplaceMode: !fb.replaceMode} }
		case "ctrl+h":
			return fb, func() tea.Msg { return FindModeMsg{ReplaceMode: true} }
		// Tab/shift+tab cycle matches from any zone.
		case "tab":
			return fb, func() tea.Msg { return FindNavMsg{Direction: +1} }
		case "shift+tab":
			return fb, func() tea.Msg { return FindNavMsg{Direction: -1} }
		}

		switch fb.focus {
		case zoneFindInput:
			switch key {
			case "enter":
				return fb, func() tea.Msg { return FindNavMsg{Direction: +1} }
			case "down":
				// Move focus to next zone.
				fb.findInput.Blur()
				if fb.replaceMode {
					fb.replaceInput.Focus()
					fb.focus = zoneReplaceInput
				} else {
					fb.focus = zoneButtons
					fb.focusedBtn = 0
				}
				return fb, nil
			case "up":
				return fb, nil // already at top
			default:
				prev := fb.findInput.Value()
				var cmd tea.Cmd
				fb.findInput, cmd = fb.findInput.Update(msg)
				if fb.findInput.Value() != prev {
					q := fb.findInput.Value()
					cs := fb.caseSensitive
					return fb, tea.Batch(cmd, func() tea.Msg {
						return FindQueryMsg{Query: q, CaseSensitive: cs}
					})
				}
				return fb, cmd
			}

		case zoneReplaceInput:
			switch key {
			case "enter":
				repl := fb.replaceInput.Value()
				return fb, func() tea.Msg { return FindReplaceMsg{Replacement: repl, All: false} }
			case "down":
				fb.replaceInput.Blur()
				fb.focus = zoneButtons
				fb.focusedBtn = 0
				return fb, nil
			case "up":
				fb.replaceInput.Blur()
				fb.findInput.Focus()
				fb.focus = zoneFindInput
				return fb, nil
			default:
				var cmd tea.Cmd
				fb.replaceInput, cmd = fb.replaceInput.Update(msg)
				return fb, cmd
			}

		case zoneButtons:
			switch key {
			case "left":
				if fb.focusedBtn > 0 {
					fb.focusedBtn--
				}
			case "right":
				if fb.focusedBtn < fb.buttonCount()-1 {
					fb.focusedBtn++
				}
			case "up":
				if fb.replaceMode {
					fb.replaceInput.Focus()
					fb.focus = zoneReplaceInput
				} else {
					fb.findInput.Focus()
					fb.focus = zoneFindInput
				}
			case "down":
				// Already at bottom; do nothing.
			case "enter":
				return fb.activateButton()
			}
		}
	}
	return fb, nil
}

func (fb FindBar) activateButton() (FindBar, tea.Cmd) {
	if !fb.replaceMode {
		switch fb.focusedBtn {
		case fbBtnCase:
			fb.caseSensitive = !fb.caseSensitive
			q, cs := fb.findInput.Value(), fb.caseSensitive
			return fb, func() tea.Msg { return FindQueryMsg{Query: q, CaseSensitive: cs} }
		case fbBtnNext:
			return fb, func() tea.Msg { return FindNavMsg{Direction: +1} }
		case fbBtnPrev:
			return fb, func() tea.Msg { return FindNavMsg{Direction: -1} }
		case fbBtnToggle:
			return fb, func() tea.Msg { return FindModeMsg{ReplaceMode: true} }
		case fbBtnClose:
			return fb, func() tea.Msg { return FindClosedMsg{} }
		}
	} else {
		switch fb.focusedBtn {
		case fbReplBtnCase:
			fb.caseSensitive = !fb.caseSensitive
			q, cs := fb.findInput.Value(), fb.caseSensitive
			return fb, func() tea.Msg { return FindQueryMsg{Query: q, CaseSensitive: cs} }
		case fbReplBtnNext:
			return fb, func() tea.Msg { return FindNavMsg{Direction: +1} }
		case fbReplBtnPrev:
			return fb, func() tea.Msg { return FindNavMsg{Direction: -1} }
		case fbReplBtnReplace:
			repl := fb.replaceInput.Value()
			return fb, func() tea.Msg { return FindReplaceMsg{Replacement: repl, All: false} }
		case fbReplBtnReplaceAll:
			repl := fb.replaceInput.Value()
			return fb, func() tea.Msg { return FindReplaceMsg{Replacement: repl, All: true} }
		case fbReplBtnToggle:
			return fb, func() tea.Msg { return FindModeMsg{ReplaceMode: false} }
		case fbReplBtnClose:
			return fb, func() tea.Msg { return FindClosedMsg{} }
		}
	}
	return fb, nil
}

// Height returns the number of content rows (excluding the pane's borders and
// indicator line). 2 for find-only, 3 for find+replace.
func (fb FindBar) Height() int {
	if fb.replaceMode {
		return 3 // find input + replace input + button row
	}
	return 2 // find input + button row
}

// View renders Height() lines each exactly innerWidth cells wide.
func (fb FindBar) View(innerWidth int) []string {
	lines := make([]string, 0, fb.Height())

	// ---- find input line ----
	var findLabel string
	if fb.replaceMode {
		findLabel = "Find:    " // 9 chars — aligns with "Replace: "
	} else {
		findLabel = "Find: " // 6 chars
	}
	fb.findInput.Width = innerWidth - visualWidth(findLabel)
	if fb.findInput.Width < 1 {
		fb.findInput.Width = 1
	}
	findLine := findLabel + fb.findInput.View()
	lines = append(lines, padTo(findLine, innerWidth))

	// ---- replace input line (replace mode only) ----
	if fb.replaceMode {
		replLabel := "Replace: " // 9 chars
		fb.replaceInput.Width = innerWidth - visualWidth(replLabel)
		if fb.replaceInput.Width < 1 {
			fb.replaceInput.Width = 1
		}
		replLine := replLabel + fb.replaceInput.View()
		lines = append(lines, padTo(replLine, innerWidth))
	}

	// ---- button row ----
	lines = append(lines, fb.viewButtons(innerWidth))

	return lines
}

func (fb FindBar) viewButtons(innerWidth int) string {
	focused := fb.focus == zoneButtons

	caseLabel := "Aa"
	if fb.caseSensitive {
		caseLabel = "Aa*"
	}

	type btnItem struct {
		label  string
		btnIdx int
	}
	var items []btnItem

	if !fb.replaceMode {
		items = []btnItem{
			{caseLabel, fbBtnCase},
			{"next", fbBtnNext},
			{"prev", fbBtnPrev},
			{"F→R", fbBtnToggle},
		}
	} else {
		items = []btnItem{
			{caseLabel, fbReplBtnCase},
			{"next", fbReplBtnNext},
			{"prev", fbReplBtnPrev},
			{"replace", fbReplBtnReplace},
			{"rep all", fbReplBtnReplaceAll},
			{"R→F", fbReplBtnToggle},
		}
	}

	// Build left group (no spaces between buttons).
	var b strings.Builder
	leftPlain := 0
	for _, item := range items {
		b.WriteString(bracketButton(item.label, focused && fb.focusedBtn == item.btnIdx))
		leftPlain += bracketWidth(item.label)
	}

	// Right-justified close button.
	closeFocused := focused && fb.focusedBtn == fb.findBtnClose()
	spacer := innerWidth - leftPlain - closeButtonW
	if spacer < 1 {
		spacer = 1
	}
	b.WriteString(strings.Repeat(" ", spacer))
	b.WriteString(closeButton(closeFocused))
	return b.String()
}

// formatMatchCount produces the indicator string from a match count and index.
func FormatMatchCount(count, current int) string {
	switch {
	case count == 0:
		return "No matches"
	case count == 1:
		return "1 match"
	default:
		return fmt.Sprintf("%d of %d matches", current+1, count)
	}
}
