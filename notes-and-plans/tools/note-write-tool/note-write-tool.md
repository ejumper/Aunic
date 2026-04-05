## The Note Write Tool
The model uses `note-write` in `note-mode` to fully replace the `note-content` of the active markdown note.
- `note-write`
    - `content`
        - required string
        - the full new contents of the `note-content`

`note-write` is the note-mode sibling of `write`.
- functionally it is the same kind of operation: whole-document replacement
- unlike `write`, it is sandboxed to the active note's `note-content`
- it is allowed by default because editing `note-content` is the purpose of `note-mode`

## Design Goal
Make `note-write` feel like the `note-mode` version of `write`, while retaining note-mode architecture:
- it only targets the active markdown note's `note-content`
- it does not need a `file_path` parameter because the target is implicit
- it does not go through normal work-mode file permissions
- it is ephemeral and is not persisted in the `transcript`
- undo/revert is handled through the editor undo stack and later note-version features, not transcript tool history

## When The Model Should Use `note-write`
`note-write` is for replacing the full `note-content`.
- use `note-write` when creating the note from scratch
- use `note-write` when the model already has the full desired note text and wants to replace the current `note-content` in one shot
- use `note-edit` when changing only part of the note

Like `write`, the tool prompt should steer the model toward the smaller tool when possible.
- full replacement is allowed
- but the prompt should explicitly say to prefer `note-edit` for ordinary partial edits

## How A `note-write` Tool Call Starts
1. **The model emits a tool call**
    - The provider returns an assistant message containing a `note-write` tool call.
    - If the provider uses `tool_id`s, Aunic should still preserve them in the in-memory message list for the duration of the run.
    - The arguments must contain `content`.

2. **Aunic parses and validates the arguments**
    - `content` must exist and must be a string

3. **Aunic records the in-memory `tool_call`**
    - The `tool_call` should be added to the in-memory message list immediately.
    - Because `note-write` is a `note-mode` tool, it should not be written to the persistent `transcript`.

## Target Scope
`note-write` can only modify the active note's `note-content`.
- it cannot modify the `transcript`
- it cannot modify arbitrary files
- it cannot modify included external files unless note-mode later expands to support multi-file note-content

Because the target is fixed:
- there is no path resolution step
- there is no external-directory handling
- there is no filesystem permission prompt for ordinary use

## Input Normalization
After parsing, Aunic should normalize the effective input used for application and rendering.

### Content normalization
- `content` may be an empty string
- empty `note-content` is allowed
- the model's provided line endings and spacing should be preserved as intentional content

If Aunic later adds normalization rules for note text, they should be minimal.
- do not silently rewrite markdown structure
- do not trim meaningful trailing spaces used for markdown line breaks

## Working Copy Model
Like `note-edit`, `note-write` should operate against the model's working note copy first.

The model does not write directly against the raw live file.
- it writes against the current `note-snapshot` in context
- Aunic then reconciles that proposed full replacement against the live `note-content`

This keeps note-mode deterministic across a run.

## Existing Content Safety Checks
Unlike `write`, `note-write` does not require a separate read tool first.
- the model already has the active `note-content` in context as part of note-mode
- that note snapshot is the baseline it is writing against

### Baseline snapshot requirement
Before applying `note-write`, Aunic should have:
- the current working `note-snapshot` seen by the model
- the current live `note-content` from the editor/file

### No no-op rejection
To stay consistent with the newer `write` plan:
- do not reject `note-write` just because the new content exactly matches the current `note-content`
- a same-content write is allowed, though the UI may render it as no visible change

## Permission Model
`note-write` has permission by default.
- `note-mode` exists specifically so the model can edit the note
- no user permission prompt should appear for ordinary `note-write` calls

This is the biggest behavioral difference from `write`.

### Still enforce scope boundaries
Even though permission is automatic:
- the tool must still be hard-sandboxed to `note-content`
- attempts to use it as a back door to edit anything else should fail

