package editor

import (
	"fmt"
	"os"
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
		textarea:   ta,
		viewport:   viewport.New(80, 24),
		filepath:   filepath,
		gutterW:    gw,
		contentW:   cw,
		width:      80,
		height:     24,
		prevValue:  ta.Value(),
		hlCache:    make(map[string]string),
		isMarkdown: strings.HasSuffix(strings.ToLower(filepath), ".md"),
	}
	m.updateContent()
	return m
}

// Value returns the current buffer content.
func (m Model) Value() string { return m.textarea.Value() }

// HasActiveSelection reports whether a text selection is currently active.
func (m Model) HasActiveSelection() bool { return m.selection.active }

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
		}
	case keyStr == "ctrl+x":
		if m.selection.active {
			m.cutSelection()
		}
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
	case keyStr == "ctrl+s":
		_ = m.save()
		return m, nil
	case keyStr == "tab":
		m.deleteSelectionIfActive()
		m.textarea.InsertString("\t")
	case keyStr == "shift+tab":
		m.selection.active = false
		m.unindent()
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
		m.movePage(1)
	case keyStr == "pgup":
		m.selection.active = false
		m.movePage(-1)
	case keyStr == "ctrl+z":
		m.selection.active = false
		m.undo()
	case keyStr == "ctrl+y", keyStr == "ctrl+shift+z":
		m.selection.active = false
		m.redo()
	default:
		if isNavigationKey(keyStr) {
			m.selection.active = false
		} else {
			m.deleteSelectionIfActive()
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

	// Selection overlay first, then cursor, so the cursor reverse-video lands
	// on top of any selection background at the head cell.
	m.applySelectionOverlay(lines)

	absRow, contentCol := m.cursorAbsolutePos()
	visibleRow := absRow - m.viewport.YOffset
	if visibleRow >= 0 && visibleRow < m.viewport.Height && visibleRow < len(lines) {
		lines[visibleRow] = injectCursor(lines[visibleRow], m.gutterW+contentCol)
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

func (m *Model) save() error {
	if m.filepath == "" {
		return fmt.Errorf("no filepath set")
	}
	return os.WriteFile(m.filepath, []byte(m.textarea.Value()), 0644)
}

// undo pops the top time-bucket of events from the undo stack, applies their
// inverses to the buffer, and restores the cursor to where it was when the
// bucket began. The popped group is pushed onto the redo stack.
func (m *Model) undo() {
	group := m.hist.popUndoGroup()
	if len(group) == 0 {
		return
	}
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
}

// redo replays the most recent undone group: applies events forward (oldest
// first) and restores the cursor to where it was after the newest event.
func (m *Model) redo() {
	group := m.hist.popRedoGroup()
	if len(group) == 0 {
		return
	}
	buf := m.textarea.Value()
	// group is in pop-order (most-recent first); apply in reverse so oldest
	// event lands first.
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

func (m *Model) scrollViewport(keyType tea.KeyType) {
	total := m.viewport.TotalLineCount()
	h := m.viewport.Height
	if total <= h {
		return
	}

	delta := 1
	if keyType == tea.KeyCtrlUp {
		delta = -1
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

func (m *Model) cutSelection() {
	text := m.selectionText()
	if text == "" {
		return
	}
	_ = clipboard.WriteAll(text)
	m.deleteSelectionIfActive()
}
