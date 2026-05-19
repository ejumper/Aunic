package agent

import (
	"strings"
	"time"

	"github.com/atotto/clipboard"
	"github.com/charmbracelet/bubbles/textarea"
	tea "github.com/charmbracelet/bubbletea"
)

const maxPromptLines = 8

// PromptSubmitMsg is emitted when the user submits the prompt box content via
// Enter (slash command only) or the send button (any content).
type PromptSubmitMsg struct{ Content string }

// PromptBox is the user-prompt text input. It uses a bubbles/textarea as a
// headless backing store and does its own rendering — no gutter, no syntax
// highlight — with the same editing features as the file editor minus the
// file-editor-only keys (ctrl+s, alt+up/down, ctrl+up/down, pgup/pgdn).
type PromptBox struct {
	ta  textarea.Model
	sel selection

	hist            history
	applyingHistory bool

	focused         bool
	width           int // inner content width (terminal width - 2 for box border)
	validModelNames map[string]bool // lowercase model names for /model coloring

	prevValue string
	prevLine  int
	prevCol   int
}

// NewPromptBox creates a PromptBox sized to innerWidth.
func NewPromptBox(innerWidth int) PromptBox {
	ta := textarea.New()
	ta.MaxHeight = 0
	ta.ShowLineNumbers = false
	ta.FocusedStyle.CursorLine = ta.FocusedStyle.CursorLine.UnsetBorderBottom()
	ta.FocusedStyle.CursorLineNumber = ta.FocusedStyle.CursorLineNumber.UnsetBorderBottom()
	ta.BlurredStyle.CursorLine = ta.BlurredStyle.CursorLine.UnsetBorderBottom()
	ta.BlurredStyle.CursorLineNumber = ta.BlurredStyle.CursorLineNumber.UnsetBorderBottom()
	ta.KeyMap.WordBackward.SetKeys("alt+left", "alt+b", "ctrl+left", "alt+ctrl+left")
	ta.KeyMap.WordForward.SetKeys("alt+right", "alt+f", "ctrl+right", "alt+ctrl+right")

	if innerWidth < 1 {
		innerWidth = 1
	}
	ta.SetWidth(innerWidth)
	ta.SetHeight(1)
	// bubbles/textarea only processes key events when focused. We keep it
	// permanently focused and control cursor rendering ourselves via p.focused.
	_ = ta.Focus()

	return PromptBox{
		ta:        ta,
		width:     innerWidth,
		prevValue: "",
	}
}

// SetWidth updates the inner content width.
func (p *PromptBox) SetWidth(innerWidth int) {
	if innerWidth < 1 {
		innerWidth = 1
	}
	p.width = innerWidth
	p.ta.SetWidth(innerWidth)
}

// Value returns the current buffer content.
func (p PromptBox) Value() string { return p.ta.Value() }

// Clear resets the prompt box to empty, recording the transition in undo history
// so ctrl+z can restore the cleared text.
func (p *PromptBox) Clear() {
	cur := p.ta.Value()
	if cur != "" {
		if ev, ok := diffRunes(cur, ""); ok {
			curLine, curCol := p.ta.Line(), p.cursorCol()
			ev.cursorBefore = position{row: curLine, col: curCol}
			ev.cursorAfter = position{row: 0, col: 0}
			ev.timestamp = time.Now().UnixMilli()
			p.hist.push(ev)
		}
	}
	p.ta.SetValue("")
	p.moveCursorTo(0, 0)
	p.prevValue = ""
}

// SetValue replaces the prompt box content and positions the cursor at the end.
func (p *PromptBox) SetValue(s string) {
	p.ta.SetValue(s)
	p.prevValue = s
	// Move cursor to end of content.
	lines := strings.Split(s, "\n")
	last := len(lines) - 1
	p.moveCursorTo(last, len([]rune(lines[last])))
}

// InsertString inserts s at the current cursor position.
func (p *PromptBox) InsertString(s string) {
	p.ta.InsertString(s)
	p.refreshAfterChange()
}

// Focus marks the prompt box as having keyboard focus.
func (p *PromptBox) Focus() { p.focused = true }

