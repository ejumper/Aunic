## The List Tool
The model uses `list` in `work-mode` to inspect the structure of a directory as a tree.
- `list`
    - `path`
        - optional string in practice
        - the directory to list
        - defaults to the current project working directory
    - `ignore`
        - optional array of strings
        - basename glob patterns to skip while walking

(note: generic `tool_id` handling and in-memory vs persisted tool history already live in `notes-and-plans/tools/tools.md`)
(note: transcript row encoding/rendering already lives in `notes-and-plans/active-markdown-note/active-markdown-note.md`)
(note: transcript-to-provider translation already lives in `notes-and-plans/building-context/transcript-to-api.md`)

## When The Model Should Use `list`
`list` is for directory structure exploration, not content search and not file reading.
- use `list` when the model needs to understand how a directory is organized
- use `list` as a first pass when orienting in an unfamiliar repo or subdirectory
- use `glob` when the model wants files matching a name/path pattern
- use `grep` when the model wants to search file contents
- use `read` after `list` when the model wants to inspect a specific file
- use `bash` only when real shell execution is needed, not for browsing the tree

## How A `list` Tool Call Starts
1. **The model emits a tool call**
    - The provider returns an assistant message containing a `list` tool call.
    - The API, not Aunic, provides the `tool_id`.
    - The arguments may contain `path` and `ignore`.

2. **Aunic parses and validates the arguments**
    - `path`, if present, must be a string
    - `ignore`, if present, must be an array of strings

3. **Aunic records the `tool_call` row**
    - The `tool_call` should be added to the in-memory message list immediately.
    - Because `list` is a persistent `work-mode` tool, it should also be written to the `transcript`.

## Parameter Quirk
OpenCode's current tool schema marks `path` as required, but the runtime implementation treats it as optional and defaults to the working directory when it is missing or empty.

To mimic how OpenCode actually behaves rather than how the schema label reads:
- Aunic should treat `path` as optional in behavior
- if `path` is missing or empty, default to the project working directory

## Search Scope
The root is determined like this:
- if `path` is omitted, use the project working directory
- if `path` is relative, resolve it relative to the project working directory
- if `path` is absolute, use it as-is subject to permission checks

`list` operates on files and directories on disk.
- it does not read file contents
- it does not inspect the transient `note-snapshot`

## Permission Flow
After argument parsing, the call goes through the shared `work-mode` permission system.

### What `list` permissions match against
`list` permission rules should match the resolved directory path.
- this mirrors OpenCode's permission model where `list` matches the directory path

### `external_directory`
If the resolved path is outside the project root, `external_directory` should apply.
- if the external path is explicitly allowed, the listing can continue
- otherwise the result is determined by the `external_directory` rule, which should default to `ask`

### `doom_loop`
If the same `list` call repeats 3 times with identical input, `doom_loop` should trigger.
- matching should be against the full effective input: resolved `path` and `ignore`
- this is a guard against the model getting stuck retrying the same listing

### Ask / Allow / Deny
If the permission result is `ask`, Aunic should pause execution and show a permission prompt in the UI.
- `once`
    - allow this one listing only
- `always`
    - allow future matching listings for the rest of the current Aunic session
- `reject`
    - deny the request

For `always`, the suggested rule should stay narrow.
- a concrete directory path is better than a broad ancestor unless the user clearly chose that

