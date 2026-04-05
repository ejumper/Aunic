## The Glob Tool
The model uses `glob` in `work-mode` to find files by path pattern.
- `glob`
    - `pattern`
        - required string
        - the glob pattern to match file paths against
    - `path`
        - optional string
        - the directory to search in
        - defaults to the current project working directory

(note: generic `tool_id` handling and in-memory vs persisted tool history already live in `notes-and-plans/tools/tools.md`)
(note: transcript row encoding/rendering already lives in `notes-and-plans/active-markdown-note/active-markdown-note.md`)
(note: transcript-to-provider translation already lives in `notes-and-plans/building-context/transcript-to-api.md`)

## When The Model Should Use `glob`
`glob` is for file-path discovery, not content search and not file reading.
- use `glob` when the model needs to find files by extension, path shape, or naming convention
- use `glob` before `read` when the model knows the kind of file it wants but not the exact path
- use `grep` when the model wants to search inside file contents
- use `read` after `glob` when the model wants to inspect one of the returned files
- use `bash` only when real shell execution is needed, not for file discovery

## How A `glob` Tool Call Starts
1. **The model emits a tool call**
    - The provider returns an assistant message containing a `glob` tool call.
    - The API, not Aunic, provides the `tool_id`.
    - The arguments should contain `pattern`, and may contain `path`.

2. **Aunic parses and validates the arguments**
    - `pattern` must exist and must be a string
    - `path`, if present, must be a string
    - an empty `pattern` is invalid

3. **Aunic records the `tool_call` row**
    - The `tool_call` should be added to the in-memory message list immediately.
    - Because `glob` is a persistent `work-mode` tool, it should also be written to the `transcript`.

## Search Scope
The search root is determined like this:
- if `path` is omitted, search from the project working directory
- if `path` is relative, resolve it relative to the project working directory
- if `path` is absolute, use it as-is subject to permission checks

`glob` searches file paths on disk.
- it does not read file contents
- it does not search the transient `note-snapshot`

## Pattern Syntax
To stay close to OpenCode, `glob` should support the same practical pattern shapes the tool description advertises.

Supported forms should include:
- `*`
    - matches any sequence of non-separator characters
- `**`
    - matches across directory separators
- `?`
    - matches a single non-separator character
- `[abc]`
    - matches any character in the brackets
- `[!abc]`
    - matches any character not in the brackets
- `*.{ts,tsx}`
    - brace expansion style alternatives

Example patterns:
- `*.js`
- `**/*.js`
- `src/**/*.{ts,tsx}`
- `docs/**/README.md`

## Permission Flow
After argument parsing, the call goes through the shared `work-mode` permission system.

### What `glob` permissions match against
`glob` permission rules should match the requested `pattern`.
- this mirrors OpenCode's permission model where `glob` matches the glob pattern
- a config can therefore allow narrow patterns and ask for broad ones

### `external_directory`
If the resolved search path is outside the project root, `external_directory` should apply.
- if the external path is explicitly allowed, the search can continue
- otherwise the result is determined by the `external_directory` rule, which should default to `ask`

### `doom_loop`
If the same `glob` call repeats 3 times with identical input, `doom_loop` should trigger.
- matching should be against the full effective input: `pattern` and resolved `path`
- this is a guard against the model getting stuck retrying the same search

### Ask / Allow / Deny
If the permission result is `ask`, Aunic should pause execution and show a permission prompt in the UI.
- `once`
    - allow this one search only
- `always`
    - allow future matching searches for the rest of the current Aunic session
- `reject`
    - deny the request

For `always`, the suggested rule should stay narrow.
- exact patterns are better than broad catch-alls
- the granted pattern should reflect the user's current intent, not silently expand it

