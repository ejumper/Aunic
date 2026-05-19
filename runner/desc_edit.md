Make a targeted find-and-replace in a file.

<parameters>
- file_path: Absolute path to the file to modify. Required.
- old_string: Text to replace. Must match the file exactly. Required.
- new_string: Replacement text. Use empty string to delete. Required.
- replace_all: If true, replace every occurrence. Default false. Optional.
</parameters>

<critical_requirements>
EXACT MATCH: old_string must match the file character-for-character.

- Every space and tab
- Every blank line and trailing newline
- Indentation level (count spaces vs. tabs)

Typographic quotes (" " ' ') are normalized to straight ASCII quotes before matching, so quote-style mismatches are tolerated.

UNIQUENESS (when replace_all=false): old_string must appear exactly once.
- Include 3–5 lines of surrounding context to make the target unique.
- Use replace_all=true to rename a term everywhere in the file.
</critical_requirements>

<special_cases>
- Delete a line: set new_string to empty string. The trailing newline is removed automatically for a clean deletion.
- Replace every occurrence: set replace_all=true.
- Create a new file: use Write instead.
</special_cases>

<errors_and_recovery>
If you get old_string_not_found:
1. Read the file again at the specific location.
2. Copy the target text character-for-character, including surrounding blank lines.
3. Include the full function or block if needed — more context is always safer.
4. Never approximate — if uncertain, read first.

If you get multiple_matches:
- Extend old_string with more surrounding lines to make it unique, OR
- Set replace_all=true if every instance should change.
</errors_and_recovery>

<tips>
- Read the file before editing to confirm exact content and indentation.
- Prefer Edit over Write for targeted changes — Write overwrites the entire file.
- Send multiple independent edits to the same file in a single response.
</tips>
