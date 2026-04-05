## Grep Tool Prompt Outline
This file outlines how Aunic should structure the system prompt / tool description for `grep`, based closely on `coding-agent-program-example/src/tools/GrepTool/prompt.ts`.

The reference prompt is a compact capability-and-usage block.

## Core Reference Language
The reference implementation's prompt text is:

```text
A powerful search tool built on ripgrep

  Usage:
  - ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` as a Bash command. The Grep tool has been optimized for correct permissions and access.
  - Supports full regex syntax (e.g., "log.*Error", "function\\s+\\w+")
  - Filter files with glob parameter (e.g., "*.js", "**/*.tsx") or type parameter (e.g., "js", "py", "rust")
  - Output modes: "content" shows matching lines, "files_with_matches" shows only file paths (default), "count" shows match counts
  - Use Agent tool for open-ended searches requiring multiple rounds
  - Pattern syntax: Uses ripgrep (not grep) - literal braces need escaping (use `interface\\{\\}` to find `interface{}` in Go code)
  - Multiline matching: By default patterns match within single lines only. For cross-line patterns like `struct \\{[\\s\\S]*?field`, use `multiline: true`
```

## Recommended Aunic Prompt Structure
The Aunic `grep` prompt should follow the same shape:

1. one-line description
2. blank line
3. `Usage:` section
4. "always use Grep, never grep/rg via Bash" rule
5. regex capability note
6. file filtering note
7. output mode note
8. open-ended search escalation note
9. ripgrep syntax caveat
10. multiline caveat

## Aunic-Specific Deviations
Aunic should stay close to the wording, but adjust only where the tool behavior differs.

### Keep if Aunic supports them
- the "never use `grep` or `rg` as Bash" instruction
- full regex syntax language
- the ripgrep syntax caveat

### Adjust if Aunic differs
- if Aunic does not support `type`, remove that clause
- if Aunic does not support output modes like `content`, `files_with_matches`, or `count`, replace that line with Aunic's actual supported modes
- if Aunic does not support `multiline`, remove or rewrite that line
- if Aunic prefers a different open-ended search tool than `Agent`, replace that name with the correct Aunic tool

## Suggested Aunic Prompt Outline
```text
A powerful search tool built on ripgrep

Usage:
- ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` as a Bash command. The Grep tool has been optimized for correct permissions and access.
- Supports full regex syntax (e.g., "log.*Error", "function\\s+\\w+")
- Filter files with [Aunic-supported filter parameters]
- [Describe Aunic's supported output modes]
- Use [Aunic's open-ended search tool] for open-ended searches requiring multiple rounds
- Pattern syntax: Uses ripgrep (not grep) - literal braces need escaping (use `interface\\{\\}` to find `interface{}` in Go code)
- [If supported] Multiline matching: By default patterns match within single lines only. For cross-line patterns like `struct \\{[\\s\\S]*?field`, use `multiline: true`
```

## Implementation Note
This prompt is intentionally dense and practical.
- it teaches the model what not to do with bash
- it encodes several common ripgrep footguns in a small amount of text
