from __future__ import annotations

import json
import re
from typing import Any, Iterable

from aunic.domain import MessageType, Role, TranscriptRow
from aunic.transcript.parser import parse_transcript_rows, split_note_and_transcript

_TRANSCRIPT_HEADER = (
    "---\n"
    "# Transcript\n"
    "| # | role      | type        | tool_name  | tool_id  | content\n"
    "|---|-----------|-------------|------------|----------|-------------------------------\n"
)
_DATA_ROW_RE = re.compile(r"^\|\s*\d+\s*\|")
_USER_TOOL_ID_RE = re.compile(r"^user_(\d+)$")


def ensure_transcript_section(text: str) -> str:
    note_text, transcript_text = split_note_and_transcript(text)
    if transcript_text is not None:
        return text

    normalized = note_text.rstrip("\n")
    if normalized:
        return f"{normalized}\n\n{_TRANSCRIPT_HEADER}"
    return _TRANSCRIPT_HEADER


def format_transcript_row(
    row_number: int,
    role: Role,
    row_type: MessageType,
    tool_name: str | None,
    tool_id: str | None,
    content: Any,
) -> str:
    encoded_content = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    return (
        f"| {str(row_number).ljust(2)} | "
        f"{role.ljust(9)} | "
        f"{row_type.ljust(11)} | "
        f"{(tool_name or '').ljust(10)} | "
        f"{(tool_id or '').ljust(8)} | "
        f"{encoded_content}"
    )


def append_transcript_row(
    text: str,
    role: Role,
    row_type: MessageType,
    tool_name: str | None,
    tool_id: str | None,
    content: Any,
) -> tuple[str, int]:
    repaired = repair_transcript_section(ensure_transcript_section(text))
    note_text, transcript_text = split_note_and_transcript(repaired)
    transcript_text = transcript_text or _TRANSCRIPT_HEADER

    next_row_number = _find_last_row_number(transcript_text) + 1
    new_row = format_transcript_row(
        next_row_number,
        role,
        row_type,
        tool_name,
        tool_id,
        content,
    )

    transcript_body = transcript_text.rstrip("\n")
    updated_transcript = f"{transcript_body}\n{new_row}\n"
    updated_note = note_text.rstrip("\n")
    if updated_note:
        return f"{updated_note}\n\n{updated_transcript}", next_row_number
    return updated_transcript, next_row_number


def append_transcript_rows(
    text: str,
    rows: Iterable[tuple[Role, MessageType, str | None, str | None, Any]],
) -> tuple[str, tuple[int, ...]]:
    updated_text = text
    row_numbers: list[int] = []
    for role, row_type, tool_name, tool_id, content in rows:
        updated_text, row_number = append_transcript_row(
            updated_text,
            role,
            row_type,
            tool_name,
            tool_id,
            content,
        )
        row_numbers.append(row_number)
    return updated_text, tuple(row_numbers)


def next_synthetic_user_tool_id(text: str) -> str:
    _, transcript_text = split_note_and_transcript(text)
    if not transcript_text:
        return "user_001"

    next_index = 1
    for row in parse_transcript_rows(transcript_text):
        if not row.tool_id:
            continue
        match = _USER_TOOL_ID_RE.fullmatch(row.tool_id)
        if match is None:
            continue
        next_index = max(next_index, int(match.group(1)) + 1)
    return f"user_{next_index:03d}"


def append_synthetic_tool_pair(
    text: str,
    *,
    tool_name: str,
    tool_call_content: Any,
    tool_response_content: Any,
    response_type: MessageType = "tool_result",
) -> tuple[str, str, tuple[int, int]]:
    if response_type not in {"tool_result", "tool_error"}:
        raise ValueError("Synthetic tool rows must end with `tool_result` or `tool_error`.")

    tool_id = next_synthetic_user_tool_id(text)
    updated_text, row_numbers = append_transcript_rows(
        text,
        [
            ("assistant", "tool_call", tool_name, tool_id, tool_call_content),
            ("tool", response_type, tool_name, tool_id, tool_response_content),
        ],
    )
    return updated_text, tool_id, (row_numbers[0], row_numbers[1])


