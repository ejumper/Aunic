package editor

import (
	"strings"
	"time"

	"github.com/atotto/clipboard"
	"github.com/charmbracelet/bubbles/textarea"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
)

// Model wires a headless textarea (buffer + cursor) to a custom rendering
// pipeline (markdown highlight + indent-aware wrap + gutter + selection
// overlay) displayed through a viewport for scrolling.
type Model struct {
	textarea textarea.Model
	viewport viewport.Model

	filepath string
	width    int
	height   int
	gutterW  int
	contentW int
	ready    bool

	// Selection lives at the editor layer, not the textarea. anchor is fixed
	// when shift-extension or mouse drag starts; head is read on demand from
	// the textarea's current cursor.
	selection  selection
	isDragging bool

	// Track previous state to decide what work to do on each Update.
	prevLine, prevCol int
	prevValue         string

	hlCache    map[string]string
	isMarkdown bool

	// Undo/redo. applyingHistory suppresses recording while we're replaying
	// events from the stack so the replay itself doesn't get re-recorded.
	hist            history
	applyingHistory bool

	// focused controls whether the cursor is rendered. True by default.
	focused bool

	// Search state. searchCurrent is -1 when there are no matches.
	searchQuery         string
	searchCaseSensitive bool
	searchMatches       []searchMatch
	searchCurrent       int

	// Marker highlight overlay. Set by SetMarkerHighlight from app.go.
	markerBg []MarkerSpan // wrapper token bytes → background+fg color
	markerUl []MarkerSpan // body bytes → underline color

	// Insert highlight overlay. Set by SetInsertHighlight after model edits;
	// cleared on any user content change.
	insertSpans []InsertSpan
}

// New creates an editor model with the given file content and path.
func New(filepath, content string) Model {
	ta := textarea.New()
	ta.MaxHeight = 0 // disable bubbles' 99-line cap; we manage scroll ourselves
	ta.ShowLineNumbers = false
	ta.FocusedStyle.CursorLine = ta.FocusedStyle.CursorLine.UnsetBorderBottom()
	ta.FocusedStyle.CursorLineNumber = ta.FocusedStyle.CursorLineNumber.UnsetBorderBottom()
	ta.BlurredStyle.CursorLine = ta.BlurredStyle.CursorLine.UnsetBorderBottom()
	ta.BlurredStyle.CursorLineNumber = ta.BlurredStyle.CursorLineNumber.UnsetBorderBottom()

	// Extend textarea's word-motion bindings to include ctrl+arrow, which is
	// what most users expect. The textarea default only binds alt+left/right
	// (emacs-style); ctrl+arrow is a distinct KeyType that terminals can send
	// via different escape sequences, so we add both ctrl variants and the
	// alt+ctrl variant (sent by some terminals as \x1b[1;7D).
	ta.KeyMap.WordBackward.SetKeys("alt+left", "alt+b", "ctrl+left", "alt+ctrl+left")
	ta.KeyMap.WordForward.SetKeys("alt+right", "alt+f", "ctrl+right", "alt+ctrl+right")
	ta.KeyMap.DeleteWordForward.SetKeys("alt+d", "ctrl+delete")

	ta.SetValue(content)
	// SetValue leaves the cursor at the end of inserted content. Walk back to (0,0).
	for ta.Line() > 0 {
		ta.CursorUp()
	}
	ta.CursorStart()
	ta.Focus()

	lines := strings.Split(content, "\n")
	gw := gutterWidth(len(lines))
	cw := 80 - gw
	ta.SetWidth(cw)

	m := Model{
		textarea:      ta,
		viewport:      viewport.New(80, 24),
		filepath:      filepath,
		gutterW:       gw,
		contentW:      cw,
		width:         80,
		height:        24,
		prevValue:     ta.Value(),
		hlCache:       make(map[string]string),
		isMarkdown:    strings.HasSuffix(strings.ToLower(filepath), ".md"),
		focused:       true,
		searchCurrent: -1,
	}
	m.updateContent()
	return m
}

// Value returns the current buffer content.
func (m Model) Value() string { return m.textarea.Value() }

// SetFocused controls whether the cursor is rendered in View.
func (m *Model) SetFocused(v bool) { m.focused = v }

// HasActiveSelection reports whether a text selection is currently active.
func (m Model) HasActiveSelection() bool { return m.selection.active }

