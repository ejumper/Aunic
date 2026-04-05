## The Edit Tool
The model uses `edit` in `work-mode` to modify an existing file in place using exact string replacement semantics.
- `edit`
    - `file_path`
        - required string
        - the file to modify
    - `old_string`
        - required string
        - the exact text to find in the target file
    - `new_string`
        - required string
        - the exact text that should replace `old_string`
    - `replace_all`
        - optional boolean
        - if `true`, replace every occurrence of `old_string`
        - if omitted, default to `false`

The model should use `edit` with the example implementation's naming and behavior.
- this replaces the older `search` / `replace` wording in the first draft
- `old_string` and `new_string` map more directly to actual execution and diff generation

(note: generic `tool_id` handling and in-memory vs persisted tool history already live in `notes-and-plans/tools/tools.md`)
(note: transcript row encoding/rendering already lives in `notes-and-plans/active-markdown-note/active-markdown-note.md`)
(note: transcript-to-provider translation already lives in `notes-and-plans/building-context/transcript-to-api.md`)

## Design Goal
Make Aunic's `edit` tool behave as close as possible to the implementation in `coding-agent-program-example`, while retaining Aunic-specific architecture:
- `tool_call` and `tool_result` / `tool_error` rows are persisted in the `transcript`
- `tool_result` and `tool_error` remain distinct row types in the `transcript`
- Aunic-specific guards like `note-content`, `external_directory`, and `doom_loop` still apply

The key idea is:
- internally, `edit` should be a structured diff-aware file operation
- only the provider-facing tool-result payload should be flattened to short success/error strings
- the `transcript` should persist structured JSON content, not just loose text messages

## When The Model Should Use `edit`
`edit` is for targeted changes to a file the model already understands.
- use `edit` when changing part of an existing file
- use `edit` when the model already read the target file and can identify the exact text to replace
- use `edit` when changing one occurrence or many occurrences of the same string
- use `write` for whole-file replacement when the model already has the full desired file content
- use `note-edit` or `note-write` rather than `edit` when the target is `note-content`

Like the example implementation:
- `edit` should prefer precise string replacement over loose patching
- `edit` should require the file to be read first before ordinary edits
- `edit` may also create a new file when `old_string` is empty and the file does not exist

## How An `edit` Tool Call Starts
1. **The model emits a tool call**
    - The provider returns an assistant message containing an `edit` tool call.
    - The API, not Aunic, provides the `tool_id`.
    - The arguments must contain `file_path`, `old_string`, and `new_string`.

2. **Aunic parses and validates the arguments**
    - `file_path` must exist and must be a string
    - `old_string` must exist and must be a string
    - `new_string` must exist and must be a string
    - `replace_all`, if present, must be a boolean

3. **Aunic records the `tool_call` row**
    - The `tool_call` should be added to the in-memory message list immediately.
    - Because `edit` is a persistent `work-mode` tool, it should also be written to the `transcript`.
    - The `tool_call` row should preserve the raw provider-emitted arguments.

## Input Normalization
After parsing, Aunic should normalize the effective input used for validation, permissions, and execution.

### Path normalization
- if `file_path` is relative, resolve it relative to the current project working directory
- if `file_path` uses `~`, expand it
- keep the original `tool_call` row untouched, but use the normalized absolute path for all internal checks

### String semantics
- `old_string` must match file contents exactly, including whitespace and newlines
- `new_string` may be an empty string, which means deletion
- `old_string` may be an empty string only for file-creation or empty-file replacement cases
- `old_string` and `new_string` must not be identical

### `replace_all`
- if omitted, `replace_all` defaults to `false`
- if `replace_all` is `true`, every exact occurrence of `old_string` should be replaced
- if `replace_all` is `false`, `old_string` must identify exactly one location in the file

## Prompt Expectations
To stay close to the example's prompt behavior, the `edit` tool description should explicitly say:
- the target file should be fully read before it is edited
- when copying from `read` output, do not include the line-number prefix in `old_string` or `new_string`
- preserve indentation exactly
- use the smallest uniquely identifying `old_string` that is still safe
- use `replace_all` for renames or repeated substitutions across a file
- prefer `edit` for targeted changes and `write` for full rewrites
- never use `edit` on the active note's `note-content`
- if possible, mention the protected active note path directly in the prompt from runtime state

