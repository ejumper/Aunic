package agent

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"
)

// ─── Command data ─────────────────────────────────────────────────────────────

type cmdCategory string

const (
	catAll     cmdCategory = "All"
	catFile    cmdCategory = "File"
	catTools   cmdCategory = "Tools"
	catAgent   cmdCategory = "Agent"
	catProgram cmdCategory = "Program"
)

var cmdCategories = []cmdCategory{catAll, catFile, catTools, catAgent, catProgram}

// CmdExecKind describes how a selected command is executed.
type CmdExecKind int

const (
	CmdExecSlash  CmdExecKind = iota // execute via executeSlashCmd
	CmdExecPrompt                    // place text in the prompt box (fallback)
	CmdExecWebBar                    // open the web search query bar
)

type cmdEntry struct {
	display    string
	token      string // e.g. "/find /replace"
	desc       string // e.g. ": search for text and replace it ..."
	category   cmdCategory
	execKind   CmdExecKind
	slashKind  SlashCmdKind // valid when execKind == CmdExecSlash
	promptText string       // valid when execKind == CmdExecPrompt
}

var allCmdEntries = []cmdEntry{
	// File
	{
		display:   "Find",
		token:     "/find",
		desc:      ": search file (/find query, ctrl+f)",
		category:  catFile,
		execKind:  CmdExecSlash,
		slashKind: SlashFind,
	},
	{
		display:   "Find & Replace",
		token:     "/find /replace",
		desc:      ": search & replace (/find text /replace text, ctrl+f x2)",
		category:  catFile,
		execKind:  CmdExecSlash,
		slashKind: SlashFindReplaceOpen,
	},
	{
		display:   "Fix Tables",
		token:     "/fix-tables",
		desc:      ": fix markdown tables (select table to fix only it)",
		category:  catFile,
		execKind:  CmdExecSlash,
		slashKind: SlashFixTables,
	},
	{
		display:   "Go to Line",
		token:     "/go",
		desc:      ": places cursor on specified line (/go #, ctrl+g)",
		category:  catFile,
		execKind:  CmdExecSlash,
		slashKind: SlashGotoOpen,
	},
	{
		display:    "Clear Markers",
		token:      "/clear",
		desc:       ": strip edit-command wrappers (/clear markers|@|!|$|%, combine like /clear @!)",
		category:   catFile,
		execKind:   CmdExecPrompt,
		promptText: "/clear ",
	},
	{
		display:   "!>> <<!",
		token:     "!>> <<!",
		desc:      ": send only text inside to model",
		category:  catFile,
		execKind:  CmdExecSlash,
		slashKind: SlashMarkerInclude,
	},
	{
		display:   "@>> <<@",
		token:     "@>> <<@",
		desc:      ": force model to write/edit here",
		category:  catFile,
		execKind:  CmdExecSlash,
		slashKind: SlashMarkerScope,
	},
	{
		display:   "$>> <<$",
		token:     "$>> <<$",
		desc:      ": protect area from model edits",
		category:  catFile,
		execKind:  CmdExecSlash,
		slashKind: SlashMarkerReadOnly,
	},
	{
		display:   "%>> <<%",
		token:     "%>> <<%",
		desc:      ": don't send text inside to model",
		category:  catFile,
		execKind:  CmdExecSlash,
		slashKind: SlashMarkerExclude,
	},
	// Tools
	{
		display:   "Web Search",
		token:     "/web",
		desc:      ": search the web powered by ddgr, (/web query)",
		category:  catTools,
		execKind:  CmdExecSlash,
		slashKind: SlashWeb,
	},
	{
		display:   "Todo List",
		token:     "/todo",
		desc:      ": author a todo list and send a prompt (requires agent: read or work)",
		category:  catTools,
		execKind:  CmdExecSlash,
		slashKind: SlashTodo,
	},
	{
		display:    "Chat to Note",
		token:      "/chat2note",
		desc:       ": condense chat into the note then remove it (/chat2note <extra guidance>)",
		category:   catTools,
		execKind:   CmdExecPrompt,
		promptText: "/chat2note ",
	},
	{
		display:    "Local Delegation",
		token:      "/local",
		desc:       ": set local model delegation mode (off / min / max)",
		category:   catTools,
		execKind:   CmdExecPrompt,
		promptText: "/local ",
	},
	// Agent
	{
		display:   "Agent Off",
		token:     "/off",
		desc:      ": disables agent tools",
		category:  catAgent,
		execKind:  CmdExecSlash,
		slashKind: SlashAgentOff,
	},
	{
		display:   "Chat Mode",
		token:     "/chat",
		desc:      ": switches to chat mode",
		category:  catAgent,
		execKind:  CmdExecSlash,
		slashKind: SlashChat,
	},
	{
		display:   "Copy Prompt",
		token:     "/cp",
		desc:      ": copy prompt & clear it (ctrl+shift+x)",
		category:  catAgent,
		execKind:  CmdExecSlash,
		slashKind: SlashCopy,
	},
	{
		display:   "Note Mode",
		token:     "/note",
		desc:      ": switches to note mode",
		category:  catAgent,
		execKind:  CmdExecSlash,
		slashKind: SlashNote,
	},
	{
		display:   "Read Mode",
		token:     "/read",
		desc:      ": enables agent read mode (read/grep/glob)",
		category:  catAgent,
		execKind:  CmdExecSlash,
		slashKind: SlashRead,
	},
	{
		display:   "Work Mode",
		token:     "/work",
		desc:      ": enables agent work mode (read/write/edit/grep/glob/bash)",
		category:  catAgent,
		execKind:  CmdExecSlash,
		slashKind: SlashWork,
	},
	{
		display:   "Pick Model",
		token:     "/model",
		desc:      ": change models (/model model-name, ctrl+m)",
		category:  catAgent,
		execKind:  CmdExecSlash,
		slashKind: SlashModel,
	},
	// Program
	{
		display:   "Background",
		token:     "/bg",
		desc:      ": sends aunic to background (ctrl+-, fg to reopen)",
		category:  catProgram,
		execKind:  CmdExecSlash,
		slashKind: SlashBg,
	},
	{
		display:    "Clear Transcript",
		token:      "/clear",
		desc:       ": remove rows from the transcript (/clear trans|chat|tool|search)",
		category:   catProgram,
		execKind:   CmdExecPrompt,
		promptText: "/clear ",
	},
}