// SelectionRows returns the inclusive logical-line range of the active
// selection. If no selection is active, hasSelection is false.
func (m Model) SelectionRows() (startRow, endRow int, hasSelection bool) {
	if !m.selection.active {
		return 0, 0, false
	}
	head := m.currentCursorPos()
	start, end := m.selection.ordered(head)
	return start.row, end.row, true
}

// GutterWidth returns the current gutter width in characters.
func (m Model) GutterWidth() int { return m.gutterW }

// IsAtLastVisualLine reports whether the cursor is on the last visual row of
// the last logical line. Used by app.go to decide whether a down-arrow at the
// editor's edge should hand focus to the transcript bar.
func (m Model) IsAtLastVisualLine() bool {
	if m.textarea.Line() < m.textarea.LineCount()-1 {
		return false
	}
	li := m.textarea.LineInfo()
	return li.RowOffset >= li.Height-1
}

// IndicatorMsg carries a status string to the agent pane's indicator area.
type IndicatorMsg string

func indicatorCmd(msg string) tea.Cmd {
	return func() tea.Msg { return IndicatorMsg(msg) }
}

// bucketDescription summarizes a group of edit events as "typing", "deletion",
// or "edit" based on what the events contain.
func bucketDescription(group []editEvent) string {
	var hasIns, hasDel bool
	for _, ev := range group {
		if len(ev.inserted) > 0 {
			hasIns = true
		}
		if len(ev.removed) > 0 {
			hasDel = true
		}
	}
	switch {
	case hasIns && !hasDel:
		return "typing"
	case hasDel && !hasIns:
		return "deletion"
	default:
		return "edit"
	}
}

func (m Model) Init() tea.Cmd {
	return m.textarea.Focus()
}

func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	var cmds []tea.Cmd

	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.ready = true

		m.gutterW = gutterWidth(m.textarea.LineCount())
		m.contentW = m.width - m.gutterW
		if m.contentW < 20 {
			m.contentW = 20
		}
		m.textarea.SetWidth(m.contentW)
		m.viewport.Width = m.width
		m.viewport.Height = m.height

		m.updateContent()
		m.syncViewport()
		return m, nil

	case tea.MouseMsg:
		return m.handleMouse(msg)

	case tea.KeyMsg:
		return m.handleKey(msg)
	}

	return m, tea.Batch(cmds...)
}

