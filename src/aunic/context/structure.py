from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

from markdown_it import MarkdownIt

from aunic.config import ContextSettings, SETTINGS
from aunic.context.markers import MarkerAnalysis, parsed_span_for_raw_span
from aunic.context.types import StructuralNode, TextSpan

_MARKDOWN = MarkdownIt("commonmark")
_HEADING_TEXT_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)\s*$")
_SEARCH_RESULTS_SECTION = "search results"


@dataclass(frozen=True)
class _MarkdownBlock:
    kind: str
    line_start: int
    line_end: int
    raw_span: TextSpan
    heading_path: tuple[str, ...]
    heading_level: int | None = None


@dataclass(frozen=True)
class MarkdownChunk:
    kind: str
    text: str
    span: TextSpan
    heading_path: tuple[str, ...]


def build_structural_nodes(
    analyses: tuple[MarkerAnalysis, ...],
    settings: ContextSettings | None = None,
) -> tuple[StructuralNode, ...]:
    config = settings or SETTINGS.context
    nodes: list[StructuralNode] = []
    for file_index, analysis in enumerate(analyses, start=1):
        blocks = _markdown_blocks(analysis.parsed_file.note_text)
        file_nodes: list[StructuralNode] = []
        heading_counter = 0
        section_counter = 0
        raw_text = analysis.parsed_file.note_text
        file_nodes.append(
            StructuralNode(
                target_id=f"t:{file_index}:0",
                file_path=analysis.parsed_file.snapshot.path,
                file_label=analysis.parsed_file.display_path,
                kind="anchor",
                label="WRITE-EDIT_ALLOWED",
                heading_path=(),
                line_start=1,
                line_end=max(1, raw_text.count("\n") + 1),
                raw_span=TextSpan(0, 0),
                parsed_span=None,
                preview="(start of file)",
                heading_id=None,
                heading_level=None,
                anchor_id=f"a:{file_index}:0",
            )
        )
        for block in _merge_chat_threads(blocks, raw_text):
            for segment in _split_block_segments(block, analysis):
                for chunk in _chunk_segment(
                    segment,
                    block.kind,
                    raw_text,
                    config.chunk_size_chars,
                ):
                    section_counter += 1
                    chunk_text = analysis.parsed_file.note_text[chunk.start : chunk.end]
                    effective_kind = block.kind
                    effective_heading_path = block.heading_path
                    effective_heading_level = block.heading_level
                    heading_override = _heading_override(chunk_text, block.heading_path)
                    if block.kind == "paragraph" and heading_override is not None:
                        effective_kind = "heading"
                        effective_heading_path = heading_override.heading_path
                        effective_heading_level = heading_override.heading_level
                    heading_id: str | None = None
                    anchor_id = f"a:{file_index}:{section_counter}"
                    if effective_kind == "heading":
                        heading_counter += 1
                        heading_id = f"h:{file_index}:{heading_counter}"
                    preview = _preview_text(
                        chunk_text,
                        config.preview_chars,
                    )
                    effective_label = _structural_label(
                        segment.label,
                        effective_heading_path,
                    )
                    parsed_span = None
                    if effective_label != "HIDDEN" and chunk.start != chunk.end:
                        parsed_span = parsed_span_for_raw_span(
                            chunk,
                            analysis.parsed_file.source_map,
                        )
                    line_start, line_end = _line_range_for_span(
                        chunk,
                        analysis.parsed_file.note_text,
                    )
                    file_nodes.append(
                        StructuralNode(
                            target_id=f"t:{file_index}:{section_counter}",
                            file_path=analysis.parsed_file.snapshot.path,
                            file_label=analysis.parsed_file.display_path,
                            kind=effective_kind,
                            label=effective_label,
                            heading_path=effective_heading_path,
                            line_start=line_start,
                            line_end=line_end,
                            raw_span=chunk,
                            parsed_span=parsed_span,
                            preview=preview,
                            heading_id=heading_id,
                            heading_level=effective_heading_level,
                            anchor_id=anchor_id,
                        )
                    )
        nodes.extend(file_nodes)
    return tuple(nodes)


