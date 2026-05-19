package runner

import (
	"context"
	_ "embed"
	"strings"

	openai "github.com/charmbracelet/openai-go"
	"github.com/ejumper/aunic/llm"
	"github.com/ejumper/aunic/transcript"
)

//go:embed chat2note_step1.md
var chat2noteStep1Prompt string

//go:embed chat2note_step2.md
var chat2noteStep2PromptPrefix string

// Chat2NoteStep1 runs the structuring step: presents the transcript rows as
// API conversation history (the same format the model sees on every normal
// turn) and asks the model to restructure them into four labeled sections.
// Returns the model's raw text response.
//
// Tools are NOT registered for this call — the model must produce plain text
// with the four sections. The system prompt enforces this; the no-tools
// request guarantees it.
//
// The conversation history is followed by a final user message that points
// the model at the system prompt's instructions. This pattern (history
// followed by an immediate task pointer) avoids the model misinterpreting
// the chat itself as the task.
func Chat2NoteStep1(ctx context.Context, cfg llm.Config, rows []transcript.Row) (string, error) {
	msgs := transcript.ToAPIMessages(rows)
	msgs = append(msgs,
		openai.UserMessage("Restructure the conversation above into the four labeled sections per the system instructions. Output plain text only — no tool calls."),
	)
	return llm.OneShotChat(ctx, cfg, chat2noteStep1Prompt, msgs)
}

// Chat2NoteStep2Prompt builds the user-prompt string for step 2: the
// integration-prompt prefix (loaded from chat2note_step2.md) followed by the
// cleaned intermediate. extra is optional user-supplied guidance appended
// after the prefix.
func Chat2NoteStep2Prompt(cleanedIntermediate, extra string) string {
	var b strings.Builder
	b.WriteString(chat2noteStep2PromptPrefix)
	if strings.TrimSpace(extra) != "" {
		b.WriteString("\nAdditional user instructions: ")
		b.WriteString(strings.TrimSpace(extra))
		b.WriteString("\n\n---\n")
	}
	b.WriteString("\n")
	b.WriteString(cleanedIntermediate)
	return b.String()
}

// CleanChat2NoteIntermediate strips sections from the structuring output
// before it is fed to step 2:
//
//   - the Superfluous Information section is always dropped (its purpose is
//     to give step 1 a place to put low-value content rather than discarding
//     it — step 2 should never see it),
//   - any section whose body is empty or only contains a "none" placeholder
//     is dropped to avoid polluting step 2's context.
//
// Heading matching is case-insensitive and tolerant of surrounding
// whitespace. Sub-headings inside a section (### or deeper) are preserved as
// part of the section body.
func CleanChat2NoteIntermediate(raw string) string {
	lines := strings.Split(raw, "\n")

	type section struct {
		heading string   // verbatim heading line, e.g. "## Primary Decisions and Action Items"
		title   string   // normalized title, e.g. "primary decisions and action items"
		body    []string // lines following the heading, up to the next ## heading
	}

	var prelude []string
	var sections []section
	cur := -1

	for _, line := range lines {
		if isLevel2Heading(line) {
			title := normalizeHeadingTitle(line)
			sections = append(sections, section{heading: line, title: title})
			cur = len(sections) - 1
			continue
		}
		if cur < 0 {
			prelude = append(prelude, line)
		} else {
			sections[cur].body = append(sections[cur].body, line)
		}
	}

	var out []string
	out = append(out, prelude...)
	for _, s := range sections {
		if s.title == "superfluous information" {
			continue
		}
		bodyText := strings.TrimSpace(strings.Join(s.body, "\n"))
		if bodyText == "" || isNonePlaceholder(bodyText) {
			continue
		}
		out = append(out, s.heading)
		out = append(out, s.body...)
	}

	cleaned := strings.TrimSpace(strings.Join(out, "\n"))
	return cleaned
}

// isLevel2Heading reports whether line is a markdown `## Heading` line (but
// not `### Subheading` or deeper).
func isLevel2Heading(line string) bool {
	t := strings.TrimLeft(line, " \t")
	if !strings.HasPrefix(t, "## ") {
		return false
	}
	// "### ..." starts with "## " too — exclude it.
	if strings.HasPrefix(t, "### ") {
		return false
	}
	return true
}

// normalizeHeadingTitle extracts the title text from a level-2 heading line
// and returns it lower-cased with surrounding whitespace trimmed.
func normalizeHeadingTitle(line string) string {
	t := strings.TrimLeft(line, " \t")
	t = strings.TrimPrefix(t, "## ")
	return strings.ToLower(strings.TrimSpace(t))
}

// isNonePlaceholder reports whether body is one of the various "no content"
// placeholders the model might emit when a section has nothing to record.
func isNonePlaceholder(body string) bool {
	b := strings.ToLower(strings.TrimSpace(body))
	b = strings.Trim(b, "()[]")
	b = strings.TrimSpace(b)
	switch b {
	case "none", "n/a", "na", "nothing", "empty", "no content", "no decisions", "no information", "no files":
		return true
	}
	return false
}