// handleKey dispatches a key event with selection awareness. The order is:
//  1. Selection-extending shift+motion keys → set anchor if absent, forward
//     the unshifted motion to the textarea.
//  2. Clipboard ops (ctrl+c / ctrl+x).
//  3. Specials (esc, ctrl+s, tab, shift+tab).
//  4. Default — pure navigation collapses any selection; anything else
//     (editing, paste) deletes the selection first, then forwards to the
//     textarea normally.
func (m Model) handleKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	keyStr := msg.String()
	var cmds []tea.Cmd

	if (msg.Type == tea.KeyCtrlUp || msg.Type == tea.KeyCtrlDown) && msg.Alt {
		m.scrollViewport(msg.Type)
		m.refreshAfterChange()
		return m, nil
	}

	switch {
	case isExtendKey(keyStr):
		m.extendSelection(keyStr)
	case keyStr == "ctrl+c":
		if m.selection.active {
			m.copySelection()
			cmds = append(cmds, indicatorCmd("Copied to clipboard"))
		}
	case keyStr == "ctrl+x":
		if m.selection.active {
			m.cutSelection()
			cmds = append(cmds, indicatorCmd("Cut to clipboard"))
		}
	case keyStr == "ctrl+v":
		m.deleteSelectionIfActive()
		text, _ := clipboard.ReadAll()
		m.textarea.InsertString(text)
		cmds = append(cmds, indicatorCmd("Pasted from clipboard"))
	case keyStr == "ctrl+a":
		lines := strings.Split(m.textarea.Value(), "\n")
		m.selection.anchor = position{row: 0, col: 0}
		m.selection.active = true
		last := len(lines) - 1
		m.moveCursorTo(last, len([]rune(lines[last])))
	case keyStr == "esc":
		if m.selection.active {
			m.selection.active = false
		}
	case keyStr == "tab":
		if m.selection.active && m.selectionSpansMultipleLines() {
			m.indentSelection()
		} else {
			m.deleteSelectionIfActive()
			m.textarea.InsertString("\t")
		}
	case keyStr == "enter":
		m.deleteSelectionIfActive()
		// Carry the current line's leading whitespace onto the new line.
		indent := ""
		lines := strings.Split(m.textarea.Value(), "\n")
		if row := m.textarea.Line(); row < len(lines) {
			indent, _ = extractIndent(lines[row])
		}
		m.textarea.InsertString("\n" + indent)
	case keyStr == "shift+enter" || keyStr == "alt+enter":
		m.deleteSelectionIfActive()
		m.textarea.InsertString("\n")
	case keyStr == "shift+tab":
		if m.selection.active && m.selectionSpansMultipleLines() {
			m.unindentSelection()
		} else {
			m.selection.active = false
			m.unindent()
		}
	case keyStr == "alt+up":
		m.selection.active = false
		m.moveLine(-1)
	case keyStr == "alt+down":
		m.selection.active = false
		m.moveLine(1)
	case keyStr == "ctrl+up":
		m.selection.active = false
		m.jumpToEmptyLine(-1)
	case keyStr == "ctrl+down":
		m.selection.active = false
		m.jumpToEmptyLine(1)
	case keyStr == "home":
		m.selection.active = false
		m.moveVisualHome()
	case keyStr == "end":
		m.selection.active = false
		m.moveVisualEnd()
	case keyStr == "pgdown":
		m.selection.active = false
		m.scrollWithCursor(1)
	case keyStr == "pgup":
		m.selection.active = false
		m.scrollWithCursor(-1)
	case keyStr == "alt+pgdown":
		m.selection.active = false
		m.movePagePreserve(1)
	case keyStr == "alt+pgup":
		m.selection.active = false
		m.movePagePreserve(-1)
	case keyStr == "ctrl+z":
		m.selection.active = false
		cmds = append(cmds, indicatorCmd(m.undo()))
	case keyStr == "ctrl+y", keyStr == "ctrl+shift+z":
		m.selection.active = false
		cmds = append(cmds, indicatorCmd(m.redo()))
	default:
		if isNavigationKey(keyStr) {
			m.selection.active = false
		} else {
			hadSel := m.selection.active
			m.deleteSelectionIfActive()
			// Backspace/delete with an active selection: the selection deletion
			// is the complete action. Don't also send the key to the textarea,
			// which would delete another character from the now-empty line.
			if hadSel && isDeleteKey(keyStr) {
				break
			}
		}
		var taCmd tea.Cmd
		m.textarea, taCmd = m.textarea.Update(msg)
		if taCmd != nil {
			cmds = append(cmds, taCmd)
		}
	}

	m.refreshAfterChange()
	return m, tea.Batch(cmds...)
}

// handleMouse implements drag-to-select. Left press starts a fresh selection
// anchor at the click position; left motion while dragging extends the head;
// release ends drag mode. Wheel and other buttons fall through to the
// viewport for scroll handling.
func (m Model) handleMouse(msg tea.MouseMsg) (tea.Model, tea.Cmd) {
	switch msg.Action {
	case tea.MouseActionPress:
		if msg.Button == tea.MouseButtonLeft {
			row, col := m.visualToBuffer(m.viewport.YOffset+msg.Y, msg.X)
			m.moveCursorTo(row, col)
			m.selection.active = false
			m.selection.anchor = position{row: row, col: col}
			m.isDragging = true
			m.refreshAfterChange()
			return m, nil
		}
	case tea.MouseActionMotion:
		if m.isDragging {
			row, col := m.visualToBuffer(m.viewport.YOffset+msg.Y, msg.X)
			m.moveCursorTo(row, col)
			head := m.currentCursorPos()
			if head != m.selection.anchor {
				m.selection.active = true
			}
			m.refreshAfterChange()
			return m, nil
		}
	case tea.MouseActionRelease:
		if msg.Button == tea.MouseButtonLeft && m.isDragging {
			m.isDragging = false
			return m, nil
		}
	}

	// Fall through: scroll wheel etc.
	var vpCmd tea.Cmd
	m.viewport, vpCmd = m.viewport.Update(msg)
	return m, vpCmd
}

