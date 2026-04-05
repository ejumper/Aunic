## Edit Tool Prompt Outline
This file outlines how Aunic should structure the system prompt / tool description for `edit`, based closely on `coding-agent-program-example/src/tools/FileEditTool/prompt.ts`.

The reference prompt is short and direct. It is mostly:
- a one-line tool description
- a `Usage:` block
- a strict read-before-edit instruction
- exact guidance about line-numbered `read` output
- guidance about uniqueness and `replace_all`

## Core Reference Language
The reference implementation's prompt text is:

```text
Performs exact string replacements in files.

Usage:
- You must use your `Read` tool at least once in the conversation before editing. This tool will error if you attempt an edit without reading the file.
- When editing text from Read tool output, ensure you preserve the exact indentation (tabs/spaces) as it appears AFTER the line number prefix. The line number prefix format is: [runtime line prefix format]. Everything after that is the actual file content to match. Never include any part of the line number prefix in the old_string or new_string.
- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.
- The edit will FAIL if `old_string` is not unique in the file. Either provide a larger string with more surrounding context to make it unique or use `replace_all` to change every instance of `old_string`.
- Use `replace_all` for replacing and renaming strings across the file. This parameter is useful if you want to rename a variable for instance.
```

For some internal/user-type variants, the reference prompt also conditionally adds:

```text
- Use the smallest old_string that's clearly unique — usually 2-4 adjacent lines is sufficient. Avoid including 10+ lines of context when less uniquely identifies the target.
```

## Recommended Aunic Prompt Structure
The Aunic `edit` prompt should follow the same shape:

1. one-line description
2. blank line
3. `Usage:` section
4. read-before-edit rule
5. exact line-number-prefix rule
6. "prefer edit for existing files" rule
7. emoji rule
8. uniqueness / `replace_all` rule
9. "use `write` for full rewrites" rule
10. "never edit the active note scope" rule
11. optional "minimal unique context" rule

## Required Dynamic Insertions
The reference prompt computes one runtime-specific phrase:
- the exact line-number prefix format used by the `read` tool

Aunic should do the same.
- if Aunic numbers lines differently in `read`, the prompt must describe Aunic's actual format
- if Aunic changes read rendering later, this prompt text should be generated from the same source of truth

Aunic should also inject one Aunic-specific runtime value:
- the protected active note path or note-scope paths from the runtime `active_markdown_note` object

## Aunic-Specific Deviations
The prompt language should stay very close to the reference text, with only necessary deviations.

### Keep the same ideas
- require a full `read` of the target file before ordinary edits
- remind the model not to include line numbers in `old_string` / `new_string`
- emphasize exact indentation
- explain why non-unique matches fail
- explicitly mention `replace_all`

### Adjust where Aunic differs
- if Aunic allows file creation through `edit` when `old_string` is empty and the file does not exist, that should be acknowledged separately in Aunic's broader tool docs, but the prompt should still keep the "prefer editing existing files" instruction
- Aunic should explicitly forbid using `edit` on the protected active note scope and tell the model to use `note-edit` / `note-write` instead
- Aunic should explicitly steer the model to `write` for full-file replacement
- Aunic's natural-stop or transcript-specific behavior does not need to be mentioned in the `edit` prompt

## Suggested Aunic Prompt Outline
```text
Performs exact string replacements in files.

Usage:
- You must fully read the target file with your `Read` tool before editing it. This tool will error if you attempt an edit without first reading that file.
- When editing text from Read tool output, ensure you preserve the exact indentation (tabs/spaces) as it appears AFTER the line number prefix. The line number prefix format is: [Aunic runtime format]. Everything after that is the actual file content to match. Never include any part of the line number prefix in the old_string or new_string.
- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
- Use `write` instead of `edit` when you want to replace the full contents of a file.
- NEVER use `edit` on the active note's note-content. The protected note scope for this run is: [active_markdown_note.note_scope_paths]. Use `note-edit` or `note-write` for those files instead.
- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.
- The edit will FAIL if `old_string` is not unique in the file. Either provide a larger string with more surrounding context to make it unique or use `replace_all` to change every instance of `old_string`.
- Use `replace_all` for replacing and renaming strings across the file. This parameter is useful if you want to rename a variable for instance.
- Use the smallest old_string that's clearly unique — usually 2-4 adjacent lines is sufficient. Avoid including 10+ lines of context when less uniquely identifies the target.
```

## Implementation Note
This prompt should stay concise.
- the reference version works because it is short, specific, and operational
- Aunic should not overload it with permission-system details or transcript internals
