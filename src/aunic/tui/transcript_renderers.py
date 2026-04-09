from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.formatted_text.utils import fragment_list_width
from prompt_toolkit.mouse_events import MouseEventType

from aunic.domain import TranscriptRow
from aunic.research.search import canonicalize_url
from aunic.transcript.flattening import flatten_tool_result_for_provider
from aunic.tui.transcript_markdown import render_chat_markdown, rendered_lines_width
from aunic.tui.types import TranscriptFilter, TranscriptViewState


@dataclass(frozen=True)
class TranscriptRenderContext:
    width: int
    tool_call_index: dict[str, TranscriptRow]
    expanded_rows: set[int]
    cached_fetch_urls: set[str]
    selected_row_number: int | None
    delete_row: Callable[[int], None]
    delete_search_result: Callable[[int, int], None]
    toggle_expand: Callable[[int], None]
    set_filter: Callable[[TranscriptFilter], None]
    toggle_sort: Callable[[], None]
    toggle_open: Callable[[], None]
    toggle_maximize: Callable[[], None]
    open_url: Callable[[str], None]
    copy_text: Callable[[str], None]
    copy_cached_fetch: Callable[[str], None]
    # Keyboard cursor state — None means no keyboard cursor active
    focused_col: str | None = None          # "delete" or "action"
    toolbar_focused_index: int | None = None  # 0-5 for toolbar buttons


@dataclass(frozen=True)
class SearchRowLayout:
    count_width: int
    domain_width: int
    title_width: int
    query_width: int
    header_label_width: int
    result_indent: int
    count_prefix_separator: str = "|"
    separator: str = " | "
    action_separator: str = " |"
    delete_width: int = 3
    action_width: int = 3


def render_filter_toolbar(
    state: TranscriptViewState,
    context: TranscriptRenderContext,
) -> StyleAndTextTuples:
    fragments: StyleAndTextTuples = []
    btn_index = 0

    def append_button(
        label: str,
        style: str,
        callback: Callable[[], None],
    ) -> None:
        nonlocal btn_index
        s = style
        if context.toolbar_focused_index == btn_index:
            s = _combine_styles(s, "reverse")
        fragments.append((s, f"[ {label} ]", _mouse(callback)))
        btn_index += 1

    append_button("v", "class:transcript.filter", context.toggle_open)
    fragments.append(("", " "))
    append_button("-" if state.maximized else "+", "class:transcript.filter", context.toggle_maximize)
    fragments.append(("", " "))

    buttons = (
        ("Chat", "chat"),
        ("Tools", "tools"),
        ("Search", "search"),
    )
    for index, (label, filter_mode) in enumerate(buttons):
        active = state.filter_mode == filter_mode
        append_button(
            label,
            "class:transcript.filter.active" if active else "class:transcript.filter",
            lambda mode=filter_mode, active=active: context.set_filter("all" if active else mode),  # type: ignore[arg-type]
        )
        if index < len(buttons) - 1:
            fragments.append(("", " "))
    fragments.append(("", " | "))
    sort_label = "Descending" if state.sort_order == "descending" else "Ascending"
    append_button(sort_label, "class:transcript.sort.active", context.toggle_sort)
    fragments.append(("", "\n"))
    return fragments


def render_closed_transcript_bar(context: TranscriptRenderContext) -> StyleAndTextTuples:
    return [("class:transcript.filter", "[ ^ ] Open Transcript", _mouse(context.toggle_open))]


def render_delete_button(row_number: int, context: TranscriptRenderContext) -> StyleAndTextTuples:
    focused = (row_number == context.selected_row_number and context.focused_col == "delete")
    style = _combine_styles("class:transcript.delete", "reverse" if focused else None)
    return [(style, " X ", _mouse(lambda: context.delete_row(row_number)))]


