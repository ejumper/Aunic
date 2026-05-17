package agent

import (
	"strings"

	"github.com/ejumper/aunic/web"
	tea "github.com/charmbracelet/bubbletea"
)

// Pane is the agent UI pane that sits below the file editor. It contains an
// Indicator line, a bordered box with the PromptBox, and a ButtonRow.
//
// Layout (from top to bottom):
//
//	indicator line                          ← faint italic, 1 row
//	┌──────────────────────────────────┐    ← top border
//	│ prompt content (1–8 lines)       │
//	│ [+] [/] [model] [agent] [mode] [↑] │ ← button row
//	└──────────────────────────────────┘    ← bottom border
type Pane struct {
	Indicator   Indicator
	PromptBox   PromptBox
	Buttons     ButtonRow
	width       int
	height      int          // terminal height, used for webBar maxRows
	buttonFocus bool
	runActive   bool         // true while a model run is in flight
	findBar     *FindBar     // non-nil when find mode is active
	gotoBar     *GotoBar     // non-nil when goto mode is active
	webBar      *WebBar      // non-nil when @web mode is active
	modelBar    *ModelBar    // non-nil when model picker is active
	conflictBar *ConflictBar // non-nil when conflict resolution UI is active
	cmdBar      *CmdBar      // non-nil when command picker is active
	webQueryBar *WebQueryBar // non-nil when web-search query input is active
}

// SetRunActive toggles the run-in-progress state, which swaps the send button
// glyph between ↑ and ■ and changes its press behavior.
func (p *Pane) SetRunActive(active bool) {
	p.runActive = active
}

// SetModelLabel sets the display label on the model button.
func (p *Pane) SetModelLabel(label string) {
	p.Buttons.ModelLabel = label
}

// SetModeLabel sets the display label on the mode button.
func (p *Pane) SetModeLabel(label string) {
	p.Buttons.ModeLabel = label
}

// SetModelNames sets the map of valid lowercase model display names, used to
// color /model <name> green in the prompt box when the name is recognized.
func (p *Pane) SetModelNames(names map[string]bool) {
	p.PromptBox.validModelNames = names
}

// OpenModel activates the model picker, replacing the prompt box.
func (p Pane) OpenModel(items []ModelItem) Pane {
	mb := NewModelBar(items, p.width-2)
	p.modelBar = &mb
	p.findBar = nil
	p.gotoBar = nil
	p.webBar = nil
	p.buttonFocus = false
	p.PromptBox.Blur()
	return p
}

// CloseModel deactivates the model picker.
func (p Pane) CloseModel() Pane {
	p.modelBar = nil
	return p
}

// IsModelMode reports whether the model picker is currently active.
func (p Pane) IsModelMode() bool { return p.modelBar != nil }

// OpenConflict activates the conflict resolution bar, replacing the prompt box.
func (p Pane) OpenConflict() Pane {
	cb := NewConflictBar(p.width - 2)
	p.conflictBar = &cb
	p.findBar = nil
	p.gotoBar = nil
	p.webBar = nil
	p.modelBar = nil
	p.buttonFocus = false
	p.PromptBox.Blur()
	return p
}

// CloseConflict deactivates the conflict resolution bar.
func (p Pane) CloseConflict() Pane {
	p.conflictBar = nil
	return p
}

// IsConflictMode reports whether the conflict resolution bar is active.
func (p Pane) IsConflictMode() bool { return p.conflictBar != nil }

// OpenCmdBar activates the command picker, replacing the prompt box.
func (p Pane) OpenCmdBar() Pane {
	cb := NewCmdBar(p.width - 2)
	p.cmdBar = &cb
	p.findBar = nil
	p.gotoBar = nil
	p.webBar = nil
	p.modelBar = nil
	p.buttonFocus = false
	p.PromptBox.Blur()
	return p
}

// CloseCmdBar deactivates the command picker.
func (p Pane) CloseCmdBar() Pane {
	p.cmdBar = nil
	return p
}

// IsCmdMode reports whether the command picker is currently active.
func (p Pane) IsCmdMode() bool { return p.cmdBar != nil }

// OpenWebQueryBar activates the web search query input, replacing the prompt box.
func (p Pane) OpenWebQueryBar() Pane {
	wb := NewWebQueryBar()
	p.webQueryBar = &wb
	p.findBar = nil
	p.gotoBar = nil
	p.modelBar = nil
	p.cmdBar = nil
	p.buttonFocus = false
	p.PromptBox.Blur()
	return p
}

// CloseWebQueryBar deactivates the web search query input.
func (p Pane) CloseWebQueryBar() Pane {
	p.webQueryBar = nil
	return p
}