// refreshAfterChange runs the post-event bookkeeping previously inlined at
// the end of Update: re-render if content changed, re-sync viewport scroll
// if the cursor moved.
func (m *Model) refreshAfterChange() {
	curLine, curCol := m.textarea.Line(), m.cursorCol()
	curValue := m.textarea.Value()
	valueChanged := curValue != m.prevValue
	cursorMoved := curLine != m.prevLine || curCol != m.prevCol

	if valueChanged && !m.applyingHistory {
		if ev, ok := diffRunes(m.prevValue, curValue); ok {
			ev.cursorBefore = position{row: m.prevLine, col: m.prevCol}
			ev.cursorAfter = position{row: curLine, col: curCol}
			ev.timestamp = time.Now().UnixMilli()
			m.hist.push(ev)
		}
	}

	if valueChanged || cursorMoved {
		m.updateContent()
		m.prevValue = curValue
	}
	if valueChanged || cursorMoved {
		m.syncViewport()
		m.prevLine = curLine
		m.prevCol = curCol
	}
}

func (m Model) View() string {
	out := m.viewport.View()
	if !m.ready {
		return out
	}
	lines := strings.Split(out, "\n")

	// The viewport renders via lipgloss Width+Height, which pads short content
	// with space-filled lines (not empty strings). Every real content row from
	// buildView contains "│"; space-only padding rows do not. Replace those

	gutterPad := strings.Repeat(" ", m.gutterW-1) + "\x1b[90m│\x1b[0m"
	for i, line := range lines {
		if !strings.Contains(line, "│") {
			lines[i] = gutterPad
		}
	}
	// Trim any trailing-newline artifact from buildView, then pad if needed.
	if len(lines) > m.viewport.Height {
		lines = lines[:m.viewport.Height]
	}
	for len(lines) < m.viewport.Height {
		lines = append(lines, gutterPad)
	}

	// Overlay order: markers → insert → brackets → search → selection → cursor (each wins over previous).
	m.applyMarkerOverlay(lines)
	m.applyInsertOverlay(lines)
	m.applyBracketOverlay(lines)
	m.applySearchOverlay(lines)
	m.applySelectionOverlay(lines)

	if m.focused {
		absRow, contentCol := m.cursorAbsolutePos()
		visibleRow := absRow - m.viewport.YOffset
		if visibleRow >= 0 && visibleRow < m.viewport.Height && visibleRow < len(lines) {
			lines[visibleRow] = injectCursor(lines[visibleRow], m.gutterW+contentCol)
		}
	}
	return strings.Join(lines, "\n")
}

// cursorCol returns the cursor's rune-based column within the current logical line.
func (m Model) cursorCol() int {
	li := m.textarea.LineInfo()
	return li.StartColumn + li.ColumnOffset
}

// currentCursorPos packages the textarea's current row + col into a position.
func (m Model) currentCursorPos() position {
	return position{row: m.textarea.Line(), col: m.cursorCol()}
}

// cursorAbsolutePos returns the cursor's screen-row offset within the full
// rendered buffer (before viewport clipping) and its visual column within the
// content area (excluding the gutter).
func (m Model) cursorAbsolutePos() (int, int) {
	return m.bufferPosToVisual(m.textarea.Line(), m.cursorCol())
}

func (m *Model) updateContent() {
	lines := strings.Split(m.textarea.Value(), "\n")
	m.gutterW = gutterWidth(len(lines))
	m.contentW = m.width - m.gutterW
	if m.contentW < 20 {
		m.contentW = 20
	}
	m.textarea.SetWidth(m.contentW)

	rendered := buildView(lines, m.contentW, m.gutterW, m.textarea.Line(), m.hlCache, m.isMarkdown, m.filepath)
	m.viewport.SetContent(rendered)
}

func (m *Model) syncViewport() {
	cursorRow, _ := m.cursorAbsolutePos()
	if cursorRow < m.viewport.YOffset {
		m.viewport.YOffset = cursorRow
	} else if cursorRow >= m.viewport.YOffset+m.viewport.Height {
		m.viewport.YOffset = cursorRow - m.viewport.Height + 1
	}
	if m.viewport.YOffset < 0 {
		m.viewport.YOffset = 0
	}
}