def render_chat_message(row: TranscriptRow, context: TranscriptRenderContext) -> StyleAndTextTuples:
    fragments: StyleAndTextTuples = []
    row_style = _row_base_style(row, context)
    total_width = max(30, context.width - 4)
    max_bubble_width = max(12, int(total_width * 0.85) - 2)
    min_bubble_width = max(12, int(total_width * 0.33) - 2)
    content_lines = render_chat_markdown(_content_as_text(row.content), max_width=max_bubble_width)
    content_width = rendered_lines_width(content_lines)
    bubble_width = min(max_bubble_width, max(content_width, 1))
    if len(content_lines) > 1 or content_width > min_bubble_width:
        bubble_width = max(bubble_width, min_bubble_width)
    top = f"┌{'─' * bubble_width}┐"
    bottom = f"└{'─' * bubble_width}┘"
    delete_line = 1

    if row.role == "assistant":
        line_idx = 0
        _append_prefixed_line(fragments, row.row_number, top, context, row_style=row_style, show_delete=line_idx == delete_line)
        for line in content_lines:
            line_idx += 1
            _append_chat_bubble_content_line(
                fragments,
                row.row_number,
                line,
                context,
                row_style=row_style,
                bubble_width=bubble_width,
                show_delete=line_idx == delete_line,
                content_base_style="class:transcript.assistant",
            )
        line_idx += 1
        _append_prefixed_line(
            fragments,
            row.row_number,
            bottom,
            context,
            row_style=row_style,
            show_delete=line_idx == delete_line,
        )
        return fragments

    left_pad = " " * max(0, total_width - bubble_width - 1)
    line_idx = 0
    _append_prefixed_line(fragments, row.row_number, f"{left_pad}{top}", context, row_style=row_style, show_delete=line_idx == delete_line)
    for line in content_lines:
        line_idx += 1
        _append_chat_bubble_content_line(
            fragments,
            row.row_number,
            line,
            context,
            row_style=row_style,
            bubble_width=bubble_width,
            show_delete=line_idx == delete_line,
            content_base_style="class:transcript.user",
            left_pad=left_pad,
        )
    line_idx += 1
    _append_prefixed_line(
        fragments,
        row.row_number,
        f"{left_pad}{bottom}",
        context,
        row_style=row_style,
        show_delete=line_idx == delete_line,
    )
    return fragments


def render_tool_result(row: TranscriptRow, context: TranscriptRenderContext) -> StyleAndTextTuples:
    fragments: StyleAndTextTuples = []
    row_style = _row_base_style(row, context)
    label_width = max(8, min(14, int(context.width * 0.18)))
    content_width = max(20, context.width - label_width - 8)
    content = flatten_tool_result_for_provider(row)
    lines = _wrap_text(content, content_width, max_lines=3)
    delete_line = len(lines) // 2
    for index, line in enumerate(lines):
        prefix = row.tool_name if index == 0 else ""
        _append_prefixed_line(
            fragments,
            row.row_number,
            f"{prefix.ljust(label_width)} | {line}",
            context,
            row_style=row_style,
            label_style="class:transcript.tool.name",
            label_width=label_width,
            content_style="class:transcript.error" if row.type == "tool_error" else "class:transcript.tool.content",
            show_delete=index == delete_line,
        )
    return fragments


def render_bash_result(row: TranscriptRow, context: TranscriptRenderContext) -> StyleAndTextTuples:
    fragments: StyleAndTextTuples = []
    row_style = _row_base_style(row, context)
    payload = row.content if isinstance(row.content, dict) else {}
    command = _command_from_tool_call(row, context) or str(payload.get("command", ""))
    command_preview = _command_preview(command, width=max(20, context.width - 20))
    toggle = "[^]" if row.row_number in context.expanded_rows else "[v]"
    expanded = row.row_number in context.expanded_rows

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    if expanded:
        stdout = str(payload.get("stdout", ""))
        stderr = str(payload.get("stderr", ""))
        stdout_lines = _wrap_text(stdout, max(20, context.width - 6), max_lines=25)
        if stderr.strip():
            stderr_lines = _wrap_text(stderr, max(20, context.width - 6), max_lines=25)

    # Header line (line 0) always shows the delete button.
    _append_delete(fragments, row.row_number, context, row_style=row_style)
    fragments.append((_combine_styles(row_style, "class:transcript.tool.name"), "bash"))
    fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), " | "))
    fragments.append((_combine_styles(row_style, "class:transcript.bash.command"), command_preview))
    action_focused = (row.row_number == context.selected_row_number and context.focused_col == "action")
    toggle_style = _combine_styles(row_style, "class:transcript.toggle", "reverse" if action_focused else None)
    fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), " | "))
    fragments.append((toggle_style, toggle, _mouse(lambda: context.toggle_expand(row.row_number))))
    fragments.append(("", "\n"))

    if not expanded:
        return fragments

    exit_code = payload.get("exit_code")
    for line in stdout_lines:
        _append_prefixed_line(fragments, row.row_number, f"    {line}", context, row_style=row_style)
    for line in stderr_lines:
        _append_prefixed_line(
            fragments,
            row.row_number,
            f"    {line}",
            context,
            row_style=row_style,
            content_style="class:transcript.error",
        )
    exit_style = "class:transcript.error" if row.type == "tool_error" or exit_code not in {0, None} else "class:transcript.tool.content"
    _append_prefixed_line(
        fragments,
        row.row_number,
        f"    exit_code={exit_code if exit_code is not None else '?'}",
        context,
        row_style=row_style,
        content_style=exit_style,
    )
    return fragments


