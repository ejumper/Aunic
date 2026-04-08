from __future__ import annotations

import re
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Literal

from prompt_toolkit.application.current import get_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.formatted_text.utils import fragment_list_width
from prompt_toolkit.layout.controls import BufferControl, UIContent
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.selection import SelectionType
from prompt_toolkit.utils import get_cwidth

if TYPE_CHECKING:
    from prompt_toolkit.document import Document

from aunic.tui.rendering import lex_markdown_line

_CODE_FENCE_RE = re.compile(r"^\s*```")
_SEPARATOR_CELL_RE = re.compile(r"^\s*:?-{3,}:?\s*$")
_MIN_COLUMN_WIDTH = 3
_MAX_ROW_LINES = 4
_TABLE_SAFETY_MARGIN = 4
_ELLIPSIS = "..."


@dataclass(frozen=True)
class MarkdownTableCell:
    text: str
    align: Literal["left", "center", "right"]


@dataclass(frozen=True)
class MarkdownTableBlock:
    start_row: int
    end_row: int
    header: tuple[MarkdownTableCell, ...]
    rows: tuple[tuple[MarkdownTableCell, ...], ...]
    raw_lines: tuple[str, ...]
    cell_source_starts: tuple[tuple[int, ...], ...]


@dataclass(frozen=True)
class RenderTableCell:
    fragments: tuple[tuple[str, str], ...]
    plain_text: str
    align: Literal["left", "center", "right"]


@dataclass(frozen=True)
class BoxTableRenderLine:
    fragments: tuple[tuple[str, str], ...]
    logical_row: int
    cell_display_starts: tuple[int, ...] = ()


@dataclass(frozen=True)
class BoxTableRender:
    lines: tuple[BoxTableRenderLine, ...]
    vertical_fallback: bool = False


@dataclass(frozen=True)
class _DisplayRowMeta:
    source_row: int
    cell_display_starts: tuple[int, ...] = ()
    cell_source_starts: tuple[int, ...] = ()


@dataclass(frozen=True)
class _EditorPreviewLayout:
    display_lines: tuple[StyleAndTextTuples, ...]
    display_row_meta: tuple[_DisplayRowMeta, ...]
    source_row_to_display_row: tuple[int, ...]

    def source_row_for_display_row(self, display_row: int) -> int:
        if not self.display_row_meta:
            return 0
        display_row = max(0, min(display_row, len(self.display_row_meta) - 1))
        return self.display_row_meta[display_row].source_row

    def display_to_source_col(self, display_row: int, display_col: int, get_processed_line) -> int:
        if not self.display_row_meta:
            return 0
        display_row = max(0, min(display_row, len(self.display_row_meta) - 1))
        meta = self.display_row_meta[display_row]
        if meta.cell_display_starts and meta.cell_source_starts:
            target = meta.cell_source_starts[0]
            for start, source_col in zip(meta.cell_display_starts, meta.cell_source_starts):
                if display_col < start:
                    break
                target = source_col
            return target
        processed_line = get_processed_line(meta.source_row)
        return processed_line.display_to_source(display_col)