// ─── Messages ────────────────────────────────────────────────────────────────

// CmdPickerOpenMsg triggers opening the command picker.
type CmdPickerOpenMsg struct{}

// CmdBarClosedMsg is emitted when the picker is dismissed without selection.
type CmdBarClosedMsg struct{}

// CmdBarSelectMsg is emitted when a command is selected.
type CmdBarSelectMsg struct {
	ExecKind   CmdExecKind
	SlashKind  SlashCmdKind
	PromptText string
}

// CmdBarIndicatorMsg requests the indicator to show the given text.
type CmdBarIndicatorMsg struct{ Text string }

// ─── CmdBar ──────────────────────────────────────────────────────────────────

const (
	cmdColGap  = 2
	cmdMaxRows = 8
)

type cmdLayout struct {
	cols      int
	rows      int
	colWidths []int
	colStarts []int // innerWidth-relative start of each column
}

// CmdBar is the command picker UI that replaces the prompt box.
type CmdBar struct {
	filtered     []cmdEntry
	activeFilter cmdCategory
	layout       cmdLayout
	innerWidth   int

	cursor       int // index in filtered; valid when !inFilterRow
	inFilterRow  bool
	filterCursor int // index into cmdCategories

	hoverIdx    int // -1 = none
	hoverFilter int // -1 = none
}

// NewCmdBar creates a CmdBar with all commands visible and "All" filter active.
func NewCmdBar(innerWidth int) CmdBar {
	cb := CmdBar{
		activeFilter: catAll,
		innerWidth:   innerWidth,
		hoverIdx:     -1,
		hoverFilter:  -1,
	}
	cb.applyFilter()
	return cb
}

func (cb *CmdBar) applyFilter() {
	var out []cmdEntry
	for _, e := range allCmdEntries {
		if cb.activeFilter == catAll || e.category == cb.activeFilter {
			out = append(out, e)
		}
	}
	sortEntriesAlpha(out)
	cb.filtered = out
	if cb.cursor >= len(cb.filtered) {
		cb.cursor = max(0, len(cb.filtered)-1)
	}
	cb.layout = computeCmdLayout(cb.filtered, cb.innerWidth)
}