// Blur removes keyboard focus.
func (p *PromptBox) Blur() { p.focused = false }

// Focused reports whether the prompt box has keyboard focus.
func (p PromptBox) Focused() bool { return p.focused }

// IsAtLastVisualLine reports whether the cursor is on the last absolute visual
// row of the content (so a down-arrow press can hand focus to the button row).
func (p PromptBox) IsAtLastVisualLine() bool {
	rows := p.allVisualRows(p.width)
	curAbs, _ := p.cursorVisualPos(p.width)
	return curAbs >= len(rows)-1
}

// IsAtFirstVisualLine reports whether the cursor is on the first absolute
// visual row of the content (so an up-arrow press can hand focus to the
// transcript bar above the prompt).
func (p PromptBox) IsAtFirstVisualLine() bool {
	curAbs, _ := p.cursorVisualPos(p.width)
	return curAbs == 0
}

// CurrentHeight returns the number of visual lines the current content
// occupies, clamped to [1, maxPromptLines].
func (p PromptBox) CurrentHeight() int {
	if p.width <= 0 {
		return 1
	}
	rows := p.allVisualRows(p.width)
	n := len(rows)
	if n < 1 {
		return 1
	}
	if n > maxPromptLines {
		return maxPromptLines
	}
	return n
}

// Update handles a Bubbletea message for the prompt box.
func (p PromptBox) Update(msg tea.Msg) (PromptBox, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		return p.handleKey(msg)
	}
	return p, nil
}

func (p PromptBox) handleKey(msg tea.KeyMsg) (PromptBox, tea.Cmd) {
	keyStr := msg.String()
	var cmds []tea.Cmd

	switch {
	case isExtendKey(keyStr):
		p.extendSelection(keyStr)

	case keyStr == "ctrl+c":
		if p.sel.active {
			p.copySelection()
		}

	case keyStr == "ctrl+x":
		if p.sel.active {
			p.cutSelection()
		}

	case keyStr == "ctrl+v":
		p.deleteSelectionIfActive()
		return p, ReadClipboardCmd()

	case keyStr == "ctrl+a":
		lines := strings.Split(p.ta.Value(), "\n")
		p.sel.anchor = position{row: 0, col: 0}
		p.sel.active = true
		last := len(lines) - 1
		p.moveCursorTo(last, len([]rune(lines[last])))

	case keyStr == "esc":
		if p.sel.active {
			p.sel.active = false
		}

	case keyStr == "tab":
		p.deleteSelectionIfActive()
		p.ta.InsertString("\t")

	case keyStr == "shift+tab":
		p.sel.active = false
		p.unindent()

	case keyStr == "home":
		p.sel.active = false
		p.moveVisualHome()

	case keyStr == "end":
		p.sel.active = false
		p.moveVisualEnd()

	case keyStr == "ctrl+z":
		p.sel.active = false
		p.undo()

	case keyStr == "ctrl+y", keyStr == "ctrl+shift+z":
		p.sel.active = false
		p.redo()

	case keyStr == "enter":
		val := p.ta.Value()
		if val == "" {
			return p, nil
		}
		p.Clear()
		return p, func() tea.Msg { return PromptSubmitMsg{Content: val} }

	case keyStr == "shift+enter" || keyStr == "alt+enter":
		p.deleteSelectionIfActive()
		p.ta.InsertString("\n")

	default:
		if isNavigationKey(keyStr) {
			p.sel.active = false
		} else {
			hadSel := p.sel.active
			p.deleteSelectionIfActive()
			if hadSel && isDeleteKey(keyStr) {
				break
			}
		}
		var taCmd tea.Cmd
		p.ta, taCmd = p.ta.Update(msg)
		if taCmd != nil {
			cmds = append(cmds, taCmd)
		}
	}

	p.refreshAfterChange()
	return p, tea.Batch(cmds...)
}

