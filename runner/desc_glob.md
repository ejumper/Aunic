Find files by name pattern, sorted by modification time. Does not end the run.

<parameters>
- pattern: Glob pattern to match against file paths. Required.
- path: Directory to search in. Must be an absolute path if provided. Defaults to the active note's directory. Optional.
</parameters>

<pattern_syntax>
- * — any sequence within a single directory (no path separators)
- ** — any sequence including directory separators
- ? — any single character (no path separator)
- [abc] — any character in the set
- {ts,tsx} — either extension
</pattern_syntax>

<examples>
- **/*.go — all Go files in any subdirectory
- src/**/*.{ts,tsx} — TypeScript files anywhere under src/
- *.md — markdown files in the search directory only
- cmd/**/main.go — main.go files under any cmd/ subdirectory
</examples>

<limitations>
- Results capped at 100 files (newest modified first).
- Searches hidden files and does NOT exclude VCS directories (.git, etc.) — narrow your path or pattern to avoid matching VCS internals.
- Requires ripgrep (rg) to be installed.
- path must be a directory, not a file.
</limitations>

<tips>
- Use Grep to search file contents; use Glob to find files by name or location.
- If results are truncated, narrow the pattern or provide a more specific path.
- Combine with Read: Glob to find candidates, then Read the relevant files.
</tips>
