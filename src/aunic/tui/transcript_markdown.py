from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from markdown_it import MarkdownIt
from markdown_it.token import Token
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.formatted_text.utils import fragment_list_width
from prompt_toolkit.utils import get_cwidth

from aunic.tui.note_tables import RenderTableCell, render_box_table

_MARKDOWN = MarkdownIt("commonmark").enable("table")
_MIN_COLUMN_WIDTH = 3
_MAX_ROW_LINES = 4
_TABLE_SAFETY_MARGIN = 4


@dataclass(frozen=True)
class _TableCell:
    fragments: tuple[tuple[str, str], ...]
    plain_text: str
    align: Literal["left", "center", "right"]


@lru_cache(maxsize=256)
def _parse_tokens(content: str) -> tuple[Token, ...]:
    return tuple(_MARKDOWN.parse(content))


def render_chat_markdown(
    content: str,
    *,
    max_width: int,
) -> list[StyleAndTextTuples]:
    tokens = _parse_tokens(content)
    lines, _ = _render_blocks(tokens, 0, len(tokens), max_width=max(1, max_width), list_depth=0)
    return lines or [[]]


def rendered_lines_width(lines: list[StyleAndTextTuples]) -> int:
    return max((fragment_list_width(line) for line in lines), default=0)


def _render_blocks(
    tokens: tuple[Token, ...],
    start: int,
    end: int,
    *,
    max_width: int,
    list_depth: int,
) -> tuple[list[StyleAndTextTuples], int]:
    lines: list[StyleAndTextTuples] = []
    index = start
    while index < end:
        token = tokens[index]
        token_type = token.type

        if token_type in {
            "paragraph_close",
            "heading_close",
            "list_item_close",
            "bullet_list_close",
            "ordered_list_close",
            "blockquote_close",
            "table_close",
        }:
            break

        block_lines: list[StyleAndTextTuples] = []
        if token_type == "heading_open":
            inline = tokens[index + 1] if index + 1 < end else None
            block_lines = _wrap_inline_token(
                inline,
                max_width=max_width,
                base_style="class:transcript.chat.heading",
            )
            index += 3
        elif token_type == "paragraph_open":
            inline = tokens[index + 1] if index + 1 < end else None
            block_lines = _wrap_inline_token(
                inline,
                max_width=max_width,
            )
            index += 3
        elif token_type in {"bullet_list_open", "ordered_list_open"}:
            block_lines, index = _render_list(
                tokens,
                index,
                end,
                max_width=max_width,
                list_depth=list_depth,
            )
        elif token_type == "blockquote_open":
            block_lines, index = _render_blockquote(
                tokens,
                index,
                end,
                max_width=max_width,
                list_depth=list_depth,
            )
        elif token_type == "table_open":
            table, index = _parse_table(tokens, index, end)
            block_lines = _render_table(table, max_width=max_width)
        elif token_type in {"fence", "code_block"}:
            block_lines = _render_code_block(token, max_width=max_width)
            index += 1
        elif token_type == "hr":
            block_lines = [[("class:transcript.chat.heading", "─" * max(3, max_width))]]
            index += 1
        elif token_type == "inline":
            block_lines = _wrap_inline_token(
                token,
                max_width=max_width,
            )
            index += 1
        else:
            index += 1
            continue

        if block_lines:
            if lines and lines[-1] != []:
                lines.append([])
            lines.extend(block_lines)

    return lines, index


def _render_list(
    tokens: tuple[Token, ...],
    start: int,
    end: int,
    *,
    max_width: int,
    list_depth: int,
) -> tuple[list[StyleAndTextTuples], int]:
    open_token = tokens[start]
    close_type = "bullet_list_close" if open_token.type == "bullet_list_open" else "ordered_list_close"
    ordered = open_token.type == "ordered_list_open"
    next_number = int(open_token.attrs.get("start", open_token.info or 1)) if ordered else 0
    index = start + 1
    item_index = 0
    lines: list[StyleAndTextTuples] = []

    while index < end and tokens[index].type != close_type:
        if tokens[index].type != "list_item_open":
            index += 1
            continue
        item_lines, index = _render_list_item(
            tokens,
            index,
            end,
            max_width=max_width,
            list_depth=list_depth,
            ordered=ordered,
            number=next_number + item_index,
        )
        lines.extend(item_lines)
        item_index += 1

    return lines, min(index + 1, end)