// allVisualRowsFrom wraps the given pre-colored content string at innerWidth,
// using the same logic as allVisualRows. Used so keyword-colored content can
// be displayed while leaving the underlying textarea value unchanged.
func (p PromptBox) allVisualRowsFrom(content string, innerWidth int) []string {
	if innerWidth < 1 {
		innerWidth = 1
	}
	lines := strings.Split(content, "\n")
	var rows []string
	for _, line := range lines {
		indent, c := extractIndent(line)
		wrapped := wrapWithIndent(c, indent, innerWidth)
		rows = append(rows, strings.Split(wrapped, "\n")...)
	}
	if len(rows) == 0 {
		rows = []string{""}
	}
	return rows
}

// View renders the prompt box content to innerWidth columns. The returned
// string is CurrentHeight() lines joined with \n, each exactly innerWidth
// columns wide.
func (p PromptBox) View(innerWidth int) string {
	// Build display rows. When the content is a slash command, color keywords
	// blue while leaving arguments in the default color.
	val := p.ta.Value()
	sc := ParseSlashCmd(val)
	if sc == nil {
		sc = FindInlineCmd(val)
	}
	var allRows []string
	if sc != nil {
		allRows = p.allVisualRowsFrom(ColorKeywords(val, sc, p.validModelNames), innerWidth)
	} else if len(ParseAtFiles(val)) > 0 {
		allRows = p.allVisualRowsFrom(ColorAtFiles(val), innerWidth)
	} else {
		allRows = p.allVisualRows(innerWidth)
	}
	height := len(allRows)
	if height < 1 {
		height = 1
	}
	if height > maxPromptLines {
		height = maxPromptLines
	}

	cursorAbs, cursorScreenCol := p.cursorVisualPos(innerWidth)

	scrollOff := cursorAbs - height + 1
	if scrollOff < 0 {
		scrollOff = 0
	}
	if maxOff := len(allRows) - height; maxOff >= 0 && scrollOff > maxOff {
		scrollOff = maxOff
	}

	end := scrollOff + height
	if end > len(allRows) {
		end = len(allRows)
	}

	visible := make([]string, height)
	copy(visible, allRows[scrollOff:end])
	for i := end - scrollOff; i < height; i++ {
		visible[i] = ""
	}

	// Pad each row to innerWidth.
	for i, row := range visible {
		vw := visualWidth(row)
		if vw < innerWidth {
			visible[i] = row + strings.Repeat(" ", innerWidth-vw)
		}
	}

	// Selection overlay before cursor so cursor lands on top.
	p.applySelectionOverlay(visible, scrollOff, innerWidth)

	// Inject cursor only when focused.
	if p.focused {
		cursorVis := cursorAbs - scrollOff
		if cursorVis >= 0 && cursorVis < height {
			visible[cursorVis] = injectCursor(visible[cursorVis], cursorScreenCol)
		}
	}

	return strings.Join(visible, "\n")
}

// --- rendering helpers -----------------------------------------------------

func (p PromptBox) allVisualRows(innerWidth int) []string {
	if innerWidth < 1 {
		innerWidth = 1
	}
	lines := strings.Split(p.ta.Value(), "\n")
	var rows []string
	for _, line := range lines {
		indent, content := extractIndent(line)
		wrapped := wrapWithIndent(content, indent, innerWidth)
		rows = append(rows, strings.Split(wrapped, "\n")...)
	}
	if len(rows) == 0 {
		rows = []string{""}
	}
	return rows
}

// cursorVisualPos returns the cursor's absolute visual row (counting from top
// of content) and visual column within that row.
func (p PromptBox) cursorVisualPos(innerWidth int) (absRow, col int) {
	if innerWidth < 1 {
		innerWidth = 1
	}
	lines := strings.Split(p.ta.Value(), "\n")
	curLine := p.ta.Line()

	abs := 0
	for i, line := range lines {
		indent, content := extractIndent(line)
		wrapped := wrapWithIndent(content, indent, innerWidth)
		nRows := len(strings.Split(wrapped, "\n"))
		if i == curLine {
			runes := []rune(line)
			c := p.cursorCol()
			if c > len(runes) {
				c = len(runes)
			}
			byteCol := len(string(runes[:c]))
			rowOff, screenCol := cursorScreenPos(line, byteCol, innerWidth)
			return abs + rowOff, screenCol
		}
		abs += nRows
	}
	return 0, 0
}

