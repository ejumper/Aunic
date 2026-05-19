package transcript

import (
	"strings"
)

// delimiter marks the boundary between the editable note and the transcript.
// A note file uses "***\n# Transcript" — a horizontal rule followed by an h1.
const transcriptHeading = "# Transcript"

// todosHeading marks the sub-section inside the transcript area that holds the
// persistent todo list. Same "***\n<heading>" shape as the main delimiter.
const todosHeading = "## Todos"

// Split separates the full file text into the editable note body and the
// transcript section (without the delimiter lines themselves). If no delimiter
// is found, transcript is "" and note is the full text.
//
// The delimiter is recognized as a line containing only "***" (possibly with
// surrounding whitespace) immediately followed by a line equal to
// "# Transcript".
func Split(text string) (note, transcript string) {
	lines := strings.Split(text, "\n")
	for i := 0; i < len(lines)-1; i++ {
		if strings.TrimSpace(lines[i]) != "***" {
			continue
		}
		if strings.TrimSpace(lines[i+1]) != transcriptHeading {
			continue
		}
		note = strings.Join(lines[:i], "\n")
		// Trim a single trailing newline from the note body so the *** delimiter
		// doesn't visually appear to leave a blank line behind when reassembled.
		note = strings.TrimRight(note, "\n")
		transcript = strings.Join(lines[i+2:], "\n")
		return
	}
	return text, ""
}

// SplitArea separates a transcript area into the rows-table portion and the
// todos section body (without their respective delimiters). If no
// "***\n## Todos" delimiter is found, todos is "" and tableArea is the full
// input.
func SplitArea(area string) (tableArea, todos string) {
	lines := strings.Split(area, "\n")
	for i := 0; i < len(lines)-1; i++ {
		if strings.TrimSpace(lines[i]) != "***" {
			continue
		}
		if strings.TrimSpace(lines[i+1]) != todosHeading {
			continue
		}
		tableArea = strings.Join(lines[:i], "\n")
		tableArea = strings.TrimRight(tableArea, "\n")
		todos = strings.Join(lines[i+2:], "\n")
		return
	}
	return area, ""
}

// Join reassembles a full note file from the editable note body, the rendered
// transcript rows, and the rendered todos block. When both rows and todosText
// are empty, the transcript delimiter is omitted entirely. When only todos are
// present without rows, an empty transcript heading is still emitted so the
// file layout stays consistent (the ## Todos section always lives inside the
// transcript area).
func Join(note string, rows []Row, todosText string) string {
	if len(rows) == 0 && todosText == "" {
		return note
	}
	var b strings.Builder
	b.WriteString(note)
	// Ensure exactly one trailing newline before the delimiter.
	if !strings.HasSuffix(note, "\n") {
		b.WriteByte('\n')
	}
	b.WriteString("***\n")
	b.WriteString(transcriptHeading)
	b.WriteString("\n\n")
	if len(rows) > 0 {
		b.WriteString(Render(rows))
	}
	if todosText != "" {
		if len(rows) > 0 {
			b.WriteByte('\n')
		}
		b.WriteString("***\n")
		b.WriteString(todosHeading)
		b.WriteString("\n\n")
		b.WriteString(todosText)
		b.WriteByte('\n')
	}
	return b.String()
}
