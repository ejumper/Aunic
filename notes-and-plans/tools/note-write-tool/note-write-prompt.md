## Note Write Tool Prompt Outline
This file outlines how Aunic should structure the system prompt / tool description for `note-write`, using [write-prompt.md](/home/ejumps/HalfaCloud/Aunic/notes-and-plans/tools/write-tool/write-prompt.md) as the reference shape and adapting it to the note-mode contract in [note-write-tool.md](/home/ejumps/HalfaCloud/Aunic/notes-and-plans/tools/note-write-tool/note-write-tool.md).

The work-mode `write` prompt is short and strongly opinionated about when `write` should be used. The `note-write` prompt should keep that same style:
- a one-line tool description
- a `Usage:` block
- a strong reminder that this is a full-replacement tool
- a strong preference for `note-edit` when the change is only partial
- a narrow scope statement that this tool only affects `note-content`

## Core Reference Language
The file-write reference prompt text is:

```text
Writes a file to the local filesystem.

Usage:
- This tool will overwrite the existing file if there is one at the provided path.
- If this is an existing file, you MUST use the Read tool first to read the file's contents. This tool will fail if you did not read the file first.
- Prefer the Edit tool for modifying existing files — it only sends the diff. Only use this tool to create new files or for complete rewrites.
- NEVER create documentation files (*.md) or README files unless explicitly requested by the User.
- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked.
```

For `note-write`, Aunic should keep the same directness and “when to use this tool” framing, while removing the parts that are specific to filesystem writes.

## Recommended Aunic Prompt Structure
The Aunic `note-write` prompt should follow the same basic shape:

1. one-line description
2. blank line
3. `Usage:` section
4. full-replacement warning
5. prefer-`note-edit` rule
6. note-content-only scope rule
7. transcript / arbitrary-file exclusion rule
8. emoji warning
9. optional short reminder that Aunic handles live-note conflicts and the model should only use this tool when it really intends a full rewrite

## Required Dynamic Insertions
At the moment, `note-write` does not require any dynamic prompt insertions.

Unlike `write`, it does not need:
- a `file_path`
- a read-before-write reminder tied to filesystem state
- protected-path injection, because this tool is already the allowed writer for the active note scope

An optional future insertion point:
- the active note label or path, only if Aunic later wants the prompt to identify which note is being rewritten

### Keep the same ideas
- make overwrite / replacement explicit
- strongly steer the model toward the smaller edit tool for partial changes
- keep the tool narrowly scoped
- avoid emojis unless asked

### Adjust where `note-write` differs
- remove all `file_path` and filesystem wording
- remove the read-before-write rule, because the active note is already in context in note-mode
- replace `Edit` with `note-edit`
- replace file-creation / filesystem language with full-`note-content` replacement language
- remove the docs/README warning, because the target is already a markdown note and that work-mode heuristic does not apply here
- explicitly say the tool edits only the active note's `note-content`
- explicitly say it cannot edit the `transcript` or arbitrary files
- keep conflict resolution out of the main prompt except for a short reminder that Aunic handles live-note conflicts and the model should use this tool only when it intends to replace the whole note

## Suggested Aunic Prompt Outline
```text
Replaces the full contents of the active note's note-content.

Usage:
- This tool replaces the entire current note-content with the new content you provide.
- Prefer `note-edit` for modifying only part of the note. Only use this tool when you intend to draft, replace, or rewrite the full note-content.
- This tool edits only the active note's note-content. It does NOT edit the transcript or arbitrary files.
- Empty note-content is allowed, so this tool may also be used to clear the note entirely.
- Only use emojis if the user explicitly requests it. Avoid writing emojis to note-content unless asked.
- If the live note changed while you were working, Aunic will handle conflict resolution. Use this tool only when you really intend a full note-content replacement.
```

This language is intentionally based on the work-mode `write` prompt outline, but narrowed to the note-mode `note-write` contract.

## Compatibility Check
The final prompt doc should stay compatible with [note-write-tool.md](/home/ejumps/HalfaCloud/Aunic/notes-and-plans/tools/note-write-tool/note-write-tool.md).

It should not accidentally retain work-mode-only rules such as:
- `file_path`
- read-before-write
- external-directory handling
- work-mode permission language
- docs / README creation warnings

It should still clearly teach the important `note-write` behaviors:
- this is a full replacement of `note-content`
- prefer `note-edit` for targeted changes
- the tool cannot edit the `transcript`
- the tool cannot edit arbitrary files
- empty note-content is allowed

## Implementation Note
This prompt should stay concise.
- it should match the short, operational style of [write-prompt.md](/home/ejumps/HalfaCloud/Aunic/notes-and-plans/tools/write-tool/write-prompt.md)
- it should not overload the prompt with permission details, transcript internals, or the full conflict-resolution flow
- the detailed live-conflict behavior should remain in the broader `note-write` tool doc, not in the prompt itself
