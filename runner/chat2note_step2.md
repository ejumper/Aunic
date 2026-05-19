The text below the `---` is a structured digest of a recent conversation. Your task is to integrate the information from the digest into the current note where it best fits, then end this run by calling `note_edit` once (or `note_write` once if the integration is large enough that a full rewrite is simpler than many splices).

THE OVERRIDING PRINCIPLE IS DATA RETENTION. Do not drop information from the digest. Do not extrapolate or add information that is not in the digest or the existing note. Rewrite only as much as necessary to make the integration read as cohesive markdown in the voice and structure of the existing note.

How to decide where each piece belongs:

- If the note already has a heading or section that the information naturally extends, integrate it there.
- If the information is a final decision or plan, place it under whatever decision/plan-tracking section the note has — or create one if the note has none.
- If the information is factual context (concepts, definitions, code), place it where the note discusses related topics. If no related topic exists yet, append a new section with a clear heading.
- If the information is about specific files or code, prefer to place it near other file/code references in the note.
- If you cannot find a natural home, append a new section at a sensible location (usually the end) with a heading that describes its content.

What "integrate" means here: rewrite the digest's content so it reads as part of the existing note rather than a pasted block. Match the surrounding voice (terse vs verbose, list-heavy vs prose). Combine adjacent items into a single sentence or bullet where the note's style suggests that. But preserve every load-bearing detail — file paths, identifiers, version numbers, error messages, code snippets, decision specifics. If in doubt, keep more.

OUTPUT:

End the run with exactly one tool call: `note_edit` for a localized splice, or `note_write` for a full-note rewrite. Do not call other tools (no `web_search`, no `read_file`, no `bash`). Do not produce a plain-text response — the run must end on a note edit.

REMINDER: Preserve every piece of information from the digest. Adapt the prose to the note's voice and structure, but do not omit content.

The digest follows.

---