## What Happens On Rejection
If the search is rejected by config or by the user:
- no search is run
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
  "pattern": "**/*.ts",
  "path": "/path/to/project",
  "message": "Search was rejected by the user."
}
```

## Search Execution
OpenCode uses ripgrep internally for tools like `glob`, `grep`, and `list`, and Aunic should do the same here wherever possible.

### Primary path: ripgrep
If `rg` is available, Aunic should search with ripgrep first.
- run ripgrep in file-listing mode rather than content-search mode
- search from the resolved `path`
- pass the requested `pattern` through as a glob filter
- collect null-delimited results

To stay close to OpenCode's current implementation:
- if the pattern is relative, prefix it with `/` before passing it to ripgrep
- run ripgrep with the search root as its working directory
- normalize returned paths to absolute paths before returning them to the model

Ripgrep exit code `1` should be treated as "no matches", not as an execution error.

### Ripgrep ignore behavior
When ripgrep is used:
- `.gitignore` should be respected by default
- `.ignore` can be used to re-include otherwise ignored paths

### Fallback path
If `rg` is unavailable or ripgrep execution fails in a way that requires fallback:
- run a doublestar-style filesystem glob walk from the resolved `path`
- apply the requested pattern against paths under that root
- skip hidden files
- return normalized paths

The fallback exists only so the tool still functions without ripgrep.
- the ripgrep path is the intended main path
- the fallback may behave a little differently at the margins, but should preserve the same broad semantics

## Hidden / Ignored Files
To stay close to OpenCode:
- hidden files should be skipped by default
- common generated / dependency directories should be skipped by default in fallback mode
- if ripgrep is in use, its normal ignore handling should remain authoritative

`glob` should not return:
- directories
- hidden files by default
- files filtered out by normal ignore handling

## Result Ordering
This is one area where OpenCode's description and current implementation do not perfectly match.

The tool description says results are sorted by modification time, newest first.
The current main ripgrep-backed implementation actually sorts returned matches by shorter path first.
The current fallback implementation sorts by modification time.

Because the goal here is to mimic how OpenCode currently works as closely as possible:
- if the ripgrep path is used, sort results by shorter path first
- if the fallback path is used, sort by modification time

If later you want "cleaner than OpenCode" behavior, this is a place where Aunic could intentionally normalize both paths to one ordering, but that would be a deliberate deviation.

## Limits
Results should be capped at 100 returned file paths.

To stay close to OpenCode's current ripgrep path:
- if the returned result count reaches the 100-file limit, treat the result as truncated
- this means a 100-item result is considered "possibly truncated" even if there were exactly 100 matches

If truncated, the result text should tell the model to narrow the pattern.

## Formatting The Tool Result
To stay close to OpenCode, the persisted `tool_result` content for `glob` should be a JSON string containing the same human-readable text the model would normally see from the tool.

If no files are found:
```text
No files found
```

If files are found:
```text
/repo/src/app.ts
/repo/src/lib/util.ts
/repo/tests/app.test.ts
```

If truncated, append:
```text
(Results are truncated. Consider using a more specific path or pattern.)
```

### Why The Result Should Stay Text
This is the closest match to OpenCode's actual `glob` tool behavior.
- the transcript can already store tool results as JSON strings
- the generic Aunic tools rendering can display this without a custom glob-only UI
- no extra structure is necessary unless Aunic later chooses to build a richer path-picker style renderer

## What Counts As A Failure
Not every unsuccessful glob outcome is a tool failure.

### `tool_error`
Use `tool_error` when Aunic itself could not or would not execute the search.
- malformed arguments
- missing `pattern`
- permission rejection
- invalid or inaccessible search root
- internal execution failure before a usable result could be produced

### `tool_result`
Use `tool_result` when the search executed normally, even if it found nothing.
- zero matches
- truncated matches
- fallback mode was used successfully

`No files found` is a normal `tool_result`, not an error.

## Returning The Result To The Model
After the result text is built:
1. write the `tool_result` or `tool_error` row to the in-memory message list
2. write the same row to the `transcript`
3. return the result to the provider in the provider-specific tool-result shape described in `notes-and-plans/building-context/transcript-to-api.md`
4. prompt the model for its next step

At that point the model can:
- narrow or broaden the pattern
- change the search root
- switch to `read` to inspect one of the returned files
- switch to `grep` if it actually wanted content search instead of path search

## Rendering In Aunic
The full transcript rendering rules already live in `notes-and-plans/active-markdown-note/active-markdown-note.md`, but for `glob` the renderer should at minimum show:
- the search pattern from the `tool_call`
- the returned file-path list from the `tool_result`
- whether the result was truncated

No special glob-only renderer is required for v1.

## Summary Of The Full Flow
1. the model emits a `glob` tool call
2. Aunic parses it and records the `tool_call`
3. validation and permission checks run
4. if rejected, return `tool_error`
5. if allowed, run ripgrep in file-listing mode if available, otherwise use the fallback glob walker
6. normalize and order the returned paths the same way OpenCode currently does
7. cap the result at 100 paths and mark it truncated if the limit is hit
8. write the OpenCode-style text result as a `tool_result`
9. return the result to the model for the next turn
