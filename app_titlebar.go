package main

import (
	"path/filepath"
	"strings"

	"github.com/mattn/go-runewidth"
)

// This file holds pure title-bar and exit-dialog rendering. None of these
// functions touch appModel directly — they take their inputs as parameters.
// The appModel methods that interpret mouse clicks against the title bar
// (handleTitleBarClick / handleDialogClick / executeDialog) live in app.go
// because they mutate the model.

const (
	titleSaveIcon   = "🖫"
	titleCloseLabel = "X"
	titleMinLabel   = "–"
)

// dialogOptionCols returns the column-prefix length and per-button column
// ranges used by the unsaved-changes dialog. handleDialogClick (in app.go)
// uses these to route mouse clicks to the right button.
func dialogOptionCols(termWidth int) (prefix string, starts, ends [3]int) {
	prefix = "Unsaved Changes! "
	labels := [3]string{"[save]", "[exit]", "[cancel]"}
	total := len(prefix)
	for _, l := range labels {
		total += len(l)
	}
	leftPad := (termWidth - total) / 2
	if leftPad < 0 {
		leftPad = 0
	}
	pos := leftPad + len(prefix)
	for i, l := range labels {
		starts[i] = pos
		ends[i] = pos + len(l)
		pos = ends[i]
	}
	return
}

// titleBarLayout returns the column boundaries of the interactive title bar
// elements for mouse hit-testing. All values are 0-indexed absolute columns.
//
//	save icon:  [0, saveEnd)   — col 0 is leading space, icon follows
//	minimize:   [minStart, minEnd)
//	close:      [closeStart, width)  — last col is trailing space
func titleBarLayout(width int) (saveEnd, minStart, minEnd, closeStart int) {
	saveW := runewidth.StringWidth(titleSaveIcon)
	closeW := runewidth.StringWidth(titleCloseLabel) // 1
	minW := runewidth.StringWidth(titleMinLabel)     // 1

	// col 0 = leading space, cols 1..saveW = icon
	saveEnd = 1 + saveW
	// col width-1 = trailing space, close occupies width-1-closeW..width-2
	closeStart = width - 1 - closeW
	// 1-space gap, then minimize
	minEnd = closeStart - 1
	minStart = minEnd - minW
	return
}

// truncateTitleStr truncates s to at most maxW visual cells, appending "…".
func truncateTitleStr(s string, maxW int) string {
	if runewidth.StringWidth(s) <= maxW {
		return s
	}
	budget := maxW - 1 // reserve 1 cell for "…"
	if budget <= 0 {
		return "…"
	}
	w := 0
	for i, r := range s {
		rw := runewidth.RuneWidth(r)
		if w+rw > budget {
			return s[:i] + "…"
		}
		w += rw
	}
	return s + "…"
}

// formatTitlePath splits fp into a faint directory prefix and a bold filename.
//
// homeDir and cwd are cached on appModel and passed in to avoid syscalls per
// render. Empty values disable the corresponding shortening (the function falls
// back to the absolute path), matching pre-cache behavior when the lookup fails.
// Priority: ~/... when under $HOME, then cwd-relative when strictly under cwd
// (no ".." traversal), then absolute path as fallback.
func formatTitlePath(fp, homeDir, cwd string) (dir, base string) {
	base = filepath.Base(fp)
	if base == "" || base == "." {
		base = "Untitled"
	}

	// ~/... when the file lives under the home directory.
	if homeDir != "" {
		if rel, err := filepath.Rel(homeDir, fp); err == nil && !strings.HasPrefix(rel, "..") {
			d := filepath.Dir(rel)
			if d == "." || d == "" {
				return "~/", base
			}
			return "~/" + d + string(filepath.Separator), base
		}
	}

	// Cwd-relative, but only when the file is strictly under cwd (no "..").
	if cwd != "" {
		if rel, err := filepath.Rel(cwd, fp); err == nil && !strings.HasPrefix(rel, "..") {
			d := filepath.Dir(rel)
			if d != "." && d != "" {
				return d + string(filepath.Separator), base
			}
			return "", base
		}
	}

	// Absolute path fallback.
	d := filepath.Dir(fp)
	if d != "" && d != "." {
		return d + string(filepath.Separator), base
	}
	return "", base
}