def render_structural_view(
    nodes: tuple[StructuralNode, ...],
    *,
    focus_target_id: str | None = None,
) -> str:
    lines: list[str] = []
    current_file: str | None = None
    for node in nodes:
        if node.file_label != current_file:
            current_file = node.file_label
            lines.append(f"FILE: {current_file}")
        heading_path = " > ".join(node.heading_path) if node.heading_path else "(root)"
        parts = [
            f"target_id={node.target_id}",
            f"kind={node.kind}",
            f"label={node.label}",
            f"lines={node.line_start}-{node.line_end}",
            f"heading_path={heading_path}",
        ]
        if node.kind == "chat_thread":
            parts.append("content_role=chat_transcript")
        if node.heading_id:
            parts.append(f"heading_id={node.heading_id}")
        if node.anchor_id:
            parts.append(f"anchor_id={node.anchor_id}")
        if focus_target_id == node.target_id:
            parts.append("FOCUS AREA")
        parts.append(f'preview="{node.preview}"')
        lines.append("- " + " | ".join(parts))
    return "\n".join(lines).strip()


_LABEL_CODES = {
    "WRITE-EDIT_ALLOWED": "W",
    "READ_ONLY-NO_EDITS": "R",
    "READ_ONLY-SEARCH_RESULTS": "S",
}


def render_target_map(
    nodes: tuple[StructuralNode, ...],
    *,
    focus_target_id: str | None = None,
) -> tuple[str, str]:
    """Return (target_map_text, read_only_map_text) in compact format.

    Writable nodes go into target_map, read-only into read_only_map.
    HIDDEN nodes are excluded entirely. No preview field (note snapshot has full text).
    """
    writable_lines: list[str] = []
    readonly_lines: list[str] = []
    w_current_file: str | None = None
    r_current_file: str | None = None

    for node in nodes:
        if node.label == "HIDDEN":
            continue
        code = _LABEL_CODES.get(node.label, "?")
        heading_path = " > ".join(node.heading_path) if node.heading_path else "(root)"
        tid = node.target_id
        if focus_target_id and focus_target_id == tid:
            tid = f"{tid}*"

        parts = [tid, node.kind, code, f"{node.line_start}-{node.line_end}", heading_path]
        if node.heading_id:
            parts.append(node.heading_id)
        if node.anchor_id:
            parts.append(node.anchor_id)
        line = " ".join(parts)

        if code == "W":
            if node.file_label != w_current_file:
                w_current_file = node.file_label
                writable_lines.append(f"FILE: {w_current_file}")
            writable_lines.append(line)
        else:
            if node.file_label != r_current_file:
                r_current_file = node.file_label
                readonly_lines.append(f"FILE: {r_current_file}")
            readonly_lines.append(line)

    return "\n".join(writable_lines).strip(), "\n".join(readonly_lines).strip()


def render_parsed_note_text(analyses: tuple[MarkerAnalysis, ...]) -> str:
    blocks: list[str] = []
    for analysis in analyses:
        text = analysis.parsed_file.parsed_text
        body = text if text else "(empty)"
        blocks.append(f"FILE: {analysis.parsed_file.display_path}\n{body}")
    return "\n\n".join(blocks).strip()


def chunk_markdown_text(
    text: str,
    *,
    target_chars: int,
    hard_cap_chars: int,
) -> tuple[MarkdownChunk, ...]:
    chunks: list[MarkdownChunk] = []
    for block in _merge_chat_threads(_markdown_blocks(text), text):
        if block.raw_span.start == block.raw_span.end:
            continue
        if (
            block.kind in {"code_fence", "code_block", "chat_thread", "heading", "thematic_break"}
            or block.raw_span.end - block.raw_span.start <= hard_cap_chars
        ):
            block_text = text[block.raw_span.start : block.raw_span.end]
            if block_text.strip():
                chunks.append(
                    MarkdownChunk(
                        kind=block.kind,
                        text=block_text,
                        span=block.raw_span,
                        heading_path=block.heading_path,
                    )
                )
            continue
        for span in _split_prose_text_with_limits(
            text[block.raw_span.start : block.raw_span.end],
            target_chars=target_chars,
            hard_cap_chars=hard_cap_chars,
        ):
            chunk_span = TextSpan(block.raw_span.start + span.start, block.raw_span.start + span.end)
            chunk_text = text[chunk_span.start : chunk_span.end]
            if chunk_text.strip():
                chunks.append(
                    MarkdownChunk(
                        kind=block.kind,
                        text=chunk_text,
                        span=chunk_span,
                        heading_path=block.heading_path,
                    )
                )
    return tuple(chunks)


