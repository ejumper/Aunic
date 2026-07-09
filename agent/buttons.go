package agent

import (
	"fmt"
	"strings"
)

// The five left-side buttons use bracket style. Indices 2 (model), 3 (agent),
// and 4 (mode) can be overridden at render time via ButtonRow fields.
var buttonLabels = [5]string{"+", "/", "kimi k2.6", "agent: off", "mode: note"}

// The send button (index 6) uses a pill style with a blue background.
const (
	sendLabel       = "↑"
	sendLabelActive = "■"
	sendBgColor     = 4
	sendFgColor     = 15
	buttonCount     = 7 // 5 bracket buttons + 1 voice button + 1 pill button
)

// ButtonRow renders the single-line button bar.
type ButtonRow struct {
	focusedIdx int
	ModelLabel string // replaces the placeholder label at button index 2 when non-empty
	AgentLabel string // replaces the placeholder label at button index 3 when non-empty
	ModeLabel  string // replaces the placeholder label at button index 4 when non-empty
	VoiceLabel string // 🔇 or 🔈 at button index 5
}

// bracketButtonColor returns the ANSI foreground color (0–7) for a button
// label, or -1 if no special color applies.
func bracketButtonColor(label string) int {
	switch label {
	case "agent: off":
		return 0
	case "agent: read":
		return 6
	case "agent: work":
		return 5
	case "mode: note":
		return 2
	case "mode: chat":
		return 3
	}
	return -1
}

func bracketButton(label string, focused bool) string {
	color := bracketButtonColor(label)
	if color >= 0 {
		fg := fmt.Sprintf("\x1b[3%dm", color)
		if focused {
			// Set color first so reverse-video uses it as the background.
			return fg + "\x1b[7m[" + label + "]\x1b[0m"
		}
		return fg + "[" + label + "]\x1b[39m"
	}
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
// Index 5 is the voice toggle button (🔇/🔈).
func ButtonXRange(i int, modelLabel, agentLabel, modeLabel, voiceLabel string) (startX, endX int) {
	labels := buttonLabels
	if modelLabel != "" {
		labels[2] = modelLabel
	}
	if agentLabel != "" {
		labels[3] = agentLabel
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
	// Index 5: voice button (follows the 5 bracket buttons)
	vl := voiceLabel
	if vl == "" {
		vl = "🔇"
	}
	vw := bracketWidth(vl)
	if i == 5 {
		return col, col + vw
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
	if b.AgentLabel != "" {
		labels[3] = b.AgentLabel
	}
	if b.ModeLabel != "" {
		labels[4] = b.ModeLabel
	}

	leftParts := make([]string, len(labels))
	for i, label := range labels {
		leftParts[i] = bracketButton(label, focused && i == b.focusedIdx)
	}

	// Voice toggle button (index 5), between [mode] and the send pill.
	voiceLabel := b.VoiceLabel
	if voiceLabel == "" {
		voiceLabel = "🔇"
	}
	leftParts = append(leftParts, bracketButton(voiceLabel, focused && b.focusedIdx == 5))

	left := strings.Join(leftParts, " ")
	label := sendLabel
	if runActive {
		label = sendLabelActive
	}
	right := pill(label, sendBgColor, sendFgColor, focused && b.focusedIdx == buttonCount-1)

	leftPlain := 0
	for i, lbl := range append(labels[:], voiceLabel) {
		leftPlain += bracketWidth(lbl)
		if i < len(labels) { // space after each bracket button except before the pill
			leftPlain++
		}
	}
	rightPlain := pillWidth(label)

	spacer := innerWidth - leftPlain - rightPlain
	if spacer < 1 {
		spacer = 1
	}
	return left + strings.Repeat(" ", spacer) + right
}
