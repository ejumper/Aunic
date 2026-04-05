## The Write Tool
The model uses `write` in `work-mode` to create a new file or fully replace the contents of an existing file.
- `write`
    - `file_path`
        - required string
        - the file to create or overwrite
    - `content`
        - required string
        - the full content to write into the file

(note: generic `tool_id` handling and in-memory vs persisted tool history already live in `notes-and-plans/tools/tools.md`)
(note: transcript row encoding/rendering already lives in `notes-and-plans/active-markdown-note/active-markdown-note.md`)
(note: transcript-to-provider translation already lives in `notes-and-plans/building-context/transcript-to-api.md`)

## Design Goal
Make Aunic's `write` tool behave as close as possible to the implementation in `coding-agent-program-example`, while retaining Aunic-specific architecture:
- `tool_call` and `tool_result` / `tool_error` rows are persisted in the `transcript`
- `tool_result` and `tool_error` remain distinct row types in the `transcript`
- Aunic-specific guards like `note-content`, `external_directory`, and `doom_loop` still apply

The main shift from the older draft is this:
- internally, `write` should behave like a structured file operation with diff-aware permissions and diff-aware results
- only the provider-facing tool-result payload should be flattened to the short success/error strings a model expects
- the `transcript` should persist structured JSON content, not XML-ish success text blocks

## When The Model Should Use `write`
`write` is for whole-file creation or replacement.
- use `write` when creating a brand new file
- use `write` when the model already has the full desired file content and wants to replace the file in one shot
- use `edit` or `patch` when changing only part of an existing file
- use `note-write` rather than `write` when the target is `note-content`

Like the example implementation, Aunic should steer the model away from using `write` for routine partial edits.
- overwriting an existing file is allowed
- but the prompt should explicitly say to prefer `edit` for normal modifications and reserve `write` for new files or full rewrites

## How A `write` Tool Call Starts
1. **The model emits a tool call**
    - The provider returns an assistant message containing a `write` tool call.
    - The API, not Aunic, provides the `tool_id`.
    - The arguments must contain `file_path` and `content`.

2. **Aunic parses and validates the arguments**
    - `file_path` must exist and must be a string
    - `content` must exist and must be a string

3. **Aunic records the `tool_call` row**
    - The `tool_call` should be added to the in-memory message list immediately.
    - Because `write` is a persistent `work-mode` tool, it should also be written to the `transcript`.
    - The `tool_call` row should preserve the raw provider-emitted arguments.

## Input Normalization
After parsing, Aunic should normalize the effective input used for validation, permissions, and execution.

### Path normalization
- if `file_path` is relative, resolve it relative to the current project working directory
- if `file_path` uses `~`, expand it
- keep the original `tool_call` row untouched, but use the normalized absolute path for all internal checks

### Content normalization
To stay close to the example:
- `content` may be an empty string
- creating an empty file is allowed
- truncating an existing file to empty content is allowed

Like the example's API normalization step:
- preserve trailing spaces in Markdown files because two trailing spaces are semantically meaningful
- for non-Markdown files, Aunic may strip trailing whitespace at the provider-input normalization boundary if that behavior is adopted consistently across file tools

## Path Constraints
After normalization:
- if the target path points to a directory, reject it with a `tool_error`
- if the target path points to a special non-regular file type that Aunic does not want to overwrite, reject it with a `tool_error`

## Aunic-Specific Guard: `note-content`
This remains an intentional Aunic divergence.

`work-mode` should not allow `write` to become a back door around the `note-mode` / `work-mode` split.
- if the target path is the active note file or another included `note-content` file, reject it
- the model should use `note-write` / `note-edit` for those files instead

## Shared / Sensitive Content Guard
The example includes a secret-scanning guard for shared memory files before write/edit.

Aunic should adopt the same pattern for any shared or syncable surfaces it has.
- if the target path is in an Aunic-managed shared/synced area and the content appears to contain secrets, reject with a `tool_error`
- this should happen before the actual write

This is separate from the `note-content` guard.
- `note-content` is about mode boundaries
- secret scanning is about preventing dangerous writes into shared state

## Existing File Safety Checks
If the target file already exists, Aunic should stay close to the example's read-before-write behavior.

### Read-before-write guard
Existing files should be fully read before they are overwritten with `write`.
- if the file exists and there is no session read record for the normalized path, reject
- if the last read was only a partial read/view, reject
- new files do not need a prior read

The error should tell the model to read the file first before writing to it.

### Early stale-read guard
Before entering permissions/execution, Aunic should do an initial stale-read check.
- if the file exists and its current modification time is newer than the last recorded full read time, reject
- the error should tell the model to read the file again before writing

