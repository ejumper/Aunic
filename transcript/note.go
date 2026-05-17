package transcript

import (
	"strings"
)

// delimiter marks the boundary between the editable note and the transcript.
// A note file uses "***\n# Transcript" — a horizontal rule followed by an h1.
const transcriptHeading = "# Transcript"

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

// Join reassembles a full note file from the editable note body and the
// rendered rows. When there are no rows, the delimiter is omitted entirely.
func Join(note string, rows []Row) string {
	if len(rows) == 0 {
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
	b.WriteString(Render(rows))
	return b.String()
}