func sortEntriesAlpha(entries []cmdEntry) {
	for i := 1; i < len(entries); i++ {
		for j := i; j > 0 && entries[j].display < entries[j-1].display; j-- {
			entries[j], entries[j-1] = entries[j-1], entries[j]
		}
	}
}

func computeCmdLayout(entries []cmdEntry, innerWidth int) cmdLayout {
	n := len(entries)
	if n == 0 {
		return cmdLayout{cols: 1, rows: 0}
	}

	tryLayout := func(cols int) (cmdLayout, bool) {
		rows := (n + cols - 1) / cols
		widths := make([]int, cols)
		// Row-major: col j contains items at indices j, j+cols, j+2*cols, …
		for j := 0; j < cols; j++ {
			for r := 0; ; r++ {
				idx := r*cols + j
				if idx >= n {
					break
				}
				if w := visualWidth(entries[idx].display); w > widths[j] {
					widths[j] = w
				}
			}
		}
		total := (cols - 1) * cmdColGap
		for _, w := range widths {
			total += w
		}
		if total > innerWidth {
			return cmdLayout{}, false
		}
		starts := make([]int, cols)
		pos := 0
		for j, w := range widths {
			starts[j] = pos
			pos += w + cmdColGap
		}
		return cmdLayout{cols: cols, rows: rows, colWidths: widths, colStarts: starts}, true
	}

	// Maximize columns (minimize rows): try from n down to 1.
	for cols := n; cols >= 1; cols-- {
		if layout, ok := tryLayout(cols); ok {
			return layout
		}
	}
	// Should never reach here (1 column always fits), but be safe.
	maxW := 0
	for _, e := range entries {
		if w := visualWidth(e.display); w > maxW {
			maxW = w
		}
	}
	return cmdLayout{cols: 1, rows: n, colWidths: []int{maxW}, colStarts: []int{0}}
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// Height returns content rows (grid + filter row).
func (cb CmdBar) Height() int {
	if cb.layout.rows == 0 {
		return 2 // empty message + filter row
	}
	return cb.layout.rows + 1
}

// InitialIndicatorText returns the indicator text for the current cursor entry,
// used to pre-fill the indicator when the bar is first opened.
func (cb CmdBar) InitialIndicatorText() string {
	if len(cb.filtered) == 0 {
		return ""
	}
	return cmdIndicatorText(cb.filtered[cb.cursor])
}

func cmdIndicatorText(e cmdEntry) string {
	return "\x1b[34m" + e.token + "\x1b[39m\x1b[2m" + colorDescInline(e.desc) + "\x1b[22m"
}

// colorDescInline colors /cmd and @cmd tokens found inside a description string
// (e.g. "(/find query, ctrl+f)") blue, leaving surrounding text faint.
// It assumes the string is already inside a \x1b[2m faint region.
func colorDescInline(desc string) string {
	runes := []rune(desc)
	var b strings.Builder
	for i := 0; i < len(runes); i++ {
		r := runes[i]
		// A command token starts with '/' or '@' preceded by ' ', '(' or start.
		if (r == '/' || r == '@') && (i == 0 || runes[i-1] == ' ' || runes[i-1] == '(') {
			b.WriteString("\x1b[22m\x1b[34m") // exit faint, enter blue
			for i < len(runes) && runes[i] != ' ' && runes[i] != ',' && runes[i] != ')' {
				b.WriteRune(runes[i])
				i++
			}
			b.WriteString("\x1b[39m\x1b[2m") // default color, back to faint
			i--                              // outer loop will increment
		} else {
			b.WriteRune(r)
		}
	}
	return b.String()
}

func cmdIndicatorCmd(text string) tea.Cmd {
	return func() tea.Msg { return CmdBarIndicatorMsg{Text: text} }
}

// ─── Update ──────────────────────────────────────────────────────────────────

func (cb CmdBar) Update(msg tea.Msg) (CmdBar, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		return cb.handleKey(msg)
	case tea.MouseMsg:
		return cb.handleMouse(msg)
	}
	return cb, nil
}