def _render_list_item(
    tokens: tuple[Token, ...],
    start: int,
    end: int,
    *,
    max_width: int,
    list_depth: int,
    ordered: bool,
    number: int,
) -> tuple[list[StyleAndTextTuples], int]:
    prefix = f"{' ' * (1 + list_depth * 2)}{f'{number}.' if ordered else '-'} "
    continuation_prefix = " " * len(prefix)
    index = start + 1
    lines: list[StyleAndTextTuples] = []
    first_block = True

    while index < end and tokens[index].type != "list_item_close":
        token = tokens[index]
        if token.type == "paragraph_open":
            inline = tokens[index + 1] if index + 1 < end else None
            block_lines = _wrap_inline_token(
                inline,
                max_width=max_width,
                first_prefix=prefix if first_block else continuation_prefix,
                continuation_prefix=continuation_prefix,
            )
            index += 3
            first_block = False
        elif token.type in {"bullet_list_open", "ordered_list_open"}:
            block_lines, index = _render_list(
                tokens,
                index,
                end,
                max_width=max_width,
                list_depth=list_depth + 1,
            )
            first_block = False
        else:
            index += 1
            continue

        if block_lines:
            if lines and lines[-1] != []:
                lines.append([])
            lines.extend(block_lines)

    return lines, min(index + 1, end)


def _render_blockquote(
    tokens: tuple[Token, ...],
    start: int,
    end: int,
    *,
    max_width: int,
    list_depth: int,
) -> tuple[list[StyleAndTextTuples], int]:
    inner_lines, index = _render_blocks(
        tokens,
        start + 1,
        end,
        max_width=max(1, max_width - 2),
        list_depth=list_depth,
    )
    prefixed: list[StyleAndTextTuples] = []
    for line in inner_lines:
        if line:
            prefixed.append(
                [("class:transcript.chat.quote", "│ ")] + [("class:transcript.chat.italic", text) if not style else (style, text) for style, text in line]
            )
        else:
            prefixed.append([("class:transcript.chat.quote", "│")])
    if index < end and tokens[index].type == "blockquote_close":
        index += 1
    return prefixed, index


def _wrap_inline_token(
    token: Token | None,
    *,
    max_width: int,
    base_style: str = "",
    first_prefix: str = "",
    continuation_prefix: str = "",
) -> list[StyleAndTextTuples]:
    inline_fragments = _render_inline_fragments(token.children or [], base_style=base_style)
    return _wrap_fragments(
        inline_fragments,
        width=max_width,
        first_prefix=first_prefix,
        continuation_prefix=continuation_prefix,
    )


def _render_inline_fragments(children: list[Token], *, base_style: str = "") -> StyleAndTextTuples:
    fragments: StyleAndTextTuples = []
    style_stack: list[str] = [base_style] if base_style else []

    for child in children:
        token_type = child.type
        if token_type == "text":
            _append_fragment(fragments, _join_styles(*style_stack), child.content)
        elif token_type == "softbreak":
            _append_fragment(fragments, "", "\n")
        elif token_type == "hardbreak":
            _append_fragment(fragments, "", "\n")
        elif token_type == "code_inline":
            _append_fragment(
                fragments,
                _join_styles(*style_stack, "class:transcript.chat.code"),
                child.content,
            )
        elif token_type == "strong_open":
            style_stack.append("class:transcript.chat.bold")
        elif token_type == "strong_close":
            _pop_style(style_stack, "class:transcript.chat.bold")
        elif token_type == "em_open":
            style_stack.append("class:transcript.chat.italic")
        elif token_type == "em_close":
            _pop_style(style_stack, "class:transcript.chat.italic")
        elif token_type == "link_open":
            style_stack.append("class:transcript.chat.link")
        elif token_type == "link_close":
            _pop_style(style_stack, "class:transcript.chat.link")
        elif token_type == "image":
            alt_text = child.content or child.attrs.get("src", "")
            _append_fragment(fragments, _join_styles(*style_stack), alt_text)
        elif child.children:
            fragments.extend(_render_inline_fragments(child.children, base_style=_join_styles(*style_stack)))
        elif child.content:
            _append_fragment(fragments, _join_styles(*style_stack), child.content)

    return fragments


