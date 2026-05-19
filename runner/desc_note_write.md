Replace the entire active markdown note with the provided content. Ends the run.

<usage>
Prefer note_edit for targeted additions or changes to an existing note. Use note_write when:
- The note is empty (most common case).
- You need to restructure the entire note from scratch.
- You have several disconnected edits across the note (since only one note_edit is allowed per run).
- The user explicitly asks you to rewrite or replace the note.
</usage>

<run_behavior>
Calling note_write is your final action for the run — the user sees the result immediately. Complete ALL research and tool calls before calling note_write.
</run_behavior>

<parameters>
- content: The complete new note content. Must be the full document — not a fragment. 
  Required.
</parameters>

<warnings>
PRESERVE EXISTING CONTENT: If the note already has sections, include all of them in your content unless you intentionally want to remove them. note_write replaces everything — there is no merge.
</warnings>

<format>
Use standard markdown unless the note already uses a different format (paragraph prose, outline, etc.).
</format>
