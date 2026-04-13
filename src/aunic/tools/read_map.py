from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aunic.domain import ToolSpec
from aunic.map.builder import MAP_PATH
from aunic.tools.base import ToolDefinition, ToolExecutionResult
from aunic.tools.runtime import RunToolContext, failure_from_payload, failure_payload


@dataclass(frozen=True)
class ReadMapArgs:
    scope: str | None = None   # absolute / ~-prefixed / relative-to-cwd path


def build_read_map_tool_registry() -> tuple[ToolDefinition[Any], ...]:
    return (
        ToolDefinition(
            spec=ToolSpec(
                name="read_map",
                description=(
                    "Read the user's pre-built index of every Aunic note on this system "
                    "(~/.aunic/map.md). Each entry is a path + short summary. "
                    "Reach for this when you do not yet know a specific query or phrase to "
                    "search for, and want to browse the user's notes by topic. "
                    "Pass scope=<path> to get only the subtree relevant to the current task. "
                    "If the index is missing, tell the user to run /map."
                ),
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": [],
                    "properties": {
                        "scope": {
                            "type": "string",
                            "description": (
                                "Optional absolute path (or ~-prefixed, or relative to cwd) "
                                "restricting the map to a subtree. When omitted, returns the "
                                "full map."
                            ),
                        },
                    },
                },
            ),
            parse_arguments=parse_read_map_args,
            execute=execute_read_map,
            persistence="ephemeral",
        ),
    )


def parse_read_map_args(payload: dict[str, Any]) -> ReadMapArgs:
    allowed = {"scope"}
    extras = sorted(set(payload) - allowed)
    if extras:
        raise ValueError(f"Unexpected fields: {', '.join(extras)}.")

    scope = payload.get("scope")
    if scope is not None and not isinstance(scope, str):
        raise ValueError("`scope` must be a string.")

    return ReadMapArgs(scope=scope or None)


async def execute_read_map(
    runtime: RunToolContext,
    args: ReadMapArgs,
) -> ToolExecutionResult:
    from aunic.map.render import parse_map, render_map

    if not MAP_PATH.exists():
        payload = failure_payload(
            category="resource_error",
            reason="map_not_built",
            message=(
                "The note map (~/.aunic/map.md) has not been built yet. "
                "Ask the user to run /map to generate it."
            ),
        )
        return ToolExecutionResult(
            tool_name="read_map",
            status="tool_error",
            in_memory_content=payload,
            transcript_content=payload,
            tool_failure=failure_from_payload(payload, tool_name="read_map"),
        )

    # Read the map
    try:
        map_text = MAP_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        payload = failure_payload(
            category="resource_error",
            reason="map_read_error",
            message=f"Could not read map file: {exc}",
        )
        return ToolExecutionResult(
            tool_name="read_map",
            status="tool_error",
            in_memory_content=payload,
            transcript_content=payload,
            tool_failure=failure_from_payload(payload, tool_name="read_map"),
        )

    # Resolve scope if provided
    scope_resolved: Path | None = None
    if args.scope is not None:
        raw = Path(args.scope).expanduser()
        if not raw.is_absolute():
            raw = runtime.session_state.cwd / raw
        scope_resolved = raw.resolve()
        if not scope_resolved.exists():
            payload = failure_payload(
                category="validation_error",
                reason="scope_not_found",
                message=f"scope path does not exist: {scope_resolved}",
                scope=args.scope,
            )
            return ToolExecutionResult(
                tool_name="read_map",
                status="tool_error",
                in_memory_content=payload,
                transcript_content=payload,
                tool_failure=failure_from_payload(payload, tool_name="read_map"),
            )
        if not scope_resolved.is_dir():
            payload = failure_payload(
                category="validation_error",
                reason="scope_not_directory",
                message=f"scope path is not a directory: {scope_resolved}",
                scope=args.scope,
            )
            return ToolExecutionResult(
                tool_name="read_map",
                status="tool_error",
                in_memory_content=payload,
                transcript_content=payload,
                tool_failure=failure_from_payload(payload, tool_name="read_map"),
            )

    # Parse and optionally filter
    entries = parse_map(map_text)
    walk_root = _parse_walk_root(map_text)
    generated_at_str = _parse_generated_at(map_text)

    if scope_resolved is not None:
        filtered = {
            p: e for p, e in entries.items()
            if _is_under(p, scope_resolved)
        }
        content_text = render_map(filtered, walk_root=scope_resolved)
        entry_count = len(filtered)
    else:
        content_text = map_text
        entry_count = len(entries)

    result_payload = {
        "map_path": str(MAP_PATH),
        "content": content_text,
        "entry_count": entry_count,
        "walk_root": str(walk_root),
        "scope_applied": str(scope_resolved) if scope_resolved is not None else None,
        "generated_at": generated_at_str,
    }

    # Compact transcript version omits full content (the model already sees it)
    transcript_payload = {
        "map_path": str(MAP_PATH),
        "entry_count": entry_count,
        "walk_root": str(walk_root),
        "scope_applied": str(scope_resolved) if scope_resolved is not None else None,
        "generated_at": generated_at_str,
        "content": content_text,
    }

    return ToolExecutionResult(
        tool_name="read_map",
        status="completed",
        in_memory_content=result_payload,
        transcript_content=transcript_payload,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent)
        return True
    except ValueError:
        return False


def _parse_walk_root(map_text: str) -> Path:
    for line in map_text.splitlines():
        if line.startswith("Generated:") and " from " in line:
            after_from = line.split(" from ", 1)[1]
            root_str = after_from.split(" (")[0].strip()
            if root_str:
                return Path(root_str)
    return Path.home()


def _parse_generated_at(map_text: str) -> str:
    for line in map_text.splitlines():
        if line.startswith("Generated:"):
            # "Generated: 2026-04-11T17:22:04Z from ..."
            parts = line.split(":", 1)
            if len(parts) == 2:
                rest = parts[1].strip()
                return rest.split(" from ")[0].strip()
    return ""
