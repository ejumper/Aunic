## Read Tool Prompt Outline
This file outlines how Aunic should structure the system prompt / tool description for `read`, based closely on `coding-agent-program-example/src/tools/FileReadTool/prompt.ts`.

The reference implementation builds this prompt from a template with a few runtime-inserted strings.

## Core Reference Language
The reference implementation's prompt text is:

```text
Reads a file from the local filesystem. You can access any file directly by using this tool.
Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid. It is okay to read a file that does not exist; an error will be returned.

Usage:
- The file_path parameter must be an absolute path, not a relative path
- By default, it reads up to 2000 lines starting from the beginning of the file[max-size instruction]
[offset instruction]
[line format instruction]
- This tool allows Claude Code to read images (eg PNG, JPG, etc). When reading an image file the contents are presented visually as Claude Code is a multimodal LLM.
- [If PDF support is enabled] This tool can read PDF files (.pdf). For large PDFs (more than 10 pages), you MUST provide the pages parameter to read specific page ranges (e.g., pages: "1-5"). Reading a large PDF without the pages parameter will fail. Maximum 20 pages per request.
- This tool can read Jupyter notebooks (.ipynb files) and returns all cells with their outputs, combining code, text, and visualizations.
- This tool can only read files, not directories. To read a directory, use an ls command via the Bash tool.
- You will regularly be asked to read screenshots. If the user provides a path to a screenshot, ALWAYS use this tool to view the file at the path. This tool will work with all temporary file paths.
- If you read a file that exists but has empty contents you will receive a system reminder warning in place of file contents.
```

The runtime-computed inserted strings in the reference implementation are:
- `max-size instruction`
- `offset instruction`
- `line format instruction`

## Recommended Aunic Prompt Structure
The Aunic `read` prompt should keep the same high-level order:

1. short description
2. direct-access reassurance
3. note that nonexistent-file reads can still be attempted
4. `Usage:` section
5. absolute-path rule
6. default read-size behavior
7. offset / limit behavior
8. line-number format rule
9. images support
10. PDF support
11. notebook support
12. "files only, not directories" rule
13. screenshot rule
14. empty-file behavior note

## Required Dynamic Insertions
To mirror the reference prompt closely, Aunic should generate the following pieces at runtime:

- line format instruction
  - describe Aunic's actual line-number output format
- max-size instruction
  - explain any byte/token or truncation rule that modifies the default "2000 lines from the beginning" language
- offset instruction
  - one version for ordinary use
  - possibly a stronger targeted-reading version if Aunic uses that distinction
- PDF support line
  - include only if PDF reading is enabled

## Aunic-Specific Deviations
Only adjust where necessary.

### Keep the same ideas
- direct file access framing
- absolute path preference
- offset/limit guidance
- image / screenshot emphasis
- file-not-directory warning

### Adjust where Aunic differs
- if Aunic supports home-directory reads differently in note-mode, that belongs in `note-read` prompting, not here
- if Aunic uses `list` instead of Bash `ls` for directories, replace that line accordingly
- if Aunic has different default line limits, page limits, or PDF thresholds, swap in the real values

## Suggested Aunic Prompt Outline
```text
Reads a file from the local filesystem. You can access any file directly by using this tool.
Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid. It is okay to read a file that does not exist; an error will be returned.

Usage:
- The file_path parameter must be an absolute path, not a relative path
- By default, it reads up to [Aunic default line limit] lines starting from the beginning of the file[Aunic max-size instruction]
[Aunic offset instruction]
[Aunic line format instruction]
- This tool allows Aunic to read images (eg PNG, JPG, etc). When reading an image file the contents are presented visually as the model is multimodal.
- [If supported] This tool can read PDF files (.pdf). For large PDFs, you MUST provide the pages parameter to read specific page ranges.
- This tool can read Jupyter notebooks (.ipynb files) and returns all cells with their outputs, combining code, text, and visualizations.
- This tool can only read files, not directories. To read a directory, use [Aunic's actual directory-browsing tool guidance].
- You will regularly be asked to read screenshots. If the user provides a path to a screenshot, ALWAYS use this tool to view the file at the path.
- If you read a file that exists but has empty contents you will receive a system reminder warning in place of file contents.
```

## Implementation Note
This prompt should remain practical and concrete.
- it is doing tool selection work
- it is not the place for deep permission/transcript discussion
