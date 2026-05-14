package editor

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

// sendKey is a test helper that feeds a key string through the editor and
// returns the resulting model. It bypasses the WindowSizeMsg path so a fresh
// New() works at the 80×24 default.
func sendKey(m Model, keyStr string) Model {
	// Construct a KeyMsg from the string. For the keys exercised in these
	// tests, the simplest mapping is via Type for arrows/home/end and
	// Runes for printable chars.
	var msg tea.KeyMsg
	switch keyStr {
	case "shift+left":
		msg = tea.KeyMsg{Type: tea.KeyShiftLeft}
	case "shift+right":
		msg = tea.KeyMsg{Type: tea.KeyShiftRight}
	case "shift+up":
		msg = tea.KeyMsg{Type: tea.KeyShiftUp}
	case "shift+down":
		msg = tea.KeyMsg{Type: tea.KeyShiftDown}
	case "shift+home":
		msg = tea.KeyMsg{Type: tea.KeyShiftHome}
	case "shift+end":
		msg = tea.KeyMsg{Type: tea.KeyShiftEnd}
	case "left":
		msg = tea.KeyMsg{Type: tea.KeyLeft}
	case "right":
		msg = tea.KeyMsg{Type: tea.KeyRight}
	case "down":
		msg = tea.KeyMsg{Type: tea.KeyDown}
	case "backspace":
		msg = tea.KeyMsg{Type: tea.KeyBackspace}
	case "esc":
		msg = tea.KeyMsg{Type: tea.KeyEsc}
	default:
		// Printable rune
		msg = tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(keyStr)}
	}
	mm, _ := m.Update(msg)
	return mm.(Model)
}

func TestSelectionExtendsRight(t *testing.T) {
	m := New("", "hello world")
	m = sendKey(m, "shift+right")
	m = sendKey(m, "shift+right")
	m = sendKey(m, "shift+right")

	if !m.selection.active {
		t.Fatal("selection should be active after shift+right")
	}
	if m.selection.anchor != (position{row: 0, col: 0}) {
		t.Errorf("anchor = %+v, want {0,0}", m.selection.anchor)
	}
	if got := m.selectionText(); got != "hel" {
		t.Errorf("selectionText() = %q, want %q", got, "hel")
	}
}

func TestSelectionExtendsLeft(t *testing.T) {
	m := New("", "hello world")
	// Move cursor to end of "hello" first using right arrows
	for i := 0; i < 5; i++ {
		m = sendKey(m, "right")
	}
	// Now select backward
	m = sendKey(m, "shift+left")
	m = sendKey(m, "shift+left")

	if !m.selection.active {
		t.Fatal("selection should be active")
	}
	if got := m.selectionText(); got != "lo" {
		t.Errorf("selectionText() = %q, want %q", got, "lo")
	}
}

func TestSelectionClearedByPlainNavigation(t *testing.T) {
	m := New("", "hello world")
	m = sendKey(m, "shift+right")
	m = sendKey(m, "shift+right")
	if !m.selection.active {
		t.Fatal("precondition: selection should be active")
	}
	m = sendKey(m, "right") // plain right arrow
	if m.selection.active {
		t.Error("selection should have been cleared by plain navigation")
	}
}

func TestSelectionDeletedOnTyping(t *testing.T) {
	m := New("", "hello world")
	for i := 0; i < 5; i++ {
		m = sendKey(m, "shift+right")
	}
	// Selection is "hello"
	m = sendKey(m, "X")
	if m.selection.active {
		t.Error("selection should be cleared after edit")
	}
	if got := m.textarea.Value(); got != "X world" {
		t.Errorf("after typing X over selection: %q, want %q", got, "X world")
	}
}

func TestSelectionDeletedOnBackspace(t *testing.T) {
	m := New("", "hello world")
	for i := 0; i < 5; i++ {
		m = sendKey(m, "shift+right")
	}
	m = sendKey(m, "backspace")
	if got := m.textarea.Value(); got != " world" {
		t.Errorf("after backspace over selection: %q, want %q", got, " world")
	}
}

func TestSelectionMultiLineDelete(t *testing.T) {
	m := New("", "first\nsecond\nthird")
	// Cursor at (0,0). Extend down twice + right twice to cover "first\nsecond\nth"
	m = sendKey(m, "shift+down")
	m = sendKey(m, "shift+down")
	m = sendKey(m, "shift+right")
	m = sendKey(m, "shift+right")

	if got := m.selectionText(); got != "first\nsecond\nth" {
		t.Errorf("selectionText() = %q, want %q", got, "first\nsecond\nth")
	}

	m = sendKey(m, "backspace")
	if got := m.textarea.Value(); got != "ird" {
		t.Errorf("after multi-line delete: %q, want %q", got, "ird")
	}
}

func TestSelectionEscClears(t *testing.T) {
	m := New("", "hello")
	m = sendKey(m, "shift+right")
	if !m.selection.active {
		t.Fatal("precondition: selection should be active")
	}
	m = sendKey(m, "esc")
	if m.selection.active {
		t.Error("esc should clear selection without quitting")
	}
}

func TestSelectionShiftHome(t *testing.T) {
	m := New("", "hello world")
	for i := 0; i < 5; i++ {
		m = sendKey(m, "right")
	}
	m = sendKey(m, "shift+home")
	if got := m.selectionText(); got != "hello" {
		t.Errorf("shift+home selected %q, want %q", got, "hello")
	}
}

func TestSelectionShiftEnd(t *testing.T) {
	m := New("", "hello world")
	m = sendKey(m, "shift+end")
	if got := m.selectionText(); got != "hello world" {
		t.Errorf("shift+end selected %q, want %q", got, "hello world")
	}
}

func TestSelectionOverlayRenders(t *testing.T) {
	m := New("", "hello world")
	// Resize so View() works.
	mm, _ := m.Update(tea.WindowSizeMsg{Width: 40, Height: 10})
	m = mm.(Model)

	m = sendKey(m, "shift+right")
	m = sendKey(m, "shift+right")
	m = sendKey(m, "shift+right")

	view := m.View()
	// Selection background ANSI code should appear.
	if !strings.Contains(view, "\x1b[104m") {
		t.Errorf("expected selection background ANSI in view, got: %q", view)
	}
	// And the close code.
	if !strings.Contains(view, "\x1b[49m") {
		t.Errorf("expected selection background close ANSI in view, got: %q", view)
	}
}

func TestApplySelectionBackgroundANSIAware(t *testing.T) {
	// A line with ANSI escapes embedded; selection should style by visual
	// cells, not by raw bytes.
	line := "ab\x1b[1mc\x1b[22md"
	got := applySelectionBackground(line, 1, 3)
	stripped := stripANSI(got)
	if stripped != "abcd" {
		t.Errorf("text corruption: %q -> %q", line, stripped)
	}
	if !strings.Contains(got, "\x1b[104m") {
		t.Errorf("expected selection background, got %q", got)
	}
}
