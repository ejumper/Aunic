from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aunic.discovery import resolve_note_set
from aunic.domain import TranscriptRow
from aunic.transcript.parser import parse_transcript_rows, split_note_and_transcript

_SNIPPET_MAX = 600
_TRUNCATION_SUFFIX = "...[truncated]"


@dataclass
class TranscriptSearchHit:
    path: str           # absolute path string
    row_number: int
    tool: str | None
    tool_id: str | None
    args_snippet: str | None
    result_snippet: str | None
    result_status: str  # "ok" | "error" | "missing" | "message"


@dataclass
class TranscriptSearchResult:
    hits: list[TranscriptSearchHit]
    total_matches: int
    returned: int
    offset: int
    limit: int
    truncated: bool
    scanned_files: int
    narrow_hint: str | None


class TranscriptSearchService:
    def search(
        self,
        *,
        query: str | None = None,
        tool: str | None = None,
        scope: Path | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> TranscriptSearchResult:
        """Search transcript rows across all Aunic notes under scope (default: home)."""
        notes = resolve_note_set(scope)
        scanned = len(notes)

        all_hits: list[TranscriptSearchHit] = []
        for note_path in notes:
            try:
                text = note_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            _, transcript_text = split_note_and_transcript(text)
            if transcript_text is None:
                continue
            rows = parse_transcript_rows(transcript_text)
            pairs = _pair_rows(rows)
            for hit in _filter_pairs(pairs, note_path=note_path, query=query, tool=tool):
                all_hits.append(hit)

        total = len(all_hits)
        page = all_hits[offset: offset + limit]
        truncated = total > offset + limit
        narrow_hint: str | None = None
        if truncated:
            lo = offset
            hi = offset + len(page)
            narrow_hint = (
                f"{total} matches, showing {lo}-{hi}. "
                "Narrow with query=, tool=, scope= or raise limit (max 100)."
            )

        return TranscriptSearchResult(
            hits=page,
            total_matches=total,
            returned=len(page),
            offset=offset,
            limit=limit,
            truncated=truncated,
            scanned_files=scanned,
            narrow_hint=narrow_hint,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _pair_rows(rows: list[TranscriptRow]) -> list[_Pair]:
    """Group tool_call + tool_result/tool_error rows by tool_id into pairs.

    Orphaned calls get result_status="missing". Message rows are emitted as
    singleton pairs with result_status="message".
    """
    pairs: list[_Pair] = []
    # Index tool_result/tool_error rows by tool_id for O(1) lookup
    results_by_id: dict[str, TranscriptRow] = {}
    for row in rows:
        if row.type in {"tool_result", "tool_error"} and row.tool_id:
            results_by_id[row.tool_id] = row

    seen_result_ids: set[str] = set()

    for row in rows:
        if row.type == "tool_call":
            result_row = results_by_id.get(row.tool_id or "") if row.tool_id else None
            if result_row is not None:
                seen_result_ids.add(row.tool_id or "")
                pairs.append(_Pair(call=row, result=result_row))
            else:
                pairs.append(_Pair(call=row, result=None))
        elif row.type == "message":
            pairs.append(_Pair(call=None, result=None, message_row=row))
        # Skip orphaned tool_result/tool_error rows — they lack a matching call.
        # (We still want to expose them if found without a call, so emit them too)
        elif row.type in {"tool_result", "tool_error"} and (row.tool_id not in seen_result_ids):
            pairs.append(_Pair(call=None, result=row))

    return pairs


@dataclass
class _Pair:
    call: TranscriptRow | None
    result: TranscriptRow | None
    message_row: TranscriptRow | None = None


def _filter_pairs(
    pairs: list[_Pair],
    *,
    note_path: Path,
    query: str | None,
    tool: str | None,
) -> list[TranscriptSearchHit]:
    hits: list[TranscriptSearchHit] = []
    for pair in pairs:
        if pair.message_row is not None:
            # Message rows: only include when query matches message content
            if query is None:
                continue
            content_str = _encode(pair.message_row.content)
            if query.lower() not in content_str.lower():
                continue
            hits.append(TranscriptSearchHit(
                path=str(note_path.resolve()),
                row_number=pair.message_row.row_number,
                tool=None,
                tool_id=None,
                args_snippet=None,
                result_snippet=_truncate(content_str),
                result_status="message",
            ))
            continue

        # Determine call row for tool name / args
        call = pair.call
        result = pair.result

        # Tool name filter
        call_tool = call.tool_name if call is not None else None
        result_tool = result.tool_name if result is not None else None
        effective_tool = call_tool or result_tool
        if tool is not None and effective_tool != tool:
            continue

        # Build snippet strings for query matching
        args_str = _encode(call.content) if call is not None else None
        result_str = _encode(result.content) if result is not None else None

        # Query filter: substring match over args + result JSON text
        if query is not None:
            q = query.lower()
            args_match = args_str is not None and q in args_str.lower()
            result_match = result_str is not None and q in result_str.lower()
            if not args_match and not result_match:
                continue

        # Determine result_status
        if result is None:
            status = "missing"
        elif result.type == "tool_error":
            status = "error"
        else:
            status = "ok"

        row_number = call.row_number if call is not None else (result.row_number if result is not None else 0)
        tool_id = (call.tool_id if call is not None else None) or (result.tool_id if result is not None else None)

        hits.append(TranscriptSearchHit(
            path=str(note_path.resolve()),
            row_number=row_number,
            tool=effective_tool,
            tool_id=tool_id,
            args_snippet=_truncate(args_str) if args_str is not None else None,
            result_snippet=_truncate(result_str) if result_str is not None else None,
            result_status=status,
        ))

    return hits


def _encode(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _truncate(text: str) -> str:
    if len(text) <= _SNIPPET_MAX:
        return text
    return text[:_SNIPPET_MAX] + _TRUNCATION_SUFFIX
