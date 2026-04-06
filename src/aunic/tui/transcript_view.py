from __future__ import annotations

import json
from typing import TYPE_CHECKING, Callable

from prompt_toolkit.application.current import get_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.containers import Window, ScrollOffsets
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType

from aunic.tui.transcript_renderers import (
    TranscriptRenderContext,
    get_renderer,
    render_closed_transcript_bar,
    render_filter_toolbar,
)

if TYPE_CHECKING:
    from aunic.domain import TranscriptRow
    from aunic.tui.controller import TuiController


class TranscriptView:
    def __init__(
        self,
        controller: TuiController,
        *,
        width: Callable[[], int] | None = None,
    ) -> None:
        self._controller = controller
        self._width = width or (lambda: 100)
        self._scroll_pos = 0
        self._row_start_lines: dict[int, int] = {}
        self._control = _ScrollableFormattedTextControl(
            text=self._render,
            focusable=True,
            show_cursor=False,
            scroll_callback=self._on_scroll,
            get_cursor_position=lambda: Point(0, self._scroll_pos),
        )
        self.window = Window(
            self._control,
            height=Dimension(preferred=15, max=40, min=3),
            dont_extend_height=True,
            wrap_lines=False,
            right_margins=[ScrollbarMargin(display_arrows=False)],
            scroll_offsets=ScrollOffsets(top=0, bottom=0),
            get_vertical_scroll=lambda w: self._scroll_pos,
        )
        self._selected_row_number: int | None = None
        self._scroll_to_selection = False
        self._row_cache: dict[int, tuple[str, StyleAndTextTuples]] = {}
        self._last_line_count = 1

    def preferred_height(self) -> int:
        return max(3, min(20, self._estimate_line_count()))

    def _estimate_line_count(self) -> int:
        """Estimate total lines from current state without full rendering."""
        rows = self._controller.visible_transcript_rows()
        expanded = self._controller.transcript_view_state.expanded_rows
        line_count = 1  # filter toolbar
        for row in rows:
            renderer = get_renderer(row)
            if renderer is None:
                continue
            if row.tool_name == "web_search" and row.type in {"tool_result", "tool_error"}:
                results = row.content if isinstance(row.content, list) else []
                valid = [r for r in results if isinstance(r, dict)]
                line_count += 1 + (len(valid) if row.row_number in expanded else 0)
            elif row.tool_name == "bash" and row.type in {"tool_result", "tool_error"}:
                if row.row_number in expanded:
                    payload = row.content if isinstance(row.content, dict) else {}
                    stdout = str(payload.get("stdout", ""))
                    stderr = str(payload.get("stderr", ""))
                    stdout_lines = len(stdout.splitlines()) or 1
                    stderr_lines = len(stderr.splitlines()) if stderr.strip() else 0
                    line_count += 1 + min(stdout_lines, 25) + min(stderr_lines, 25) + 1
                else:
                    line_count += 1
            elif row.tool_name == "web_fetch" and row.type in {"tool_result", "tool_error"}:
                line_count += 1
            elif row.type == "message":
                content = row.content if isinstance(row.content, str) else str(row.content)
                line_count += len(content.splitlines()) + 2  # top/bottom borders
            else:
                line_count += 3  # generic tool result estimate
        if not rows:
            line_count += 1
        return line_count

    def ensure_selection(self) -> None:
        rows = self._controller.visible_transcript_rows()
        if not rows:
            self._selected_row_number = None
            return
        visible_numbers = {row.row_number for row in rows}
        if self._selected_row_number not in visible_numbers:
            self._selected_row_number = rows[0].row_number

    def move_selection(self, delta: int) -> None:
        rows = self._controller.visible_transcript_rows()
        if not rows:
            self._selected_row_number = None
            return
        self.ensure_selection()
        row_numbers = [row.row_number for row in rows]
        index = row_numbers.index(self._selected_row_number) if self._selected_row_number in row_numbers else 0
        index = max(0, min(len(row_numbers) - 1, index + delta))
        self._selected_row_number = row_numbers[index]
        self._scroll_to_selection = True
        try:
            get_app().invalidate()
        except Exception:
            pass

    def toggle_selected_expand(self) -> None:
        self.ensure_selection()
        if self._selected_row_number is None:
            return
        self._controller.toggle_transcript_expand(self._selected_row_number)

    async def delete_selected_row(self) -> None:
        self.ensure_selection()
        if self._selected_row_number is None:
            return
        await self._controller.delete_transcript_row(self._selected_row_number)
        rows = self._controller.visible_transcript_rows()
        self._selected_row_number = rows[0].row_number if rows else None

    def _render(self) -> StyleAndTextTuples:
        rows = self._controller.visible_transcript_rows()
        prev_selection = self._selected_row_number
        self.ensure_selection()
        if self._selected_row_number != prev_selection:
            self._scroll_to_selection = True
        tool_call_index = self._controller.tool_call_index()
        context = TranscriptRenderContext(
            width=max(20, self._width() - 2),
            tool_call_index=tool_call_index,
            expanded_rows=self._controller.transcript_view_state.expanded_rows,
            cached_fetch_urls=self._controller.cached_fetch_urls(),
            selected_row_number=self._selected_row_number,
            delete_row=self._delete_row_from_mouse,
            delete_search_result=self._delete_search_result_from_mouse,
            toggle_expand=self._controller.toggle_transcript_expand,
            set_filter=self._controller.set_transcript_filter,
            toggle_sort=self._controller.toggle_transcript_sort,
            toggle_open=self._controller.toggle_transcript_open,
            open_url=self._controller.open_transcript_url,
            copy_text=self._controller.copy_text_to_clipboard,
            copy_cached_fetch=self._controller.copy_cached_fetch_url,
        )

        fragments = render_filter_toolbar(self._controller.transcript_view_state, context)
        scroll_to = self._scroll_to_selection
        self._scroll_to_selection = False

        line_count = 1
        row_start_lines: dict[int, int] = {}
        active_cache_keys: set[int] = set()

        for row in rows:
            renderer = get_renderer(row)
            if renderer is None:
                continue
            cache_key = self._cache_key_for_row(row, tool_call_index)
            cached = self._row_cache.get(row.row_number)
            if cached is None or cached[0] != cache_key:
                row_fragments = renderer(row, context)
                self._row_cache[row.row_number] = (cache_key, row_fragments)
            else:
                row_fragments = cached[1]
            active_cache_keys.add(row.row_number)
            row_start_lines[row.row_number] = line_count
            line_count += _line_count(row_fragments)
            fragments.extend(row_fragments)

        self._row_cache = {
            row_number: value
            for row_number, value in self._row_cache.items()
            if row_number in active_cache_keys
        }
        if not rows:
            fragments.append(("class:transcript.tool.content", "(no transcript rows to display)\n"))
            line_count += 1

        self._row_start_lines = row_start_lines
        self._last_line_count = line_count

        if scroll_to and self._selected_row_number in row_start_lines:
            self._scroll_pos = row_start_lines[self._selected_row_number]

        max_scroll = max(0, line_count - 3)
        self._scroll_pos = max(0, min(self._scroll_pos, max_scroll))

        return fragments

    def _cache_key_for_row(
        self,
        row: TranscriptRow,
        tool_call_index: dict[str, TranscriptRow],
    ) -> str:
        tool_call_content = None
        if row.tool_id and row.tool_id in tool_call_index:
            tool_call_content = tool_call_index[row.tool_id].content
        payload = {
            "row": {
                "row_number": row.row_number,
                "role": row.role,
                "type": row.type,
                "tool_name": row.tool_name,
                "tool_id": row.tool_id,
                "content": row.content,
            },
            "tool_call_content": tool_call_content,
            "expanded": row.row_number in self._controller.transcript_view_state.expanded_rows,
            "selected": row.row_number == self._selected_row_number,
            "width": self._width(),
            "cached_fetch_urls": sorted(self._controller.cached_fetch_urls()),
            "filter_mode": self._controller.transcript_view_state.filter_mode,
            "sort_order": self._controller.transcript_view_state.sort_order,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    def _delete_row_from_mouse(self, row_number: int) -> None:
        app = get_app()
        app.create_background_task(self._controller.delete_transcript_row(row_number))

    def _delete_search_result_from_mouse(self, row_number: int, result_index: int) -> None:
        app = get_app()
        app.create_background_task(self._controller.delete_search_result(row_number, result_index))

    def _on_scroll(self, direction: int) -> None:
        scroll_lines = 3
        new_scroll = self._scroll_pos + direction * scroll_lines
        max_scroll = max(0, self._last_line_count - 3)
        self._scroll_pos = max(0, min(new_scroll, max_scroll))
        try:
            get_app().invalidate()
        except Exception:
            pass


class _ScrollableFormattedTextControl(FormattedTextControl):
    def __init__(self, *args, scroll_callback: Callable[[int], None] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._scroll_callback = scroll_callback

    def mouse_handler(self, mouse_event: MouseEvent):
        if self._scroll_callback is not None:
            if mouse_event.event_type == MouseEventType.SCROLL_UP:
                self._scroll_callback(-1)
                return None
            if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
                self._scroll_callback(1)
                return None
        return super().mouse_handler(mouse_event)


def _line_count(fragments: StyleAndTextTuples) -> int:
    return max(1, sum(fragment[1].count("\n") for fragment in fragments))