def render_search_result(row: TranscriptRow, context: TranscriptRenderContext) -> StyleAndTextTuples:
    fragments: StyleAndTextTuples = []
    row_style = _row_base_style(row, context)
    query = _query_from_tool_call(row, context)
    results = row.content if isinstance(row.content, list) else []
    count = len(results)
    toggle = "[^]" if row.row_number in context.expanded_rows else "[v]"
    expanded = row.row_number in context.expanded_rows

    layout = _search_row_layout(width=context.width, result_count=count)
    valid_results = [r for r in results if isinstance(r, dict)] if expanded else []

    _append_delete(fragments, row.row_number, context, row_style=row_style)
    fragments.append(
        (
            _combine_styles(row_style, "class:transcript.search.count"),
            "Search".ljust(layout.header_label_width),
        )
    )
    fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), layout.separator))
    fragments.append(
        (
            _combine_styles(row_style, "class:transcript.tool.content"),
            _fit_cell(query, layout.query_width),
        )
    )
    action_focused = (row.row_number == context.selected_row_number and context.focused_col == "action")
    toggle_style = _combine_styles(row_style, "class:transcript.toggle", "reverse" if action_focused else None)
    fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), layout.action_separator))
    fragments.append(
        (
            toggle_style,
            toggle,
            _mouse(lambda: context.toggle_expand(row.row_number)),
        )
    )
    fragments.append(("", "\n"))

    if not expanded:
        return fragments

    for index, result in enumerate(valid_results, start=1):
        result_index = index - 1
        title = str(result.get("title", "(no title)"))
        url = str(result.get("url", ""))
        domain = _normalized_host(url)
        title_style = "class:transcript.link.cached" if _is_cached_url(url, context) else "class:transcript.tool.content"
        title_handler = _mouse(lambda url=url: context.copy_cached_fetch(url)) if _is_cached_url(url, context) else None

        if layout.result_indent > 0:
            fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), " " * layout.result_indent))
        fragments.append(
            (
                _combine_styles(row_style, "class:transcript.delete"),
                " X ",
                _mouse(lambda ri=result_index: context.delete_search_result(row.row_number, ri)),
            )
        )
        fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), layout.count_prefix_separator))
        fragments.append(
            (
                _combine_styles(row_style, "class:transcript.search.count"),
                _format_count_cell(str(index), layout.count_width),
            )
        )
        fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), layout.separator))
        fragments.append(
            (
                _combine_styles(row_style, "class:transcript.tool.content"),
                _fit_cell(domain, layout.domain_width),
            )
        )
        fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), layout.separator))
        title_text = _fit_cell(title, layout.title_width)
        if title_handler is not None:
            fragments.append((_combine_styles(row_style, title_style), title_text, title_handler))
        else:
            fragments.append((_combine_styles(row_style, title_style), title_text))
        fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), layout.action_separator))
        fragments.append(
            (
                _combine_styles(row_style, "class:transcript.link"),
                "↗",
                _mouse(lambda url=url: context.open_url(url)),
            )
        )
        fragments.append(("", "\n"))
    return fragments


