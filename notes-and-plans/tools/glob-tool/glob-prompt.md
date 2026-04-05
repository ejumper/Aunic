## Glob Tool Prompt Outline
This file outlines how Aunic should structure the system prompt / tool description for `glob`, based closely on `coding-agent-program-example/src/tools/GlobTool/prompt.ts`.

Note:
- the local directory now matches the tool name: `glob-tool`

## Core Reference Language
The reference implementation's prompt text is:

```text
- Fast file pattern matching tool that works with any codebase size
- Supports glob patterns like "**/*.js" or "src/**/*.ts"
- Returns matching file paths sorted by modification time
- Use this tool when you need to find files by name patterns
- When you are doing an open ended search that may require multiple rounds of globbing and grepping, use the Agent tool instead
```

This is more of a bullet-style description than a full `Usage:` block.

## Recommended Aunic Prompt Structure
The Aunic `glob` prompt should stay similarly short.

Suggested order:
1. speed / capability statement
2. pattern examples
3. result-ordering statement
4. "when to use this tool" statement
5. open-ended search escalation statement

## Aunic-Specific Deviations
Only change what is necessary.

### Keep if accurate
- "Fast file pattern matching tool that works with any codebase size"
- the example glob patterns
- "Use this tool when you need to find files by name patterns"

### Adjust if Aunic differs
- if Aunic does not actually sort by modification time in its main path, replace that line with the real ordering
- if Aunic does not have an `Agent` tool for open-ended multi-round search, replace that with the correct Aunic tool name

## Suggested Aunic Prompt Outline
```text
- Fast file pattern matching tool that works with any codebase size
- Supports glob patterns like "**/*.js" or "src/**/*.ts"
- Returns matching file paths [using Aunic's actual ordering]
- Use this tool when you need to find files by name patterns
- When you are doing an open ended search that may require multiple rounds of globbing and grepping, use [Aunic's equivalent tool] instead
```

## Implementation Note
This prompt should remain very short.
- the reference implementation treats glob as a straightforward capability tool
- most of the deeper behavior belongs in the tool spec, not the prompt
