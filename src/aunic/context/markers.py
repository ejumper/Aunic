from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from aunic.context.types import (
    EditLabel,
    MarkerSpan,
    ParseWarning,
    ParsedNoteFile,
    SourceMapSegment,
    TextSpan,
)
from aunic.transcript.parser import find_transcript_section, split_note_and_transcript

_MarkerKind = Literal["exclude", "include_only", "read_only", "write_scope"]

_MARKER_DEFINITIONS: tuple[tuple[_MarkerKind, str, str], ...] = (
    ("exclude", "%>>", "<<%"),
    ("include_only", "!>>", "<<!"),
    ("read_only", "$>>", "<<$"),
    ("write_scope", "@>>", "<<@"),
)
_OPENERS = tuple(sorted(((kind, token) for kind, token, _ in _MARKER_DEFINITIONS), key=lambda item: len(item[1]), reverse=True))
_CLOSERS = tuple(sorted(((kind, token) for kind, _, token in _MARKER_DEFINITIONS), key=lambda item: len(item[1]), reverse=True))


@dataclass(frozen=True)
class MarkerAnalysis:
    parsed_file: ParsedNoteFile
    labels_by_char: tuple[EditLabel | None, ...]
    visible_by_char: tuple[bool, ...]
    wrapper_by_char: tuple[bool, ...]
    prompt_visible_spans: tuple[MarkerSpan, ...]


def analyze_note_file(snapshot, display_path: str) -> MarkerAnalysis:
    note_text, transcript_text = split_note_and_transcript(snapshot.raw_text)
    transcript_section = find_transcript_section(snapshot.raw_text)
    matched, warnings = _scan_marker_spans(snapshot.path, note_text)
    marker_spans = matched
    prompt_spans: tuple[MarkerSpan, ...] = ()

    wrapper_counts = _range_counts(
        len(note_text),
        tuple(
            part
            for span in matched
            for part in (span.open_span, span.close_span)
        ),
    )
    exclude_counts = _range_counts(
        len(note_text),
        tuple(span.content_span for span in marker_spans if span.marker_type == "exclude"),
    )
    include_counts = _range_counts(
        len(note_text),
        tuple(span.content_span for span in marker_spans if span.marker_type == "include_only"),
    )
    read_only_counts = _range_counts(
        len(note_text),
        tuple(span.content_span for span in marker_spans if span.marker_type == "read_only"),
    )
    write_scope_counts = _range_counts(
        len(note_text),
        tuple(span.content_span for span in marker_spans if span.marker_type == "write_scope"),
    )
    has_include_only = any(span.marker_type == "include_only" for span in marker_spans)
    has_write_scope = any(span.marker_type == "write_scope" for span in marker_spans)

    visible_chars: list[bool] = []
    wrapper_chars: list[bool] = []
    labels: list[EditLabel | None] = []
    for index, _ in enumerate(note_text):
        is_wrapper = wrapper_counts[index] > 0
        wrapper_chars.append(is_wrapper)
        is_visible = not is_wrapper and exclude_counts[index] == 0
        if has_include_only:
            is_visible = is_visible and include_counts[index] > 0
        visible_chars.append(is_visible)

        if is_wrapper:
            labels.append(None)
            continue

        label: EditLabel = "WRITE-EDIT_ALLOWED"
        if has_write_scope and write_scope_counts[index] == 0:
            label = "READ_ONLY-NO_EDITS"
        if read_only_counts[index] > 0:
            label = "READ_ONLY-NO_EDITS"
        if has_include_only and include_counts[index] == 0:
            label = "HIDDEN"
        if exclude_counts[index] > 0:
            label = "HIDDEN"
        labels.append(label)

    parsed_text, source_map = _build_parsed_text(note_text, visible_chars, wrapper_chars)
    hinted_parsed_text = _inject_hidden_hints(parsed_text, source_map, note_text, marker_spans)
    parsed_file = ParsedNoteFile(
        snapshot=snapshot,
        display_path=display_path,
        parsed_text=parsed_text,
        marker_spans=marker_spans,
        prompt_spans=prompt_spans,
        warnings=tuple(warnings),
        source_map=source_map,
        note_text=note_text,
        transcript_text=transcript_text,
        transcript_start_offset=transcript_section[0] if transcript_section else None,
        hinted_parsed_text=hinted_parsed_text,
    )
    return MarkerAnalysis(
        parsed_file=parsed_file,
        labels_by_char=tuple(labels),
        visible_by_char=tuple(visible_chars),
        wrapper_by_char=tuple(wrapper_chars),
        prompt_visible_spans=(),
    )


