from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass
from typing import Any, Callable

from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.mouse_events import MouseEventType

from aunic.domain import TranscriptRow
from aunic.research.search import canonicalize_url
from aunic.transcript.flattening import flatten_tool_result_for_provider
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
    open_url: Callable[[str], None]
    copy_text: Callable[[str], None]
    copy_cached_fetch: Callable[[str], None]


def render_filter_toolbar(
    state: TranscriptViewState,
    context: TranscriptRenderContext,
) -> StyleAndTextTuples:
    fragments: StyleAndTextTuples = []

    def append_button(
        label: str,
        style: str,
        callback: Callable[[], None],
    ) -> None:
        fragments.append((style, f"[ {label} ]", _mouse(callback)))

    append_button("v", "class:transcript.filter", context.toggle_open)
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
    return [("class:transcript.delete", " X ", _mouse(lambda: context.delete_row(row_number)))]


def render_chat_message(row: TranscriptRow, context: TranscriptRenderContext) -> StyleAndTextTuples:
    fragments: StyleAndTextTuples = []
    row_style = _row_base_style(row, context)
    total_width = max(30, context.width - 4)
    bubble_width = max(12, int(total_width * 0.67) - 2)
    other_width = max(8, total_width - bubble_width - 1)
    content_lines = _wrap_text(_content_as_text(row.content), bubble_width)
    top = f"┌{'─' * bubble_width}┐"
    bottom = f"└{'─' * bubble_width}┘"
    total_lines = len(content_lines) + 2  # top + content + bottom
    delete_line = total_lines // 2

    if row.role == "assistant":
        line_idx = 0
        _append_prefixed_line(fragments, row.row_number, top, context, row_style=row_style, show_delete=line_idx == delete_line)
        for line in content_lines:
            line_idx += 1
            _append_prefixed_line(
                fragments,
                row.row_number,
                f"│{line.ljust(bubble_width)}│{' ' * (other_width + 1)}",
                context,
                row_style=row_style,
                content_style="class:transcript.assistant",
                show_delete=line_idx == delete_line,
            )
        line_idx += 1
        _append_prefixed_line(
            fragments,
            row.row_number,
            f"{bottom}{' ' * (other_width + 1)}",
            context,
            row_style=row_style,
            show_delete=line_idx == delete_line,
        )
        return fragments

    left_pad = " " * (other_width + 1)
    line_idx = 0
    _append_prefixed_line(fragments, row.row_number, f"{left_pad}{top}", context, row_style=row_style, show_delete=line_idx == delete_line)
    for line in content_lines:
        line_idx += 1
        _append_prefixed_line(
            fragments,
            row.row_number,
            f"{left_pad}│{line.ljust(bubble_width)}│",
            context,
            row_style=row_style,
            content_style="class:transcript.user",
            show_delete=line_idx == delete_line,
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

    # Pre-count total lines to place delete button at the middle.
    total_lines = 1  # header
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    if expanded:
        stdout = str(payload.get("stdout", ""))
        stderr = str(payload.get("stderr", ""))
        stdout_lines = _wrap_text(stdout, max(20, context.width - 6), max_lines=25)
        total_lines += len(stdout_lines)
        if stderr.strip():
            stderr_lines = _wrap_text(stderr, max(20, context.width - 6), max_lines=25)
            total_lines += len(stderr_lines)
        total_lines += 1  # exit_code line
    delete_line = total_lines // 2

    # Header line (line 0)
    if delete_line == 0:
        _append_delete(fragments, row.row_number, context, row_style=row_style)
    else:
        _append_delete_padding(fragments, row_style=row_style)
    fragments.append((_combine_styles(row_style, "class:transcript.tool.name"), "bash"))
    fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), " | "))
    fragments.append((_combine_styles(row_style, "class:transcript.bash.command"), command_preview))
    fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), " | "))
    fragments.append((_combine_styles(row_style, "class:transcript.toggle"), toggle, _mouse(lambda: context.toggle_expand(row.row_number))))
    fragments.append(("", "\n"))

    if not expanded:
        return fragments

    exit_code = payload.get("exit_code")
    line_idx = 1
    for line in stdout_lines:
        _append_prefixed_line(fragments, row.row_number, f"    {line}", context, row_style=row_style, show_delete=line_idx == delete_line)
        line_idx += 1
    if stderr_lines:
        for line in stderr_lines:
            _append_prefixed_line(
                fragments,
                row.row_number,
                f"    {line}",
                context,
                row_style=row_style,
                content_style="class:transcript.error",
                show_delete=line_idx == delete_line,
            )
            line_idx += 1
    exit_style = "class:transcript.error" if row.type == "tool_error" or exit_code not in {0, None} else "class:transcript.tool.content"
    _append_prefixed_line(
        fragments,
        row.row_number,
        f"    exit_code={exit_code if exit_code is not None else '?'}",
        context,
        row_style=row_style,
        content_style=exit_style,
        show_delete=line_idx == delete_line,
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

    valid_results = [r for r in results if isinstance(r, dict)] if expanded else []

    # Header line — always shows row-level delete (individual results have their own)
    _append_delete(fragments, row.row_number, context, row_style=row_style)
    fragments.append((_combine_styles(row_style, "class:transcript.search.count"), str(count)))
    fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), " | "))
    fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), textwrap.shorten(query, width=max(20, context.width - 18), placeholder="...")))
    fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), " | "))
    fragments.append((_combine_styles(row_style, "class:transcript.toggle"), toggle, _mouse(lambda: context.toggle_expand(row.row_number))))
    fragments.append(("", "\n"))

    if not expanded:
        return fragments

    line_idx = 1
    for index, result in enumerate(valid_results, start=1):
        result_index = index - 1
        title = str(result.get("title", "(no title)"))
        snippet = textwrap.shorten(str(result.get("snippet", "")), width=max(12, context.width // 3), placeholder="...")
        url = str(result.get("url", ""))
        title_style = "class:transcript.link.cached" if _is_cached_url(url, context) else "class:transcript.tool.content"
        title_handler = _mouse(lambda url=url: context.copy_cached_fetch(url)) if _is_cached_url(url, context) else None

        fragments.append((
            _combine_styles(row_style, "class:transcript.delete"),
            " X ",
            _mouse(lambda ri=result_index: context.delete_search_result(row.row_number, ri)),
        ))
        fragments.append((_combine_styles(row_style, "class:transcript.search.count"), f"{index}".rjust(2)))
        fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), " | "))
        title_text = textwrap.shorten(title, width=max(12, context.width // 3), placeholder="...")
        if title_handler is not None:
            fragments.append((_combine_styles(row_style, title_style), title_text, title_handler))
        else:
            fragments.append((_combine_styles(row_style, title_style), title_text))
        fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), " | "))
        fragments.append((_combine_styles(row_style, "class:transcript.search.snippet"), snippet, _mouse(lambda text=str(result.get("snippet", "")): context.copy_text(text))))
        fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), " | "))
        fragments.append((_combine_styles(row_style, "class:transcript.link"), "↗", _mouse(lambda url=url: context.open_url(url))))
        fragments.append(("", "\n"))
        line_idx += 1
    return fragments


