from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from aunic.context.types import MarkerSpan, ParsedNoteFile, SourceMapSegment
from aunic.domain import ToolSpec, WorkMode
from aunic.tools.base import ToolDefinition, ToolExecutionResult
from aunic.tools.filesystem import (
    _apply_exact_edit,
    _build_structured_patch,
    build_mutating_file_tool_registry,
    build_read_tool_registry,
)
from aunic.tui.note_tables import normalize_markdown_tables
from aunic.tools.memory_manifest import build_memory_manifest as _build_memory_manifest  # noqa: F401
from aunic.tools.plan import build_plan_tool_registry
from aunic.tools.research import build_research_tool_registry
from aunic.tools.runtime import RunToolContext, failure_from_payload, failure_payload
from aunic.tools.memory_tools import build_memory_tool_registry
from aunic.tools.sleep import build_sleep_tool_registry
from aunic.tools.stop_process import build_stop_process_tool_registry
from aunic.tools.task_tools import build_task_tool_registry

try:
    from aunic.tools.bash import build_bash_tool_registry
except Exception:  # pragma: no cover - until file exists during incremental edits
    build_bash_tool_registry = lambda: ()  # type: ignore[assignment]


@dataclass(frozen=True)
class NoteEditArgs:
    old_string: str
    new_string: str
    replace_all: bool = False


@dataclass(frozen=True)
class NoteWriteArgs:
    content: str


READ_MODE_TOOL_NAMES: frozenset[str] = frozenset({"read", "grep", "glob", "list"})
WORK_MODE_TOOL_NAMES: frozenset[str] = frozenset({"edit", "write", "bash"})
OUTSIDE_NOTE_TOOL_NAMES: frozenset[str] = READ_MODE_TOOL_NAMES | WORK_MODE_TOOL_NAMES
PLANNING_ACTIVE_STATUSES: frozenset[str] = frozenset({"drafting", "awaiting_approval"})


def build_note_tool_registry(
    *,
    work_mode: WorkMode = "off",
    project_root: Path | None = None,
    planning_status: str = "none",
) -> tuple[ToolDefinition[Any], ...]:
    registry: list[ToolDefinition[Any]] = []
    registry.extend(build_memory_tool_registry(project_root=project_root))
    registry.extend(build_plan_tool_registry())
    registry.extend(build_sleep_tool_registry())
    registry.extend(build_stop_process_tool_registry())
    planning_active = planning_status in PLANNING_ACTIVE_STATUSES
    if not planning_active:
        registry.extend(build_note_only_registry())
    registry.extend(build_research_tool_registry())
    if work_mode in {"read", "work"} or planning_active:
        registry.extend(build_read_tool_registry())
    if work_mode in {"read", "work"}:
        registry.extend(build_task_tool_registry())
    if work_mode == "work" and not planning_active:
        registry.extend(build_mutating_file_tool_registry())
        registry.extend(build_bash_tool_registry())
    return tuple(registry)


def build_chat_tool_registry(
    *, work_mode: WorkMode = "off", project_root: Path | None = None
) -> tuple[ToolDefinition[Any], ...]:
    registry: list[ToolDefinition[Any]] = list(build_memory_tool_registry(project_root=project_root))
    registry.extend(build_sleep_tool_registry())
    registry.extend(build_stop_process_tool_registry())
    registry.extend(build_research_tool_registry())
    if work_mode in {"read", "work"}:
        registry.extend(build_read_tool_registry())
        registry.extend(build_task_tool_registry())
    if work_mode == "work":
        registry.extend(build_mutating_file_tool_registry())
        registry.extend(build_bash_tool_registry())
    return tuple(registry)


def build_note_only_registry() -> tuple[ToolDefinition[Any], ...]:
    return (
        ToolDefinition(
            spec=ToolSpec(
                name="note_edit",
                description=(
                    "Edit the current active markdown note's note-content only using exact "
                    "old_string/new_string replacement semantics. old_string must come from "
                    "the current note-content, not transcript rows or tool output."
                ),
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["old_string", "new_string"],
                    "properties": {
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                        "replace_all": {"type": "boolean"},
                    },
                },
            ),
            parse_arguments=parse_note_edit_args,
            execute=execute_note_edit,
            persistence="ephemeral",
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="note_write",
                description=(
                    "Replace the entire note-content of the current active markdown note. "
                    "This writes note-content only, not transcript rows or chat replies."
                ),
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["content"],
                    "properties": {
                        "content": {"type": "string"},
                    },
                },
            ),
            parse_arguments=parse_note_write_args,
            execute=execute_note_write,
            persistence="ephemeral",
        ),
    )