func (p PromptBox) bufferPosToVisual(pos position, innerWidth int) (int, int) {
	lines := strings.Split(p.ta.Value(), "\n")
	if pos.row < 0 {
		pos.row = 0
	}
	if pos.row >= len(lines) {
		pos.row = len(lines) - 1
	}
	runes := []rune(lines[pos.row])
	if pos.col < 0 {
		pos.col = 0
	}
	if pos.col > len(runes) {
		pos.col = len(runes)
	}
	byteCol := len(string(runes[:pos.col]))
	rowOff, screenCol := cursorScreenPos(lines[pos.row], byteCol, innerWidth)

	base := 0
	for i := 0; i < pos.row; i++ {
		indent, content := extractIndent(lines[i])
		wrapped := wrapWithIndent(content, indent, innerWidth)
		base += len(strings.Split(wrapped, "\n"))
	}
	return base + rowOff, screenCol
}

func (p *PromptBox) applySelectionOverlay(visible []string, scrollOff, innerWidth int) {
	if !p.sel.active {
		return
	}
	head := p.currentPos()
	if p.sel.isEmpty(head) {
		return
	}
	start, end := p.sel.ordered(head)

	startAbs, startCol := p.bufferPosToVisual(start, innerWidth)
	endAbs, endCol := p.bufferPosToVisual(end, innerWidth)

	for absRow := startAbs; absRow <= endAbs; absRow++ {
		visRow := absRow - scrollOff
		if visRow < 0 || visRow >= len(visible) {
			continue
		}
		var fromCol, toCol int
		if absRow == startAbs {
			fromCol = startCol
		} else {
			fromCol = 0
		}
		if absRow == endAbs {
			toCol = endCol
		} else {
			toCol = -1
		}
		visible[visRow] = applySelectionBackground(visible[visRow], fromCol, toCol)
	}
}

// --- cursor helpers --------------------------------------------------------

func (p PromptBox) cursorCol() int {
	li := p.ta.LineInfo()
	return li.StartColumn + li.ColumnOffset
}

func (p PromptBox) currentPos() position {
	return position{row: p.ta.Line(), col: p.cursorCol()}
}

func (p *PromptBox) moveCursorTo(row, col int) {
	if p.ta.LineCount() == 0 {
		return
	}
	if row < 0 {
		row = 0
	}
	if row >= p.ta.LineCount() {
		row = p.ta.LineCount() - 1
	}
	for p.ta.Line() > 0 {
		p.ta.CursorStart()
		p.ta.CursorUp()
	}
	for p.ta.Line() < row {
		p.ta.CursorEnd()
		p.ta.CursorDown()
	}
	p.ta.SetCursor(col)
}

func (p *PromptBox) moveVisualHome() {
	li := p.ta.Line()
	lines := strings.Split(p.ta.Value(), "\n")
	if li >= len(lines) {
		return
	}
	line := lines[li]
	runes := []rune(line)
	c := p.cursorCol()
	if c > len(runes) {
		c = len(runes)
	}
	byteCol := len(string(runes[:c]))
	home, _ := wrapRowBounds(line, byteCol, p.width)
	p.ta.SetCursor(len([]rune(line[:home])))
}

func (p *PromptBox) moveVisualEnd() {
	li := p.ta.Line()
	lines := strings.Split(p.ta.Value(), "\n")
	if li >= len(lines) {
		return
	}
	line := lines[li]
	runes := []rune(line)
	c := p.cursorCol()
	if c > len(runes) {
		c = len(runes)
	}
	byteCol := len(string(runes[:c]))
	indent, content := extractIndent(line)
	if byteCol <= len(indent) && len(content) > 0 {
		byteCol = len(indent) + 1
	}
	_, end := wrapRowBounds(line, byteCol, p.width)
	if end > 0 {
		endRow, _ := cursorScreenPos(line, end, p.width)
		lastRow, _ := cursorScreenPos(line, end-1, p.width)
		if endRow != lastRow {
			end--
		}
	}
	p.ta.SetCursor(len([]rune(line[:end])))
}