def render_fetch_result(row: TranscriptRow, context: TranscriptRenderContext) -> StyleAndTextTuples:
    fragments: StyleAndTextTuples = []
    row_style = _row_base_style(row, context)
    payload = row.content if isinstance(row.content, dict) else {}
    title = str(payload.get("title", "(no title)"))
    url = str(payload.get("url", ""))
    cached = _is_cached_url(url, context)
    domain = _normalized_host(url)
    layout = _search_row_layout(width=context.width, result_count=1)

    fragments.append(
        (
            _combine_styles(row_style, "class:transcript.delete"),
            " X  Fetch",
            _mouse(lambda: context.delete_row(row.row_number)),
        )
    )
    fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), layout.separator))
    fragments.append(
        (
            _combine_styles(row_style, "class:transcript.tool.content"),
            _fit_cell(domain, layout.domain_width),
        )
    )
    fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), layout.separator))
    title_style = _combine_styles(
        row_style,
        "class:transcript.link.cached" if cached else "class:transcript.tool.content",
    )
    title_text = _fit_cell(title, layout.title_width)
    if cached:
        fragments.append((title_style, title_text, _mouse(lambda url=url: context.copy_cached_fetch(url))))
    else:
        fragments.append((title_style, title_text))
    action_focused = (row.row_number == context.selected_row_number and context.focused_col == "action")
    link_style = _combine_styles(row_style, "class:transcript.link", "reverse" if action_focused else None)
    fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), layout.action_separator))
    fragments.append((link_style, "↗", _mouse(lambda url=url: context.open_url(url))))
    fragments.append(("", "\n"))
    return fragments


def get_renderer(row: TranscriptRow) -> Callable[[TranscriptRow, TranscriptRenderContext], StyleAndTextTuples] | None:
    if row.type == "tool_call":
        return None
    if row.type == "message":
        return render_chat_message
    if row.type in {"tool_result", "tool_error"}:
        if row.tool_name == "bash":
            return render_bash_result
        if row.tool_name == "web_search":
            return render_search_result
        if row.tool_name == "web_fetch":
            return render_fetch_result
        return render_tool_result
    return None


def _append_delete(
    fragments: StyleAndTextTuples,
    row_number: int,
    context: TranscriptRenderContext,
    *,
    row_style: str,
) -> None:
    for style, text, *rest in render_delete_button(row_number, context):
        fragments.append((_combine_styles(row_style, style), text, *rest))


def _append_delete_padding(fragments: StyleAndTextTuples, *, row_style: str) -> None:
    fragments.append((_combine_styles(row_style, "class:transcript.delete"), "   "))


def _append_prefixed_line(
    fragments: StyleAndTextTuples,
    row_number: int,
    text: str,
    context: TranscriptRenderContext,
    *,
    row_style: str,
    label_style: str | None = None,
    label_width: int | None = None,
    content_style: str | None = None,
    show_delete: bool = False,
) -> None:
    if show_delete:
        _append_delete(fragments, row_number, context, row_style=row_style)
    else:
        _append_delete_padding(fragments, row_style=row_style)

    if label_style is None:
        fragments.append((_combine_styles(row_style, content_style or ""), text))
        fragments.append(("", "\n"))
        return

    label_text = text[:label_width or 0]
    remainder = text[label_width or 0 :]
    fragments.append((_combine_styles(row_style, label_style), label_text))
    fragments.append((_combine_styles(row_style, content_style or ""), remainder))
    fragments.append(("", "\n"))


def _append_chat_bubble_content_line(
    fragments: StyleAndTextTuples,
    row_number: int,
    line_fragments: StyleAndTextTuples,
    context: TranscriptRenderContext,
    *,
    row_style: str,
    bubble_width: int,
    show_delete: bool,
    content_base_style: str,
    left_pad: str = "",
) -> None:
    if show_delete:
        _append_delete(fragments, row_number, context, row_style=row_style)
    else:
        _append_delete_padding(fragments, row_style=row_style)

    if left_pad:
        fragments.append((_combine_styles(row_style, content_base_style), left_pad))
    fragments.append((_combine_styles(row_style, content_base_style), "│"))
    for style, text in line_fragments:
        fragments.append((_combine_styles(row_style, content_base_style, style), text))
    pad_width = max(0, bubble_width - fragment_list_width(line_fragments))
    if pad_width:
        fragments.append((_combine_styles(row_style, content_base_style), " " * pad_width))
    fragments.append((_combine_styles(row_style, content_base_style), "│"))
    fragments.append(("", "\n"))