def parse_note_edit_args(payload: dict[str, Any]) -> NoteEditArgs:
    _ensure_no_extra_keys(payload, {"old_string", "new_string", "replace_all"})
    old_string = _require_string(payload, "old_string")
    new_string = _require_string(payload, "new_string")
    replace_all = payload.get("replace_all", False)
    if not isinstance(replace_all, bool):
        raise ValueError("`replace_all` must be a boolean.")
    return NoteEditArgs(old_string=old_string, new_string=new_string, replace_all=replace_all)


def parse_note_write_args(payload: dict[str, Any]) -> NoteWriteArgs:
    _ensure_no_extra_keys(payload, {"content"})
    return NoteWriteArgs(content=_require_string(payload, "content"))


async def execute_note_edit(runtime: RunToolContext, args: NoteEditArgs) -> ToolExecutionResult:
    if not args.old_string:
        return _note_tool_error(
            "note_edit",
            failure_payload(
                category="validation_error",
                reason="empty_old_string",
                message="note-edit requires a non-empty old_string.",
            ),
        )
    if args.old_string == args.new_string:
        return _note_tool_error(
            "note_edit",
            failure_payload(
                category="validation_error",
                reason="no_op",
                message="old_string and new_string must differ.",
            ),
        )
    baseline = runtime.working_note_content
    updated, actual_old, error = _apply_exact_edit(
        baseline,
        old_string=args.old_string,
        new_string=args.new_string,
        replace_all=args.replace_all,
    )
    if error is not None:
        return _note_tool_error("note_edit", error)
    if updated == baseline:
        return _note_tool_error(
            "note_edit",
            failure_payload(
                category="validation_error",
                reason="no_op",
                message="Edit would leave the note unchanged.",
            ),
        )
    live_snapshot, live_note, _ = await runtime.read_live_note()
    if live_note != baseline:
        return _note_tool_error(
            "note_edit",
            failure_payload(
                category="conflict",
                reason="live_note_conflict",
                message="The live note changed after the model read it, so the edit could not be applied safely.",
            ),
        )
    touched_ranges = _touched_row_ranges_from_patch(_build_structured_patch(baseline, updated))
    normalized = normalize_markdown_tables(updated, touched_row_ranges=touched_ranges)
    await runtime.write_live_note_content(normalized, expected_revision=live_snapshot.revision_id)
    payload = {
        "type": "note_content_edit",
        "old_string": args.old_string,
        "new_string": args.new_string,
        "actual_old_string": actual_old,
        "original_content": baseline,
        "structured_patch": _build_structured_patch(baseline, normalized),
        "replace_all": args.replace_all,
        "user_modified": False,
        "meta": {"content_source": "tool_call"},
    }
    return ToolExecutionResult(
        tool_name="note_edit",
        status="completed",
        in_memory_content=payload,
        transcript_content=None,
    )


async def execute_note_write(runtime: RunToolContext, args: NoteWriteArgs) -> ToolExecutionResult:
    baseline = runtime.working_note_content
    live_snapshot, live_note, _ = await runtime.read_live_note()
    if live_note != baseline:
        return _note_tool_error(
            "note_write",
            failure_payload(
                category="conflict",
                reason="live_note_conflict",
                message="The live note changed after the model read it, so the full-note write could not be applied safely.",
            ),
        )

    parsed_file = _active_parsed_file(runtime)
    if parsed_file is not None:
        include_spans = [s for s in parsed_file.marker_spans if s.marker_type == "include_only"]
        exclude_spans = [s for s in parsed_file.marker_spans if s.marker_type == "exclude"]

        # Multiple include_only spans: note_write is not safe (registry should have removed
        # this tool already, but guard here defensively).
        if len(include_spans) > 1:
            return _note_tool_error(
                "note_write",
                failure_payload(
                    category="protected_rejection",
                    reason="multiple_include_only_markers",
                    message=(
                        "note_write cannot be used when multiple !>> <<! spans are present. "
                        "Use note_edit with exact old_string/new_string pairs instead."
                    ),
                ),
            )

        # Single include_only span: scope the write to the span content only.
        if len(include_spans) == 1:
            span = include_spans[0]
            raw = baseline
            new_raw = raw[: span.open_span.end] + args.content + raw[span.close_span.start :]
            return await _commit_note_write(runtime, new_raw, baseline, live_snapshot.revision_id)

        # Exclude spans present — classify each as top / bottom / middle.
        if exclude_spans:
            classifications = [
                _classify_exclude_span(s, parsed_file.source_map) for s in exclude_spans
            ]
            if "middle" in classifications:
                return _note_tool_error(
                    "note_write",
                    failure_payload(
                        category="protected_rejection",
                        reason="middle_exclude_marker",
                        message=(
                            "note_write cannot be used when a %>> <<% span sits between "
                            "writable content. Use note_edit with exact old_string/new_string "
                            "pairs instead."
                        ),
                    ),
                )

            # Only edge spans — preserve them, place write between top and bottom.
            top_spans = [s for s, c in zip(exclude_spans, classifications) if c == "top"]
            bot_spans = [s for s, c in zip(exclude_spans, classifications) if c == "bottom"]
            raw = baseline
            top_end = max(s.close_span.end for s in top_spans) if top_spans else 0
            bot_start = min(s.open_span.start for s in bot_spans) if bot_spans else len(raw)
            top_raw = raw[:top_end].rstrip("\n")
            bot_raw = raw[bot_start:].lstrip("\n")
            parts = [p for p in (top_raw, args.content, bot_raw) if p]
            new_raw = "\n\n".join(parts)
            return await _commit_note_write(runtime, new_raw, baseline, live_snapshot.revision_id)

    return await _commit_note_write(runtime, args.content, baseline, live_snapshot.revision_id)