// CmdBarInitialIndicator returns the indicator text for the first focused
// command after the picker opens. Returns "" if the picker is not active.
func (p Pane) CmdBarInitialIndicator() string {
	if p.cmdBar == nil {
		return ""
	}
	return p.cmdBar.InitialIndicatorText()
}

// RunCancelRequestedMsg is emitted when the user presses the send button (■)
// while a run is active. app.go handles it by calling the run's cancel func.
type RunCancelRequestedMsg struct{}

// ModelOpenMsg is emitted when the model button (index 2) is pressed.
type ModelOpenMsg struct{}

// ModeTogglePressMsg is emitted when the mode button (index 4) is pressed.
// app.go flips between "note" and "chat" modes.
type ModeTogglePressMsg struct{}

// FocusTranscriptMsg is emitted when the prompt box receives up-arrow on its
// first visual line. app.go handles it by transferring focus to the transcript
// bar, mirroring how down-arrow at the last line hands focus to the buttons.
type FocusTranscriptMsg struct{}

// FocusEditorMsg is emitted when the transcript bar hits the top edge during
// up-arrow navigation. app.go sets focus to the editor — except when the bar
// is in full-height mode, in which case the editor is hidden and the message
// is a no-op.
type FocusEditorMsg struct{}

// FocusPromptMsg is emitted when the transcript bar hits the bottom edge
// during down-arrow navigation (or when collapsed and the user presses down
// on the [^] button). app.go sets focus to the prompt box.
type FocusPromptMsg struct{}

// NewPane creates a Pane sized to the given terminal width.
func NewPane(width int) Pane {
	innerWidth := width - 2
	if innerWidth < 2 {
		innerWidth = 2
	}
	return Pane{
		PromptBox: NewPromptBox(innerWidth),
		width:     width,
	}
}

// SetWidth resizes all components to the new terminal width.
func (p *Pane) SetWidth(width int) {
	p.width = width
	innerWidth := width - 2
	if innerWidth < 2 {
		innerWidth = 2
	}
	p.PromptBox.SetWidth(innerWidth)
	if p.modelBar != nil {
		p.modelBar.innerWidth = innerWidth
	}
	if p.conflictBar != nil {
		p.conflictBar.innerWidth = innerWidth
	}
	if p.cmdBar != nil {
		p.cmdBar.innerWidth = innerWidth
		layout := computeCmdLayout(p.cmdBar.filtered, innerWidth)
		p.cmdBar.layout = layout
	}
	if p.webBar != nil {
		p.webBar.innerWidth = innerWidth
		if p.webBar.state == wbPage && p.webBar.page != nil {
			p.webBar.pageLines = renderMarkdownPage(p.webBar.page.Markdown, innerWidth)
		}
	}
}

// SetHeight records the terminal height so OpenWeb can size the webBar correctly.
func (p *Pane) SetHeight(h int) {
	p.height = h
	if p.webBar != nil {
		p.webBar.maxRows = webMaxRows(h)
		// Clamp user override so a smaller terminal doesn't leave the bar
		// taller than the available space.
		if p.webBar.userMaxRows > 0 {
			maxAllowed := 3*h/4 - 3
			if maxAllowed < 4 {
				maxAllowed = 4
			}
			if p.webBar.userMaxRows > maxAllowed {
				p.webBar.userMaxRows = maxAllowed
			}
		}
	}
}

// webMaxRows computes the maximum content rows for the WebBar: 2/3 of the
// terminal height minus the 3 rows the Pane frame occupies (indicator + 2 borders).
func webMaxRows(termHeight int) int {
	r := termHeight*2/3 - 3
	if r < 4 {
		r = 4
	}
	return r
}

// ── Find / Goto / Web open + close ───────────────────────────────────────────

// OpenFind activates find mode, replacing the prompt box with a find bar.
// query pre-fills the find input (pass "" to start empty).
func (p Pane) OpenFind(replaceMode bool, query string) Pane {
	fb := NewFindBar(replaceMode, query)
	p.findBar = &fb
	p.gotoBar = nil
	p.webBar = nil
	p.buttonFocus = false
	p.PromptBox.Blur()
	return p
}

// CloseFind deactivates find mode.
func (p Pane) CloseFind() Pane {
	p.findBar = nil
	return p
}

