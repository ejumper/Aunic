// Package prompts holds embedded system prompt templates for Aunic harnesses.
// Edit the .md files in this directory to refine prompt content without
// recompiling the application logic.
package prompts

import _ "embed"

//go:embed pi_system.md
var PiSystem string

//go:embed claude_system.md
var ClaudeSystem string
