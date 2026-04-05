from __future__ import annotations

import json
from typing import Any, Literal

from aunic.domain import TranscriptRow
from aunic.transcript.flattening import flatten_tool_result_for_provider

TranscriptProtocol = Literal["anthropic", "openai_compatible"]


def group_assistant_rows(
    rows: list[TranscriptRow],
) -> list[TranscriptRow | list[TranscriptRow]]:
    grouped: list[TranscriptRow | list[TranscriptRow]] = []
    current_group: list[TranscriptRow] = []

    for row in rows:
        if row.role == "assistant":
            current_group.append(row)
            continue
        if current_group:
            grouped.append(list(current_group))
            current_group.clear()
        grouped.append(row)

    if current_group:
        grouped.append(list(current_group))
    return grouped


def translate_for_anthropic(
    groups: list[TranscriptRow | list[TranscriptRow]],
    note_snapshot: str,
    user_prompt: str,
) -> list[dict[str, Any]]:
    translated: list[dict[str, Any]] = []
    index = 0
    while index < len(groups):
        item = groups[index]
        if isinstance(item, list):
            translated.append(_translate_assistant_group_for_anthropic(item))
            index += 1
            continue

        if item.role == "tool":
            blocks: list[dict[str, Any]] = []
            while index < len(groups):
                candidate = groups[index]
                if isinstance(candidate, list) or candidate.role != "tool":
                    break
                blocks.append(_translate_tool_row_for_anthropic(candidate))
                index += 1
            translated.append({"role": "user", "content": blocks})
            continue

        translated.append({"role": item.role, "content": _content_as_text(item.content)})
        index += 1

    translated.append({"role": "user", "content": compose_final_user_message(note_snapshot, user_prompt)})
    return translated


def translate_for_openai(
    groups: list[TranscriptRow | list[TranscriptRow]],
    note_snapshot: str,
    user_prompt: str,
) -> list[dict[str, Any]]:
    translated: list[dict[str, Any]] = []
    for item in groups:
        if isinstance(item, list):
            translated.append(_translate_assistant_group_for_openai(item))
            continue

        if item.role == "tool":
            translated.append(
                {
                    "role": "tool",
                    "tool_call_id": item.tool_id,
                    "content": flatten_tool_result_for_provider(item),
                }
            )
            continue

        translated.append({"role": item.role, "content": _content_as_text(item.content)})

    translated.append({"role": "user", "content": compose_final_user_message(note_snapshot, user_prompt)})
    return translated


def translate_transcript(
    rows: list[TranscriptRow],
    protocol: TranscriptProtocol,
    note_snapshot: str,
    user_prompt: str,
) -> list[dict[str, Any]]:
    groups = group_assistant_rows(rows)
    if protocol == "anthropic":
        return translate_for_anthropic(groups, note_snapshot, user_prompt)
    if protocol == "openai_compatible":
        return translate_for_openai(groups, note_snapshot, user_prompt)
    raise ValueError(f"Unsupported transcript protocol {protocol!r}.")


def _translate_assistant_group_for_anthropic(
    rows: list[TranscriptRow],
) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    for row in rows:
        if row.type == "message":
            blocks.append({"type": "text", "text": _content_as_text(row.content)})
        elif row.type == "tool_call":
            blocks.append(
                {
                    "type": "tool_use",
                    "id": row.tool_id,
                    "name": row.tool_name,
                    "input": _tool_input(row.content),
                }
            )

    if blocks and all(block["type"] == "text" for block in blocks):
        return {"role": "assistant", "content": "\n".join(block["text"] for block in blocks)}
    return {"role": "assistant", "content": blocks}


def _translate_assistant_group_for_openai(
    rows: list[TranscriptRow],
) -> dict[str, Any]:
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for row in rows:
        if row.type == "message":
            text_parts.append(_content_as_text(row.content))
            continue
        if row.type == "tool_call":
            tool_calls.append(
                {
                    "id": row.tool_id,
                    "type": "function",
                    "function": {
                        "name": row.tool_name,
                        "arguments": json.dumps(
                            row.content,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                }
            )

    return {
        "role": "assistant",
        "content": "\n".join(text_parts) if text_parts else None,
        "tool_calls": tool_calls or None,
    }


def _translate_tool_row_for_anthropic(row: TranscriptRow) -> dict[str, Any]:
    block = {
        "type": "tool_result",
        "tool_use_id": row.tool_id,
        "content": flatten_tool_result_for_provider(row),
    }
    if row.type == "tool_error":
        block["is_error"] = True
    return block


def _content_as_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, separators=(",", ":"))


def _tool_input(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if content is None:
        return {}
    return {"value": content}


def compose_final_user_message(note_snapshot: str, user_prompt: str) -> str:
    return f"{note_snapshot}\n\n---\n\n{user_prompt}"
