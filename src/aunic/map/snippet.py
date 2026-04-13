from __future__ import annotations

import re

from aunic.transcript.parser import split_note_and_transcript

_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
_WHITESPACE_RE = re.compile(r"\s+")
_MAX_SNIPPET_LEN = 200


def compute_auto_snippet(note_content: str, max_len: int = _MAX_SNIPPET_LEN) -> str:
    """Return a short plain-text snippet for a note, suitable for map.md.

    Steps:
    1. Take the note-content half (strip transcript if present).
    2. Strip leading YAML frontmatter (---\\n...\\n---\\n).
    3. Collapse whitespace to single spaces.
    4. Truncate to max_len, appending "…" if cut.
    5. Empty result → "(empty)".
    """
    note_only, _ = split_note_and_transcript(note_content)
    text = note_only

    # Strip leading YAML frontmatter
    text = _FRONTMATTER_RE.sub("", text, count=1)

    # Collapse whitespace
    text = _WHITESPACE_RE.sub(" ", text).strip()

    if not text:
        return "(empty)"

    if len(text) <= max_len:
        return text

    return text[:max_len] + "…"
