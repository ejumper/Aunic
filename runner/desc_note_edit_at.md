Fill in numbered slots in the active note. Ends the run.

The note contains two kinds of slot markers placed by the user:
- <!--Write #N location--> — an insert slot; your content is placed at this point.
- <!--Rewrite #N start-->...<!--Rewrite #N end--> — a rewrite slot; your content replaces everything between the markers.

<parameters>
- edits: Object mapping slot number (as a string key, e.g. "1") to new content for that slot. Required.
  - Omit a slot key to leave that slot unchanged.
  - Set a slot to empty string to clear its content while preserving the markers.
</parameters>

<run_behavior>
Calling note_edit_at is your final action for the run. Complete all research, tool calls and generation before calling it. All provided slots are applied in a single atomic write.
</run_behavior>

<errors>
- invalid_slot: a key in edits does not match any slot number in the note. Check the note for the correct #N values.
- live_note_conflict: the note was edited by the user since the run started. The edit is rejected to protect their changes.
</errors>

<tips>
- Fill multiple slots in one call — include all slot numbers you want to update in the edits object.
- Read the note to confirm slot numbers before calling. Passing a wrong number returns an error without applying any edits.
</tips>