def _markdown_blocks(text: str) -> list[_MarkdownBlock]:
    tokens = _MARKDOWN.parse(text)
    blocks: list[_MarkdownBlock] = []
    heading_stack: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type == "heading_open" and token.level == 0 and token.map:
            inline = tokens[index + 1] if index + 1 < len(tokens) else None
            heading_text = inline.content.strip() if inline is not None else ""
            level = int(token.tag[1]) if token.tag.startswith("h") else 1
            heading_stack = heading_stack[: level - 1]
            heading_stack.append(heading_text)
            blocks.append(
                _MarkdownBlock(
                    kind="heading",
                    line_start=token.map[0] + 1,
                    line_end=token.map[1],
                    raw_span=_span_for_lines(text, token.map[0], token.map[1]),
                    heading_path=tuple(heading_stack),
                    heading_level=level,
                )
            )
            index += 3
            continue

        if token.level == 0 and token.type in {
            "paragraph_open",
            "blockquote_open",
            "bullet_list_open",
            "ordered_list_open",
        } and token.map:
            blocks.append(
                _MarkdownBlock(
                    kind=_normalize_block_kind(token.type),
                    line_start=token.map[0] + 1,
                    line_end=token.map[1],
                    raw_span=_span_for_lines(text, token.map[0], token.map[1]),
                    heading_path=tuple(heading_stack),
                    heading_level=None,
                )
            )
            index = _advance_past_block(tokens, index)
            continue

        if token.level == 0 and token.type in {"fence", "code_block", "hr"} and token.map:
            blocks.append(
                _MarkdownBlock(
                    kind=_normalize_block_kind(token.type),
                    line_start=token.map[0] + 1,
                    line_end=token.map[1],
                    raw_span=_span_for_lines(text, token.map[0], token.map[1]),
                    heading_path=tuple(heading_stack),
                    heading_level=None,
                )
            )
            index += 1
            continue

        index += 1

    if blocks:
        return blocks
    if not text:
        return [
            _MarkdownBlock(
                kind="paragraph",
                line_start=1,
                line_end=1,
                raw_span=TextSpan(0, 0),
                heading_path=(),
                heading_level=None,
            )
        ]
    return [
        _MarkdownBlock(
            kind="paragraph",
            line_start=1,
            line_end=max(1, text.count("\n") + 1),
            raw_span=TextSpan(0, len(text)),
            heading_path=(),
            heading_level=None,
        )
    ]


def _merge_chat_threads(blocks: list[_MarkdownBlock], text: str) -> list[_MarkdownBlock]:
    if not blocks:
        return []

    merged: list[_MarkdownBlock] = []
    index = 0
    while index < len(blocks):
        candidate = [blocks[index]]
        next_index = index + 1
        while next_index < len(blocks):
            previous = candidate[-1]
            current = blocks[next_index]
            gap = text[previous.raw_span.end : current.raw_span.start]
            if gap.strip():
                break
            if current.kind not in {"thematic_break", "blockquote", "paragraph"}:
                break
            if previous.kind not in {"thematic_break", "blockquote", "paragraph"}:
                break
            candidate.append(current)
            next_index += 1

        if len(candidate) >= 2 and any(item.kind == "blockquote" for item in candidate) and any(
            item.kind == "thematic_break" for item in candidate
        ):
            merged.append(
                _MarkdownBlock(
                    kind="chat_thread",
                    line_start=candidate[0].line_start,
                    line_end=candidate[-1].line_end,
                    raw_span=TextSpan(
                        candidate[0].raw_span.start,
                        candidate[-1].raw_span.end,
                    ),
                    heading_path=candidate[0].heading_path,
                    heading_level=None,
                )
            )
            index = next_index
            continue

        merged.append(blocks[index])
        index += 1

    return merged


@dataclass(frozen=True)
class _Segment:
    raw_span: TextSpan
    label: str
    is_empty_marker: bool = False


@dataclass(frozen=True)
class _HeadingOverride:
    heading_level: int
    heading_path: tuple[str, ...]


def _heading_override(text: str, heading_path: tuple[str, ...]) -> _HeadingOverride | None:
    match = _HEADING_TEXT_RE.fullmatch(text.strip())
    if match is None:
        return None
    heading_level = len(match.group(1))
    heading_text = match.group(2).strip()
    parent_depth = max(0, heading_level - 1)
    parent_path = heading_path[:parent_depth]
    return _HeadingOverride(
        heading_level=heading_level,
        heading_path=parent_path + (heading_text,),
    )


