package claude

import (
	"fmt"
	"strings"

	"github.com/ejumper/aunic/markers"
)

// BuildPrompt assembles the full text sent via Process.SendPrompt. noteCtx
// and webCtx are already fully tagged (or empty, if not applicable this
// turn); userPrompt is wrapped here. Each distinct kind of non-user-authored
// content gets its own XML-style tag so the model can structurally tell
// "reference material Aunic is providing" apart from "the user's actual
// words" — flat string concatenation was explicitly rejected because it
// blurs that boundary.
func BuildPrompt(userPrompt, noteCtx, webCtx string) string {
	var b strings.Builder
	if noteCtx != "" {
		b.WriteString(noteCtx)
		b.WriteString("\n\n")
	}
	if webCtx != "" {
		fmt.Fprintf(&b, "<web-search-results>\n%s\n</web-search-results>\n\n", webCtx)
	}
	fmt.Fprintf(&b, "<user-request>\n%s\n</user-request>", userPrompt)
	return b.String()
}

// BuildNoteContext renders the marker-filtered snapshot as a tagged block,
// including a short legend explaining the HTML-comment annotations only when
// the note actually uses shaping markers (snap.HasShaping) — keeps the
// common (no-markers) case free of unused-concept noise.
//
// This is a prompt-level mitigation, not real enforcement: markers.Snapshot's
// write-scope/exclude/protected machinery (WritePolicy, Protected, Slots,
// ApplyEdits/ResolveEdit/ResolveWrite) is fully implemented but consumed
// nowhere in this harness — it was built for a bespoke note_write/note_edit
// tool interface that no longer exists. There is no code path that would
// reject or repair a violation of the boundaries described in the legend.
func BuildNoteContext(notePath string, snap markers.Snapshot) string {
	var b strings.Builder
	fmt.Fprintf(&b, "<note-context path=%q>\n", notePath)
	if snap.HasShaping {
		b.WriteString(markerLegend)
	}
	b.WriteString(snap.Visible)
	b.WriteString("\n</note-context>")
	return b.String()
}

const markerLegend = `This note uses edit-scope annotations rendered as HTML comments:
- <!--Write #N location--> or <!--Rewrite #N start-->...<!--Rewrite #N end--> mark the ONLY regions you may edit. Everything else is shown for context but must not be changed.
- <!--PROTECTED #N start: NO EDITS-->...<!--PROTECTED #N end--> marks content you must never modify or remove.
- <!-- elided --> marks content hidden from you entirely — you have no access to it and must not guess at or reconstruct it.
When you edit the underlying note file, preserve the original marker syntax (@>>/<<@, %>>/<<%, etc.) verbatim — only change the content between them, never delete the marker tokens themselves.

`