def analyze_chat_file(snapshot, display_path: str) -> MarkerAnalysis:
    note_text, transcript_text = split_note_and_transcript(snapshot.raw_text)
    transcript_section = find_transcript_section(snapshot.raw_text)
    matched, warnings = _scan_marker_spans(snapshot.path, note_text)
    active_spans = tuple(
        span
        for span in matched
        if span.marker_type in {"exclude", "include_only"}
    )
    marker_spans = active_spans
    prompt_spans: tuple[MarkerSpan, ...] = ()

    wrapper_counts = _range_counts(
        len(note_text),
        tuple(
            part
            for span in active_spans
            for part in (span.open_span, span.close_span)
        ),
    )
    exclude_counts = _range_counts(
        len(note_text),
        tuple(span.content_span for span in marker_spans if span.marker_type == "exclude"),
    )
    include_counts = _range_counts(
        len(note_text),
        tuple(span.content_span for span in marker_spans if span.marker_type == "include_only"),
    )
    has_include_only = any(span.marker_type == "include_only" for span in marker_spans)

    visible_chars: list[bool] = []
    wrapper_chars: list[bool] = []
    labels: list[EditLabel | None] = []
    for index, _ in enumerate(note_text):
        is_wrapper = wrapper_counts[index] > 0
        wrapper_chars.append(is_wrapper)
        is_visible = not is_wrapper and exclude_counts[index] == 0
        if has_include_only:
            is_visible = is_visible and include_counts[index] > 0
        visible_chars.append(is_visible)

        if is_wrapper:
            labels.append(None)
        elif is_visible:
            labels.append("WRITE-EDIT_ALLOWED")
        else:
            labels.append("HIDDEN")

    parsed_text, source_map = _build_parsed_text(note_text, visible_chars, wrapper_chars)
    parsed_file = ParsedNoteFile(
        snapshot=snapshot,
        display_path=display_path,
        parsed_text=parsed_text,
        marker_spans=marker_spans,
        prompt_spans=prompt_spans,
        warnings=tuple(warnings),
        source_map=source_map,
        note_text=note_text,
        transcript_text=transcript_text,
        transcript_start_offset=transcript_section[0] if transcript_section else None,
    )
    return MarkerAnalysis(
        parsed_file=parsed_file,
        labels_by_char=tuple(labels),
        visible_by_char=tuple(visible_chars),
        wrapper_by_char=tuple(wrapper_chars),
        prompt_visible_spans=(),
    )


def text_for_raw_span(
    text: str,
    span: TextSpan,
    *,
    visible_by_char: tuple[bool, ...],
    wrapper_by_char: tuple[bool, ...],
) -> str:
    parts: list[str] = []
    for index in range(span.start, span.end):
        if index < 0 or index >= len(text):
            continue
        if wrapper_by_char[index]:
            continue
        if visible_by_char[index]:
            parts.append(text[index])
    return "".join(parts)


def parsed_span_for_raw_span(
    span: TextSpan,
    source_map: tuple[SourceMapSegment, ...],
) -> TextSpan | None:
    first: int | None = None
    last: int | None = None
    for segment in source_map:
        overlap_start = max(span.start, segment.raw_span.start)
        overlap_end = min(span.end, segment.raw_span.end)
        if overlap_start >= overlap_end:
            continue
        parsed_start = segment.parsed_span.start + (overlap_start - segment.raw_span.start)
        parsed_end = parsed_start + (overlap_end - overlap_start)
        if first is None:
            first = parsed_start
        last = parsed_end
    if first is None or last is None:
        return None
    return TextSpan(first, last)