def _render_code_block(token: Token, *, max_width: int) -> list[StyleAndTextTuples]:
    raw_lines = token.content.rstrip("\n").splitlines() or [token.content]
    lines: list[StyleAndTextTuples] = []
    for raw_line in raw_lines or [""]:
        lines.extend(
            _wrap_fragments(
                [("class:transcript.chat.code", raw_line)],
                width=max_width,
            )
        )
    return lines or [[("class:transcript.chat.code", "")]]


def _parse_table(
    tokens: tuple[Token, ...],
    start: int,
    end: int,
) -> tuple[tuple[tuple[_TableCell, ...], tuple[tuple[_TableCell, ...], ...]], int]:
    header: list[_TableCell] = []
    rows: list[tuple[_TableCell, ...]] = []
    current_row: list[_TableCell] = []
    current_align: Literal["left", "center", "right"] = "left"
    in_header = True
    index = start + 1

    while index < end and tokens[index].type != "table_close":
        token = tokens[index]
        if token.type == "tbody_open":
            in_header = False
        elif token.type == "tr_open":
            current_row = []
        elif token.type in {"th_open", "td_open"}:
            current_align = _alignment_from_attrs(token.attrs)
        elif token.type == "inline":
            fragments = _render_inline_fragments(token.children or [])
            current_row.append(
                _TableCell(
                    fragments=tuple((style, text) for style, text in fragments),
                    plain_text="".join(text for _, text in fragments).replace("\n", " ").strip(),
                    align=current_align,
                )
            )
        elif token.type == "tr_close":
            row_tuple = tuple(current_row)
            if row_tuple:
                if in_header and not header:
                    header = list(row_tuple)
                else:
                    rows.append(row_tuple)
        index += 1

    return (tuple(header), tuple(rows)), min(index + 1, end)


def _render_table(
    table: tuple[tuple[_TableCell, ...], tuple[tuple[_TableCell, ...], ...]],
    *,
    max_width: int,
) -> list[StyleAndTextTuples]:
    header, rows = table
    if not header:
        return []
    shared_header = tuple(
        RenderTableCell(
            fragments=cell.fragments,
            plain_text=cell.plain_text,
            align=cell.align,
        )
        for cell in header
    )
    shared_rows = tuple(
        tuple(
            RenderTableCell(
                fragments=cell.fragments,
                plain_text=cell.plain_text,
                align=cell.align,
            )
            for cell in row
        )
        for row in rows
    )
    rendered = render_box_table(
        shared_header,
        shared_rows,
        max_width=max_width,
        header_style="class:transcript.chat.bold",
        allow_vertical_fallback=True,
    )
    return [list(line.fragments) for line in rendered.lines]


