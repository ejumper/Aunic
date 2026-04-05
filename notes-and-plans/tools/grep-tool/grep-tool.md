## The Grep Tool
The model uses `grep` in `work-mode` to search file contents for text or patterns.
- `grep`
    - `pattern`
        - required string
        - the text or regex pattern to search for
    - `path`
        - optional string
        - the directory to search in
        - defaults to the current project working directory
    - `include`
        - optional string
        - a glob-style file filter such as `*.py` or `*.{ts,tsx}`
    - `literal_text`
        - optional boolean
        - defaults to `false`
        - if `true`, the tool escapes regex metacharacters so the search behaves like exact text matching

(note: generic `tool_id` handling and in-memory vs persisted tool history already live in `notes-and-plans/tools/tools.md`)
(note: transcript row encoding/rendering already lives in `notes-and-plans/active-markdown-note/active-markdown-note.md`)
(note: transcript-to-provider translation already lives in `notes-and-plans/building-context/transcript-to-api.md`)

## When The Model Should Use `grep`
`grep` is for content search, not file-name search and not file reading.
- use `grep` when the model needs to find which files mention some string, identifier, error text, or code pattern
- use `literal_text=true` when searching for exact text that contains `.`, `(`, `)`, `[`, `]`, `?`, `*`, or other regex characters
- use `glob` when the model needs to find files by path or extension
- use `read` after `grep` when the model wants to inspect the actual file contents
- use `bash` only when real shell execution is needed, not for repo search

## How A `grep` Tool Call Starts
1. **The model emits a tool call**
    - The provider returns an assistant message containing a `grep` tool call.
    - The API, not Aunic, provides the `tool_id`.
    - The arguments should contain `pattern`, and may contain `path`, `include`, and `literal_text`.

2. **Aunic parses and validates the arguments**
    - `pattern` must exist and must be a string
    - `path`, if present, must be a string
    - `include`, if present, must be a string
    - `literal_text`, if present, must be a boolean
    - an empty `pattern` is invalid

3. **Aunic records the `tool_call` row**
    - The `tool_call` should be added to the in-memory message list immediately.
    - Because `grep` is a persistent `work-mode` tool, it should also be written to the `transcript`.

## Search Scope
The search root is determined like this:
- if `path` is omitted, search from the project working directory
- if `path` is relative, resolve it relative to the project working directory
- if `path` is absolute, use it as-is subject to permission checks

`grep` should search directories, not individual in-memory note snapshots.
- it works against files on disk
- it does not search the transient `note-snapshot`

## Pattern Handling
If `literal_text=false`:
- treat `pattern` as a regex pattern
- compile and validate it before searching if a local fallback implementation is going to be used

If `literal_text=true`:
- escape regex metacharacters first
- search for the exact literal text the model provided

The permission matcher for `grep` should use the model-supplied `pattern`, not the escaped internal version.
- this keeps permission rules predictable
- `external_directory` handles path-based safety separately

## Permission Flow
After argument parsing, the call goes through the shared `work-mode` permission system.

### What `grep` permissions match against
`grep` permission rules should match the requested `pattern`.
- example: a config could allow `TODO` and ask for everything else
- this mirrors OpenCode's permission model where `grep` matches the regex pattern

### `external_directory`
If the resolved search path is outside the project root, `external_directory` should apply.
- if the external path is explicitly allowed, the search can continue
- otherwise the result is determined by the `external_directory` rule, which should default to `ask`

### `doom_loop`
If the same `grep` call repeats 3 times with identical input, `doom_loop` should trigger.
- matching should be against the full effective input: `pattern`, resolved `path`, `include`, and `literal_text`
- this is a guard against the model getting stuck retrying the same search

### Ask / Allow / Deny
If the permission result is `ask`, Aunic should pause execution and show a permission prompt in the UI.
- `once`
    - allow this one search only
- `always`
    - allow future matching searches for the rest of the current Aunic session
- `reject`
    - deny the request

For `always`, the suggested rule should usually be narrow.
- a literal search can whitelist the exact pattern
- a broad regex should not expand to an even broader wildcard unless that is clearly intended

## What Happens On Rejection
If the search is rejected by config or by the user:
- no search is run
- no files are read
- Aunic writes a `tool_error` row with the same `tool_id`
- the error content should clearly say whether the rejection came from:
    - validation
    - a `deny` rule
    - `external_directory`
    - `doom_loop`
    - an explicit user rejection

Suggested `tool_error` content format:
```json
{
  "category": "permission_denied",
  "reason": "user_reject",
  "pattern": "TODO",
  "path": "/path/to/project",
  "message": "Search was rejected by the user."
}
```

## Search Execution
OpenCode uses ripgrep internally for tools like `grep`, `glob`, and `list`, and Aunic should do the same here wherever possible.