func (m *Model) unindent() {
	lines := strings.Split(m.textarea.Value(), "\n")
	idx := m.textarea.Line()
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

	// Move to col 0 and delete forward — much more targeted than SetValue,
	// and it preserves the undo stack.
	curCol := m.cursorCol()
	m.textarea.CursorStart()
	for i := 0; i < remove; i++ {
		m.textarea, _ = m.textarea.Update(tea.KeyMsg{Type: tea.KeyDelete})
	}
	newCol := curCol - remove
	if newCol < 0 {
		newCol = 0
	}
	m.textarea.SetCursor(newCol)
}

func (m *Model) selectionSpansMultipleLines() bool {
	head := position{row: m.textarea.Line(), col: m.cursorCol()}
	start, end := m.selection.ordered(head)
	return start.row != end.row
}

func (m *Model) indentSelection() {
	head := position{row: m.textarea.Line(), col: m.cursorCol()}
	start, end := m.selection.ordered(head)

	lines := strings.Split(m.textarea.Value(), "\n")
	for i := start.row; i <= end.row && i < len(lines); i++ {
		lines[i] = "\t" + lines[i]
	}
	m.textarea.SetValue(strings.Join(lines, "\n"))

	m.moveCursorTo(head.row, head.col+1)
	m.selection.anchor = position{row: m.selection.anchor.row, col: m.selection.anchor.col + 1}
	m.selection.active = true
}

func (m *Model) unindentSelection() {
	head := position{row: m.textarea.Line(), col: m.cursorCol()}
	start, end := m.selection.ordered(head)

	lines := strings.Split(m.textarea.Value(), "\n")
	removed := make([]int, len(lines))
	for i := start.row; i <= end.row && i < len(lines); i++ {
		switch {
		case strings.HasPrefix(lines[i], "\t"):
			lines[i] = lines[i][1:]
			removed[i] = 1
		case strings.HasPrefix(lines[i], "    "):
			lines[i] = lines[i][4:]
			removed[i] = 4
		case strings.HasPrefix(lines[i], "  "):
			lines[i] = lines[i][2:]
			removed[i] = 2
		}
	}
	m.textarea.SetValue(strings.Join(lines, "\n"))

	headCol := head.col - removed[head.row]
	if headCol < 0 {
		headCol = 0
	}
	m.moveCursorTo(head.row, headCol)

	anchorRow := m.selection.anchor.row
	anchorCol := m.selection.anchor.col - removed[anchorRow]
	if anchorCol < 0 {
		anchorCol = 0
	}
	m.selection.anchor = position{row: anchorRow, col: anchorCol}
	m.selection.active = true
}

// moveLine swaps the current logical line with the one delta rows away
// (delta=-1 for up, delta=+1 for down), preserving the cursor column.
func (m *Model) moveLine(delta int) {
	idx := m.textarea.Line()
	lines := strings.Split(m.textarea.Value(), "\n")
	target := idx + delta
	if target < 0 || target >= len(lines) {
		return
	}
	curCol := m.cursorCol()
	lines[idx], lines[target] = lines[target], lines[idx]
	m.textarea.SetValue(strings.Join(lines, "\n"))
	// Clamp col to the length of the moved line (lines[target] after swap).
	if maxCol := len([]rune(lines[target])); curCol > maxCol {
		curCol = maxCol
	}
	m.moveCursorTo(target, curCol)
}

// jumpToEmptyLine moves the cursor to the next (delta=1) or previous (delta=-1)
// empty line. If no empty line exists in that direction the cursor moves to the
// document boundary.
func (m *Model) jumpToEmptyLine(delta int) {
	lines := strings.Split(m.textarea.Value(), "\n")
	cur := m.textarea.Line()
	for idx := cur + delta; idx >= 0 && idx < len(lines); idx += delta {
		if lines[idx] == "" {
			m.moveCursorTo(idx, 0)
			return
		}
	}
	if delta < 0 {
		m.moveCursorTo(0, 0)
	} else {
		last := len(lines) - 1
		m.moveCursorTo(last, len([]rune(lines[last])))
	}
}

// GotoLine moves the cursor to the start of line n (1-indexed).
// Returns true on success, false if n is out of range.
func (m *Model) GotoLine(n int) bool {
	if n < 1 || n > m.textarea.LineCount() {
		return false
	}
	m.selection.active = false
	m.moveCursorTo(n-1, 0)
	m.refreshAfterChange()
	return true
}

