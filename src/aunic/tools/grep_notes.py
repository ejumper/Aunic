from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aunic.discovery import resolve_note_set
from aunic.domain import ToolSpec
from aunic.tools.base import ToolDefinition, ToolExecutionResult
from aunic.tools.runtime import RunToolContext, failure_from_payload, failure_payload
from aunic.transcript.parser import find_transcript_section, split_note_and_transcript

_HARD_LIMIT_CAP = 100
_COLLECTION_CAP_MULTIPLIER = 10

_VALID_SECTIONS = frozenset({"note-content", "transcript", "all"})


@dataclass(frozen=True)
class GrepNotesArgs:
    pattern: str
    section: str = "all"         # "note-content" | "transcript" | "all"
    scope: str | None = None     # absolute / ~-prefixed / relative-to-cwd path
    case_sensitive: bool = False
    literal_text: bool = False
    context: int = 2             # 0..10 lines of surrounding context
    limit: int = 20              # hard-capped at 100
    offset: int = 0


def build_grep_notes_tool_registry() -> tuple[ToolDefinition[Any], ...]:
    return (
        ToolDefinition(
            spec=ToolSpec(
                name="grep_notes",
                description=(
                    "Ripgrep-shaped content search scoped to Aunic notes only, with an optional "
                    "section= filter. Use section='transcript' to find past executed commands and "
                    "tool calls without prose noise; use section='note-content' to find prose "
                    "mentions without transcript rows. Returns absolute path, line number, and "
                    "surrounding context for each match. "
                    "Reach for this when you know a literal phrase or pattern and want to find "
                    "every note that contains it, or to distinguish 'where did I write about X' "
                    "(section='note-content') from 'where did I actually do X' "
                    "(section='transcript')."
                ),
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["pattern"],
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": (
                                "Python regex pattern (or literal string if literal_text=true) "
                                "to search for inside Aunic notes."
                            ),
                        },
                        "section": {
                            "type": "string",
                            "enum": ["note-content", "transcript", "all"],
                            "description": (
                                "Which half of each note to search. 'note-content' skips "
                                "transcript rows; 'transcript' skips prose. Defaults to 'all'."
                            ),
                        },
                        "scope": {
                            "type": "string",
                            "description": (
                                "Absolute path (or ~-prefixed, or relative to cwd) restricting "
                                "the walk to a subtree. Defaults to the user home directory."
                            ),
                        },
                        "case_sensitive": {
                            "type": "boolean",
                            "description": "Case-sensitive match. Default false.",
                        },
                        "literal_text": {
                            "type": "boolean",
                            "description": (
                                "Treat pattern as literal text rather than a regex. Default false."
                            ),
                        },
                        "context": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 10,
                            "description": "Lines of context before and after each match. Default 2.",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "description": "Max hits to return. Default 20.",
                        },
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "Number of hits to skip for pagination. Default 0.",
                        },
                    },
                },
            ),
            parse_arguments=parse_grep_notes_args,
            execute=execute_grep_notes,
        ),
    )


def parse_grep_notes_args(payload: dict[str, Any]) -> GrepNotesArgs:
    allowed = {
        "pattern", "section", "scope", "case_sensitive",
        "literal_text", "context", "limit", "offset",
    }
    extras = sorted(set(payload) - allowed)
    if extras:
        raise ValueError(f"Unexpected fields: {', '.join(extras)}.")

    pattern = payload.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("`pattern` must be a non-empty string.")

    section = payload.get("section", "all")
    if section not in _VALID_SECTIONS:
        raise ValueError("`section` must be 'note-content', 'transcript', or 'all'.")

    scope = payload.get("scope")
    if scope is not None and not isinstance(scope, str):
        raise ValueError("`scope` must be a string.")

    case_sensitive = payload.get("case_sensitive", False)
    if not isinstance(case_sensitive, bool):
        raise ValueError("`case_sensitive` must be a boolean.")

    literal_text = payload.get("literal_text", False)
    if not isinstance(literal_text, bool):
        raise ValueError("`literal_text` must be a boolean.")

    context = payload.get("context", 2)
    if not isinstance(context, int) or isinstance(context, bool):
        raise ValueError("`context` must be an integer.")
    if context < 0 or context > 10:
        raise ValueError("`context` must be between 0 and 10.")

    limit = payload.get("limit", 20)
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("`limit` must be an integer.")
    if limit < 1 or limit > _HARD_LIMIT_CAP:
        raise ValueError(f"`limit` must be between 1 and {_HARD_LIMIT_CAP}.")

    offset = payload.get("offset", 0)
    if not isinstance(offset, int) or isinstance(offset, bool):
        raise ValueError("`offset` must be an integer.")
    if offset < 0:
        raise ValueError("`offset` must be >= 0.")

    return GrepNotesArgs(
        pattern=pattern,
        section=section,
        scope=scope or None,
        case_sensitive=case_sensitive,
        literal_text=literal_text,
        context=context,
        limit=limit,
        offset=offset,
    )