### Primary path: ripgrep
If `rg` is available, Aunic should search with ripgrep first.
- run ripgrep with line-number output
- include file paths in the output
- if `include` is provided, pass it through as a glob filter
- search the resolved `path`
- ripgrep exit code `1` should be treated as "no matches", not as an execution error

The ripgrep path should be the default behavior because it is:
- fast
- close to OpenCode
- naturally aligned with `.gitignore`

### Ripgrep ignore behavior
When ripgrep is used:
- `.gitignore` should be respected by default
- `.ignore` can be used to re-include otherwise ignored paths

(note: ignore/rendering details already exist in OpenCode's design and should be mirrored here rather than reinvented)

### Fallback path
If `rg` is unavailable or ripgrep execution fails in a way that requires fallback:
- compile the pattern locally
- walk the directory tree
- skip directories and hidden files
- apply `include` if present
- scan text files line-by-line

The fallback is only there so the tool still functions without ripgrep.
- the ripgrep path is the intended main path
- the fallback may be somewhat less feature-complete than ripgrep

## Hidden / Ignored Files
To stay close to OpenCode:
- hidden files should be skipped by default
- common generated / dependency directories should be skipped by default in fallback mode
- if ripgrep is in use, its normal ignore handling should remain authoritative

`grep` should not search:
- binary files that cannot be sensibly scanned as text
- directories
- hidden files by default

## Match Collection
OpenCode's implementation groups output by file but searches at line level, so Aunic should keep that same feel.

When ripgrep is used:
- each matching output line is parsed as `file:line:content`
- the tool captures:
    - file path
    - matching line number
    - matching line text
    - file modification time

When fallback mode is used:
- the tool may only capture the first matching line per file
- this is acceptable as a fallback accommodation as long as the main ripgrep path remains the default

## Sorting And Limits
Collected matches should be sorted by file modification time, newest first.
- this matches OpenCode's behavior
- sorting is not lexical by file path

Results should be truncated to the first 100 match entries after sorting.
- if more than 100 match entries exist, mark the result as truncated
- the result text should tell the model to narrow the search
- the displayed `Found N matches` count should reflect the returned entries, not the hidden pre-truncation total

## Formatting The Tool Result
To stay close to OpenCode, the persisted `tool_result` content for `grep` should be a JSON string containing the same human-readable text the model would normally see from the tool.

If no matches are found:
```text
No files found
```

If matches are found:
```text
Found 3 matches
/repo/src/foo.py:
  Line 12: TODO: remove this branch
  Line 29: TODO: add tests

/repo/src/bar.py:
  Line 8: TODO: handle timeout
```

If truncated, append:
```text
(Results are truncated. Consider using a more specific path or pattern.)
```

### Why The Result Should Stay Text
This is the closest match to OpenCode's actual `grep` tool behavior.
- the transcript can already store tool results as JSON strings
- the generic Aunic tools rendering can display this without a custom grep-specific UI
- no extra structure is necessary unless Aunic later chooses to build a richer grep result renderer

## What Counts As A Failure
Not every unsuccessful grep outcome is a tool failure.

### `tool_error`
Use `tool_error` when Aunic itself could not or would not execute the search.
- malformed arguments
- missing `pattern`
- invalid regex pattern
- permission rejection
- invalid or inaccessible search root
- internal execution failure before a usable search result could be produced

### `tool_result`
Use `tool_result` when the search executed normally, even if it found nothing.
- zero matches
- matches were truncated
- fallback mode was used successfully

`No files found` is a normal `tool_result`, not an error.

## Returning The Result To The Model
After the result text is built:
1. write the `tool_result` or `tool_error` row to the in-memory message list
2. write the same row to the `transcript`
3. return the result to the provider in the provider-specific tool-result shape described in `notes-and-plans/building-context/transcript-to-api.md`
4. prompt the model for its next step

At that point the model can:
- refine the pattern
- narrow the path
- add an `include` filter
- switch to `read` to inspect one of the returned files
- switch to `glob` if it was really doing file discovery rather than content search

## Rendering In Aunic
The full transcript rendering rules already live in `notes-and-plans/active-markdown-note/active-markdown-note.md`, but for `grep` the renderer should at minimum show:
- the search pattern from the `tool_call`
- the returned grouped text from the `tool_result`
- whether the result was truncated

No special grep-only renderer is required for v1.

## Summary Of The Full Flow
1. the model emits a `grep` tool call
2. Aunic parses it and records the `tool_call`
3. validation and permission checks run
4. if rejected, return `tool_error`
5. if allowed, run ripgrep if available, otherwise use the fallback scanner
6. collect matches, sort by file mtime, and truncate to 100 entries if needed
7. write the OpenCode-style text result as a `tool_result`
8. return the result to the model for the next turn