func (cb CmdBar) handleKey(msg tea.KeyMsg) (CmdBar, tea.Cmd) {
	n := len(cb.filtered)
	cols := cb.layout.cols

	if cb.inFilterRow {
		switch msg.String() {
		case "esc":
			return cb, func() tea.Msg { return CmdBarClosedMsg{} }
		case "left":
			if cb.filterCursor > 0 {
				cb.filterCursor--
			}
		case "right":
			if cb.filterCursor < len(cmdCategories)-1 {
				cb.filterCursor++
			}
		case "up":
			if n > 0 {
				cb.inFilterRow = false
				// Stay in the same column as filterCursor roughly maps to.
				rows := cb.layout.rows
				targetCol := (cb.filterCursor * cols) / len(cmdCategories)
				if targetCol >= cols {
					targetCol = cols - 1
				}
				// Last item in targetCol.
				for r := rows - 1; r >= 0; r-- {
					idx := r*cols + targetCol
					if idx < n {
						cb.cursor = idx
						break
					}
				}
				return cb, cmdIndicatorCmd(cmdIndicatorText(cb.filtered[cb.cursor]))
			}
		case "enter":
			newFilter := cmdCategories[cb.filterCursor]
			if newFilter != cb.activeFilter {
				cb.activeFilter = newFilter
				cb.cursor = 0
				cb.applyFilter()
				if len(cb.filtered) > 0 {
					return cb, cmdIndicatorCmd(cmdIndicatorText(cb.filtered[0]))
				}
			}
		}
		return cb, nil
	}

	// Grid navigation.
	switch msg.String() {
	case "esc":
		return cb, func() tea.Msg { return CmdBarClosedMsg{} }

	case "enter":
		if n > 0 && cb.cursor < n {
			e := cb.filtered[cb.cursor]
			return cb, func() tea.Msg {
				return CmdBarSelectMsg{
					ExecKind:   e.execKind,
					SlashKind:  e.slashKind,
					PromptText: e.promptText,
				}
			}
		}

	case "up":
		if cb.cursor >= cols {
			cb.cursor -= cols
			return cb, cmdIndicatorCmd(cmdIndicatorText(cb.filtered[cb.cursor]))
		}

	case "down":
		next := cb.cursor + cols
		if next < n {
			cb.cursor = next
			return cb, cmdIndicatorCmd(cmdIndicatorText(cb.filtered[cb.cursor]))
		}
		// At or past bottom: drop to filter row.
		cb.inFilterRow = true
		curCol := cb.cursor % cols
		cb.filterCursor = (curCol * len(cmdCategories)) / cols
		if cb.filterCursor >= len(cmdCategories) {
			cb.filterCursor = len(cmdCategories) - 1
		}
		return cb, nil

	case "left":
		if cb.cursor%cols > 0 {
			cb.cursor--
			return cb, cmdIndicatorCmd(cmdIndicatorText(cb.filtered[cb.cursor]))
		}

	case "right":
		if cb.cursor+1 < n {
			cb.cursor++
			return cb, cmdIndicatorCmd(cmdIndicatorText(cb.filtered[cb.cursor]))
		}
	}

	return cb, nil
}