## Application Flow
If validation passes, Aunic should perform `note-write` in this order.

1. **Capture the baseline**
    - store the model's current working `note-snapshot`
    - read the current live `note-content`

2. **Apply to the model working copy**
    - replace the entire working `note-snapshot` with the new `content`
    - this becomes the model's new note state for the rest of the run

3. **Check for live-note divergence**
    - compare the live `note-content` to the baseline snapshot the model wrote against
    - if they still match, the live note can be updated directly
    - if they do not match, enter note conflict resolution instead of blindly overwriting user changes

4. **Apply to the live note**
    - replace the full live `note-content` with the new `content`
    - if possible, update the editor buffer live rather than waiting for a disk save

5. **Update note-mode state**
    - update the in-memory working note copy
    - update any cached rendered note-content state used by the editor/UI

6. **Build the structured result**
    - compute a structured diff from old `note-content` to new `note-content`
    - return a structured ephemeral `tool_result`

## Conflict Handling
Because `note-write` replaces the whole note, conflict handling matters more than for a narrow edit.

If the live `note-content` changed after the model saw its snapshot:
- do not blindly overwrite the live note
- enter the same general conflict-resolution path used by `note-edit`, adapted for full-document replacement

At minimum, conflict handling should know:
- the baseline snapshot the model wrote against
- the current live `note-content`
- the model's proposed full replacement

The goal is to avoid erasing user edits that happened mid-run.

## Result Formatting
Like the revised `write` plan, the in-memory `tool_result` for `note-write` should be structured JSON.

Suggested shape:
```json
{
  "type": "note_content_write",
  "content": "full new note-content here",
  "original_content": "old note-content here",
  "structured_patch": [
    {
      "...": "diff hunk data"
    }
  ],
  "meta": {
    "content_source": "tool_call"
  }
}
```

If the final written content differs from the original tool call because of conflict resolution or another note-mode adjustment:
```json
{
  "type": "note_content_write",
  "content": "final written note-content here",
  "original_content": "old note-content here",
  "structured_patch": [
    {
      "...": "diff hunk data"
    }
  ],
  "meta": {
    "content_source": "conflict_resolution"
  }
}
```

## Why `note-write` Results Are Not Persisted
Unlike `write`, `note-write` should not create persistent transcript rows.
- note-mode file-editing tools are ephemeral
- the lasting artifact is the updated `note-content`, not a stored tool history row
- this keeps the `transcript` focused on durable chat/work/search history

So for `note-write`:
- add the `tool_call` and `tool_result` / `tool_error` to the in-memory message list only
- do not write them to the markdown-table `transcript`

## What Counts As A Failure
### `tool_error`
Use `tool_error` when Aunic itself could not or would not perform the write.
- malformed arguments
- missing `content`
- attempt to target anything other than `note-content`
- live-note conflict that could not be safely resolved
- internal execution failure before a usable success result could be produced

Suggested shape:
```json
{
  "category": "note_write_failed",
  "reason": "live_note_conflict",
  "message": "The live note changed after the model read it, so the full-note write could not be applied safely."
}
```

### `tool_result`
Use `tool_result` when the `note-content` was successfully updated.
- successful write of an entirely new note body
- successful replacement of existing note-content
- successful write of empty note-content
- successful write after conflict resolution

## Returning The Result To The Model
After the result object is built:
1. write the `tool_result` or `tool_error` to the in-memory message list
2. do not persist it to the `transcript`
3. return the provider-specific tool-result shape to the model
4. continue the run against the updated working note copy

## Prompt Guidance For The Model
The `note-write` tool description should explicitly say:
- this tool replaces the full `note-content`
- prefer `note-edit` for smaller targeted changes
- use `note-write` when drafting or rewriting the whole note
- this tool cannot edit the `transcript` or arbitrary files

## Undoing Bad Writes
Bad `note-write` calls are not undone through transcript history.
- they are undone through the editor undo stack
- later they can also be undone through note-version history

This matches the general note-mode approach already used for `note-edit`.
