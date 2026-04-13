from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aunic.domain import ProviderGeneratedRow, TranscriptRow
from aunic.loop.types import LoopEvent, ToolFailure
from aunic.tools.runtime import failure_from_payload


@dataclass
class GeneratedRowsResult:
    """Summary of rows appended from a provider-generated batch."""

    valid_turns: int
    successful_edit_count: int
    successful_note_tool: bool
    tool_failures: list[ToolFailure]


def next_run_log_row_number(run_log: list[TranscriptRow]) -> int:
    """Return the next sequential row number for a run log."""
    if not run_log:
        return 1
    return run_log[-1].row_number + 1


def tool_result_message(tool_name: str, content: object, status: str = "completed") -> str:
    """Build a human-readable summary for a tool result event."""
    if isinstance(content, dict):
        if "message" in content:
            return str(content["message"])
        if tool_name == "web_search":
            return f"web_search returned {len(content) if isinstance(content, list) else 'results'}."
        if tool_name == "web_fetch":
            return f"web_fetch fetched {content.get('title') or content.get('url') or 'a page'}."
        if tool_name == "bash":
            return f"bash finished with status {status}."
        if tool_name == "read":
            return f"read returned {content.get('type', 'content')}."
        if tool_name in {"edit", "write", "note_edit", "note_write"}:
            return f"{tool_name} finished."
        if tool_name.startswith("mcp__"):
            server = content.get("server")
            tool = content.get("tool")
            if server and tool:
                return f"MCP tool {server}.{tool} finished with status {status}."
            return f"MCP tool {tool_name} finished with status {status}."
    if isinstance(content, list):
        return f"{tool_name} returned {len(content)} item(s)."
    if isinstance(content, str):
        return content
    return f"{tool_name} finished."


async def process_generated_rows(
    *,
    generated_rows: list[ProviderGeneratedRow],
    run_log: list[TranscriptRow],
    write_row: Callable[..., Awaitable[int | None]],
    tool_map: dict[str, Any],
    on_tool_event: Callable[[LoopEvent], Awaitable[None]],
    write_message_rows: bool = True,
    track_edits: bool = False,
) -> GeneratedRowsResult:
    """Append provider-generated rows into the run log and transcript.

    Args:
        generated_rows: Rows emitted by the provider SDK tool bridge.
        run_log: In-memory transcript list (mutated in place).
        write_row: Async callable matching the signature
            ``(role, type, tool_name, tool_id, content) -> int | None``.
            Returns the assigned row number, or None if not written.
        tool_map: Maps tool names to definitions (for persistence checks).
        on_tool_event: Async callback called once per completed tool row.
        write_message_rows: When False, message rows are not persisted to
            the transcript file (they still appear in the run-log).
        track_edits: When True, count successful file-edit tool calls in the
            result (used by note mode).
    """
    valid_turns = 0
    successful_edit_count = 0
    successful_note_tool = False
    tool_failures: list[ToolFailure] = []

    for generated in generated_rows:
        row = generated.row
        transcript_content = (
            row.content if generated.transcript_content is None else generated.transcript_content
        )
        definition = tool_map.get(row.tool_name or "")

        if row.type == "message":
            if write_message_rows:
                row_number = await write_row(
                    row.role, row.type, row.tool_name, row.tool_id, transcript_content
                )
            else:
                row_number = next_run_log_row_number(run_log)
            if row_number is not None:
                run_log.append(
                    TranscriptRow(
                        row_number=row_number,
                        role=row.role,
                        type=row.type,
                        tool_name=row.tool_name,
                        tool_id=row.tool_id,
                        content=row.content,
                    )
                )
            continue

        persistent = definition.persistence == "persistent" if definition is not None else True
        if persistent:
            row_number = await write_row(
                row.role, row.type, row.tool_name, row.tool_id, transcript_content
            )
        else:
            row_number = next_run_log_row_number(run_log)
        run_log.append(
            TranscriptRow(
                row_number=row_number or next_run_log_row_number(run_log),
                role=row.role,
                type=row.type,
                tool_name=row.tool_name,
                tool_id=row.tool_id,
                content=row.content,
            )
        )

        if row.role != "tool":
            continue

        if row.type == "tool_result":
            valid_turns += 1
        if row.type == "tool_error" and isinstance(row.content, dict):
            tool_failures.append(failure_from_payload(row.content, tool_name=row.tool_name))
        if track_edits and row.tool_name in {"edit", "write", "note_edit", "note_write"} and row.type == "tool_result":
            successful_edit_count += 1
        if row.tool_name in {"note_edit", "note_write"} and row.type == "tool_result":
            successful_note_tool = True

        status = "completed" if row.type == "tool_result" else "tool_error"
        await on_tool_event(
            LoopEvent(
                kind="tool_result",
                message=tool_result_message(row.tool_name or "tool", row.content, status),
                details={
                    "tool_name": row.tool_name,
                    "status": status,
                    "generated": True,
                },
            )
        )

    return GeneratedRowsResult(
        valid_turns=valid_turns,
        successful_edit_count=successful_edit_count,
        successful_note_tool=successful_note_tool,
        tool_failures=tool_failures,
    )