func (cb CmdBar) handleMouse(msg tea.MouseMsg) (CmdBar, tea.Cmd) {
	// Coordinate system: Y=0 indicator, Y=1 top border, Y=2+ content.
	contentRow := msg.Y - 2
	contentCol := msg.X - 1 // subtract left border
	if contentRow < 0 || contentCol < 0 {
		return cb, nil
	}

	rows := cb.layout.rows
	isFilterRow := (rows == 0 && contentRow == 0) || contentRow == rows

	if isFilterRow {
		filterIdx := cb.hitTestFilter(contentCol)
		switch msg.Action {
		case tea.MouseActionMotion:
			if filterIdx != cb.hoverFilter {
				cb.hoverFilter = filterIdx
				cb.hoverIdx = -1
			}
			return cb, nil
		case tea.MouseActionPress:
			if msg.Button != tea.MouseButtonLeft || filterIdx < 0 {
				return cb, nil
			}
			cb.inFilterRow = true
			cb.filterCursor = filterIdx
			newFilter := cmdCategories[filterIdx]
			if newFilter != cb.activeFilter {
				cb.activeFilter = newFilter
				cb.cursor = 0
				cb.applyFilter()
				if len(cb.filtered) > 0 {
					return cb, cmdIndicatorCmd(cmdIndicatorText(cb.filtered[0]))
				}
			}
			return cb, nil
		}
		return cb, nil
	}

	if contentRow < rows {
		hitIdx := cb.hitTestGrid(contentRow, contentCol)
		switch msg.Action {
		case tea.MouseActionMotion:
			if hitIdx != cb.hoverIdx {
				cb.hoverIdx = hitIdx
				cb.hoverFilter = -1
				if hitIdx >= 0 {
					return cb, cmdIndicatorCmd(cmdIndicatorText(cb.filtered[hitIdx]))
				}
			}
			return cb, nil
		case tea.MouseActionPress:
			if msg.Button != tea.MouseButtonLeft || hitIdx < 0 {
				return cb, nil
			}
			e := cb.filtered[hitIdx]
			return cb, func() tea.Msg {
				return CmdBarSelectMsg{
					ExecKind:   e.execKind,
					SlashKind:  e.slashKind,
					PromptText: e.promptText,
				}
			}
		}
	}

	return cb, nil
}

func (cb CmdBar) hitTestGrid(contentRow, contentCol int) int {
	n := len(cb.filtered)
	cols := cb.layout.cols
	for j := range cb.layout.colStarts {
		start := cb.layout.colStarts[j]
		end := start + cb.layout.colWidths[j]
		if contentCol >= start && contentCol < end {
			idx := contentRow*cols + j
			if idx < n {
				return idx
			}
		}
	}
	return -1
}

func (cb CmdBar) hitTestFilter(contentCol int) int {
	col := 0
	for i, cat := range cmdCategories {
		w := visualWidth(string(cat)) + 2 // "[" + name + "]"
		if contentCol >= col && contentCol < col+w {
			return i
		}
		col += w + 1 // space separator
	}
	return -1
}

// ─── View ────────────────────────────────────────────────────────────────────

func (cb CmdBar) View(innerWidth int) []string {
	n := len(cb.filtered)
	rows := cb.layout.rows
	cols := cb.layout.cols

	out := make([]string, 0, cb.Height())

	if n == 0 {
		out = append(out, padTo("\x1b[2m\x1b[3m(no commands)\x1b[0m", innerWidth))
	} else {
		for r := 0; r < rows; r++ {
			var rowB strings.Builder
			written := 0
			for j := 0; j < cols; j++ {
				idx := r*cols + j
				if idx >= n {
					break
				}
				start := cb.layout.colStarts[j]
				if start > written {
					rowB.WriteString(strings.Repeat(" ", start-written))
					written = start
				}
				e := cb.filtered[idx]
				w := visualWidth(e.display)
				focused := !cb.inFilterRow && idx == cb.cursor
				hovered := idx == cb.hoverIdx
				switch {
				case focused:
					rowB.WriteString("\x1b[7m" + e.display + "\x1b[0m")
				case hovered:
					rowB.WriteString("\x1b[4m" + e.display + "\x1b[0m")
				default:
					rowB.WriteString(e.display)
				}
				written += w
			}
			out = append(out, padTo(rowB.String(), innerWidth))
		}
	}

	out = append(out, cb.renderFilterRow(innerWidth))
	return out
}

func (cb CmdBar) renderFilterRow(innerWidth int) string {
	var b strings.Builder
	for i, cat := range cmdCategories {
		if i > 0 {
			b.WriteByte(' ')
		}
		label := string(cat)
		isActive := cat == cb.activeFilter
		isFocused := cb.inFilterRow && i == cb.filterCursor
		isHovered := i == cb.hoverFilter

		switch {
		case isFocused && isActive:
			b.WriteString("\x1b[7m\x1b[1m[" + label + "]\x1b[0m")
		case isFocused:
			b.WriteString("\x1b[7m[" + label + "]\x1b[0m")
		case isActive:
			b.WriteString("\x1b[1m[" + label + "]\x1b[0m")
		case isHovered:
			b.WriteString("\x1b[4m[" + label + "]\x1b[0m")
		default:
			b.WriteString("[" + label + "]")
		}
	}
	return padTo(b.String(), innerWidth)
}