// --- editing helpers -------------------------------------------------------

func (p *PromptBox) unindent() {
	lines := strings.Split(p.ta.Value(), "\n")
	idx := p.ta.Line()
	if idx >= len(lines) {
		return
	}
	line := lines[idx]
	var remove int
	switch {
	case strings.HasPrefix(line, "\t"):
		remove = 1
	case strings.HasPrefix(line, "    "):
		remove = 4
	case strings.HasPrefix(line, "  "):
		remove = 2
	default:
		return
	}
	curCol := p.cursorCol()
	p.ta.CursorStart()
	for i := 0; i < remove; i++ {
		p.ta, _ = p.ta.Update(tea.KeyMsg{Type: tea.KeyDelete})
	}
	newCol := curCol - remove
	if newCol < 0 {
		newCol = 0
	}
	p.ta.SetCursor(newCol)
}

func (p *PromptBox) deleteSelectionIfActive() {
	if !p.sel.active {
		return
	}
	head := p.currentPos()
	if p.sel.isEmpty(head) {
		p.sel.active = false
		return
	}
	start, end := p.sel.ordered(head)

	lines := strings.Split(p.ta.Value(), "\n")
	if start.row >= len(lines) || end.row >= len(lines) {
		p.sel.active = false
		return
	}

	startRunes := []rune(lines[start.row])
	endRunes := []rune(lines[end.row])
	if start.col > len(startRunes) {
		start.col = len(startRunes)
	}
	if end.col > len(endRunes) {
		end.col = len(endRunes)
	}

	// When a cross-line selection ends at col 0, the newline before that
	// position is not part of the selected content — selecting a full line
	// should clear it, not delete it. Adjust end to the EOL of the prior row.
	if end.row > start.row && end.col == 0 {
		end.row--
		endRunes = []rune(lines[end.row])
		end.col = len(endRunes)
	}

	merged := string(startRunes[:start.col]) + string(endRunes[end.col:])
	newLines := make([]string, 0, len(lines)-(end.row-start.row))
	newLines = append(newLines, lines[:start.row]...)
	newLines = append(newLines, merged)
	newLines = append(newLines, lines[end.row+1:]...)

	p.ta.SetValue(strings.Join(newLines, "\n"))
	p.moveCursorTo(start.row, start.col)
	p.sel.active = false
}

func (p PromptBox) selectionText() string {
	if !p.sel.active {
		return ""
	}
	head := p.currentPos()
	if p.sel.isEmpty(head) {
		return ""
	}
	start, end := p.sel.ordered(head)

	lines := strings.Split(p.ta.Value(), "\n")
	if start.row >= len(lines) || end.row >= len(lines) {
		return ""
	}

	if start.row == end.row {
		runes := []rune(lines[start.row])
		if start.col > len(runes) {
			start.col = len(runes)
		}
		if end.col > len(runes) {
			end.col = len(runes)
		}
		return string(runes[start.col:end.col])
	}

	var parts []string
	startRunes := []rune(lines[start.row])
	if start.col > len(startRunes) {
		start.col = len(startRunes)
	}
	parts = append(parts, string(startRunes[start.col:]))
	for i := start.row + 1; i < end.row; i++ {
		parts = append(parts, lines[i])
	}
	endRunes := []rune(lines[end.row])
	if end.col > len(endRunes) {
		end.col = len(endRunes)
	}
	parts = append(parts, string(endRunes[:end.col]))
	return strings.Join(parts, "\n")
}

func (p *PromptBox) copySelection() {
	if text := p.selectionText(); text != "" {
		_ = clipboard.WriteAll(text)
	}
}

func (p *PromptBox) cutSelection() {
	text := p.selectionText()
	if text == "" {
		return
	}
	_ = clipboard.WriteAll(text)
	p.deleteSelectionIfActive()
}

// --- selection extension ---------------------------------------------------

