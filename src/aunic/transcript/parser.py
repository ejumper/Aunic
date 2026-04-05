from __future__ import annotations

import json
import re

from aunic.domain import MessageType, Role, TranscriptRow

_TRANSCRIPT_SECTION_RE = re.compile(r"(?m)^---\n# Transcript(?:\n|$)")
_VALID_ROLES: set[str] = {"system", "user", "assistant", "tool"}
_VALID_TYPES: set[str] = {"message", "tool_call", "tool_result", "tool_error"}


def find_transcript_section(text: str) -> tuple[int, int] | None:
    match = _TRANSCRIPT_SECTION_RE.search(text)
    if match is None:
        return None
    return match.start(), len(text)


def split_note_and_transcript(text: str) -> tuple[str, str | None]:
    section = find_transcript_section(text)
    if section is None:
        return text, None
    start, end = section
    return text[:start].rstrip("\n"), text[start:end]


def parse_transcript_rows(transcript_text: str) -> list[TranscriptRow]:
    if not transcript_text.strip():
        return []

    rows: list[TranscriptRow] = []
    for line in transcript_text.splitlines():
        if not line.startswith("|") or line.count("|") < 6:
            continue

        delimiters = [index for index, char in enumerate(line) if char == "|"]
        if len(delimiters) < 6:
            continue

        first_six = delimiters[:6]
        values = [
            line[first_six[index] + 1 : first_six[index + 1]].strip()
            for index in range(5)
        ]
        raw_content = line[first_six[5] + 1 :].strip()
        if raw_content.endswith("|"):
            raw_content = raw_content[:-1].rstrip()

        try:
            row_number = int(values[0])
        except ValueError:
            continue

        role = values[1]
        row_type = values[2]
        if role not in _VALID_ROLES or row_type not in _VALID_TYPES:
            continue

        try:
            content = json.loads(raw_content)
        except json.JSONDecodeError:
            continue

        rows.append(
            TranscriptRow(
                row_number=row_number,
                role=role,  # type: ignore[arg-type]
                type=row_type,  # type: ignore[arg-type]
                tool_name=values[3] or None,
                tool_id=values[4] or None,
                content=content,
            )
        )

    return rows