// undo pops the top time-bucket of events from the undo stack, applies their
// inverses to the buffer, and restores the cursor to where it was when the
// bucket began. The popped group is pushed onto the redo stack.
func (m *Model) undo() string {
	group := m.hist.popUndoGroup()
	if len(group) == 0 {
		return "Nothing to undo"
	}
	desc := bucketDescription(group)
	buf := m.textarea.Value()
	for _, ev := range group {
		buf = applyInverse(buf, ev)
	}
	target := group[len(group)-1].cursorBefore

	m.applyingHistory = true
	m.textarea.SetValue(buf)
	m.moveCursorTo(target.row, target.col)
	m.prevValue = buf
	m.applyingHistory = false

	m.hist.pushRedoGroup(group)
	return "Undo: " + desc
}

// redo replays the most recent undone group: applies events forward (oldest
// first) and restores the cursor to where it was after the newest event.
func (m *Model) redo() string {
	group := m.hist.popRedoGroup()
	if len(group) == 0 {
		return "Nothing to redo"
	}
	desc := bucketDescription(group)
	buf := m.textarea.Value()
	for i := len(group) - 1; i >= 0; i-- {
		buf = applyForward(buf, group[i])
	}
	target := group[0].cursorAfter

	m.applyingHistory = true
	m.textarea.SetValue(buf)
	m.moveCursorTo(target.row, target.col)
	m.prevValue = buf
	m.applyingHistory = false

	m.hist.pushUndoGroup(group)
	return "Redo: " + desc
}

// --- Selection: extension, deletion, clipboard --------------------------

// isExtendKey reports whether a key extends or starts a selection.
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

// isDeleteKey reports whether a key is a backward or forward deletion key.
// Used to guard against double-deletion when a selection was just cleared.
func isDeleteKey(s string) bool {
	switch s {
	case "backspace", "ctrl+h", "delete", "ctrl+d", "ctrl+delete":
		return true
	}
	return false
}

// isNavigationKey reports whether a key is pure cursor motion (no edit). Used
// to decide whether an active selection should collapse (true) or get
// replaced (false) when this key arrives.
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

// extendSelection sets the anchor (if not already set) and forwards the
// equivalent unshifted motion to the textarea so its cursor moves; the
// selection head is then read from the textarea on next render.
func (m *Model) extendSelection(keyStr string) {
	if !m.selection.active {
		m.selection.anchor = m.currentCursorPos()
		m.selection.active = true
	}

	switch keyStr {
	case "shift+left":
		m.textarea, _ = m.textarea.Update(tea.KeyMsg{Type: tea.KeyLeft})
	case "shift+right":
		m.textarea, _ = m.textarea.Update(tea.KeyMsg{Type: tea.KeyRight})
	case "shift+up":
		m.textarea, _ = m.textarea.Update(tea.KeyMsg{Type: tea.KeyUp})
	case "shift+down":
		m.textarea, _ = m.textarea.Update(tea.KeyMsg{Type: tea.KeyDown})
	case "shift+home":
		m.moveVisualHome()
	case "shift+end":
		m.moveVisualEnd()
	case "ctrl+shift+left":
		m.textarea, _ = m.textarea.Update(tea.KeyMsg{Type: tea.KeyLeft, Alt: true})
	case "ctrl+shift+right":
		m.textarea, _ = m.textarea.Update(tea.KeyMsg{Type: tea.KeyRight, Alt: true})
	case "ctrl+shift+home":
		m.moveCursorTo(0, 0)
	case "ctrl+shift+end":
		last := m.textarea.LineCount() - 1
		if last < 0 {
			return
		}
		lines := strings.Split(m.textarea.Value(), "\n")
		m.moveCursorTo(last, len([]rune(lines[last])))
	}
}

