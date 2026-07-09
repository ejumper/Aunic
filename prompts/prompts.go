// Package prompts holds embedded system prompt templates for Aunic harnesses.
// Edit the .md files in this directory to refine prompt content without
// recompiling the application logic.
package prompts

import _ "embed"

//go:embed pi_system.md
var PiSystem string

//go:embed pi_note_mode.md
var PiNoteMode string

//go:embed pi_read_mode.md
var PiReadMode string

//go:embed pi_off_mode.md
var PiOffMode string

//go:embed claude_system.md
var ClaudeSystem string