def _render_table_row_lines(
    cells: tuple[_TableCell, ...],
    column_widths: list[int],
    *,
    is_header: bool,
    hard: bool,
) -> list[StyleAndTextTuples]:
    wrapped_cells = [
        _wrap_fragments(list(cell.fragments), width=column_widths[index], hard=hard)
        for index, cell in enumerate(cells)
    ]
    max_lines = max((len(lines) for lines in wrapped_cells), default=1)
    vertical_offsets = [(max_lines - len(lines)) // 2 for lines in wrapped_cells]
    rendered: list[StyleAndTextTuples] = []

    for line_index in range(max_lines):
        line: StyleAndTextTuples = [("", "│")]
        for column_index, cell in enumerate(cells):
            lines_for_cell = wrapped_cells[column_index]
            offset = vertical_offsets[column_index]
            content_index = line_index - offset
            cell_line = lines_for_cell[content_index] if 0 <= content_index < len(lines_for_cell) else []
            align = "center" if is_header else cell.align
            padded = _pad_fragments(
                cell_line,
                column_widths[column_index],
                align=align,
            )
            line.append(("", " "))
            if is_header:
                line.extend(
                    (
                        _join_styles("class:transcript.chat.bold", style),
                        text,
                    )
                    for style, text in padded
                )
            else:
                line.extend(padded)
            line.append(("", " │"))
        rendered.append(line)
    return rendered


def _table_border_line(
    kind: Literal["top", "middle", "bottom"],
    column_widths: list[int],
) -> StyleAndTextTuples:
    left, mid, cross, right = {
        "top": ("┌", "─", "┬", "┐"),
        "middle": ("├", "─", "┼", "┤"),
        "bottom": ("└", "─", "┴", "┘"),
    }[kind]
    text = left
    for index, width in enumerate(column_widths):
        text += mid * (width + 2)
        text += cross if index < len(column_widths) - 1 else right
    return [("", text)]


def _render_table_vertical(
    header: tuple[_TableCell, ...],
    rows: tuple[tuple[_TableCell, ...], ...],
    *,
    max_width: int,
) -> list[StyleAndTextTuples]:
    headers = [cell.plain_text or f"Column {index + 1}" for index, cell in enumerate(header)]
    separator = [("", "─" * min(max_width, 40))]
    lines: list[StyleAndTextTuples] = []
    wrap_indent = "  "

    for row_index, row in enumerate(rows):
        if row_index > 0:
            lines.append(separator)
        for column_index, cell in enumerate(row):
            label = headers[column_index]
            label_prefix = [("class:transcript.chat.bold", f"{label}: ")]
            label_width = _plain_text_width(f"{label}: ")
            cell_lines = _wrap_fragments(
                list(cell.fragments),
                width=max(10, max_width - label_width),
                first_prefix=f"{label}: ",
                continuation_prefix=wrap_indent,
            )
            if cell_lines:
                first_line = cell_lines[0]
                first_line[0] = ("class:transcript.chat.bold", first_line[0][1])
            lines.extend(cell_lines)
    return lines


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


def _cell_min_width(cell: _TableCell) -> int:
    words = [word for word in re.split(r"\s+", cell.plain_text) if word]
    if not words:
        return _MIN_COLUMN_WIDTH
    return max(max(_plain_text_width(word) for word in words), _MIN_COLUMN_WIDTH)


def _cell_ideal_width(cell: _TableCell) -> int:
    return max(_plain_text_width(cell.plain_text), _MIN_COLUMN_WIDTH)


def _plain_text_width(text: str) -> int:
    return sum(max(get_cwidth(char), 0) for char in text)


def _alignment_from_attrs(attrs: dict[str, str] | None) -> Literal["left", "center", "right"]:
    if not attrs:
        return "left"
    style = attrs.get("style", "")
    if "text-align:center" in style:
        return "center"
    if "text-align:right" in style:
        return "right"
    return "left"


def _append_fragment(fragments: StyleAndTextTuples, style: str, text: str) -> None:
    if not text:
        return
    if fragments and fragments[-1][0] == style:
        previous_style, previous_text = fragments[-1]
        fragments[-1] = (previous_style, previous_text + text)
        return
    fragments.append((style, text))


def _join_styles(*styles: str) -> str:
    return " ".join(style for style in styles if style)


def _pop_style(stack: list[str], style: str) -> None:
    for index in range(len(stack) - 1, -1, -1):
        if stack[index] == style:
            stack.pop(index)
            return