### No no-op rejection
To stay close to the example implementation:
- do **not** reject writes just because the requested content exactly matches the current file contents
- a same-content write is allowed, though the UI may still render it as producing no visible changes

This replaces the older OpenCode-like no-op plan.

## Permission Flow
After validation and safety checks, the call goes through the shared `work-mode` permission system.

### What `write` permissions match against
To stay close to the example:
- permission checks should match the actual normalized target path
- permission checks should also consider symlink-resolved variants of that path
- deny/ask/allow matching should be done against those effective paths, not against an artificial project-root permission path

This is safer than the older project-root scoping rule and closer to the example's actual behavior.

### Permission safety checks
Before a broad allow rule is honored, Aunic should still run write-path safety checks.
- dangerous config files
- dangerous config directories
- symlink escapes
- platform-specific unsafe path forms
- any other Aunic-protected write targets

### Permission preview should use a diff
Before asking for permission, Aunic should compute a structured diff between:
- old file content
- new requested content

That diff should be the main thing shown in the permission dialog.

### Permission dialog behavior
To stay close to the example:
- the dialog should distinguish create vs overwrite
- the dialog should show the target path
- the dialog should show the diff, or the new content preview for file creation
- the dialog should be allowed to adjust the final approved content before the write happens

If the permission dialog changes the content before approval:
- the original `tool_call` row stays unchanged
- execution should use the approved content
- the `tool_result` should persist the actual written content
- the `tool_result` may include metadata indicating that the final content differed from the original request

### `external_directory`
If the target path is outside the project root, `external_directory` should still apply as an Aunic-specific rule.
- if the external path is explicitly allowed, the write can continue
- otherwise the result is determined by the `external_directory` rule, which should default to `ask`

### `doom_loop`
If the same `write` call repeats 3 times with identical effective input, `doom_loop` should trigger.
- matching should use the normalized effective input: resolved `file_path` and effective `content`
- if permission approval modified the content, the post-approval content is what matters for execution, but the original repeated tool call can still be used for retry detection

### Ask / Allow / Deny
If the permission result is `ask`, Aunic should pause execution and show a permission prompt in the UI.
- `once`
    - allow this one write only
- `always`
    - allow future matching writes for the rest of the current Aunic session
- `reject`
    - deny the request

When possible, the "always allow" option should behave closer to the example:
- use session-scoped permission updates
- if the path is outside the working directory, suggest allowing the relevant directory rather than broad unrelated scope

## What Happens On Rejection
If the write is rejected by validation, safety checks, config, or the user:
- no file is created or changed
- parent directories are not created
- no write-side file-history update is finalized
- Aunic writes a `tool_error` row with the same `tool_id`

The error should clearly indicate which layer rejected the call:
- validation
- `note-content`
- secret/shared-content guard
- read-before-write
- stale-read
- path safety
- a `deny` rule
- `external_directory`
- `doom_loop`
- explicit user rejection
- write-time execution failure

Suggested `tool_error` content format:
```json
{
  "category": "write_failed",
  "reason": "file_not_read",
  "file_path": "/path/to/project/src/file.py",
  "message": "File has not been read yet. Read it first before writing to it."
}
```

`tool_error` content should stay JSON in the `transcript` so it fits Aunic's transcript table encoding rules.

## Write Execution
If validation, safety checks, and permissions pass, Aunic should perform the write in this order.

1. **Resolve effective input**
    - use the normalized absolute path
    - use the final approved content if the permission dialog adjusted it

2. **Detect whether this is a create or update**
    - determine file existence from the filesystem metadata, not from whether old content is truthy
    - an existing empty file is still an update, not a create

3. **Read current file state**
    - if the file exists, read the current content and metadata
    - if the path does not exist, treat old content as `null`
    - preserve the detected encoding when possible

4. **Create parent directories**
    - ensure the parent directory exists before the critical write section
    - create it if necessary

5. **Prepare file-history backup**
    - if Aunic file history is enabled, capture the pre-edit state before the write
    - this should happen before the final critical write step, similar to the example

6. **Re-check staleness immediately before writing**
    - do a second stale-read check right before the actual write
    - avoid async gaps between the final read/compare and the write itself
    - if the file's timestamp changed, compare content when appropriate to avoid false positives from noisy mtimes
    - if the file truly changed since the last full read, fail with a `tool_error`

7. **Write the file**
    - write the final content as a full replacement
    - preserve the content's intended line endings rather than silently rewriting them based on previous file state

8. **Notify editor / language tooling**
    - notify Aunic-managed LSP/editor integrations that the file changed
    - clear any stale delivered diagnostics for that file if Aunic tracks them

