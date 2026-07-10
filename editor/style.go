package editor

// Named ANSI SGR sequences used across the editor renderers. Kept as constants
// so the same styling choice reads the same everywhere and can't drift by a
// digit. These are the standalone escapes; sequences composed inside a larger
// string literal are left inline.
const (
	ansiReset      = "\x1b[0m"  // reset all attributes
	ansiReverse    = "\x1b[7m"  // reverse video — focus/selection highlight
	ansiReverseOff = "\x1b[27m" // reverse video off
	ansiDim        = "\x1b[2m"  // dim/faint text
	ansiGray       = "\x1b[90m" // bright-black — muted/placeholder text

	ansiSelectionBG = "\x1b[103m" // bright-yellow background — text selection
	ansiBGDefault   = "\x1b[49m"  // default background (closes a bg span)
)
