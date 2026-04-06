from __future__ import annotations

from dataclasses import replace

from aunic.domain import TranscriptRow

MODEL_COMPACTION_KEEP_RECENT = 5
MODEL_COMPACTION_RESULT_PLACEHOLDER = "[Old tool result content cleared]"
MODEL_COMPACTION_ERROR_PLACEHOLDER = "[Old tool error content cleared]"
MODEL_COMPACTION_TOOLS = frozenset(
    {
        "web_search",
        "web_fetch",
        "read",
        "bash",
        "grep",
        "glob",
        "list",
        "edit",
        "write",
    }
)


def compact_transcript_for_model(
    rows: list[TranscriptRow],
    *,
    keep_recent: int = MODEL_COMPACTION_KEEP_RECENT,
) -> list[TranscriptRow]:
    if not rows:
        return []
    if keep_recent < 0:
        raise ValueError("`keep_recent` must be >= 0.")

    recent_counts: dict[str, int] = {}
    keep_row_numbers: set[int] = set()
    for row in reversed(rows):
        if row.tool_name not in MODEL_COMPACTION_TOOLS:
            continue
        if row.type not in {"tool_result", "tool_error"}:
            continue
        count = recent_counts.get(row.tool_name, 0)
        if count < keep_recent:
            keep_row_numbers.add(row.row_number)
            recent_counts[row.tool_name] = count + 1

    compacted: list[TranscriptRow] = []
    for row in rows:
        if row.tool_name not in MODEL_COMPACTION_TOOLS or row.type not in {"tool_result", "tool_error"}:
            compacted.append(row)
            continue
        if row.row_number in keep_row_numbers:
            compacted.append(row)
            continue
        placeholder = (
            MODEL_COMPACTION_RESULT_PLACEHOLDER
            if row.type == "tool_result"
            else MODEL_COMPACTION_ERROR_PLACEHOLDER
        )
        compacted.append(replace(row, content=placeholder))
    return compacted


def filter_incomplete_tool_pairs_for_model(rows: list[TranscriptRow]) -> list[TranscriptRow]:
    if not rows:
        return []

    tool_call_ids: set[str] = set()
    tool_result_ids: set[str] = set()
    for row in rows:
        if row.role == "assistant" and row.type == "tool_call" and isinstance(row.tool_id, str) and row.tool_id:
            tool_call_ids.add(row.tool_id)
        elif row.role == "tool" and row.type in {"tool_result", "tool_error"} and isinstance(row.tool_id, str) and row.tool_id:
            tool_result_ids.add(row.tool_id)

    valid_tool_ids = tool_call_ids & tool_result_ids
    if not valid_tool_ids and (tool_call_ids or tool_result_ids):
        return [
            row
            for row in rows
            if not (
                (row.role == "assistant" and row.type == "tool_call")
                or (row.role == "tool" and row.type in {"tool_result", "tool_error"})
            )
        ]

    filtered: list[TranscriptRow] = []
    for row in rows:
        if row.role == "assistant" and row.type == "tool_call":
            if row.tool_id in valid_tool_ids:
                filtered.append(row)
            continue
        if row.role == "tool" and row.type in {"tool_result", "tool_error"}:
            if row.tool_id in valid_tool_ids:
                filtered.append(row)
            continue
        filtered.append(row)
    return filtered


def prepare_transcript_for_model(
    rows: list[TranscriptRow],
    *,
    keep_recent: int = MODEL_COMPACTION_KEEP_RECENT,
) -> list[TranscriptRow]:
    return compact_transcript_for_model(
        filter_incomplete_tool_pairs_for_model(rows),
        keep_recent=keep_recent,
    )