async def _commit_note_write(
    runtime: RunToolContext,
    new_raw_content: str,
    baseline: str,
    expected_revision: str,
) -> ToolExecutionResult:
    normalized = normalize_markdown_tables(new_raw_content)
    await runtime.write_live_note_content(normalized, expected_revision=expected_revision)
    payload = {
        "type": "note_content_write",
        "content": normalized,
        "original_content": baseline,
        "structured_patch": _build_structured_patch(baseline, normalized),
        "meta": {"content_source": "tool_call"},
    }
    return ToolExecutionResult(
        tool_name="note_write",
        status="completed",
        in_memory_content=payload,
        transcript_content=None,
    )


def _active_parsed_file(runtime: RunToolContext) -> ParsedNoteFile | None:
    if runtime.context_result is None:
        return None
    for pf in runtime.context_result.parsed_files:
        if pf.snapshot.path == runtime.active_file:
            return pf
    return runtime.context_result.parsed_files[0] if runtime.context_result.parsed_files else None


def _classify_exclude_span(
    span: MarkerSpan,
    source_map: tuple[SourceMapSegment, ...],
) -> Literal["top", "bottom", "middle"]:
    """Classify an exclude span by whether visible content exists before/after it.

    If source_map is empty (fully-hidden note) every span is treated as "top" so
    that note_write places the written content beneath all hidden blocks."""
    has_before = any(seg.raw_span.start < span.open_span.start for seg in source_map)
    has_after = any(seg.raw_span.end > span.close_span.end for seg in source_map)
    if not has_before:
        return "top"
    if not has_after:
        return "bottom"
    return "middle"


def reapply_note_edit_payload_to_note_content(
    current_note_content: str,
    payload: dict[str, Any],
) -> str | None:
    old_string = payload.get("actual_old_string") or payload.get("old_string")
    new_string = payload.get("new_string")
    replace_all = bool(payload.get("replace_all", False))
    if not isinstance(old_string, str) or not old_string:
        return None
    if not isinstance(new_string, str):
        return None

    updated, _, error = _apply_exact_edit(
        current_note_content,
        old_string=old_string,
        new_string=new_string,
        replace_all=replace_all,
    )
    if error is not None or updated == current_note_content:
        return None

    touched_ranges = _touched_row_ranges_from_patch(_build_structured_patch(current_note_content, updated))
    return normalize_markdown_tables(updated, touched_row_ranges=touched_ranges)


def _note_tool_error(tool_name: str, payload: dict[str, Any]) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=tool_name,
        status="tool_error",
        in_memory_content=payload,
        transcript_content=None,
        tool_failure=failure_from_payload(payload, tool_name=tool_name),
    )


def _require_string(payload: dict[str, Any], key: str) -> str:
    if key not in payload:
        raise ValueError(f"Missing required field `{key}`.")
    value = payload[key]
    if not isinstance(value, str):
        raise ValueError(f"`{key}` must be a string.")
    return value


def _ensure_no_extra_keys(payload: dict[str, Any], expected: set[str]) -> None:
    extras = sorted(set(payload) - expected)
    if extras:
        raise ValueError(f"Unexpected fields: {', '.join(extras)}.")


def _touched_row_ranges_from_patch(structured_patch: list[dict[str, Any]]) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    for op in structured_patch:
        start = int(op.get("new_start_line", 1)) - 1
        end = int(op.get("new_end_line", start + 1)) - 1
        if end < start:
            end = start
        ranges.append((max(0, start), max(0, end)))
    return tuple(ranges)