## Aunic-Specific Guard: `note-content`
This remains an intentional Aunic divergence.

`work-mode` should not allow `edit` to become a back door around the `note-mode` / `work-mode` split.
- Aunic should keep a runtime `active_markdown_note` object for the current session
- that runtime object should expose the protected note scope as one or more normalized absolute paths, for example `active_markdown_note.note_scope_paths`
- if the target path is the active note file or another included `note-content` file, reject it
- the model should use `note-edit` / `note-write` for those files instead
- the same runtime object should also be used to inject the protected note path(s) into the `edit` system prompt so the model is warned before it calls the tool

## Validation And Safety Checks
Before doing the actual edit, Aunic should stay close to the example's validation flow.

### Secret / shared-content guard
The example includes a secret-scanning guard for shared memory files before edit/write.

Aunic should adopt the same pattern for any shared or syncable surfaces it has.
- if the target path is in an Aunic-managed shared/synced area and the proposed `new_string` appears to introduce secrets, reject with a `tool_error`
- this should happen before the actual edit

### No-op validation
Like the example:
- reject the tool call if `old_string` and `new_string` are exactly the same
- reject the tool call if applying the edit would leave the file unchanged

### Deny-rule validation
Before any edit I/O:
- normalize the path
- resolve the active note scope from the runtime `active_markdown_note` object
- if the normalized target path is inside `active_markdown_note.note_scope_paths`, reject with a `tool_error`
- check whether the path is already denied by write-permission settings
- if so, reject with a `tool_error`

### UNC-path safety
To stay close to the example's behavior:
- if the path is a UNC/network path form, do not do filesystem I/O before permissions are resolved
- let the permission system decide first

### File size guard
The example has a maximum editable file size guard.

Aunic should copy that pattern.
- if the target file is too large to edit safely, reject with a `tool_error`
- the error should tell the model to use a more targeted approach if one exists

### Missing file behavior
Like the example:
- if the file does not exist and `old_string` is empty, treat the request as file creation and allow validation to continue
- if the file does not exist and `old_string` is not empty, reject with a `tool_error`
- include a current-working-directory note in the error
- if possible, suggest a nearby path or similar filename

### Empty `old_string` on existing files
If `old_string` is empty and the target file already exists:
- allow it only if the existing file is empty
- if the existing file has content, reject with a `tool_error`

This keeps file-creation behavior aligned with the example.

### Notebook redirect
If the target path is a notebook file like `.ipynb`:
- reject with a `tool_error`
- tell the model to use a notebook-specific edit tool when Aunic has one

### Read-before-edit guard
For existing non-empty files, Aunic should stay close to the example's read-before-edit behavior.
- if the file exists and there is no session read record for the normalized path, reject
- if the last read was only a partial read/view, reject
- new-file creation does not require a prior read
- empty-file replacement with `old_string: ""` also does not need a prior read if Aunic decides to follow the example exactly

The error should tell the model to read the file first before editing it.

### Early stale-read guard
Before entering permissions/execution, Aunic should do an initial stale-read check for existing files.
- if the file exists and its current modification time is newer than the last recorded full read time, reject
- if the file was fully read and its contents are still identical, allow the edit to continue
- otherwise tell the model to read the file again before editing

### Match validation
For existing files:
- first try an exact `old_string` match
- if that fails, Aunic may normalize quote style for matching the way the example does
- if no match is found, reject with a `tool_error`

### Multiple-match validation
If `old_string` matches more than one location:
- if `replace_all` is `false`, reject with a `tool_error`
- the error should say how many matches were found and tell the model to either add more context or set `replace_all: true`
- if `replace_all` is `true`, the edit can continue

### Structured-config validation
The example validates edits to settings files by simulating the post-edit content and confirming it still conforms to schema.

Aunic should adopt that pattern for any structured config files it owns.
- if the target is an Aunic settings/config file and the before-version is valid, the after-version should also have to validate
- if the resulting config would be invalid, reject with a `tool_error`

## Permission Flow
After validation and safety checks, the call goes through the shared `work-mode` permission system.

### What `edit` permissions match against
To stay close to the example:
- permission checks should match the actual normalized target path
- permission checks should also consider symlink-resolved variants of that path
- deny/ask/allow matching should be done against those effective paths, not against an artificial project-root permission path

