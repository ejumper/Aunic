## Write Tool Prompt Outline
This file outlines how Aunic should structure the system prompt / tool description for `write`, based closely on `coding-agent-program-example/src/tools/FileWriteTool/prompt.ts`.

The reference prompt is short and strongly opinionated about when `write` should be used.

## Core Reference Language
The reference implementation's prompt text is:

```text
Writes a file to the local filesystem.

Usage:
- This tool will overwrite the existing file if there is one at the provided path.
- If this is an existing file, you MUST use the Read tool first to read the file's contents. This tool will fail if you did not read the file first.
- Prefer the Edit tool for modifying existing files — it only sends the diff. Only use this tool to create new files or for complete rewrites.
- NEVER create documentation files (*.md) or README files unless explicitly requested by the User.
- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked.
```

## Recommended Aunic Prompt Structure
The Aunic `write` prompt should follow the same shape:

1. one-line description
2. blank line
3. `Usage:` section
4. overwrite warning
5. pre-read rule for existing files
6. prefer-edit rule
7. docs/README warning
8. emoji warning

## Aunic-Specific Deviations
Only change what is necessary.

### Keep the same ideas
- overwrite is explicit
- existing files must be read first
- prefer `edit` for modifications
- avoid unrequested docs/README creation
- avoid emojis unless asked

### Adjust where Aunic differs
- if Aunic wants the docs/README rule to be broader or narrower, adjust that one line only
- if Aunic has a stronger `note-content` boundary, that should live elsewhere in the broader tool docs, not in the short prompt

## Suggested Aunic Prompt Outline
```text
Writes a file to the local filesystem.

Usage:
- This tool will overwrite the existing file if there is one at the provided path.
- If this is an existing file, you MUST use the Read tool first to read the file's contents. This tool will fail if you did not read the file first.
- Prefer the Edit tool for modifying existing files — it only sends the diff. Only use this tool to create new files or for complete rewrites.
- NEVER create documentation files (*.md) or README files unless explicitly requested by the User.
- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked.
```

## Implementation Note
This prompt works because it is narrow and directive.
- it tells the model exactly when `write` is appropriate
- it keeps common low-value behavior, like surprise docs creation, under control