def _split_block_segments(block: _MarkdownBlock, analysis: MarkerAnalysis) -> list[_Segment]:
    segments: list[_Segment] = []
    start = block.raw_span.start
    end = block.raw_span.end
    labels = analysis.labels_by_char
    raw_text = analysis.parsed_file.note_text
    wrappers = analysis.wrapper_by_char

    index = start
    while index < end:
        while index < end and wrappers[index]:
            index += 1
        if index >= end:
            break
        label = labels[index]
        if label is None:
            index += 1
            continue
        segment_start = index
        while (
            index < end
            and not wrappers[index]
            and labels[index] == label
        ):
            index += 1
        if raw_text[segment_start:index].strip():
            segments.append(_Segment(raw_span=TextSpan(segment_start, index), label=label))

    for span in analysis.parsed_file.marker_spans:
        if span.content_span.start != span.content_span.end:
            continue
        if not (start <= span.content_span.start <= end):
            continue
        label = _label_for_empty_marker(span.marker_type)
        segments.append(
            _Segment(
                raw_span=span.content_span,
                label=label,
                is_empty_marker=True,
            )
        )

    segments.sort(key=lambda item: (item.raw_span.start, item.raw_span.end))
    return segments


def _chunk_segment(
    segment: _Segment,
    kind: str,
    raw_text: str,
    max_chars: int,
) -> list[TextSpan]:
    if segment.raw_span.start == segment.raw_span.end:
        return [segment.raw_span]
    if segment.label == "HIDDEN":
        return [segment.raw_span]
    if kind in {"code_fence", "code_block", "chat_thread", "heading", "thematic_break"}:
        return [segment.raw_span]
    if segment.raw_span.end - segment.raw_span.start <= max_chars:
        return [segment.raw_span]

    relative_spans = _split_prose_text(
        raw_text[segment.raw_span.start : segment.raw_span.end],
        max_chars,
    )
    return [
        TextSpan(segment.raw_span.start + item.start, segment.raw_span.start + item.end)
        for item in relative_spans
    ]


def _split_prose_text(text: str, max_chars: int) -> list[TextSpan]:
    if len(text) <= max_chars:
        return [TextSpan(0, len(text))]
    chunks: list[TextSpan] = []
    start = 0
    while start < len(text):
        remaining = len(text) - start
        if remaining <= max_chars:
            chunks.append(TextSpan(start, len(text)))
            break
        segment = text[start : start + max_chars]
        boundary = _find_boundary(segment)
        if boundary <= 0 or boundary > len(segment):
            boundary = max_chars
        chunks.append(TextSpan(start, start + boundary))
        start += boundary
    return chunks


def _split_prose_text_with_limits(
    text: str,
    *,
    target_chars: int,
    hard_cap_chars: int,
) -> list[TextSpan]:
    if len(text) <= hard_cap_chars:
        return [TextSpan(0, len(text))]

    chunks: list[TextSpan] = []
    start = 0
    while start < len(text):
        remaining = len(text) - start
        if remaining <= hard_cap_chars:
            chunks.append(TextSpan(start, len(text)))
            break
        relative_boundary = _find_progressive_boundary(
            text[start : start + hard_cap_chars],
            target_chars=target_chars,
        )
        if relative_boundary <= 0:
            relative_boundary = hard_cap_chars
        chunks.append(TextSpan(start, start + relative_boundary))
        start += relative_boundary
    return chunks


_BLANK_LINE_PATTERN = r"\n\s*\n+"
_LIST_PREFIX_PATTERN_LAST = r"(?m)^(?:\s*(?:\d+\.\s+|-\s+|\*\s+|[^:\n]{1,80}:\s+))"
_LIST_PREFIX_PATTERN_FIRST = r"(?m)^(?:\s*(?:\d+\.\s+|\d+\)\s+|-\s+|\*\s+|[^:\n]{1,80}:\s+))"
_SENTENCE_PATTERN = r"[.!?][\"')\]]?\s+(?=[A-Z])"


def _find_progressive_boundary(text: str, *, target_chars: int) -> int:
    boundary = _find_regex_boundary(text, _BLANK_LINE_PATTERN, direction="first_after", min_pos=target_chars)
    if boundary:
        return boundary
    boundary = _find_indent_boundary(text, direction="first_after", min_pos=target_chars)
    if boundary:
        return boundary
    boundary = _find_regex_boundary(text, _LIST_PREFIX_PATTERN_FIRST, direction="first_after", min_pos=target_chars, use_start=True)
    if boundary:
        return boundary
    boundary = _find_regex_boundary(text, _SENTENCE_PATTERN, direction="first_after", min_pos=target_chars)
    if boundary:
        return boundary
    boundary = _find_whitespace_boundary(text, direction="first_after", min_pos=target_chars)
    if boundary:
        return boundary
    return len(text)