### Permission preview should use a diff
Before asking for permission, Aunic should compute a structured diff between:
- old file content
- new edited file content

That diff should be the main thing shown in the permission dialog.

### Permission dialog behavior
To stay close to the example:
- the dialog should distinguish create vs update
- the dialog should show the target path
- the dialog should show the diff, or the created content preview for a new file
- the dialog should be allowed to adjust the final approved edit before execution

If the permission dialog changes the edit before approval:
- the original `tool_call` row stays unchanged
- execution should use the approved `old_string`, `new_string`, and `replace_all`
- the `tool_result` should persist the actual applied edit
- the `tool_result` should include metadata indicating that the final applied edit differed from the original request

### `external_directory`
If the target path is outside the project root, `external_directory` should still apply as an Aunic-specific rule.
- if the external path is explicitly allowed, the edit can continue
- otherwise the result is determined by the `external_directory` rule, which should default to `ask`

### `doom_loop`
If the same `edit` call repeats 3 times with identical effective input, `doom_loop` should trigger.
- matching should use the normalized effective input: resolved `file_path`, effective `old_string`, effective `new_string`, and effective `replace_all`
- if permission approval modified the edit, the post-approval input is what matters for execution, but the original repeated tool call can still be used for retry detection

### Ask / Allow / Deny
If the permission result is `ask`, Aunic should pause execution and show a permission prompt in the UI.
- `once`
    - allow this one edit only
- `always`
    - allow future matching edits for the rest of the current Aunic session
- `reject`
    - deny the request

When possible, the "always allow" option should behave closer to the example:
- use session-scoped permission updates
- if the path is outside the working directory, suggest allowing the relevant directory rather than broad unrelated scope

## What Happens On Rejection
If the edit is rejected by validation, safety checks, config, or the user:
- no file is created or changed
- parent directories are not created unless execution had already reached the create path
- no file-history update is finalized
- Aunic writes a `tool_error` row with the same `tool_id`

The error should clearly indicate which layer rejected the call:
- validation
- `note-content`
- secret/shared-content guard
- read-before-edit
- stale-read
- settings/config validation
- path safety
- a `deny` rule
- `external_directory`
- `doom_loop`
- explicit user rejection
- edit-time execution failure

Suggested `tool_error` content format:
```json
{
  "category": "edit_failed",
  "reason": "file_not_read",
  "file_path": "/path/to/project/src/file.py",
  "message": "File has not been read yet. Read it first before editing it."
}
```

`tool_error` content should stay JSON in the `transcript` so it fits Aunic's transcript table encoding rules.

## Edit Execution
If validation, safety checks, and permissions pass, Aunic should perform the edit in this order.

1. **Resolve effective input**
    - use the normalized absolute path
    - use the final approved `old_string`, `new_string`, and `replace_all` if the permission dialog adjusted them

2. **Detect whether this is a create or update**
    - determine file existence from filesystem metadata, not from whether old content is truthy
    - an existing empty file is still an update, not a create
    - a missing file with `old_string: ""` is a create

3. **Create parent directories**
    - ensure the parent directory exists before the critical write section
    - this matters because `edit` can create a new file in the example-compatible design

4. **Prepare file-history backup**
    - if Aunic file history is enabled, capture the pre-edit state before the critical write section
    - this should happen before the final write, similar to the example

5. **Read current file state with metadata**
    - read the current content
    - detect whether the file exists
    - preserve the detected encoding when possible
    - preserve the detected line endings when possible

6. **Re-check staleness immediately before writing**
    - do a second stale-read check right before the actual write
    - avoid async gaps between the final read/compare and the write itself
    - if the file's timestamp changed, compare content when appropriate to avoid false positives from noisy mtimes
    - if the file truly changed since the last full read, fail with a `tool_error`

7. **Resolve the actual match**
    - find the actual matched string in the current file contents
    - if quote normalization was needed for matching, preserve the file's quote style in the replacement string

8. **Generate the patch**
    - compute a structured patch between old file content and updated file content
    - compute whether this is a create or an update
    - reject if the resulting file would still be unchanged

9. **Write the file**
    - write the updated file contents to disk
    - preserve the existing encoding and line endings when possible

10. **Notify editor / language tooling**
    - notify Aunic-managed LSP/editor integrations that the file changed
    - clear any stale delivered diagnostics for that file if Aunic tracks them