def render_fetch_result(row: TranscriptRow, context: TranscriptRenderContext) -> StyleAndTextTuples:
    fragments: StyleAndTextTuples = []
    row_style = _row_base_style(row, context)
    payload = row.content if isinstance(row.content, dict) else {}
    title = str(payload.get("title", "(no title)"))
    snippet = textwrap.shorten(str(payload.get("snippet", "")), width=max(12, context.width // 2), placeholder="...")
    url = str(payload.get("url", ""))
    cached = _is_cached_url(url, context)

    _append_delete(fragments, row.row_number, context, row_style=row_style)
    fragments.append((
        _combine_styles(row_style, "class:transcript.link.cached" if cached else "class:transcript.tool.content"),
        textwrap.shorten(title, width=max(12, context.width // 3), placeholder="..."),
        _mouse(lambda url=url: context.copy_cached_fetch(url)) if cached else None,
    ))
    fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), " | "))
    fragments.append((_combine_styles(row_style, "class:transcript.fetch.snippet"), snippet, _mouse(lambda text=str(payload.get("snippet", "")): context.copy_text(text))))
    fragments.append((_combine_styles(row_style, "class:transcript.tool.content"), " | "))
    fragments.append((_combine_styles(row_style, "class:transcript.link"), "↗", _mouse(lambda url=url: context.open_url(url))))
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
