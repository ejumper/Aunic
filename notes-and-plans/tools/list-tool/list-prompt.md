## List Tool Prompt Outline
This file outlines how Aunic should structure the system prompt / tool description for `list`.

Important reference note:
- I could not find a one-to-one `ListTool/prompt.ts` in `coding-agent-program-example/src/tools`
- the reference app appears to rely on other tools plus shell guidance for directory listing, instead of exposing a dedicated local filesystem `list` tool the way Aunic does

That means this file is necessarily an Aunic-specific adaptation, not a direct prompt port.

## Closest Reference Behavior
The closest reference behavior is:
- the Bash prompt strongly steers the model away from using shell for tasks that have dedicated tools
- the Read prompt explicitly says directories should be handled with `ls` via Bash, because the reference app does not seem to have a standalone local `list` tool

So Aunic's `list` prompt should be written in the same general style as the reference app's simple capability prompts:
- short
- operational
- explicit about when to use it
- explicit about when to use another tool instead

## Recommended Aunic Prompt Structure
Suggested order:
1. one-line description
2. small bullet list of capabilities
3. examples of what it returns
4. "use this tool when..." guidance
5. "use glob / grep / read instead when..." guidance

## Suggested Aunic Prompt Language
Because there is no direct reference prompt file, the language below is the recommended closest-style adaptation:

```text
- Fast directory tree listing tool for exploring filesystem structure
- Lists files and directories under a path without reading file contents
- Use this tool when you need to understand how a directory is organized
- Use this tool for browsing a repo or subdirectory before choosing files to read
- Use Glob when you need to find files by name patterns
- Use Grep when you need to search file contents
- Use Read when you already know the specific file you want to inspect
- Do not use Bash `ls`, `find`, or `tree` for ordinary directory browsing when this tool can do the job
```

## Aunic-Specific Deviations
This entire prompt is an Aunic-specific deviation because the reference app does not expose a matching dedicated tool prompt.

Still, it should preserve the reference app's prompt style:
- concise
- tool-selection focused
- no long implementation detail dump

## Implementation Note
If Aunic later adds more list-specific parameters such as `ignore`, `depth`, or output modes, add one or two short prompt bullets for them.
- keep it short
- put full behavior details in `list-tool.md`, not here
