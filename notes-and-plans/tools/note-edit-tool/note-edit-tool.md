## The Note Edit Tool
The model uses `note-edit` in `note-mode` to make targeted exact-string edits to the active markdown note's `note-content`.
- `note-edit`
    - `old_string`
        - required string
        - the exact text to find in the current working note copy
    - `new_string`
        - required string
        - the exact text that should replace `old_string`
    - `replace_all`
        - optional boolean
        - if `true`, replace every occurrence of `old_string`
        - if omitted, default to `false`

`note-edit` is the note-mode sibling of `edit`.
- functionally it uses the same exact-string replacement model
- unlike `edit`, it is sandboxed to the active note's `note-content`
- unlike `edit`, it is ephemeral and is not persisted in the markdown-table `transcript`

## Design Goal
Make `note-edit` feel like the note-mode version of the stronger `edit` tool, while retaining note-mode architecture:
- it only targets the active note's `note-content`
- it operates against the model's working note copy first, then reconciles with the live note
- it returns structured `tool_result` / `tool_error` objects in memory
- it does not write tool history rows into the persistent `transcript`

The key idea is:
- internally, `note-edit` should be a structured diff-aware note operation
- note-mode should keep the same precise replacement semantics as `edit`
- the main differences are scope, default permission, and conflict handling against the live note

## When The Model Should Use `note-edit`
`note-edit` is for targeted note changes.
- use `note-edit` when changing part of the current note
- use `note-edit` when the model already has the note text in context and can identify the exact text to replace
- use `note-edit` when changing one occurrence or many occurrences of the same string inside the note
- use `note-write` when replacing the full `note-content`

Like `edit`, the prompt should steer the model toward precise replacements.
- prefer `note-edit` for smaller changes
- use `replace_all` for repeated substitutions across the note
- use `note-write` when the model already has the full final note text

## How A `note-edit` Tool Call Starts
1. **The model emits a tool call**
    - The provider returns an assistant message containing a `note-edit` tool call.
    - If the provider uses `tool_id`s, Aunic should preserve them in the in-memory message list for the duration of the run.
    - The arguments must contain `old_string` and `new_string`.

2. **Aunic parses and validates the arguments**
    - `old_string` must exist and must be a string
    - `new_string` must exist and must be a string
    - `replace_all`, if present, must be a boolean

3. **Aunic records the in-memory `tool_call`**
    - The `tool_call` should be added to the in-memory message list immediately.
    - Because `note-edit` is a `note-mode` tool, it should not be written to the persistent `transcript`.

## Target Scope
`note-edit` can only modify the active note's `note-content`.
- it cannot modify the `transcript`
- it cannot modify arbitrary files
- it cannot modify included external files unless note-mode later expands to support multi-file note-content

Because the target is fixed:
- there is no `file_path` parameter
- there is no path resolution step
- there is no filesystem permission prompt for ordinary use

## Input Normalization
After parsing, Aunic should normalize the effective input used for application and rendering.

### String semantics
- `old_string` must match note contents exactly, including whitespace and newlines
- `new_string` may be an empty string, which means deletion
- `old_string` must not be empty
- `old_string` and `new_string` must not be identical

Unlike `edit`, `note-edit` should not have a create-mode branch.
- creating or replacing the whole note belongs to `note-write`
- `note-edit` should stay focused on targeted edits

### `replace_all`
- if omitted, `replace_all` defaults to `false`
- if `replace_all` is `true`, every exact occurrence of `old_string` in the working note copy should be replaced
- if `replace_all` is `false`, `old_string` must identify exactly one location

## Prompt Expectations
To keep `note-edit` robust, its tool description should explicitly say:
- preserve indentation and whitespace exactly
- use the smallest uniquely identifying `old_string` that is still safe
- do not include any note-rendering prefixes if the UI ever shows numbered lines
- use `replace_all` for repeated substitutions
- prefer `note-edit` for targeted changes and `note-write` for full rewrites
- this tool only edits `note-content`

## Working Copy Model
`note-edit` should operate against a working note copy first, not directly against the live file.

At minimum, Aunic should track:
- the baseline `note-snapshot` that the model saw when it generated the tool call
- the current working note copy used for subsequent note-mode tool calls
- the current live `note-content` from the editor/file

The sequence should be:
- validate and apply the edit against the current working note copy
- treat the updated working note copy as the new model-visible note state
- reconcile that result against the live `note-content` before mutating the live note

This keeps note-mode deterministic across a run even if the user edits the note mid-turn.

## Validation And Safety Checks
Before doing the actual edit, `note-edit` should follow a stronger validation flow than the older draft.

### No-op validation
Like `edit`:
- reject the tool call if `old_string` and `new_string` are exactly the same
- reject the tool call if applying the edit would leave the working note copy unchanged

### Match validation against the working copy
For the current working note copy:
- first try an exact `old_string` match
- if that fails, Aunic may normalize quote style for matching the way `edit` does
- if no match is found, reject with a `tool_error`

### Multiple-match validation
If `old_string` matches more than one location in the working note copy:
- if `replace_all` is `false`, reject with a `tool_error`
- the error should say how many matches were found and tell the model to either add more context or set `replace_all: true`
- if `replace_all` is `true`, the edit can continue

### Quote-style preservation
For prose-heavy note content, `note-edit` should borrow the same helpful behavior from `edit`:
- if quote normalization was needed to find the match, preserve the note's actual quote style in the replacement string
- this is especially useful for curly quotes in normal writing