def _content_as_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, indent=2)


def _wrap_text(text: str, width: int, *, max_lines: int | None = None) -> list[str]:
    if width <= 1:
        return [text]
    wrapped: list[str] = []
    raw_lines = text.splitlines() or [text]
    for raw_line in raw_lines:
        if raw_line == "":
            wrapped.append("")
            continue
        wrapped.extend(textwrap.wrap(raw_line, width=width) or [""])
    if max_lines is not None and len(wrapped) > max_lines:
        extra = len(wrapped) - max_lines
        wrapped = wrapped[:max_lines]
        if wrapped:
            wrapped[-1] = textwrap.shorten(f"{wrapped[-1]} [... {extra} more lines]", width=width, placeholder="...")
    return wrapped or [""]


def _search_row_layout(*, width: int, result_count: int) -> SearchRowLayout:
    count_width = max(2, len(str(max(result_count, 1))))
    header_label_width = max(len("Search"), len("|") + count_width)
    result_indent = header_label_width - (len("|") + count_width)
    effective_width = min(width, 110)
    fixed_width = 15 + count_width + result_indent
    domain_width, title_width = _split_domain_title_widths(max(2, effective_width - fixed_width))
    query_width = domain_width + len(" | ") + title_width
    return SearchRowLayout(
        count_width=count_width,
        domain_width=domain_width,
        title_width=title_width,
        query_width=query_width,
        header_label_width=header_label_width,
        result_indent=result_indent,
    )


def _split_domain_title_widths(total_width: int) -> tuple[int, int]:
    domain_width = max(1, int(round(total_width * 0.30)))
    title_width = max(1, total_width - domain_width)
    if domain_width + title_width > total_width:
        title_width = max(1, total_width - domain_width)
    return domain_width, title_width


def _fit_cell(text: str, width: int) -> str:
    normalized = " ".join(str(text).split())
    if width <= 0:
        return ""
    if len(normalized) <= width:
        return normalized.ljust(width)
    if width == 1:
        return "…"
    return f"{normalized[: width - 1]}…"


def _format_count_cell(text: str, width: int) -> str:
    return str(text).rjust(width)


def _normalized_host(url: str) -> str:
    if not url:
        return "(unknown)"
    parsed = urlparse(url)
    host = parsed.hostname or parsed.netloc
    return host or "(unknown)"


def _row_base_style(row: TranscriptRow, context: TranscriptRenderContext) -> str:
    return ""


def _combine_styles(*styles: str | None) -> str:
    return " ".join(style for style in styles if style)


def _mouse(callback: Callable[[], None] | None):
    if callback is None:
        return None

    def handler(mouse_event) -> None:
        if mouse_event.event_type == MouseEventType.MOUSE_UP:
            callback()

    return handler


def _command_from_tool_call(row: TranscriptRow, context: TranscriptRenderContext) -> str:
    if row.tool_id is None:
        return ""
    tool_call = context.tool_call_index.get(row.tool_id)
    if tool_call is None or not isinstance(tool_call.content, dict):
        return ""
    command = tool_call.content.get("command")
    return command if isinstance(command, str) else ""


def _query_from_tool_call(row: TranscriptRow, context: TranscriptRenderContext) -> str:
    if row.tool_id is None:
        return ""
    tool_call = context.tool_call_index.get(row.tool_id)
    if tool_call is None or not isinstance(tool_call.content, dict):
        return ""
    queries = tool_call.content.get("queries")
    if isinstance(queries, list) and queries and isinstance(queries[0], str):
        return queries[0]
    query = tool_call.content.get("query")
    if isinstance(query, str):
        return query
    return ""


def _command_preview(command: str, *, width: int) -> str:
    lines = [line.strip() for line in command.splitlines() if line.strip()]
    if not lines:
        return "$ (no command)"
    preview = f"$ {lines[0]}"
    if len(lines) > 1:
        preview += " ..."
    return textwrap.shorten(preview, width=max(12, width), placeholder="...")


def _is_cached_url(url: str, context: TranscriptRenderContext) -> bool:
    if not url:
        return False
    try:
        return canonicalize_url(url) in context.cached_fetch_urls
    except Exception:
        return False