def warning_to_dict(warning: ParseWarning) -> dict[str, int | str]:
    return {
        "path": str(warning.path),
        "code": warning.code,
        "message": warning.message,
        "line": warning.line,
        "column": warning.column,
        "offset": warning.offset,
    }


def _scan_marker_spans(path: Path, text: str) -> tuple[tuple[MarkerSpan, ...], list[ParseWarning]]:
    stacks: dict[_MarkerKind, list[TextSpan]] = {kind: [] for kind, _, _ in _MARKER_DEFINITIONS}
    matched: list[MarkerSpan] = []
    warnings: list[ParseWarning] = []

    index = 0
    while index < len(text):
        opener = _match_token(text, index, _OPENERS)
        if opener is not None:
            kind, token = opener
            stacks[kind].append(TextSpan(index, index + len(token)))
            index += len(token)
            continue

        closer = _match_token(text, index, _CLOSERS)
        if closer is not None:
            kind, token = closer
            if stacks[kind]:
                open_span = stacks[kind].pop()
                matched.append(
                    MarkerSpan(
                        marker_type=kind,
                        open_span=open_span,
                        content_span=TextSpan(open_span.end, index),
                        close_span=TextSpan(index, index + len(token)),
                    )
                )
            else:
                warnings.append(
                    _build_warning(
                        path,
                        text,
                        index,
                        code="unmatched_close_marker",
                        message=f"Unmatched closing marker {token!r}.",
                    )
                )
            index += len(token)
            continue

        index += 1

    for kind, _, token in _MARKER_DEFINITIONS:
        for open_span in stacks[kind]:
            warnings.append(
                _build_warning(
                    path,
                    text,
                    open_span.start,
                    code="unmatched_open_marker",
                    message=f"Unmatched opening marker {token!r}.",
                )
            )

    matched.sort(key=lambda item: (item.open_span.start, item.close_span.end))
    return tuple(matched), warnings


def _match_token(
    text: str,
    index: int,
    tokens: tuple[tuple[_MarkerKind, str], ...],
) -> tuple[_MarkerKind, str] | None:
    for kind, token in tokens:
        if text.startswith(token, index):
            return kind, token
    return None


def _build_warning(
    path: Path,
    text: str,
    offset: int,
    *,
    code: str,
    message: str,
) -> ParseWarning:
    line = text.count("\n", 0, offset) + 1
    previous_newline = text.rfind("\n", 0, offset)
    column = offset + 1 if previous_newline < 0 else offset - previous_newline
    return ParseWarning(
        path=path,
        code=code,
        message=message,
        line=line,
        column=column,
        offset=offset,
    )


def _range_counts(length: int, spans: tuple[TextSpan, ...]) -> list[int]:
    diff = [0] * (length + 1)
    for span in spans:
        if span.start >= span.end:
            continue
        diff[span.start] += 1
        diff[span.end] -= 1
    counts: list[int] = []
    running = 0
    for index in range(length):
        running += diff[index]
        counts.append(running)
    return counts


def _build_parsed_text(
    text: str,
    visible_by_char: list[bool],
    wrapper_by_char: list[bool],
) -> tuple[str, tuple[SourceMapSegment, ...]]:
    parsed_parts: list[str] = []
    source_map: list[SourceMapSegment] = []
    current_raw_start: int | None = None
    current_raw_end: int | None = None
    current_parsed_start: int | None = None

    for index, char in enumerate(text):
        keep = visible_by_char[index] and not wrapper_by_char[index]
        if keep:
            parsed_index = len(parsed_parts)
            parsed_parts.append(char)
            if (
                current_raw_start is None
                or current_raw_end is None
                or current_raw_end != index
            ):
                if current_raw_start is not None and current_raw_end is not None and current_parsed_start is not None:
                    source_map.append(
                        SourceMapSegment(
                            parsed_span=TextSpan(current_parsed_start, parsed_index),
                            raw_span=TextSpan(current_raw_start, current_raw_end),
                        )
                    )
                current_raw_start = index
                current_raw_end = index + 1
                current_parsed_start = parsed_index
            else:
                current_raw_end = index + 1
        elif current_raw_start is not None and current_raw_end is not None and current_parsed_start is not None:
            source_map.append(
                SourceMapSegment(
                    parsed_span=TextSpan(current_parsed_start, len(parsed_parts)),
                    raw_span=TextSpan(current_raw_start, current_raw_end),
                )
            )
            current_raw_start = None
            current_raw_end = None
            current_parsed_start = None

    if current_raw_start is not None and current_raw_end is not None and current_parsed_start is not None:
        source_map.append(
            SourceMapSegment(
                parsed_span=TextSpan(current_parsed_start, len(parsed_parts)),
                raw_span=TextSpan(current_raw_start, current_raw_end),
            )
        )

    return "".join(parsed_parts), tuple(source_map)


