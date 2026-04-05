## Note Edit Tool Prompt Outline
This file outlines how Aunic should structure the system prompt / tool description for `note-edit`.

The reference prompt for file editing should be short and direct.
- a one-line tool description
- a `Usage:` block
- exact guidance about preserving note text
- guidance about uniqueness and `replace_all`
- guidance about using the smallest safely unique `old_string`

## Core Reference Language
The file-edit reference prompt text is:

```text
Performs exact string replacements in files.

Usage:
- NEVER write new files.
- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.
- The edit will FAIL if `old_string` is not unique in the file. Either provide a larger string with more surrounding context to make it unique or use `replace_all` to change every instance of `old_string`.
- Use `replace_all` for replacing and renaming strings across the file. This parameter is useful if you want to rename a variable for instance.
- Use the smallest old_string that's clearly unique — usually 2-4 adjacent lines is sufficient. Avoid including 10+ lines of context when less uniquely identifies the target.
```

For `note-edit`, Aunic should keep as much of that language and shape as practical, while removing the file-specific rules that do not apply.

## Recommended Aunic Prompt Structure
The Aunic `note-edit` prompt should follow the same basic shape:

1. one-line description
2. blank line
3. `Usage:` section
4. exact note-text / rendering-prefix rule
5. "prefer `note-edit` for targeted changes" rule
6. "use `note-write` for full rewrites" rule
7. "this tool only edits `note-content`" rule
8. emoji rule
9. uniqueness / `replace_all` rule
10. optional "minimal unique context" rule
11. optional short reminder that Aunic handles live-note conflicts and the model should keep edits precise

## Edit Prompt 
```text
Performs exact string replacements in the active note's note-content.

Usage:
- When editing text from the note UI, ensure you preserve the exact indentation and whitespace.
- ALWAYS prefer `note-edit` for targeted changes inside the current note-content.
- Use `note-write` instead of `note-edit` when you want to replace the full contents of note-content.
- This tool edits only the active note's note-content. It does NOT edit the transcript or arbitrary files.
- This tool does not create new files 
- This tool does not replace note-content from scratch; use `note-write` for that.
- Only use emojis if the user explicitly requests it. Avoid adding emojis to note-content unless asked.
- The edit will FAIL if `old_string` is not unique in note-content. Either provide a larger string with more surrounding context to make it unique or use `replace_all` to change every instance of `old_string`.
- Use `replace_all` for replacing and renaming repeated strings across the note.
- Use the smallest old_string that's clearly unique — usually 2-4 adjacent lines is sufficient. Avoid including 10+ lines of context when less uniquely identifies the target.
```