// OpenFindCmd opens the find bar pre-configured for a slash command result.
// Handles SlashFind, SlashFindReplaceOpen, and SlashFindReplace.
func (p Pane) OpenFindCmd(sc *SlashCmdResult) Pane {
	fb := NewFindBar(sc.Kind != SlashFind, sc.FindQuery)
	switch sc.Kind {
	case SlashFind:
		// Complete find command: focus the [next] button so the user can
		// immediately navigate matches.
		if sc.FindQuery != "" {
			fb.findInput.Blur()
			fb.focus = zoneButtons
			fb.focusedBtn = fbBtnNext
		}
	case SlashFindReplaceOpen:
		// Move focus to the replace input.
		fb.findInput.Blur()
		fb.replaceInput.Focus()
		fb.focus = zoneReplaceInput
	case SlashFindReplace:
		// Pre-fill replace field; focus the ↓ next button.
		fb.replaceInput.SetValue(sc.ReplaceQuery)
		fb.findInput.Blur()
		fb.focus = zoneButtons
		fb.focusedBtn = fbBtnNext
	}
	p.findBar = &fb
	p.gotoBar = nil
	p.webBar = nil
	p.buttonFocus = false
	p.PromptBox.Blur()
	return p
}

// OpenGoto activates goto mode, replacing the prompt box with a goto bar.
func (p Pane) OpenGoto() Pane {
	gb := NewGotoBar()
	p.gotoBar = &gb
	p.findBar = nil
	p.webBar = nil
	p.buttonFocus = false
	p.PromptBox.Blur()
	return p
}

// CloseGoto deactivates goto mode.
func (p Pane) CloseGoto() Pane {
	p.gotoBar = nil
	return p
}

// OpenWeb activates @web mode. It creates a WebBar in the loading state and
// returns the pane plus a tea.Cmd that runs the DDG search asynchronously.
func (p Pane) OpenWeb(query string, n int) (Pane, tea.Cmd) {
	maxRows := webMaxRows(p.height)
	innerWidth := p.width - 2
	if innerWidth < 2 {
		innerWidth = 2
	}
	wb := NewWebBar(innerWidth, maxRows)
	p.webBar = &wb
	p.findBar = nil
	p.gotoBar = nil
	p.buttonFocus = false
	p.PromptBox.Blur()
	return p, WebSearchCmd(query, n)
}

// OpenWebForURL opens the WebBar in fetching state for a direct URL, without
// running a search first. Used when the user opens a transcript URL in the pager.
func (p Pane) OpenWebForURL(url string) (Pane, tea.Cmd) {
	maxRows := webMaxRows(p.height)
	innerWidth := p.width - 2
	if innerWidth < 2 {
		innerWidth = 2
	}
	wb := NewWebBar(innerWidth, maxRows)
	wb.loadMsg = "Fetching…"
	p.webBar = &wb
	p.findBar = nil
	p.gotoBar = nil
	p.buttonFocus = false
	p.PromptBox.Blur()
	return p, WebFetchCmdNoRecord(url)
}

// CloseWeb deactivates @web mode.
func (p Pane) CloseWeb() Pane {
	p.webBar = nil
	return p
}

// IsWebMode reports whether the @web bar is currently active.
func (p Pane) IsWebMode() bool { return p.webBar != nil }

// SetWebUserMaxRows applies a user-chosen override for the webBar's max
// content rows (set when the user drags the pane's top border). Pass 0 to
// clear the override.
func (p *Pane) SetWebUserMaxRows(rows int) {
	if p.webBar != nil {
		p.webBar.userMaxRows = rows
	}
}

// WebUserMaxRows returns the current user-chosen override (0 if none).
func (p Pane) WebUserMaxRows() int {
	if p.webBar == nil {
		return 0
	}
	return p.webBar.userMaxRows
}

// ── State mutation helpers called from app.go ─────────────────────────────────

// ApplyWebResults populates the WebBar with search results and switches it to
// the results list view.
func (p *Pane) ApplyWebResults(results []web.Result) {
	if p.webBar == nil {
		return
	}
	p.webBar.results = results
	p.webBar.state = wbResults
	p.webBar.cursor = 0
	p.webBar.topResult = 0
	p.webBar.expanded = make(map[int]bool)
}

// ApplyWebPage switches the WebBar to the page pager view. Used for new
// page fetches (from search results, transcript URL, or page-link click);
// pushes the previous page onto the back history and clears forward history.
func (p *Pane) ApplyWebPage(page web.Page) {
	if p.webBar == nil {
		return
	}
	if p.webBar.page != nil {
		p.webBar.historyBack = append(p.webBar.historyBack, *p.webBar.page)
		p.webBar.historyFwd = nil
	}
	p.webBar.applyPage(page)
}

// ApplyWebFetchError returns the WebBar to results mode if it was in loading
// state (e.g. fetch failed after the user selected a result).
func (p *Pane) ApplyWebFetchError() {
	if p.webBar != nil && p.webBar.state == wbLoading {
		p.webBar.state = wbResults
	}
}