class NoteTablePreviewBufferControl(BufferControl):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._last_preview_layout: _EditorPreviewLayout | None = None

    def create_content(self, width: int, height: int, preview_search: bool = False) -> UIContent:
        buffer = self.buffer
        buffer.load_history_if_not_yet_loaded()

        search_control = self.search_buffer_control
        preview_now = preview_search or bool(
            self.preview_search()
            and search_control
            and search_control.buffer.text
            and get_app().layout.search_target_buffer_control == self
        )

        if preview_now and search_control is not None:
            ss = self.search_state
            document = buffer.document_for_search(
                type(ss)(
                    text=search_control.buffer.text,
                    direction=ss.direction,
                    ignore_case=ss.ignore_case,
                )
            )
        else:
            document = buffer.document

        get_processed_line = self._create_get_processed_line_func(document, width, height)
        self._last_get_processed_line = get_processed_line

        layout = _build_editor_preview_layout(document, width, get_processed_line)
        self._last_preview_layout = layout

        def translate_rowcol(row: int, col: int) -> Point:
            display_row = row
            if row < len(layout.source_row_to_display_row):
                display_row = layout.source_row_to_display_row[row]
            return Point(x=get_processed_line(row).source_to_display(col), y=display_row)

        def get_line(i: int) -> StyleAndTextTuples:
            if i < len(layout.display_lines):
                return list(layout.display_lines[i]) + [("", " ")]
            return [("", " ")]

        content = UIContent(
            get_line=get_line,
            line_count=len(layout.display_lines),
            cursor_position=translate_rowcol(document.cursor_position_row, document.cursor_position_col),
        )

        if get_app().layout.current_control == self:
            menu_position = self.menu_position() if self.menu_position else None
            if menu_position is not None:
                menu_row, menu_col = buffer.document.translate_index_to_position(menu_position)
                content.menu_position = translate_rowcol(menu_row, menu_col)
            elif buffer.complete_state:
                menu_row, menu_col = buffer.document.translate_index_to_position(
                    min(buffer.cursor_position, buffer.complete_state.original_document.cursor_position)
                )
                content.menu_position = translate_rowcol(menu_row, menu_col)
            else:
                content.menu_position = None

        return content

    def source_row_to_display_row(self, row: int) -> int:
        if self._last_preview_layout is None or row >= len(self._last_preview_layout.source_row_to_display_row):
            return row
        return self._last_preview_layout.source_row_to_display_row[row]

    def display_row_to_source_row(self, display_row: int) -> int:
        if self._last_preview_layout is None:
            return display_row
        return self._last_preview_layout.source_row_for_display_row(display_row)

    def display_to_source_position(self, display_row: int, display_col: int) -> tuple[int, int]:
        if self._last_preview_layout is None or self._last_get_processed_line is None:
            return display_row, display_col
        source_row = self._last_preview_layout.source_row_for_display_row(display_row)
        source_col = self._last_preview_layout.display_to_source_col(
            display_row,
            display_col,
            self._last_get_processed_line,
        )
        return source_row, source_col

    def mouse_handler(self, mouse_event: MouseEvent):
        buffer = self.buffer
        position = mouse_event.position
        if self._last_preview_layout is None or self._last_get_processed_line is None:
            return super().mouse_handler(mouse_event)

        source_row, source_col = self.display_to_source_position(position.y, position.x)
        index = buffer.document.translate_row_col_to_index(source_row, source_col)

        if get_app().layout.current_control == self:
            if mouse_event.event_type == MouseEventType.MOUSE_DOWN:
                buffer.exit_selection()
                buffer.cursor_position = index
            elif mouse_event.event_type == MouseEventType.MOUSE_MOVE and mouse_event.button != MouseButton.NONE:
                if buffer.selection_state is None and abs(buffer.cursor_position - index) > 0:
                    buffer.start_selection(selection_type=SelectionType.CHARACTERS)
                buffer.cursor_position = index
            elif mouse_event.event_type == MouseEventType.MOUSE_UP:
                if abs(buffer.cursor_position - index) > 1:
                    if buffer.selection_state is None:
                        buffer.start_selection(selection_type=SelectionType.CHARACTERS)
                    buffer.cursor_position = index

                double_click = self._last_click_timestamp and time.time() - self._last_click_timestamp < 0.3
                self._last_click_timestamp = time.time()

                if double_click:
                    start, end = buffer.document.find_boundaries_of_current_word()
                    buffer.cursor_position += start
                    buffer.start_selection(selection_type=SelectionType.CHARACTERS)
                    buffer.cursor_position += end - start
            else:
                return NotImplemented
        else:
            if self.focus_on_click() and mouse_event.event_type == MouseEventType.MOUSE_UP:
                get_app().layout.current_control = self
                buffer.cursor_position = index
            else:
                return NotImplemented

        return None


def detect_markdown_table_blocks(text: str) -> tuple[MarkdownTableBlock, ...]:
    return _detect_table_blocks(text)