// moveCursorTo walks the textarea cursor to (row, col). Textarea exposes no
// direct "goto logical line" API and its CursorUp/Down navigate by *visual*
// rows — inside a wrapped logical line they only adjust the column within the
// wrap grid without changing Line(). To cross logical-line boundaries
// reliably we go via the line edges: CursorUp from CursorStart() steps to the
// previous logical line; CursorDown from CursorEnd() steps to the next.
func (m *Model) moveCursorTo(row, col int) {
	if m.textarea.LineCount() == 0 {
		return
	}
	if row < 0 {
		row = 0
	}
	if row >= m.textarea.LineCount() {
		row = m.textarea.LineCount() - 1
	}
	for m.textarea.Line() > 0 {
		m.textarea.CursorStart()
		m.textarea.CursorUp()
	}
	for m.textarea.Line() < row {
		m.textarea.CursorEnd()
		m.textarea.CursorDown()
	}
	m.textarea.SetCursor(col)
}

func (m *Model) moveVisualHome() {
	li := m.textarea.Line()
	lines := strings.Split(m.textarea.Value(), "\n")
	if li >= len(lines) {
		return
	}
	line := lines[li]
	runes := []rune(line)
	c := m.cursorCol()
	if c > len(runes) {
		c = len(runes)
	}
	byteCol := len(string(runes[:c]))
	home, _ := wrapRowBounds(line, byteCol, m.contentW)
	newRuneCol := len([]rune(line[:home]))
	m.textarea.SetCursor(newRuneCol)
}

func (m *Model) moveVisualEnd() {
	li := m.textarea.Line()
	lines := strings.Split(m.textarea.Value(), "\n")
	if li >= len(lines) {
		return
	}
	line := lines[li]
	runes := []rune(line)
	c := m.cursorCol()
	if c > len(runes) {
		c = len(runes)
	}
	byteCol := len(string(runes[:c]))
	// wrapRowBounds treats the indent as its own virtual row and returns
	// indentLen as the "end" when the cursor is inside the indent region.
	// Skip past the indent so we get the real end of the first content row.
	indent, content := extractIndent(line)
	if byteCol <= len(indent) && len(content) > 0 {
		byteCol = len(indent) + 1
	}
	_, end := wrapRowBounds(line, byteCol, m.contentW)
	if end > 0 {
		// If placing the cursor at the exclusive end crosses a visual
		// row boundary (cursor appears on next row), use the last
		// character of this row instead.
		endRow, _ := cursorScreenPos(line, end, m.contentW)
		lastRow, _ := cursorScreenPos(line, end-1, m.contentW)
		if endRow != lastRow {
			end--
		}
	}
	newRuneCol := len([]rune(line[:end]))
	m.textarea.SetCursor(newRuneCol)
}

func (m *Model) movePage(offset int) {
	total := m.viewport.TotalLineCount()
	h := m.viewport.Height
	if total <= h {
		return
	}

	cursorRow, cursorCol := m.cursorAbsolutePos()
	cursorViewRow := cursorRow - m.viewport.YOffset

	delta := offset // +1 or -1
	m.viewport.YOffset += delta
	if m.viewport.YOffset < 0 {
		m.viewport.YOffset = 0
	}
	maxOff := total - h
	if m.viewport.YOffset > maxOff {
		m.viewport.YOffset = maxOff
	}

	targetVisualRow := cursorViewRow + delta
	if targetVisualRow < 0 {
		targetVisualRow = 0
	}
	if targetVisualRow >= h {
		targetVisualRow = h - 1
	}
	m.setCursorAtVisual(m.viewport.YOffset+targetVisualRow, cursorCol)
}

func (m *Model) movePagePreserve(offset int) {
	totalVisual := m.viewport.TotalLineCount()
	pageSize := m.viewport.Height
	if pageSize <= 0 || totalVisual <= pageSize {
		return
	}

	cursorAbsRow, cursorContentCol := m.cursorAbsolutePos()
	cursorViewRow := cursorAbsRow - m.viewport.YOffset

	newYOffset := m.viewport.YOffset + (offset * pageSize)
	maxOffset := totalVisual - pageSize
	if newYOffset < 0 {
		newYOffset = 0
	}
	if newYOffset > maxOffset {
		newYOffset = maxOffset
	}

	targetVisualRow := newYOffset + cursorViewRow
	if targetVisualRow >= totalVisual {
		targetVisualRow = totalVisual - 1
	}
	if targetVisualRow < 0 {
		targetVisualRow = 0
	}

	targetRow, targetCol := m.visualToBuffer(targetVisualRow, m.gutterW+cursorContentCol)
	m.viewport.YOffset = newYOffset
	m.moveCursorTo(targetRow, targetCol)
}

