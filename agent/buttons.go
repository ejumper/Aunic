package agent

import (
	"fmt"
	"strings"
)

// The five left-side buttons use bracket style. Indices 2 (model) and 4 (mode)
// can be overridden at render time via ButtonRow.ModelLabel / ButtonRow.ModeLabel.
var buttonLabels = [5]string{"+", "/", "kimi k2.6", "agent: off", "mode: note"}

// The send button (index 5) uses a pill style with a blue background.
const (
	sendLabel       = "↑"
	sendLabelActive = "■"
	sendBgColor     = 4
	sendFgColor     = 15
	buttonCount     = 6 // 5 bracket buttons + 1 pill button
)

// ButtonRow renders the single-line button bar.
type ButtonRow struct {
	focusedIdx int
	ModelLabel string // replaces the placeholder label at button index 2 when non-empty
	ModeLabel  string // replaces the placeholder label at button index 4 when non-empty
}

func bracketButton(label string, focused bool) string {
	if focused {
		return "\x1b[7m[" + label + "]\x1b[0m"
	}
	return "[" + label + "]"
}

func bracketWidth(label string) int {
	return visualWidth(label) + 2 // +1 for "[", +1 for "]"
}

// ButtonXRange returns the [startX, endX) screen X range of left-side button i
// within the pane inner content (X=1 is first inner col, after the left border).
func ButtonXRange(i int, modelLabel, modeLabel string) (startX, endX int) {
	labels := buttonLabels
	if modelLabel != "" {
		labels[2] = modelLabel
	}
	if modeLabel != "" {
		labels[4] = modeLabel
	}
	col := 1 // inner content starts at X=1 (X=0 is the "│" border)
	for j, label := range labels {
		w := bracketWidth(label)
		if j == i {
			return col, col + w
		}
		col += w + 1 // +1 for the space between buttons
	}
	return -1, -1
}

func pill(label string, bgColor, fgColor int, focused bool) string {
	if focused {
		bgColor, fgColor = fgColor, bgColor
	}
	leftCap := fmt.Sprintf("\x1b[38;5;%dm▐\x1b[0m", bgColor)
	content := fmt.Sprintf("\x1b[48;5;%dm\x1b[38;5;%dm%s\x1b[0m", bgColor, fgColor, label)
	rightCap := fmt.Sprintf("\x1b[38;5;%dm▌\x1b[0m", bgColor)
	return leftCap + content + rightCap
}

func pillWidth(label string) int {
	return visualWidth(label) + 2 // +1 for ▐, +1 for ▌
}

// closeButton renders a right-justified close button (" x ") with an ANSI
// color-1 (red) background. Width is always 3 visual cells.
func closeButton(focused bool) string {
	if focused {
		return "\x1b[7m x \x1b[0m"
	}
	return "\x1b[48;5;1m\x1b[38;5;15m x \x1b[0m"
}

const closeButtonW = 3

// View renders the button row padded to innerWidth. When runActive is true the
// send button shows ■ instead of ↑.
func (b ButtonRow) View(innerWidth int, focused bool, runActive bool) string {
	labels := buttonLabels
	if b.ModelLabel != "" {
		labels[2] = b.ModelLabel
	}
	if b.ModeLabel != "" {
		labels[4] = b.ModeLabel
	}

	leftParts := make([]string, len(labels))
	for i, label := range labels {
		leftParts[i] = bracketButton(label, focused && i == b.focusedIdx)
	}
	left := strings.Join(leftParts, " ")
	label := sendLabel
	if runActive {
		label = sendLabelActive
	}
	right := pill(label, sendBgColor, sendFgColor, focused && b.focusedIdx == buttonCount-1)

	leftPlain := 0
	for i, label := range labels {
		leftPlain += bracketWidth(label)
		if i < len(labels)-1 {
			leftPlain++ // space between buttons
		}
	}
	rightPlain := pillWidth(label)

	spacer := innerWidth - leftPlain - rightPlain
	if spacer < 1 {
		spacer = 1
	}
	return left + strings.Repeat(" ", spacer) + right
}