def normalize_markdown_tables(
    text: str,
    *,
    touched_row_ranges: tuple[tuple[int, int], ...] | None = None,
) -> str:
    lines = list(_document_lines(text))
    if not lines:
        return text

    blocks = detect_markdown_table_blocks(text)
    if not blocks:
        return text

    for block in blocks:
        if touched_row_ranges is not None and not any(
            _rows_intersect((block.start_row, block.end_row), row_range) for row_range in touched_row_ranges
        ):
            continue
        normalized = _normalize_markdown_table_block(block)
        lines[block.start_row : block.end_row + 1] = normalized
    return "\n".join(lines)


def render_box_table(
    header: tuple[RenderTableCell, ...],
    rows: tuple[tuple[RenderTableCell, ...], ...],
    *,
    max_width: int,
    header_style: str = "",
    allow_vertical_fallback: bool = True,
) -> BoxTableRender:
    if not header:
        return BoxTableRender(())

    min_widths = []
    ideal_widths = []
    for column_index in range(len(header)):
        cells = [header[column_index], *(row[column_index] for row in rows if column_index < len(row))]
        min_widths.append(max(_cell_min_width(cell) for cell in cells))
        ideal_widths.append(max(_cell_ideal_width(cell) for cell in cells))

    num_columns = len(header)
    border_overhead = 1 + num_columns * 3
    available_width = max(max_width - border_overhead - _TABLE_SAFETY_MARGIN, num_columns * _MIN_COLUMN_WIDTH)

    total_min = sum(min_widths)
    total_ideal = sum(ideal_widths)
    needs_hard_wrap = False

    if total_ideal <= available_width:
        column_widths = ideal_widths
    elif total_min <= available_width:
        column_widths = _fit_column_widths(min_widths, ideal_widths, available_width)
    else:
        needs_hard_wrap = True
        scale_factor = available_width / total_min if total_min else 1
        column_widths = [max(int(width * scale_factor), _MIN_COLUMN_WIDTH) for width in min_widths]

    max_row_lines = 1
    for is_header, cells in [(True, header), *((False, row) for row in rows)]:
        for index, cell in enumerate(cells):
            wrapped = _wrap_fragments(list(cell.fragments), width=column_widths[index], hard=needs_hard_wrap)
            max_row_lines = max(max_row_lines, len(wrapped))
    if max_row_lines > _MAX_ROW_LINES and allow_vertical_fallback:
        return _render_table_vertical(header, rows, max_width=max_width, header_style=header_style)
    if max_row_lines > _MAX_ROW_LINES:
        return BoxTableRender((), vertical_fallback=True)

    lines: list[BoxTableRenderLine] = []
    logical_last_row = 1 + len(rows)
    lines.append(_table_border_line("top", column_widths, logical_row=0))
    lines.extend(
        _render_table_row_lines(
            header,
            column_widths,
            is_header=True,
            hard=needs_hard_wrap,
            logical_row=0,
            header_style=header_style,
        )
    )
    lines.append(_table_border_line("middle", column_widths, logical_row=1))
    for row_index, row in enumerate(rows):
        logical_row = row_index + 2
        lines.extend(
            _render_table_row_lines(
                row,
                column_widths,
                is_header=False,
                hard=needs_hard_wrap,
                logical_row=logical_row,
                header_style=header_style,
            )
        )
        if row_index < len(rows) - 1:
            lines.append(_table_border_line("middle", column_widths, logical_row=logical_row))
    lines.append(_table_border_line("bottom", column_widths, logical_row=logical_last_row))

    width_rendered = max((fragment_list_width(list(line.fragments)) for line in lines), default=0)
    if width_rendered > max_width - _TABLE_SAFETY_MARGIN:
        if allow_vertical_fallback:
            return _render_table_vertical(header, rows, max_width=max_width, header_style=header_style)
        return BoxTableRender((), vertical_fallback=True)
    return BoxTableRender(tuple(lines))