// scrollWithCursor scrolls the viewport by delta visual lines while keeping
// the cursor at the same screen row. The cursor's logical position tracks
// the scroll exactly — if the scroll is clamped at document boundaries, the
// cursor moves only as far as the viewport actually moved.
func (m *Model) scrollWithCursor(delta int) {
	total := m.viewport.TotalLineCount()
	h := m.viewport.Height
	if total <= h {
		return
	}

	cursorAbsRow, cursorContentCol := m.cursorAbsolutePos()

	newYOffset := m.viewport.YOffset + delta
	maxOffset := total - h
	if newYOffset < 0 {
		newYOffset = 0
	}
	if newYOffset > maxOffset {
		newYOffset = maxOffset
	}

	actualDelta := newYOffset - m.viewport.YOffset
	if actualDelta == 0 {
		return
	}
	m.viewport.YOffset = newYOffset

	targetAbsRow := cursorAbsRow + actualDelta
	if targetAbsRow < 0 {
		targetAbsRow = 0
	}
	if targetAbsRow >= total {
		targetAbsRow = total - 1
	}
	m.setCursorAtVisual(targetAbsRow, cursorContentCol)
}

func (m *Model) scrollViewportLine(delta int) {
	total := m.viewport.TotalLineCount()
	h := m.viewport.Height
	if total <= h {
		return
	}

	m.viewport.YOffset += delta
	if m.viewport.YOffset < 0 {
		m.viewport.YOffset = 0
	}
	maxOff := total - h
	if m.viewport.YOffset > maxOff {
		m.viewport.YOffset = maxOff
	}

	cursorRow, cursorCol := m.cursorAbsolutePos()
	if cursorRow < m.viewport.YOffset {
		m.setCursorAtVisual(m.viewport.YOffset, cursorCol)
	} else if cursorRow >= m.viewport.YOffset+h {
		m.setCursorAtVisual(m.viewport.YOffset+h-1, cursorCol)
	}
}

func (m *Model) scrollViewport(keyType tea.KeyType) {
	delta := 1
	if keyType == tea.KeyCtrlUp {
		delta = -1
	}
	m.scrollViewportLine(delta)
}

func (m *Model) setCursorAtVisual(visualRow, contentCol int) {
	row, col := m.visualToBuffer(visualRow, m.gutterW+contentCol)
	m.moveCursorTo(row, col)
}

// deleteSelectionIfActive removes the selected text from the buffer and
// places the cursor at the start of where the selection was. No-op if no
// selection is active.
func (m *Model) deleteSelectionIfActive() {
	if !m.selection.active {
		return
	}
	head := m.currentCursorPos()
	if m.selection.isEmpty(head) {
		m.selection.active = false
		return
	}
	start, end := m.selection.ordered(head)

	lines := strings.Split(m.textarea.Value(), "\n")
	if start.row >= len(lines) || end.row >= len(lines) {
		m.selection.active = false
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

	m.textarea.SetValue(strings.Join(newLines, "\n"))
	m.moveCursorTo(start.row, start.col)
	m.selection.active = false
}

// selectionText returns the currently selected substring of the buffer, or
// "" if no selection.
func (m Model) selectionText() string {
	if !m.selection.active {
		return ""
	}
	head := m.currentCursorPos()
	if m.selection.isEmpty(head) {
		return ""
	}
	start, end := m.selection.ordered(head)

	lines := strings.Split(m.textarea.Value(), "\n")
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

func (m Model) copySelection() {
	if text := m.selectionText(); text != "" {
		_ = clipboard.WriteAll(text)
	}
}

// WrapSelection inserts open before the selection start and close after the
// selection end, leaving the body content unchanged. Returns false (and makes
// no change) when there is no active non-empty selection. After wrapping,
// the selection is cleared and the cursor sits just after the close token.
func (m *Model) WrapSelection(open, close string) bool {
	if !m.selection.active {
		return false
	}
	head := m.currentCursorPos()
	if m.selection.isEmpty(head) {
		return false
	}
	text := m.selectionText()
	m.deleteSelectionIfActive()
	m.textarea.InsertString(open + text + close)
	return true
}

func (m *Model) cutSelection() {
	text := m.selectionText()
	if text == "" {
		return
	}
	_ = clipboard.WriteAll(text)
	m.deleteSelectionIfActive()
}