func isExtendKey(s string) bool {
	switch s {
	case "shift+left", "shift+right", "shift+up", "shift+down",
		"shift+home", "shift+end",
		"ctrl+shift+left", "ctrl+shift+right",
		"ctrl+shift+home", "ctrl+shift+end":
		return true
	}
	return false
}

func isDeleteKey(s string) bool {
	switch s {
	case "backspace", "ctrl+h", "delete", "ctrl+d":
		return true
	}
	return false
}

func isNavigationKey(s string) bool {
	switch s {
	case "left", "right", "up", "down",
		"home", "end", "pgup", "pgdown",
		"ctrl+left", "ctrl+right",
		"ctrl+up", "ctrl+down",
		"alt+left", "alt+right",
		"ctrl+home", "ctrl+end",
		"ctrl+a", "ctrl+e",
		"ctrl+p", "ctrl+n",
		"ctrl+b", "ctrl+f":
		return true
	}
	return false
}

func (p *PromptBox) extendSelection(keyStr string) {
	if !p.sel.active {
		p.sel.anchor = p.currentPos()
		p.sel.active = true
	}
	switch keyStr {
	case "shift+left":
		p.ta, _ = p.ta.Update(tea.KeyMsg{Type: tea.KeyLeft})
	case "shift+right":
		p.ta, _ = p.ta.Update(tea.KeyMsg{Type: tea.KeyRight})
	case "shift+up":
		p.ta, _ = p.ta.Update(tea.KeyMsg{Type: tea.KeyUp})
	case "shift+down":
		p.ta, _ = p.ta.Update(tea.KeyMsg{Type: tea.KeyDown})
	case "shift+home":
		p.moveVisualHome()
	case "shift+end":
		p.moveVisualEnd()
	case "ctrl+shift+left":
		p.ta, _ = p.ta.Update(tea.KeyMsg{Type: tea.KeyLeft, Alt: true})
	case "ctrl+shift+right":
		p.ta, _ = p.ta.Update(tea.KeyMsg{Type: tea.KeyRight, Alt: true})
	case "ctrl+shift+home":
		p.moveCursorTo(0, 0)
	case "ctrl+shift+end":
		last := p.ta.LineCount() - 1
		if last < 0 {
			return
		}
		lines := strings.Split(p.ta.Value(), "\n")
		p.moveCursorTo(last, len([]rune(lines[last])))
	}
}

// --- undo/redo -------------------------------------------------------------

func (p *PromptBox) refreshAfterChange() {
	curLine, curCol := p.ta.Line(), p.cursorCol()
	curValue := p.ta.Value()

	if curValue != p.prevValue && !p.applyingHistory {
		if ev, ok := diffRunes(p.prevValue, curValue); ok {
			ev.cursorBefore = position{row: p.prevLine, col: p.prevCol}
			ev.cursorAfter = position{row: curLine, col: curCol}
			ev.timestamp = time.Now().UnixMilli()
			p.hist.push(ev)
		}
	}

	p.prevValue = curValue
	p.prevLine = curLine
	p.prevCol = curCol
}

func (p *PromptBox) undo() {
	group := p.hist.popUndoGroup()
	if len(group) == 0 {
		return
	}
	buf := p.ta.Value()
	for _, ev := range group {
		buf = applyInverse(buf, ev)
	}
	target := group[len(group)-1].cursorBefore

	p.applyingHistory = true
	p.ta.SetValue(buf)
	p.moveCursorTo(target.row, target.col)
	p.prevValue = buf
	p.applyingHistory = false

	p.hist.pushRedoGroup(group)
}

func (p *PromptBox) redo() {
	group := p.hist.popRedoGroup()
	if len(group) == 0 {
		return
	}
	buf := p.ta.Value()
	for i := len(group) - 1; i >= 0; i-- {
		buf = applyForward(buf, group[i])
	}
	target := group[0].cursorAfter

	p.applyingHistory = true
	p.ta.SetValue(buf)
	p.moveCursorTo(target.row, target.col)
	p.prevValue = buf
	p.applyingHistory = false

	p.hist.pushUndoGroup(group)
}