def repair_transcript_section(text: str) -> str:
    note_text, transcript_text = split_note_and_transcript(text)
    if transcript_text is not None:
        if "| # |" in transcript_text and "|---|" in transcript_text:
            return text

    lines = text.splitlines()
    data_indexes = [index for index, line in enumerate(lines) if _is_transcript_data_row(line)]
    if not data_indexes:
        return text

    first_data_index = data_indexes[0]
    prefix_lines = lines[:first_data_index]
    suffix_lines = lines[first_data_index:]
    while prefix_lines and not prefix_lines[-1].strip():
        prefix_lines.pop()

    rebuilt_lines = [*prefix_lines]
    if rebuilt_lines:
        rebuilt_lines.append("")
    rebuilt_lines.extend(_TRANSCRIPT_HEADER.rstrip("\n").splitlines())
    rebuilt_lines.extend(suffix_lines)
    return "\n".join(rebuilt_lines).rstrip("\n") + "\n"


def delete_rows_by_tool_id(text: str, tool_id: str) -> str:
    note_text, transcript_text = split_note_and_transcript(text)
    if transcript_text is None:
        return text

    remaining = [row for row in parse_transcript_rows(transcript_text) if row.tool_id != tool_id]
    return _rebuild_transcript_text(note_text, remaining)


def delete_search_result_item(text: str, row_number: int, result_index: int) -> str:
    note_text, transcript_text = split_note_and_transcript(text)
    if transcript_text is None:
        return text

    rows = parse_transcript_rows(transcript_text)
    target = next((row for row in rows if row.row_number == row_number), None)
    if target is None or not isinstance(target.content, list):
        return text
    if result_index < 0 or result_index >= len(target.content):
        return text

    new_content = [item for i, item in enumerate(target.content) if i != result_index]
    updated_rows = [
        TranscriptRow(
            row_number=row.row_number,
            role=row.role,
            type=row.type,
            tool_name=row.tool_name,
            tool_id=row.tool_id,
            content=new_content if row.row_number == row_number else row.content,
        )
        for row in rows
    ]
    return _rebuild_transcript_text(note_text, updated_rows)


def delete_row_by_number(text: str, row_number: int) -> str:
    note_text, transcript_text = split_note_and_transcript(text)
    if transcript_text is None:
        return text

    rows = parse_transcript_rows(transcript_text)
    target = next((row for row in rows if row.row_number == row_number), None)
    if target is None:
        return text

    if target.tool_id:
        remaining = [row for row in rows if row.tool_id != target.tool_id]
    else:
        remaining = [row for row in rows if row.row_number != row_number]
    return _rebuild_transcript_text(note_text, remaining)


def _renumber_rows(lines: list[str]) -> list[str]:
    rows = [
        row
        for row in parse_transcript_rows("\n".join(lines))
    ]
    return [
        format_transcript_row(
            index,
            row.role,
            row.type,
            row.tool_name,
            row.tool_id,
            row.content,
        )
        for index, row in enumerate(rows, start=1)
    ]


def _find_last_row_number(transcript_text: str) -> int:
    rows = parse_transcript_rows(transcript_text)
    if not rows:
        return 0
    return rows[-1].row_number


def _is_transcript_data_row(line: str) -> bool:
    return _DATA_ROW_RE.match(line.strip()) is not None


def _rebuild_transcript_text(note_text: str, rows: list[TranscriptRow]) -> str:
    rendered_rows = [
        format_transcript_row(
            index,
            row.role,
            row.type,
            row.tool_name,
            row.tool_id,
            row.content,
        )
        for index, row in enumerate(rows, start=1)
    ]
    transcript = _TRANSCRIPT_HEADER + ("\n".join(rendered_rows) + "\n" if rendered_rows else "")
    normalized_note = note_text.rstrip("\n")
    if normalized_note:
        return f"{normalized_note}\n\n{transcript}"
    return transcript