def _span_has_visible_content(
    span: TextSpan,
    visible_by_char: list[bool],
    wrapper_by_char: list[bool],
) -> bool:
    for index in range(span.start, span.end):
        if index < 0 or index >= len(visible_by_char):
            continue
        if visible_by_char[index] and not wrapper_by_char[index]:
            return True
    return False


_HIDDEN_HINT = "<!-- [hidden content] -->"

# Matches all marker open/close tokens so they can be removed when checking
# whether a raw gap contains real hidden content beyond mere syntax.
import re as _re
_MARKER_TOKEN_RE = _re.compile(r'[%!$@]>>|<<[%!$@]')


def _gap_has_hidden_content(raw_start: int, raw_end: int, note_text: str) -> bool:
    """Return True when the raw gap contains non-whitespace content beyond marker tokens."""
    if raw_start >= raw_end:
        return False
    gap_text = note_text[raw_start:raw_end]
    stripped = _MARKER_TOKEN_RE.sub("", gap_text)
    return bool(stripped.strip())


def _inject_hidden_hints(
    parsed_text: str,
    source_map: tuple[SourceMapSegment, ...],
    note_text: str,
    marker_spans: tuple[MarkerSpan, ...],
) -> str:
    """Return parsed_text with HTML hint comments inserted where hidden content
    was stripped out.  A hint is emitted for each gap in the source_map whose
    raw slice contains non-whitespace content beyond bare marker tokens."""
    if not marker_spans:
        return parsed_text

    result: list[str] = []
    prev_raw_end = 0
    prev_parsed_end = 0

    for seg in source_map:
        raw_gap_end = seg.raw_span.start

        # Emit any parsed text up to this segment's start.
        result.append(parsed_text[prev_parsed_end : seg.parsed_span.start])

        # Inject a hint if the raw gap before this segment has hidden content.
        if _gap_has_hidden_content(prev_raw_end, raw_gap_end, note_text):
            result.append(_HIDDEN_HINT)

        # Emit this segment's parsed text.
        result.append(parsed_text[seg.parsed_span.start : seg.parsed_span.end])

        prev_raw_end = seg.raw_span.end
        prev_parsed_end = seg.parsed_span.end

    # Any trailing parsed text after the last segment.
    result.append(parsed_text[prev_parsed_end:])

    # Check for a trailing raw gap (hidden content after the last visible segment).
    if _gap_has_hidden_content(prev_raw_end, len(note_text), note_text):
        result.append(_HIDDEN_HINT)

    return "".join(result)


def reparse_hinted_text(note_text: str, path: Path) -> str:
    """Re-run marker analysis on raw note_text and return hinted_parsed_text.
    Used to refresh the model's view after a note write."""
    from aunic.context.types import FileSnapshot
    from datetime import UTC, datetime
    import hashlib

    snapshot = FileSnapshot(
        path=path,
        raw_text=note_text,
        revision_id="reparse",
        content_hash=hashlib.md5(note_text.encode()).hexdigest(),
        mtime_ns=0,
        size_bytes=len(note_text.encode()),
        captured_at=datetime.now(UTC),
    )
    analysis = analyze_note_file(snapshot, str(path))
    return analysis.parsed_file.hinted_parsed_text