func renderTitleBar(width int, fp, homeDir, cwd string, unsaved, showDialog bool, dialogFocus int) string {
	if showDialog {
		return renderDialogBar(width, dialogFocus)
	}

	dir, base := formatTitlePath(fp, homeDir, cwd)
	if unsaved {
		base += "*"
	}

	saveW := runewidth.StringWidth(titleSaveIcon)
	leftW := 1 + saveW + 1 // leading space + icon + trailing space

	closeW := runewidth.StringWidth(titleCloseLabel)
	minW := runewidth.StringWidth(titleMinLabel)
	rightW := minW + 1 + closeW + 1 // min + space + close + trailing space

	centerAvail := width - leftW - rightW
	if centerAvail < 0 {
		centerAvail = 0
	}

	// Truncate to fit: drop dir first, then truncate base with "…".
	dirW := runewidth.StringWidth(dir)
	baseW := runewidth.StringWidth(base)
	if dirW+baseW > centerAvail {
		if baseW <= centerAvail {
			dir = "" // drop directory prefix; base alone fits
			dirW = 0
		} else {
			dir = ""
			dirW = 0
			base = truncateTitleStr(base, centerAvail)
			baseW = runewidth.StringWidth(base)
		}
	}

	centerPlain := dirW + baseW
	leftPad := (centerAvail - centerPlain) / 2
	if leftPad < 0 {
		leftPad = 0
	}
	rightPad := centerAvail - leftPad - centerPlain
	if rightPad < 0 {
		rightPad = 0
	}

	const (
		bg      = "\x1b[44m" // ANSI 4 background (blue)
		fgReset = "\x1b[39m" // reset foreground only, keep background
		rst     = "\x1b[0m"  // full reset
	)

	saveColor := "\x1b[37m" // ANSI 7 (white) — nothing to save
	if unsaved {
		saveColor = "\x1b[97m" // ANSI 15 (bright white) — unsaved
	}

	var b strings.Builder
	b.WriteString(bg)
	// Leading space + save icon
	b.WriteString(" ")
	b.WriteString(saveColor)
	b.WriteString(titleSaveIcon)
	b.WriteString(fgReset + " ")
	// Center padding + path
	b.WriteString(strings.Repeat(" ", leftPad))
	if dir != "" {
		b.WriteString("\x1b[2;3;37m") // faint italic white — file path
		b.WriteString(dir)
		b.WriteString("\x1b[22;23;39m") // reset faint, italic, fg
	}
	b.WriteString("\x1b[1;3;97m") // ANSI 15 (bright white) bold italic — file name
	b.WriteString(base)
	b.WriteString("\x1b[22;23;39m") // reset bold, italic, fg — keep bg
	b.WriteString(strings.Repeat(" ", rightPad))
	// Minimize — bold ANSI 11 (bright yellow)
	b.WriteString("\x1b[1;93m")
	b.WriteString(titleMinLabel)
	b.WriteString("\x1b[22;39m ")
	// Close — bold ANSI 9 (bright red) + trailing space
	b.WriteString("\x1b[1;91m")
	b.WriteString(titleCloseLabel)
	b.WriteString("\x1b[22;39m " + rst)

	return b.String()
}

func renderDialogBar(width, dialogFocus int) string {
	const (
		base       = "\x1b[4m\x1b[34m"
		focusOpen  = "\x1b[44m\x1b[97m"
		focusClose = "\x1b[0m\x1b[4m\x1b[34m"
		rst        = "\x1b[0m"
	)

	prefix, _, _ := dialogOptionCols(width)
	labels := [3]string{"[save]", "[exit]", "[cancel]"}

	total := len(prefix)
	for _, l := range labels {
		total += len(l)
	}
	leftPad := (width - total) / 2
	if leftPad < 0 {
		leftPad = 0
	}
	rightPad := width - leftPad - total
	if rightPad < 0 {
		rightPad = 0
	}

	italicLabel := "\x1b[3mUnsaved Changes!\x1b[23m "

	var b strings.Builder
	b.WriteString(base)
	b.WriteString(strings.Repeat(" ", leftPad))
	b.WriteString(italicLabel)
	for i, label := range labels {
		if i == dialogFocus {
			b.WriteString(focusOpen)
			b.WriteString(label)
			b.WriteString(focusClose)
		} else {
			b.WriteString(label)
		}
	}
	b.WriteString(strings.Repeat(" ", rightPad))
	b.WriteString(rst)
	return b.String()
}