def _build_editor_preview_layout(document: Document, width: int, get_processed_line) -> _EditorPreviewLayout:
    text = document.text
    lines = _document_lines(text)
    blocks = detect_markdown_table_blocks(text)
    active_rows = _active_table_rows(document)

    block_by_start = {block.start_row: block for block in blocks}
    display_lines: list[StyleAndTextTuples] = []
    display_row_meta: list[_DisplayRowMeta] = []
    source_row_to_display_row = [0] * max(1, len(lines))

    row = 0
    while row < len(lines):
        block = block_by_start.get(row)
        if block is not None:
            if active_rows is not None and _rows_intersect(active_rows, (block.start_row, block.end_row)):
                for source_row in range(block.start_row, block.end_row + 1):
                    source_row_to_display_row[source_row] = len(display_lines)
                    display_lines.append(tuple(get_processed_line(source_row).fragments))
                    display_row_meta.append(_DisplayRowMeta(source_row=source_row))
                row = block.end_row + 1
                continue

            preview = _render_box_preview(block, width)
            if preview is not None:
                for display_row, meta in enumerate(preview.display_row_meta, start=len(display_lines)):
                    source_row = meta.source_row
                    if source_row < len(source_row_to_display_row) and source_row_to_display_row[source_row] == 0:
                        source_row_to_display_row[source_row] = display_row
                display_lines.extend(preview.display_lines)
                display_row_meta.extend(preview.display_row_meta)
                row = block.end_row + 1
                continue

        source_row_to_display_row[row] = len(display_lines)
        display_lines.append(tuple(get_processed_line(row).fragments))
        display_row_meta.append(_DisplayRowMeta(source_row=row))
        row += 1

    if not display_lines:
        display_lines.append((("", ""),))
        display_row_meta.append(_DisplayRowMeta(source_row=0))
        source_row_to_display_row[0] = 0

    return _EditorPreviewLayout(
        display_lines=tuple(display_lines),
        display_row_meta=tuple(display_row_meta),
        source_row_to_display_row=tuple(source_row_to_display_row),
    )


def _active_table_rows(document: Document) -> tuple[int, int] | None:
    if document.selection is None:
        row = document.cursor_position_row
        return (row, row)

    start, end = document.selection_range()
    lower = min(start, end)
    upper = max(start, end)
    if upper > lower:
        upper -= 1
    start_row, _ = document.translate_index_to_position(lower)
    end_row, _ = document.translate_index_to_position(max(lower, upper))
    return (start_row, end_row)


def _rows_intersect(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] <= right[1] and right[0] <= left[1]


def _document_lines(text: str) -> tuple[str, ...]:
    return tuple(text.split("\n"))


@lru_cache(maxsize=32)
def _detect_table_blocks(text: str) -> tuple[MarkdownTableBlock, ...]:
    lines = _document_lines(text)
    blocks: list[MarkdownTableBlock] = []
    in_code_block = False
    row = 0

    while row < len(lines):
        line = lines[row]
        if _CODE_FENCE_RE.match(line):
            in_code_block = not in_code_block
            row += 1
            continue
        if in_code_block:
            row += 1
            continue

        header_cells = _split_table_cells(line)
        if header_cells is None or row + 1 >= len(lines):
            row += 1
            continue

        separator_cells = _split_table_cells(lines[row + 1])
        if (
            separator_cells is None
            or len(header_cells) != len(separator_cells)
            or len(header_cells) < 2
            or not all(_SEPARATOR_CELL_RE.match(cell.text) for cell in separator_cells)
        ):
            row += 1
            continue

        aligns = tuple(_alignment_from_separator(cell.text) for cell in separator_cells)
        body_rows: list[tuple[MarkdownTableCell, ...]] = []
        raw_lines = [line, lines[row + 1]]
        cell_starts = [
            tuple(cell.content_start for cell in header_cells),
            tuple(cell.content_start for cell in separator_cells),
        ]
        scan = row + 2
        while scan < len(lines):
            body_cells = _split_table_cells(lines[scan])
            if body_cells is None or len(body_cells) != len(header_cells):
                break
            body_rows.append(
                tuple(MarkdownTableCell(text=cell.text, align=aligns[index]) for index, cell in enumerate(body_cells))
            )
            raw_lines.append(lines[scan])
            cell_starts.append(tuple(cell.content_start for cell in body_cells))
            scan += 1

        if not body_rows:
            row += 1
            continue

        blocks.append(
            MarkdownTableBlock(
                start_row=row,
                end_row=scan - 1,
                header=tuple(
                    MarkdownTableCell(text=cell.text, align=aligns[index]) for index, cell in enumerate(header_cells)
                ),
                rows=tuple(body_rows),
                raw_lines=tuple(raw_lines),
                cell_source_starts=tuple(cell_starts),
            )
        )
        row = scan

    return tuple(blocks)