11. **Update session file tracking**
    - update the session read state to the newly written content and fresh modification time
    - this keeps future edits and writes aligned with the latest file state

12. **Finalize file-history state**
    - record the old state and new state in Aunic's file history/session tracking

13. **Build the structured result**
    - compute a structured diff for the final applied edit
    - store whether the edit was a create or update
    - store whether the approved edit was user-modified relative to the original tool call

## Result Formatting In The `transcript`
Like the revised `write` plan, the persistent `tool_result` for `edit` should be structured JSON.

Suggested shape:
```json
{
  "type": "file_edit",
  "operation": "update",
  "file_path": "/path/to/project/src/file.py",
  "old_string": "old text requested by the model",
  "new_string": "new text requested by the model",
  "actual_old_string": "actual matched text from disk",
  "original_file": "full file content before edit",
  "structured_patch": [
    {
      "...": "diff hunk data"
    }
  ],
  "replace_all": false,
  "user_modified": false
}
```

Create result:
```json
{
  "type": "file_edit",
  "operation": "create",
  "file_path": "/path/to/project/new-file.txt",
  "old_string": "",
  "new_string": "new file contents",
  "actual_old_string": "",
  "original_file": "",
  "structured_patch": [
    {
      "...": "diff hunk data"
    }
  ],
  "replace_all": false,
  "user_modified": false
}
```

If approval-time editing changed the actual applied edit:
```json
{
  "type": "file_edit",
  "operation": "update",
  "file_path": "/path/to/project/src/file.py",
  "old_string": "final approved old string",
  "new_string": "final approved new string",
  "actual_old_string": "actual matched text from disk",
  "original_file": "full file content before edit",
  "structured_patch": [
    {
      "...": "diff hunk data"
    }
  ],
  "replace_all": true,
  "user_modified": true
}
```

## Why The `transcript` Should Store Structured Edit Results
The example computes rich structured data but flattens the result to a short success string for the model.

Aunic should keep the richer internal shape in the `transcript`.
- future runs can inspect what changed without reparsing prose
- transcript rendering can show a diff-focused UI
- transcript-to-provider translation can still flatten the result into the short success message the model expects
- this preserves compatibility with the active-markdown-note transcript table because the stored value is still JSON-safe structured data

## Provider-Facing Translation
When Aunic converts the structured `tool_result` row into the provider-facing tool-result block, it can stay close to the example.

Suggested behavior:
- if `replace_all` is `true`, return a short success message saying all occurrences were replaced
- otherwise return a short success message saying the file was updated successfully
- if `user_modified` is `true`, append a note that the approved changes were modified before acceptance

This provider-facing flattening should happen at transcript-to-provider time, not at transcript-write time.

## What Counts As A Failure
### `tool_error`
Use `tool_error` when Aunic itself could not or would not perform the edit.
- malformed arguments
- missing `file_path`, `old_string`, or `new_string`
- attempt to target `note-content`
- file too large
- read-before-edit violation
- stale-read violation
- no match found
- multiple matches without `replace_all`
- invalid create attempt
- invalid structured-config result
- permission denied
- explicit user rejection
- internal execution failure

### `tool_result`
Use `tool_result` only when the file was successfully changed.
- successful single replacement
- successful `replace_all`
- successful deletion
- successful create of a missing file via `old_string: ""`

This is an intentional Aunic difference from thinner designs that blur success and failure into one generic result block.

## Returning The Result To The Model
After the result object is built:
1. write the `tool_result` or `tool_error` to the in-memory message list
2. because `edit` is a persistent `work-mode` tool, also write it to the `transcript`
3. translate the structured result into the provider's expected tool-result format
4. continue the run against the updated session read state

## Differences From `note-edit`
| Aspect | `note-edit` (note-mode) | `edit` (work-mode) |
|------|------|------|
| Scope | Active markdown `note-content` only | Any file except protected `note-content` targets |
| Target | Note snapshot and then live note reconciliation | File on disk |
| Transcript | Ephemeral in-memory only | Persistent transcript-table rows |
| Permission model | Allowed by default within note scope | Shared work-mode permission system |
| Read requirement | Not needed because note snapshot is already in context | Required for existing files before ordinary edits |
| Conflict handling | Snapshot-vs-live-note conflict handling | Stale-file detection against read state |