// ── Common queries ────────────────────────────────────────────────────────────

// FindQuery returns the current find input text, or "" if not in find mode.
func (p Pane) FindQuery() string {
	if p.findBar == nil {
		return ""
	}
	return p.findBar.FindQuery()
}

// SetPromptFocus returns a copy of the pane with promptbox focus set to the
// given value. Always clears buttonFocus.
func (p Pane) SetPromptFocus(focused bool) Pane {
	p.buttonFocus = false
	if focused {
		p.PromptBox.Focus()
	} else {
		p.PromptBox.Blur()
	}
	return p
}

// Height returns the total number of terminal rows the pane occupies.
func (p Pane) Height() int {
	if p.findBar != nil {
		return 3 + p.findBar.Height()
	}
	if p.gotoBar != nil {
		return 3 + p.gotoBar.Height()
	}
	if p.webBar != nil {
		return 3 + p.webBar.Height()
	}
	if p.modelBar != nil {
		return 3 + p.modelBar.Height()
	}
	if p.conflictBar != nil {
		return 3 + p.conflictBar.Height()
	}
	if p.cmdBar != nil {
		return 3 + p.cmdBar.Height()
	}
	if p.webQueryBar != nil {
		return 3 + p.webQueryBar.Height()
	}
	return 4 + p.PromptBox.CurrentHeight()
}

// ── Update ────────────────────────────────────────────────────────────────────

// Update handles a Bubbletea message for the agent pane.
func (p Pane) Update(msg tea.Msg) (Pane, tea.Cmd) {
	if p.findBar != nil {
		fb, cmd := p.findBar.Update(msg)
		p.findBar = &fb
		return p, cmd
	}
	if p.gotoBar != nil {
		gb, cmd := p.gotoBar.Update(msg)
		p.gotoBar = &gb
		return p, cmd
	}
	if p.webBar != nil {
		wb, cmd := p.webBar.Update(msg)
		p.webBar = &wb
		return p, cmd
	}
	if p.modelBar != nil {
		mb, cmd := p.modelBar.Update(msg)
		p.modelBar = &mb
		return p, cmd
	}
	if p.conflictBar != nil {
		cb, cmd := p.conflictBar.Update(msg)
		p.conflictBar = &cb
		return p, cmd
	}
	if p.cmdBar != nil {
		cb, cmd := p.cmdBar.Update(msg)
		p.cmdBar = &cb
		return p, cmd
	}
	if p.webQueryBar != nil {
		wb, cmd := p.webQueryBar.Update(msg)
		p.webQueryBar = &wb
		return p, cmd
	}

	switch msg := msg.(type) {
	case tea.MouseMsg:
		if msg.Action != tea.MouseActionPress || msg.Button != tea.MouseButtonLeft {
			return p, nil
		}
		// Button row is at Y = indicator(1) + top border(1) + prompt content rows.
		if msg.Y == 2+p.PromptBox.CurrentHeight() {
			// "/" button (index 1): compute its X range from button layout.
			slashStartX, slashEndX := ButtonXRange(1, p.Buttons.ModelLabel, p.Buttons.ModeLabel)
			if msg.X >= slashStartX && msg.X < slashEndX {
				return p, func() tea.Msg { return CmdPickerOpenMsg{} }
			}
			// Model button (index 2): open model picker.
			modelStartX, modelEndX := ButtonXRange(2, p.Buttons.ModelLabel, p.Buttons.ModeLabel)
			if msg.X >= modelStartX && msg.X < modelEndX {
				return p, func() tea.Msg { return ModelOpenMsg{} }
			}
			// Mode button (index 4): toggle note/chat mode.
			modeStartX, modeEndX := ButtonXRange(4, p.Buttons.ModelLabel, p.Buttons.ModeLabel)
			if msg.X >= modeStartX && msg.X < modeEndX {
				return p, func() tea.Msg { return ModeTogglePressMsg{} }
			}
			// Send pill occupies the last pillWidth columns before the right border.
			pw := pillWidth(sendLabel)
			pillStart := p.width - 1 - pw
			if msg.X >= pillStart && msg.X < p.width-1 {
				if p.runActive {
					return p, func() tea.Msg { return RunCancelRequestedMsg{} }
				}
				content := p.PromptBox.Value()
				if content == "" {
					return p, nil
				}
				p.PromptBox.Clear()
				return p, func() tea.Msg { return PromptSubmitMsg{Content: content} }
			}
		}
		return p, nil

	case tea.KeyMsg:
		if p.buttonFocus {
			switch msg.String() {
			case "up":
				p.buttonFocus = false
				p.PromptBox.Focus()
			case "left":
				if p.Buttons.focusedIdx > 0 {
					p.Buttons.focusedIdx--
				}
			case "right":
				if p.Buttons.focusedIdx < buttonCount-1 {
					p.Buttons.focusedIdx++
				}
			case "enter":
				if p.Buttons.focusedIdx == buttonCount-1 { // send button (↑ / ■ pill)
					if p.runActive {
						return p, func() tea.Msg { return RunCancelRequestedMsg{} }
					}
					content := p.PromptBox.Value()
					p.PromptBox.Clear()
					return p, func() tea.Msg { return PromptSubmitMsg{Content: content} }
				} else if p.Buttons.focusedIdx == 1 { // "/" button → command picker
					return p, func() tea.Msg { return CmdPickerOpenMsg{} }
				} else if p.Buttons.focusedIdx == 2 { // model button
					return p, func() tea.Msg { return ModelOpenMsg{} }
				} else if p.Buttons.focusedIdx == 4 { // mode button
					return p, func() tea.Msg { return ModeTogglePressMsg{} }
				}
			}
			return p, nil
		}
		// Intercept down when cursor is already at the last visual line.
		if msg.String() == "down" && p.PromptBox.IsAtLastVisualLine() {
			p.buttonFocus = true
			p.Buttons.focusedIdx = 0
			p.PromptBox.Blur()
			return p, nil
		}
		// Intercept up when cursor is already on the first visual line:
		// hand focus to the transcript bar above the prompt.
		if msg.String() == "up" && p.PromptBox.IsAtFirstVisualLine() {
			return p, func() tea.Msg { return FocusTranscriptMsg{} }
		}
		pb, cmd := p.PromptBox.Update(msg)
		p.PromptBox = pb
		return p, cmd
	}
	return p, nil
}