## What Happens On Rejection
If the listing is rejected by config or by the user:
- no directory walk is run
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
  "path": "/path/to/project/src",
  "message": "Listing was rejected by the user."
}
```

## Listing Execution
OpenCode's `list` tool walks the filesystem directly rather than using ripgrep, and Aunic should do the same here.

1. **Resolve the listing root**
    - turn relative paths into absolute paths
    - if the path does not exist, return a `tool_error`

2. **Walk the tree recursively**
    - use a recursive filesystem walk rooted at the resolved `path`
    - skip inaccessible entries rather than failing the whole listing

3. **Collect entries**
    - do not include the root path itself in the intermediate collected list
    - include descendant directories and files
    - when a directory is collected, store it with a trailing separator so it can later render as a directory node

4. **Stop at the global limit**
    - if the collected entry count reaches the limit, stop the walk and mark the result as truncated

## Hidden / Ignored Entries
To stay close to OpenCode, `list` should skip hidden entries and a small built-in set of common junk/generated entries.

### Hidden entries
Skip any path whose basename starts with `.`.
- hidden files are skipped
- hidden directories are skipped recursively

### Built-in ignored entries
OpenCode's current implementation has a built-in list like:
- `__pycache__`
- `node_modules`
- `dist`
- `build`
- `target`
- `vendor`
- `bin`
- `obj`
- `.git`
- `.idea`
- `.vscode`
- `.DS_Store`
- `*.pyc`
- `*.pyo`
- `*.pyd`
- `*.so`
- `*.dll`
- `*.exe`

There is also a special-case skip for any path containing `__pycache__/`.

### User `ignore` patterns
This is another OpenCode quirk worth mirroring exactly:
- the `ignore` patterns are matched against the current entry's basename
- they are not matched against the full relative path
- matching uses basename glob semantics like `filepath.Match`

That means:
- `["*.txt"]` can skip files by extension
- `["dir1"]` can skip a directory named `dir1`
- path-shaped patterns like `src/**/*.ts` should not be expected to work the way `glob` works

If a skipped entry is a directory:
- stop descending into it

If a skipped entry is a file:
- just omit it

## Tree Building
After entries are collected, Aunic should build the same basic hierarchical tree OpenCode builds.

Each node should carry:
- `name`
- `path`
- `type`
    - `file`
    - `directory`
- `children`

Directories should render with a trailing separator.

## Ordering
To stay close to OpenCode's current implementation:
- preserve the natural walk/insertion order
- do not add a new explicit alphabetical or modification-time sorting pass

The current OpenCode `list` tool does not perform a dedicated post-walk sort.
- tree child order therefore reflects walk order and insertion order
- this is different from some of the other tools

## Limits
Results should be capped at 1000 collected entries.

If the result is truncated:
- stop the walk once the cap is reached
- prepend a truncation warning before the tree output

OpenCode's current truncation message is effectively:
```text
There are more than 1000 files in the directory. Use a more specific path or use the Glob tool to find specific files. The first 1000 files and directories are included below:
```

## Formatting The Tool Result
To stay close to OpenCode, the persisted `tool_result` content for `list` should be a JSON string containing the same human-readable tree text the model would normally see from the tool.

The output should begin with the root:
```text
- /repo/
```

Then child entries should be indented with two spaces per level:
```text
- /repo/
  - src/
    - app.py
    - lib/
      - util.py
  - tests/
    - test_app.py
  - pyproject.toml
```

Directories should end with a trailing separator.

If truncated, prepend the truncation warning and then include the tree below it.

### Why The Result Should Stay Text
This is the closest match to OpenCode's actual `list` tool behavior.
- the transcript can already store tool results as JSON strings
- the generic Aunic tools rendering can display this without a custom tree widget
- no extra structure is necessary unless Aunic later chooses to build a richer tree browser

## What Counts As A Failure
Not every disappointing list outcome is a tool failure.

### `tool_error`
Use `tool_error` when Aunic itself could not or would not execute the listing.
- malformed arguments
- invalid `ignore` structure
- permission rejection
- non-existent path
- invalid or inaccessible root before a usable listing could be produced
- internal execution failure before a usable result could be produced

### `tool_result`
Use `tool_result` when the listing executed normally.
- a normal tree result
- a truncated tree result
- an almost-empty directory that only shows the root line

If the directory exists but contains no visible children after skips, that is still a normal `tool_result`.

## Returning The Result To The Model
After the result text is built:
1. write the `tool_result` or `tool_error` row to the in-memory message list
2. write the same row to the `transcript`
3. return the result to the provider in the provider-specific tool-result shape described in `notes-and-plans/building-context/transcript-to-api.md`
4. prompt the model for its next step

At that point the model can:
- narrow the path
- add ignore basenames
- switch to `glob` for targeted file finding
- switch to `read` to inspect a specific file found in the tree
- switch to `grep` if it actually wanted content search rather than structure discovery

## Rendering In Aunic
The full transcript rendering rules already live in `notes-and-plans/active-markdown-note/active-markdown-note.md`, but for `list` the renderer should at minimum show:
- the requested path from the `tool_call`
- the returned tree text from the `tool_result`
- whether the result was truncated

No special list-only renderer is required for v1.

## Summary Of The Full Flow
1. the model emits a `list` tool call
2. Aunic parses it and records the `tool_call`
3. validation and permission checks run
4. if rejected, return `tool_error`
5. if allowed, walk the directory tree from the requested root
6. skip hidden entries, built-in ignored entries, and basename matches from `ignore`
7. collect up to 1000 entries and build the hierarchical tree
8. write the OpenCode-style tree text as a `tool_result`
9. return the result to the model for the next turn