9. **Update session file tracking**
    - record the file as written
    - update the session read state to the newly written content and fresh modification time
    - this keeps future writes aligned with the latest file state

10. **Finalize file-history state**
    - record the old state and new state in Aunic's file history/session tracking

11. **Build the structured result**
    - compute a structured diff for updates
    - for creates, store an empty patch array plus the full created content

12. **Optionally attach diagnostics metadata**
    - Aunic may attach diagnostics metadata if it is already available cheaply
    - it should not block the write result on waiting for diagnostics
    - unlike the older draft, diagnostics should be structured metadata, not appended XML-like text blocks

## Result Formatting
To stay close to the example while preserving Aunic's transcript architecture, the persisted `tool_result` content for `write` should be a JSON object.

Suggested shape:
```json
{
  "type": "create",
  "file_path": "/path/to/project/src/new_file.py",
  "content": "full file contents here",
  "structured_patch": [],
  "original_file": null,
  "meta": {
    "content_source": "tool_call"
  }
}
```

For an overwrite:
```json
{
  "type": "update",
  "file_path": "/path/to/project/src/existing_file.py",
  "content": "new full file contents here",
  "structured_patch": [
    {
      "...": "diff hunk data"
    }
  ],
  "original_file": "old full file contents here",
  "meta": {
    "content_source": "permission_dialog"
  }
}
```

### Why The Result Should Be Structured In The `transcript`
This is the best way to stay close to the example without giving up Aunic's transcript design.
- the `transcript` content cell already supports arbitrary JSON values
- Aunic's transcript renderer can show a created-file preview or an update diff from this object
- transcript-to-provider translation can flatten this object into a short provider-facing success string when needed
- Aunic keeps richer local history than the provider sees

### Provider-facing success text
Even though the `transcript` stores structured JSON, the provider-facing tool result can stay simple, similar to the example:
- create: `File created successfully at: /path/to/project/src/new_file.py`
- update: `The file /path/to/project/src/existing_file.py has been updated successfully.`

That flattening should happen at the provider translation layer, not in the persisted `transcript` row.

## Aunic Transcript Compatibility
This revised plan remains compatible with the markdown-table `transcript`.

### `tool_call` row
- `role = assistant`
- `type = tool_call`
- `tool_name = write`
- `tool_id = provider-generated tool id`
- `content = JSON object containing the raw tool arguments`

### Successful `tool_result` row
- `role = tool`
- `type = tool_result`
- `tool_name = write`
- `tool_id = same tool id`
- `content = JSON object containing the structured write result`

### Failed `tool_error` row
- `role = tool`
- `type = tool_error`
- `tool_name = write`
- `tool_id = same tool id`
- `content = JSON object containing the structured error`

This keeps Aunic's explicit `tool_error` versus `tool_result` distinction intact.

## Transcript Rendering Implications
Because the `tool_result` content is structured JSON:
- create results can render as "wrote N lines to path" plus a truncated content preview
- update results can render as a diff view using `structured_patch`
- rejected writes can render as an error row and, when possible, also show the proposed diff using the stored call/result context

This is closer to the example's UI model than the older text-only plan.

## What Counts As A Failure
Not every unsuccessful write attempt is the same kind of failure.

### `tool_error`
Use `tool_error` when Aunic itself could not or would not perform the write.
- malformed arguments
- missing `file_path`
- missing `content`
- target path is a directory
- target is protected `note-content`
- content failed a shared/sensitive-content guard
- existing file was not fully read first
- file was modified since last read
- permission rejection
- directory creation failure
- write failure
- internal execution failure before a usable success result could be produced

### `tool_result`
Use `tool_result` only when the file was actually written successfully.
- successful write to a new file
- successful overwrite of an existing file
- successful write of empty content
- successful write where approval changed the final content

Unlike the older draft:
- same-content writes are not automatically errors

## Returning The Result To The Model
After the result object is built:
1. write the `tool_result` or `tool_error` row to the in-memory message list
2. write the same row to the `transcript`
3. translate the row into the provider-specific tool-result shape described in `notes-and-plans/building-context/transcript-to-api.md`
4. prompt the model for its next step

## Prompt Guidance For The Model
To stay close to the example's prompt behavior, the `write` tool description should explicitly say:
- existing files must be read first before overwrite
- prefer `edit` for normal modifications because it only sends the diff
- use `write` mainly for new files or complete rewrites
- avoid creating documentation or README-style files unless the user explicitly asked for them

The exact wording can be tuned later, but the behavior should be documented now so `write` and `edit` feel intentionally distinct.
