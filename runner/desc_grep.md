A powerful search tool built on ripgrep.

Usage:
- ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` as a Bash command.
- Supports full regex syntax (e.g., "log.*Error", "function\\s+\\w+")
- Filter files with glob parameter (e.g., "*.js", "**/*.tsx") or type parameter (e.g., "js", "py", "go")
- Output modes: "content" shows matching lines, "files_with_matches" shows only file paths (default), "count" shows match counts
- Pattern syntax: Uses ripgrep — literal braces need escaping (use `interface\{\}` to find `interface{}` in Go code)
- Multiline matching: By default patterns match within single lines. For cross-line patterns, use multiline: true