// ── View ──────────────────────────────────────────────────────────────────────

// View renders the full agent pane as a multi-line string without a trailing
// newline.
func (p Pane) View() string {
	innerWidth := p.width - 2
	if innerWidth < 2 {
		innerWidth = 2
	}

	var b strings.Builder

	// Indicator line
	b.WriteString(p.Indicator.View(p.width))
	b.WriteByte('\n')

	// Top border (rounded corners)
	b.WriteString("╭")
	b.WriteString(strings.Repeat("─", innerWidth))
	b.WriteString("╮")
	b.WriteByte('\n')

	// Content lines
	if p.findBar != nil {
		for _, line := range p.findBar.View(innerWidth) {
			b.WriteString("│")
			b.WriteString(line)
			b.WriteString("│")
			b.WriteByte('\n')
		}
	} else if p.gotoBar != nil {
		for _, line := range p.gotoBar.View(innerWidth) {
			b.WriteString("│")
			b.WriteString(line)
			b.WriteString("│")
			b.WriteByte('\n')
		}
	} else if p.webBar != nil {
		for _, line := range p.webBar.View(innerWidth) {
			b.WriteString("│")
			b.WriteString(line)
			b.WriteString("│")
			b.WriteByte('\n')
		}
	} else if p.modelBar != nil {
		for _, line := range p.modelBar.View(innerWidth) {
			b.WriteString("│")
			b.WriteString(line)
			b.WriteString("│")
			b.WriteByte('\n')
		}
	} else if p.conflictBar != nil {
		for _, line := range p.conflictBar.View(innerWidth) {
			b.WriteString("│")
			b.WriteString(line)
			b.WriteString("│")
			b.WriteByte('\n')
		}
	} else if p.cmdBar != nil {
		for _, line := range p.cmdBar.View(innerWidth) {
			b.WriteString("│")
			b.WriteString(line)
			b.WriteString("│")
			b.WriteByte('\n')
		}
	} else if p.webQueryBar != nil {
		for _, line := range p.webQueryBar.View(innerWidth) {
			b.WriteString("│")
			b.WriteString(line)
			b.WriteString("│")
			b.WriteByte('\n')
		}
	} else {
		// Prompt content
		for _, line := range strings.Split(p.PromptBox.View(innerWidth), "\n") {
			b.WriteString("│")
			b.WriteString(line)
			b.WriteString("│")
			b.WriteByte('\n')
		}
		// Button row
		b.WriteString("│")
		b.WriteString(p.Buttons.View(innerWidth, p.buttonFocus, p.runActive))
		b.WriteString("│")
		b.WriteByte('\n')
	}

	// Bottom border (rounded corners, no trailing newline)
	b.WriteString("╰")
	b.WriteString(strings.Repeat("─", innerWidth))
	b.WriteString("╯")

	return b.String()
}
