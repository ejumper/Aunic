The active markdown note is used to preserve the context of a run in an organized fashion. note_edit makes one or more targeted find-and-replace operations in the active markdown note. **Ends the run** when every requested edit applies successfully; on partial failure, the successful edits are kept and you get a recovery turn to retry only the failed ones. Prefer note_edit over note_write when the note already has content and you only need to add or update specific sections.

<placement_strategy>
The active markdown note is used to preserve the context of runs in an organized and structured fashion. Edits should NOT be purely linear, they should find the most relevant place in the active markdown note for the information, and create new sections if no relevant section exists. Consider what you did on the run, including but not limited to:
- research and information gathered
- features, files, and anything else you created
- decisions you came to
- errors discovered
- errors fixed
- anything else pertinent to session context
Then consider what is already in the active markdown note, and how the current run's information could be fit into the existing note structure.
Consider any explicit or implicit instructions from the user on what belongs in the active markdown note. Words like "here", "the note", "document" probably point towards instructions on what to record.
</placement_strategy>

<run_behavior>
Calling note_edit is your final action for the run when every requested edit applies — the user sees the result immediately.
- Do ALL research, browsing, and analysis before calling note_edit.
- For multiple changes, batch them into one note_edit call with the `edits` array — do not split into multiple runs.
- If some edits in a batch fail, the successful ones are kept and the run continues for one more turn so you can retry the failures. The run does not end until every edit in the call succeeds.
</run_behavior>

<parameters>
Two equivalent forms — provide EITHER the top-level form OR the edits array, not both.

Single edit (top-level form):
- old_string: Exact text currently in the note to replace. Must be unique unless replace_all=true. Required.
- new_string: Replacement text. Required.
- replace_all: If true, replaces every occurrence of old_string. Default false. Optional.

Multiple edits (batch form):
- edits: Array of {old_string, new_string, replace_all?} ops. Each item follows the same rules as a single edit. Ops apply sequentially in array order. Non-empty. Required when using this form.
</parameters>

<batch_operation>
- Ops apply sequentially in the order given. Each op sees the note state left by earlier successful ops in the same batch.
- A failed op does NOT abort the batch — later ops still attempt to apply.
- Cascading: if op 2 was crafted to depend on op 1's new content and op 1 fails, op 2 will also fail (old_string_not_found). Both come back in the failed list.
- Successful ops are kept on the note even when later ops fail.
- Validation errors (empty old_string, old_string == new_string) reject the entire batch with nothing applied. Fix the args and resubmit the whole call.
</batch_operation>

<format>
Use standard markdown unless the note already uses a different format (paragraph prose, outline, etc.).
</format>

<critical_requirements>
EXACT MATCH: old_string must match character-for-character.
- Blank lines between paragraphs count (a missing blank line = no match)
- Header level must match exactly (## vs ### fails)
- Leading/trailing whitespace matters
- If old_string appears more than once, set replace_all=true or include more surrounding context to make it unique

PROTECTED RANGES: Do not let old_string overlap $>> <<$ blocks. Choose a target that falls entirely outside them.

PLAN BATCH ORDER: Earlier ops change what later ops will see. Before submitting a batch, mentally apply each op in order and verify the next op's old_string will still be present.
</critical_requirements>

<special_cases>
- Add a new section at the end: set old_string to the last line of the note, new_string to that line + new section content.
- Delete a section: set new_string to empty string.
- Update a repeated term everywhere: set replace_all=true.
- Make several unrelated changes in one shot: use the edits array — one op per change.
</special_cases>

<warning>
Do not confuse note_edit with edit. Use note_edit to alter the active markdown note, use edit to alter any other file. If the user specifies a file they would like to edit implicitly or explicitly, use edit not note_edit.
</warning>

<response_shape>
On full success the response carries `applied` and the run ends. On partial or total failure the response carries `applied`, `total`, and a `failed` array of `{index, error, message}` entries — index is 1-based and matches the position in the request.
</response_shape>

<recovery>
If you get old_string_not_found (single edit, or per-entry in a batch failed list):
1. Re-read the relevant section of the note. Earlier successful ops in the same batch may have changed surrounding text.
2. Copy the target text character-for-character from the current note state, including surrounding blank lines.
3. Include a header or bullet above/below the change point to anchor uniqueness.
4. For batches: resubmit ONLY the failed ops. Do not resubmit ops marked applied — they are already on the note.
5. Never approximate — if unsure, include the entire paragraph or section.

If you get multiple_matches: add more surrounding lines to old_string until it's unique, OR set replace_all=true if every occurrence should change.

If you get protected_range: the target overlaps a $>> <<$ block. Choose a different anchor point outside the protected area, or restructure the edit so the replaced span sits entirely outside.

Conflicts that arise from the user editing the note mid-run are routed to a conflict-resolution UI for the user to decide — those outcomes appear in your failed list (as old_string_not_found when the user rejects the edit) and should not be auto-retried; defer to the user's decision and move on.
</recovery>

<batch_examples>
Correct — sequential ops where op 2 still matches after op 1:

```
edits: [
  { old_string: "## Old Title\n",      new_string: "## New Title\n" },
  { old_string: "see Old Title above", new_string: "see New Title above" }
]
```

Incorrect — op 2's old_string was already changed by op 1, so op 2 will fail:

```
edits: [
  { old_string: "## Old Title", new_string: "## New Title" },
  { old_string: "## Old Title", new_string: "## New Title (revised)" }
]
```

Recovery — on a 3-op batch where ops 1 and 3 applied but op 2 failed, resubmit ONLY op 2 (corrected). Do not resend op 1 or op 3.
</batch_examples>