@dataclass(frozen=True)
class _SplitCell:
    text: str
    content_start: int


def _split_table_cells(line: str) -> tuple[_SplitCell, ...] | None:
    if "|" not in line:
        return None

    parts: list[tuple[int, int]] = []
    segment_start = 0
    escaped = False
    for index, char in enumerate(line):
        if char == "\\" and not escaped:
            escaped = True
            continue
        if char == "|" and not escaped:
            parts.append((segment_start, index))
            segment_start = index + 1
        escaped = False
    parts.append((segment_start, len(line)))

    if line.lstrip().startswith("|"):
        parts = parts[1:]
    if line.rstrip().endswith("|"):
        parts = parts[:-1]

    if len(parts) < 2:
        return None

    cells: list[_SplitCell] = []
    for start, end in parts:
        segment = line[start:end]
        leading = len(segment) - len(segment.lstrip(" "))
        cells.append(
            _SplitCell(
                text=segment.strip().replace(r"\|", "|"),
                content_start=start + leading,
            )
        )
    return tuple(cells)


def _alignment_from_separator(cell: str) -> Literal["left", "center", "right"]:
    stripped = cell.strip()
    if stripped.startswith(":") and stripped.endswith(":"):
        return "center"
    if stripped.endswith(":"):
        return "right"
    return "left"


def _normalize_markdown_table_block(block: MarkdownTableBlock) -> list[str]:
    column_count = len(block.header)
    widths = [0] * column_count
    all_rows = [block.header, *block.rows]
    for row in all_rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], _plain_text_width(cell.text))

    rendered = [
        _render_markdown_row(block.header, widths),
        _render_markdown_separator(tuple(cell.align for cell in block.header), widths),
    ]
    rendered.extend(_render_markdown_row(row, widths) for row in block.rows)
    return rendered


def _render_markdown_row(cells: tuple[MarkdownTableCell, ...], widths: list[int]) -> str:
    parts = ["|"]
    for index, cell in enumerate(cells):
        padded = _align_text(_escape_markdown_cell(cell.text), widths[index], cell.align)
        parts.append(f" {padded} |")
    return "".join(parts)


def _render_markdown_separator(
    aligns: tuple[Literal["left", "center", "right"], ...],
    widths: list[int],
) -> str:
    parts = ["|"]
    for align, width in zip(aligns, widths):
        count = max(3, width)
        if align == "center":
            text = ":" + "-" * max(1, count - 2) + ":"
        elif align == "right":
            text = "-" * max(1, count - 1) + ":"
        else:
            text = ":" + "-" * max(1, count - 1)
        parts.append(f" {text} |")
    return "".join(parts)


def _escape_markdown_cell(text: str) -> str:
    return text.replace("|", r"\|")


@lru_cache(maxsize=128)
def _render_box_preview(block: MarkdownTableBlock, max_width: int) -> _EditorPreviewLayout | None:
    header = tuple(
        RenderTableCell(
            fragments=tuple(lex_markdown_line(cell.text, hide_emphasis_markers=True)),
            plain_text=cell.text,
            align=cell.align,
        )
        for cell in block.header
    )
    rows = tuple(
        tuple(
            RenderTableCell(
                fragments=tuple(lex_markdown_line(cell.text, hide_emphasis_markers=True)),
                plain_text=cell.text,
                align=cell.align,
            )
            for cell in row
        )
        for row in block.rows
    )
    rendered = render_box_table(
        header,
        rows,
        max_width=max(1, max_width),
        header_style="class:md.bold",
        allow_vertical_fallback=False,
    )
    if rendered.vertical_fallback or not rendered.lines:
        return None

    display_lines: list[StyleAndTextTuples] = []
    display_row_meta: list[_DisplayRowMeta] = []
    source_row_to_display_row = [0] * (block.end_row - block.start_row + 1)
    first_seen: dict[int, int] = {}

    for index, line in enumerate(rendered.lines):
        source_row = block.start_row + min(line.logical_row, block.end_row - block.start_row)
        display_lines.append(line.fragments)
        meta = _DisplayRowMeta(
            source_row=source_row,
            cell_display_starts=line.cell_display_starts,
            cell_source_starts=block.cell_source_starts[min(line.logical_row, len(block.cell_source_starts) - 1)]
            if line.cell_display_starts
            else (),
        )
        display_row_meta.append(meta)
        first_seen.setdefault(line.logical_row, index)

    for logical_row in range(block.end_row - block.start_row + 1):
        display_row = first_seen.get(logical_row, 0)
        source_row_to_display_row[logical_row] = display_row

    return _EditorPreviewLayout(
        display_lines=tuple(display_lines),
        display_row_meta=tuple(display_row_meta),
        source_row_to_display_row=tuple(source_row_to_display_row),
    )