def _find_boundary(text: str) -> int:
    boundary = _find_regex_boundary(text, _BLANK_LINE_PATTERN, direction="last")
    if boundary:
        return boundary
    boundary = _find_indent_boundary(text, direction="last")
    if boundary:
        return boundary
    boundary = _find_regex_boundary(text, _LIST_PREFIX_PATTERN_LAST, direction="last", use_start=True)
    if boundary:
        return boundary
    boundary = _find_regex_boundary(text, _SENTENCE_PATTERN, direction="last")
    if boundary:
        return boundary
    return _find_whitespace_boundary(text, direction="last")


def _find_regex_boundary(
    text: str,
    pattern: str,
    *,
    direction: str,
    min_pos: int = 0,
    use_start: bool = False,
) -> int:
    """Return a regex-matched boundary position.

    direction="last": position of the last match (skips position-0 starts when use_start=True).
    direction="first_after": position of the first match at or after min_pos.
    """
    pos_fn = lambda m: m.start() if use_start else m.end()
    if direction == "last":
        result = 0
        for m in re.finditer(pattern, text):
            p = pos_fn(m)
            if p > 0:
                result = p
        return result
    # first_after
    for m in re.finditer(pattern, text):
        p = pos_fn(m)
        if p >= min_pos:
            return p
    return 0


def _find_indent_boundary(text: str, *, direction: str, min_pos: int = 0) -> int:
    """Return an indentation-change boundary position."""
    boundary = 0
    previous_indent: int | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.strip():
            indent = len(line) - len(line.lstrip(" \t"))
            if previous_indent is not None and indent != previous_indent:
                if direction == "last":
                    boundary = offset
                elif offset >= min_pos:
                    return offset
            previous_indent = indent
        offset += len(line)
    return boundary


def _find_whitespace_boundary(text: str, *, direction: str, min_pos: int = 0) -> int:
    """Return a whitespace boundary position."""
    if direction == "last":
        for index in range(len(text) - 1, -1, -1):
            if text[index].isspace():
                return index + 1
        return len(text)
    # first_after
    for index in range(min_pos, len(text)):
        if text[index].isspace():
            return index + 1
    return 0


def _preview_text(text: str, max_chars: int) -> str:
    collapsed = " ".join(text.split())
    if not collapsed:
        return "(empty)"
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max_chars - 3].rstrip() + "..."


def _span_for_lines(text: str, line_start: int, line_end: int) -> TextSpan:
    starts = _line_starts(text)
    start = starts[line_start] if line_start < len(starts) else len(text)
    end = starts[line_end] if line_end < len(starts) else len(text)
    return TextSpan(start, end)


def _line_range_for_span(span: TextSpan, text: str) -> tuple[int, int]:
    starts = _line_starts(text)
    if span.start == span.end:
        line = bisect.bisect_right(starts, span.start) or 1
        return line, line
    start_line = bisect.bisect_right(starts, span.start)
    end_line = bisect.bisect_right(starts, max(span.start, span.end - 1))
    return start_line, end_line


def _line_starts(text: str) -> tuple[int, ...]:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return tuple(starts)


def _advance_past_block(tokens, index: int) -> int:
    token = tokens[index]
    close_type = token.type.replace("_open", "_close")
    depth = 1
    cursor = index + 1
    while cursor < len(tokens):
        current = tokens[cursor]
        if current.type == token.type:
            depth += 1
        elif current.type == close_type:
            depth -= 1
            if depth == 0:
                return cursor + 1
        cursor += 1
    return index + 1


def _normalize_block_kind(token_type: str) -> str:
    return {
        "paragraph_open": "paragraph",
        "blockquote_open": "blockquote",
        "bullet_list_open": "unordered_list",
        "ordered_list_open": "ordered_list",
        "fence": "code_fence",
        "code_block": "code_block",
        "hr": "thematic_break",
    }[token_type]


def _label_for_empty_marker(marker_type: str) -> str:
    if marker_type == "exclude":
        return "HIDDEN"
    if marker_type == "include_only":
        return "WRITE-EDIT_ALLOWED"
    if marker_type == "read_only":
        return "READ_ONLY-NO_EDITS"
    return "WRITE-EDIT_ALLOWED"


def _structural_label(label: str, heading_path: tuple[str, ...]) -> str:
    if heading_path and heading_path[0].strip().casefold() == _SEARCH_RESULTS_SECTION:
        return "READ_ONLY-SEARCH_RESULTS"
    return label