### Deletion behavior
If `new_string` is empty:
- treat the edit as deletion
- Aunic may use the same newline-aware deletion behavior as `edit` so line removal feels cleaner

## Permission Model
`note-edit` has permission by default.
- `note-mode` exists specifically so the model can edit the note
- no user permission prompt should appear for ordinary `note-edit` calls

### Still enforce scope boundaries
Even though permission is automatic:
- the tool must still be hard-sandboxed to `note-content`
- attempts to use it as a back door to edit anything else should fail

## Application Flow
If validation passes, Aunic should perform `note-edit` in this order.

1. **Capture the baseline**
    - store the current working note copy the model wrote against
    - read the current live `note-content`

2. **Resolve the actual match in the working copy**
    - find the actual matched text in the working note copy
    - if quote normalization was needed, compute the final replacement string so style is preserved

3. **Apply to the working note copy**
    - apply the edit to the working note copy using `replace_all` when requested
    - reject if the resulting working note copy would still be unchanged

4. **Generate the patch**
    - compute a structured diff between the old working note copy and the updated working note copy
    - this patch should be reused for result formatting and UI rendering

5. **Check for live-note divergence**
    - compare the live `note-content` to the baseline working copy the model wrote against
    - if they still match, the live note can be updated directly
    - if they do not match, enter note conflict resolution instead of blindly overwriting user changes

6. **Apply to the live note**
    - apply the final approved edit to the live `note-content`
    - if possible, update the editor buffer live rather than waiting for a disk save

7. **Update note-mode state**
    - replace the in-memory working note copy with the updated note content
    - update any cached rendered note-content state used by the editor/UI

8. **Build the structured result**
    - return a structured ephemeral `tool_result`
    - include whether the final applied edit differed from the original tool call

## Conflict Handling
The older draft already points toward conflict resolution, but it should be framed more explicitly now.

If the live `note-content` changed after the model saw its baseline working copy:
- do not blindly replay the same raw replacement against the live note
- enter the note conflict-resolution flow
- use the baseline working copy, the current live note, and the proposed edited note to resolve the conflict

At minimum, conflict handling should know:
- the baseline working note copy the model wrote against
- the current live `note-content`
- the updated working note copy after the proposed edit
- the original requested `old_string`, `new_string`, and `replace_all`

The goal is to avoid erasing user edits that happened mid-run while still keeping the model's internal note state coherent.

## Result Formatting
Like the revised `edit` and `note-write` plans, the in-memory `tool_result` for `note-edit` should be structured JSON.

Suggested shape:
```json
{
  "type": "note_content_edit",
  "old_string": "old text requested by the model",
  "new_string": "new text requested by the model",
  "actual_old_string": "actual matched text from the working note copy",
  "original_content": "full note-content before edit",
  "structured_patch": [
    {
      "...": "diff hunk data"
    }
  ],
  "replace_all": false,
  "user_modified": false,
  "meta": {
    "content_source": "tool_call"
  }
}
```

If the final applied edit differs from the original tool call because of conflict resolution or UI adjustment:
```json
{
  "type": "note_content_edit",
  "old_string": "final approved old string",
  "new_string": "final approved new string",
  "actual_old_string": "actual matched text from the working note copy",
  "original_content": "full note-content before edit",
  "structured_patch": [
    {
      "...": "diff hunk data"
    }
  ],
  "replace_all": true,
  "user_modified": true,
  "meta": {
    "content_source": "conflict_resolution"
  }
}
```

## Why `note-edit` Results Are Not Persisted
Unlike work-mode `edit`, `note-edit` should not create persistent transcript rows.
- note-mode tool activity is ephemeral
- the durable artifact is the updated `note-content`, not a permanent file-operation log
- this keeps the markdown-table `transcript` focused on persistent work-mode actions

So for `note-edit`:
- add the `tool_call` and `tool_result` / `tool_error` to the in-memory message list only
- do not write them to the markdown-table `transcript`

## What Counts As A Failure
### `tool_error`
Use `tool_error` when Aunic itself could not or would not perform the edit.
- malformed arguments
- missing `old_string` or `new_string`
- empty `old_string`
- identical `old_string` and `new_string`
- no match found in the working note copy
- multiple matches without `replace_all`
- live-note conflict that could not be safely resolved
- internal execution failure before a usable success result could be produced

Suggested shape:
```json
{
  "category": "note_edit_failed",
  "reason": "multiple_matches",
  "message": "The requested old_string matched 3 locations in the working note copy. Add more context or set replace_all to true."
}
```

### `tool_result`
Use `tool_result` only when the note edit was successfully applied.
- successful single replacement
- successful `replace_all`
- successful deletion
- successful edit after conflict resolution

## Returning The Result To The Model
After the result object is built:
1. write the `tool_result` or `tool_error` to the in-memory message list
2. do not persist it to the `transcript`
3. return the provider-specific tool-result shape to the model
4. continue the run against the updated working note copy

## Prompt Guidance For The Model
The `note-edit` tool description should explicitly say:
- this tool performs exact string replacements in `note-content`
- preserve indentation and whitespace exactly
- use `replace_all` only when every occurrence should change
- prefer `note-edit` for targeted changes
- use `note-write` for full-note rewrites
- this tool cannot edit the `transcript` or arbitrary files

## Undoing Bad Edits
Bad `note-edit` calls are not undone through transcript history.
- they are undone through the editor undo stack
- later they can also be undone through note-version history

This matches the general note-mode approach already used for `note-write`.