def _fit_column_widths(
    min_widths: list[int],
    ideal_widths: list[int],
    available_width: int,
) -> list[int]:
    extra_space = available_width - sum(min_widths)
    overflows = [ideal - minimum for ideal, minimum in zip(ideal_widths, min_widths)]
    total_overflow = sum(overflows)
    widths = list(min_widths)
    if total_overflow <= 0:
        return widths

    remainders: list[tuple[float, int]] = []
    used = 0
    for index, overflow in enumerate(overflows):
        if overflow <= 0:
            continue
        proportional = overflow * extra_space / total_overflow
        whole = int(proportional)
        widths[index] += whole
        used += whole
        remainders.append((proportional - whole, index))

    for _, index in sorted(remainders, reverse=True):
        if used >= extra_space:
            break
        widths[index] += 1
        used += 1
    return widths


def _render_table_row_lines(
    cells: tuple[RenderTableCell, ...],
    column_widths: list[int],
    *,
    is_header: bool,
    hard: bool,
    logical_row: int,
    header_style: str,
) -> list[BoxTableRenderLine]:
    wrapped_cells = [
        _wrap_fragments(list(cell.fragments), width=column_widths[index], hard=hard)
        for index, cell in enumerate(cells)
    ]
    max_lines = max((len(lines) for lines in wrapped_cells), default=1)
    vertical_offsets = [(max_lines - len(lines)) // 2 for lines in wrapped_cells]
    rendered: list[BoxTableRenderLine] = []

    for line_index in range(max_lines):
        line: StyleAndTextTuples = [("", "│")]
        cell_display_starts: list[int] = []
        current_width = 1
        for column_index, cell in enumerate(cells):
            lines_for_cell = wrapped_cells[column_index]
            offset = vertical_offsets[column_index]
            content_index = line_index - offset
            cell_line = lines_for_cell[content_index] if 0 <= content_index < len(lines_for_cell) else []
            align = "center" if is_header else cell.align
            padded = _pad_fragments(cell_line, column_widths[column_index], align=align)
            line.append(("", " "))
            current_width += 1
            cell_display_starts.append(current_width)
            if is_header:
                line.extend((_combine_styles(header_style, style), text) for style, text in padded)
            else:
                line.extend(padded)
            current_width += fragment_list_width(padded)
            line.append(("", " │"))
            current_width += 2
        rendered.append(
            BoxTableRenderLine(
                fragments=tuple(line),
                logical_row=logical_row,
                cell_display_starts=tuple(cell_display_starts),
            )
        )
    return rendered


def _table_border_line(
    kind: Literal["top", "middle", "bottom"],
    column_widths: list[int],
    *,
    logical_row: int,
) -> BoxTableRenderLine:
    left, mid, cross, right = {
        "top": ("┌", "─", "┬", "┐"),
        "middle": ("├", "─", "┼", "┤"),
        "bottom": ("└", "─", "┴", "┘"),
    }[kind]
    text = left
    for index, width in enumerate(column_widths):
        text += mid * (width + 2)
        text += cross if index < len(column_widths) - 1 else right
    return BoxTableRenderLine((( "", text),), logical_row=logical_row)


def _render_table_vertical(
    header: tuple[RenderTableCell, ...],
    rows: tuple[tuple[RenderTableCell, ...], ...],
    *,
    max_width: int,
    header_style: str,
) -> BoxTableRender:
    headers = [cell.plain_text or f"Column {index + 1}" for index, cell in enumerate(header)]
    separator = BoxTableRenderLine((("", "─" * min(max_width, 40)),), logical_row=0)
    lines: list[BoxTableRenderLine] = []
    wrap_indent = "  "

    for row_index, row in enumerate(rows):
        logical_row = row_index + 2
        if row_index > 0:
            lines.append(separator)
        for column_index, cell in enumerate(row):
            label = headers[column_index]
            cell_lines = _wrap_fragments(
                list(cell.fragments),
                width=max(10, max_width - _plain_text_width(f"{label}: ")),
                first_prefix=f"{label}: ",
                continuation_prefix=wrap_indent,
            )
            first = True
            for cell_line in cell_lines:
                if first and cell_line:
                    first_line = list(cell_line)
                    first_line[0] = (_combine_styles(header_style, first_line[0][0]), first_line[0][1])
                    lines.append(BoxTableRenderLine(tuple(first_line), logical_row=logical_row))
                    first = False
                else:
                    lines.append(BoxTableRenderLine(tuple(cell_line), logical_row=logical_row))
    return BoxTableRender(tuple(lines), vertical_fallback=True)


def _wrap_fragments(
    fragments: StyleAndTextTuples,
    *,
    width: int,
    first_prefix: str = "",
    continuation_prefix: str = "",
    hard: bool = True,
) -> list[StyleAndTextTuples]:
    tokens = _fragment_tokens(fragments)
    first_prefix_fragments: StyleAndTextTuples = [("", first_prefix)] if first_prefix else []
    continuation_prefix_fragments: StyleAndTextTuples = [("", continuation_prefix)] if continuation_prefix else []
    prefix_width = fragment_list_width(first_prefix_fragments)
    continuation_width = fragment_list_width(continuation_prefix_fragments)
    lines: list[StyleAndTextTuples] = [list(first_prefix_fragments)]
    current_width = prefix_width
    line_has_content = False
    pending_space = False

    def new_line() -> None:
        nonlocal current_width, line_has_content, pending_space
        lines.append(list(continuation_prefix_fragments))
        current_width = continuation_width
        line_has_content = False
        pending_space = False

    for token in tokens:
        if token.kind == "newline":
            new_line()
            continue
        if token.kind == "space":
            pending_space = pending_space or line_has_content
            continue

        while True:
            leading_space_width = 1 if pending_space and line_has_content else 0
            available_width = max(1, width - current_width - leading_space_width)
            token_width = fragment_list_width(token.fragments)

            if token_width <= available_width:
                if leading_space_width:
                    lines[-1].append(("", " "))
                    current_width += 1
                lines[-1].extend(token.fragments)
                current_width += token_width
                line_has_content = True
                pending_space = False
                break

            if line_has_content:
                new_line()
                continue

            if not hard:
                lines[-1].extend(_truncate_fragments(token.fragments, available_width))
                current_width += min(token_width, available_width)
                line_has_content = True
                pending_space = False
                break

            head, tail = _split_fragments_to_width(token.fragments, max(1, available_width))
            if head:
                lines[-1].extend(head)
                current_width += fragment_list_width(head)
                line_has_content = True
            token = _FragmentToken(kind="word", fragments=tail)
            if not tail:
                pending_space = False
                break
            new_line()

    return lines or [[]]


@dataclass(frozen=True)
class _FragmentToken:
    kind: Literal["word", "space", "newline"]
    fragments: StyleAndTextTuples


def _fragment_tokens(fragments: StyleAndTextTuples) -> list[_FragmentToken]:
    tokens: list[_FragmentToken] = []
    current_kind: Literal["word", "space"] | None = None
    current_fragments: StyleAndTextTuples = []

    def flush() -> None:
        nonlocal current_kind, current_fragments
        if current_kind is not None and current_fragments:
            tokens.append(_FragmentToken(kind=current_kind, fragments=current_fragments))
        current_kind = None
        current_fragments = []

    for style, text in fragments:
        buffer = ""
        buffer_kind: Literal["word", "space"] | None = None
        for char in text:
            if char == "\n":
                if buffer:
                    if current_kind != buffer_kind:
                        flush()
                        current_kind = buffer_kind
                    current_fragments.append((style, buffer))
                    buffer = ""
                    buffer_kind = None
                flush()
                tokens.append(_FragmentToken(kind="newline", fragments=[]))
                continue

            kind: Literal["word", "space"] = "space" if char.isspace() else "word"
            if buffer_kind is None:
                buffer_kind = kind
            if kind != buffer_kind:
                if current_kind != buffer_kind:
                    flush()
                    current_kind = buffer_kind
                current_fragments.append((style, buffer))
                buffer = char
                buffer_kind = kind
            else:
                buffer += char

        if buffer:
            if current_kind != buffer_kind:
                flush()
                current_kind = buffer_kind
            current_fragments.append((style, buffer))

    flush()
    return tokens


def _split_fragments_to_width(
    fragments: StyleAndTextTuples,
    width: int,
) -> tuple[StyleAndTextTuples, StyleAndTextTuples]:
    if width <= 0:
        return [], list(fragments)

    head: StyleAndTextTuples = []
    tail: StyleAndTextTuples = []
    used = 0
    split = False

    for style, text in fragments:
        if split:
            tail.append((style, text))
            continue
        current = ""
        remainder = ""
        for char in text:
            char_width = max(get_cwidth(char), 0)
            if used + char_width <= width:
                current += char
                used += char_width
            else:
                remainder += char
                split = True
        if current:
            head.append((style, current))
        if remainder:
            tail.append((style, remainder))

    return head, tail


def _truncate_fragments(fragments: StyleAndTextTuples, width: int) -> StyleAndTextTuples:
    head, _ = _split_fragments_to_width(fragments, width)
    return head


def _pad_fragments(
    fragments: StyleAndTextTuples,
    width: int,
    *,
    align: Literal["left", "center", "right"] = "left",
) -> StyleAndTextTuples:
    current_width = fragment_list_width(fragments)
    extra = max(0, width - current_width)
    if align == "right":
        left = extra
        right = 0
    elif align == "center":
        left = extra // 2
        right = extra - left
    else:
        left = 0
        right = extra

    padded: StyleAndTextTuples = []
    if left:
        padded.append(("", " " * left))
    padded.extend(fragments)
    if right:
        padded.append(("", " " * right))
    return padded


def _cell_min_width(cell) -> int:
    text = cell.plain_text if isinstance(cell, RenderTableCell) else cell.text
    words = [word for word in re.split(r"\s+", text) if word]
    if not words:
        return _MIN_COLUMN_WIDTH
    return max(max(_plain_text_width(word) for word in words), _MIN_COLUMN_WIDTH)


def _cell_ideal_width(cell) -> int:
    text = cell.plain_text if isinstance(cell, RenderTableCell) else cell.text
    return max(_plain_text_width(text), _MIN_COLUMN_WIDTH)


def _plain_text_width(text: str) -> int:
    return sum(max(get_cwidth(char), 0) for char in text)


def _truncate_text(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if _plain_text_width(text) <= width:
        return text
    ellipsis_width = _plain_text_width(_ELLIPSIS)
    if ellipsis_width >= width:
        return "." * min(width, 3)
    remaining = width - ellipsis_width
    pieces: list[str] = []
    used = 0
    for char in text:
        char_width = max(get_cwidth(char), 0)
        if used + char_width > remaining:
            break
        pieces.append(char)
        used += char_width
    return "".join(pieces).rstrip() + _ELLIPSIS


def _align_text(text: str, width: int, align: Literal["left", "center", "right"]) -> str:
    text_width = _plain_text_width(text)
    if text_width >= width:
        return text
    pad = width - text_width
    if align == "right":
        return " " * pad + text
    if align == "center":
        left = pad // 2
        right = pad - left
        return " " * left + text + " " * right
    return text + " " * pad


def _combine_styles(*styles: str) -> str:
    return " ".join(style for style in styles if style)