async def execute_grep_notes(
    runtime: RunToolContext,
    args: GrepNotesArgs,
) -> ToolExecutionResult:
    # Resolve scope path
    scope_path: Path | None = None
    if args.scope is not None:
        raw = Path(args.scope).expanduser()
        if not raw.is_absolute():
            raw = runtime.session_state.cwd / raw
        scope_path = raw.resolve()
        if not scope_path.exists():
            payload = failure_payload(
                category="validation_error",
                reason="scope_not_found",
                message=f"scope path does not exist: {scope_path}",
                scope=args.scope,
            )
            return ToolExecutionResult(
                tool_name="grep_notes",
                status="tool_error",
                in_memory_content=payload,
                transcript_content=payload,
                tool_failure=failure_from_payload(payload, tool_name="grep_notes"),
            )
        if not scope_path.is_dir():
            payload = failure_payload(
                category="validation_error",
                reason="scope_not_directory",
                message=f"scope path is not a directory: {scope_path}",
                scope=args.scope,
            )
            return ToolExecutionResult(
                tool_name="grep_notes",
                status="tool_error",
                in_memory_content=payload,
                transcript_content=payload,
                tool_failure=failure_from_payload(payload, tool_name="grep_notes"),
            )

    # Compile regex
    try:
        raw_pattern = re.escape(args.pattern) if args.literal_text else args.pattern
        flags = 0 if args.case_sensitive else re.IGNORECASE
        compiled = re.compile(raw_pattern, flags)
    except re.error as exc:
        payload = failure_payload(
            category="validation_error",
            reason="invalid_regex",
            message=f"Invalid regex pattern: {exc}",
            pattern=args.pattern,
        )
        return ToolExecutionResult(
            tool_name="grep_notes",
            status="tool_error",
            in_memory_content=payload,
            transcript_content=payload,
            tool_failure=failure_from_payload(payload, tool_name="grep_notes"),
        )

    # Walk notes
    notes = resolve_note_set(scope_path)
    scanned = len(notes)

    collection_cap = _COLLECTION_CAP_MULTIPLIER * args.limit
    all_hits: list[dict[str, Any]] = []
    capped = False

    for note_path in notes:
        if len(all_hits) >= collection_cap:
            capped = True
            break
        try:
            text = note_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for hit in _grep_note(text, note_path=note_path, args=args, compiled=compiled):
            all_hits.append(hit)
            if len(all_hits) >= collection_cap:
                capped = True
                break

    total = len(all_hits)
    page = all_hits[args.offset: args.offset + args.limit]
    truncated = total > args.offset + args.limit

    narrow_hint: str | None = None
    if truncated or capped:
        lo = args.offset
        hi = args.offset + len(page)
        parts = [f"{total} matches, showing {lo}-{hi}."]
        if capped:
            parts.append(f"Collection capped at {collection_cap}; refine pattern.")
        parts.append("Narrow with pattern=, scope=, section= or raise limit (max 100).")
        narrow_hint = " ".join(parts)

    result_payload = {
        "hits": page,
        "total_matches": total,
        "returned": len(page),
        "offset": args.offset,
        "limit": args.limit,
        "truncated": truncated or capped,
        "scanned_files": scanned,
        "narrow_hint": narrow_hint,
    }

    return ToolExecutionResult(
        tool_name="grep_notes",
        status="completed",
        in_memory_content=result_payload,
        transcript_content=result_payload,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _grep_note(
    text: str,
    *,
    note_path: Path,
    args: GrepNotesArgs,
    compiled: re.Pattern[str],
) -> list[dict[str, Any]]:
    """Return all matching hits from a single note file."""
    section = args.section
    context_n = args.context

    # Determine transcript start line once for section attribution and base_line.
    transcript_section = find_transcript_section(text)
    transcript_start_line: int | None = None
    if transcript_section is not None:
        transcript_start_offset, _ = transcript_section
        transcript_start_line = text[:transcript_start_offset].count("\n") + 1

    # Choose haystack and base_line based on section filter.
    if section == "note-content":
        note_content, _ = split_note_and_transcript(text)
        haystack = note_content
        base_line = 1
    elif section == "transcript":
        _, transcript_text = split_note_and_transcript(text)
        if transcript_text is None:
            return []
        haystack = transcript_text
        # base_line: line in the original file where the transcript half begins.
        base_line = transcript_start_line if transcript_start_line is not None else 1
    else:  # "all"
        haystack = text
        base_line = 1

    lines = haystack.splitlines()
    hits: list[dict[str, Any]] = []

    for local_idx, line in enumerate(lines):
        if not compiled.search(line):
            continue

        abs_line = base_line + local_idx

        # Per-hit section attribution when scanning the whole file.
        if section == "all":
            if transcript_start_line is not None and abs_line >= transcript_start_line:
                hit_section = "transcript"
            else:
                hit_section = "note-content"
        else:
            hit_section = section

        # Context lines clipped to haystack bounds.
        ctx_before_start = max(0, local_idx - context_n)
        ctx_after_end = min(len(lines), local_idx + context_n + 1)

        hits.append({
            "path": str(note_path.resolve()),
            "line": abs_line,
            "section": hit_section,
            "match": line,
            "context_before": lines[ctx_before_start:local_idx],
            "context_after": lines[local_idx + 1: ctx_after_end],
        })

    return hits
