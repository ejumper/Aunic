Search file contents with ripgrep.

<parameters>
- pattern: Regular expression to search for. Required.
- path: File or directory to search in. Must be absolute if provided. Defaults to the active note's directory. Optional.
- glob: Glob pattern to filter which files are searched (e.g. "*.go", "*.{ts,tsx}"). Optional.
- type: File type shorthand (e.g. "go", "py", "js", "ts"). Faster than glob for language filtering. Optional.
- output_mode: "files_with_matches" (default), "content", or "count". Optional.
- -i: Case-insensitive search. Optional.
- context / -C: Lines of context before and after each match (content mode only). Optional.
- -B / -A: Lines before / after each match independently (content mode only). Optional.
- head_limit: Cap output at N results. Default 250. Set to 0 for unlimited. Optional.
- offset: Skip the first N results before applying head_limit (for pagination). Optional.
- multiline: If true, patterns can span multiple lines (. matches newlines). Default false. Optional.
</parameters>

<output_modes>
- files_with_matches (default): list of file paths containing the pattern. Use to locate which files to read next.
- content: matching lines with optional context. Use when you need to see surrounding code.
- count: match count per file. Use for quantitative analysis.
</output_modes>

<limitations>
- Default cap: 250 results. Set head_limit=0 to disable (use with caution on large codebases).
- Excludes VCS directories: .git, .svn, .hg, .bzr, .jj, .sl.
- Includes hidden files.
- Lines exceeding 500 characters are truncated in output.
- Requires ripgrep (rg) to be installed.
</limitations>

<tips>
- NEVER invoke grep or rg as a Bash command — always use this tool.
- Escape regex metacharacters in code searches: use interface\{\} to find interface{} in Go.
- Use files_with_matches first to narrow candidates, then Read the relevant files.
- Use offset to paginate through a large result set across multiple calls.
- Use type over glob when targeting a whole language — it is faster and simpler.
</tips>
