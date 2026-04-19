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
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

from aunic.tui.transcript_renderers import (
    TranscriptRenderContext,
    extract_row_text,
    get_renderer,
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
        self._toolbar_control = FormattedTextControl(
            text=self._render_toolbar,
            focusable=False,
            show_cursor=False,
        )
        self.toolbar_window = Window(
            self._toolbar_control,
            height=1,
            dont_extend_height=True,
            wrap_lines=False,
        )
        self._control = _ScrollableFormattedTextControl(
            text=self._render_body,
            focusable=True,
            show_cursor=False,
            scroll_callback=self._on_scroll,
            blank_area_callback=self._is_blank_body_mouse_event,
            get_cursor_position=lambda: Point(0, self._scroll_pos),
        )
        self.window = _TranscriptBodyWindow(
            self._control,
            height=Dimension(preferred=15, max=40, min=3),
            dont_extend_height=True,
            wrap_lines=False,
            right_margins=[ScrollbarMargin(display_arrows=False)],
            scroll_offsets=ScrollOffsets(top=0, bottom=0),
            get_vertical_scroll=lambda w: self._scroll_pos,
            content_width_callback=self._on_body_width_changed,
            right_click_row_callback=self._on_right_click_line,
        )
        self._selected_row_number: int | None = None
        self._cursor_col: str = "delete"   # "delete" or "action"
        self._toolbar_focused: bool = False
        self._toolbar_index: int = 0
        self._scroll_to_selection = False
        self._row_cache: dict[int, tuple[str, StyleAndTextTuples]] = {}
        self._last_line_count = 1
        self._content_width_override: int | None = None

    @property
    def toolbar_height(self) -> int:
        return 1

    def preferred_height(self) -> int:
        body_height = max(2, min(19, self._estimate_body_line_count()))
        return self.toolbar_height + body_height

    def _estimate_body_line_count(self) -> int:
        """Estimate body lines from current state without full rendering."""
        rows = self._controller.visible_transcript_rows()
        expanded = self._controller.transcript_view_state.expanded_rows
        line_count = 0
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
            self._cursor_col = "delete"
            self._toolbar_focused = False

    def move_up(self) -> None:
        if self._toolbar_focused:
            # Leave toolbar, go to first row
            self._toolbar_focused = False
            self._cursor_col = "delete"
            rows = self._controller.visible_transcript_rows()
            if rows:
                self._selected_row_number = rows[0].row_number
            self._scroll_to_selection = True
            self._invalidate()
            return
        rows = self._controller.visible_transcript_rows()
        if not rows:
            return
        self.ensure_selection()
        row_numbers = [row.row_number for row in rows]
        index = row_numbers.index(self._selected_row_number) if self._selected_row_number in row_numbers else 0
        if index == 0:
            # Go to toolbar
            self._toolbar_focused = True
            self._toolbar_index = 0
        else:
            index -= 1
            self._selected_row_number = row_numbers[index]
            if self._cursor_col == "action":
                row = next((r for r in rows if r.row_number == self._selected_row_number), None)
                if row is None or not _row_has_action(row):
                    self._cursor_col = "delete"
            self._scroll_to_selection = True
        self._invalidate()

    def move_down(self) -> None:
        if self._toolbar_focused:
            self._toolbar_focused = False
            self._cursor_col = "delete"
            rows = self._controller.visible_transcript_rows()
            if rows:
                self._selected_row_number = rows[0].row_number
            self._scroll_to_selection = True
            self._invalidate()
            return
        rows = self._controller.visible_transcript_rows()
        if not rows:
            return
        self.ensure_selection()
        row_numbers = [row.row_number for row in rows]
        index = row_numbers.index(self._selected_row_number) if self._selected_row_number in row_numbers else 0
        if index >= len(row_numbers) - 1:
            # Wrap to top
            self._selected_row_number = row_numbers[0]
            self._cursor_col = "delete"
        else:
            index += 1
            self._selected_row_number = row_numbers[index]
            if self._cursor_col == "action":
                row = next((r for r in rows if r.row_number == self._selected_row_number), None)
                if row is None or not _row_has_action(row):
                    self._cursor_col = "delete"
        self._scroll_to_selection = True
        self._invalidate()

    def move_right(self) -> None:
        if self._toolbar_focused:
            self._toolbar_index = min(5, self._toolbar_index + 1)
            self._invalidate()
            return
        self.ensure_selection()
        if self._selected_row_number is None:
            return
        rows = self._controller.visible_transcript_rows()
        row = next((r for r in rows if r.row_number == self._selected_row_number), None)
        if row is not None and _row_has_action(row):
            self._cursor_col = "action"
            self._invalidate()

    def move_left(self) -> None:
        if self._toolbar_focused:
            self._toolbar_index = max(0, self._toolbar_index - 1)
            self._invalidate()
            return
        self._cursor_col = "delete"
        self._invalidate()

    def activate(self) -> None:
        if self._toolbar_focused:
            self._activate_toolbar_button()
            return
        self.ensure_selection()
        if self._selected_row_number is None:
            return
        if self._cursor_col == "delete":
            try:
                get_app().create_background_task(self.delete_selected_row())
            except Exception:
                pass
        else:
            rows = self._controller.visible_transcript_rows()
            row = next((r for r in rows if r.row_number == self._selected_row_number), None)
            if row is None:
                return
            if row.tool_name in {"bash", "web_search"}:
                self._controller.toggle_transcript_expand(row.row_number)
            elif row.tool_name == "web_fetch":
                payload = row.content if isinstance(row.content, dict) else {}
                url = str(payload.get("url", ""))
                if url:
                    self._controller.open_transcript_url(url)
        self._invalidate()

    def go_home(self) -> None:
        self._toolbar_focused = False
        rows = self._controller.visible_transcript_rows()
        if rows:
            self._selected_row_number = rows[0].row_number
            self._cursor_col = "delete"
            self._scroll_to_selection = True
        self._invalidate()

    def go_end(self) -> None:
        self._toolbar_focused = False
        rows = self._controller.visible_transcript_rows()
        if rows:
            self._selected_row_number = rows[-1].row_number
            self._cursor_col = "delete"
            self._scroll_to_selection = True
        self._invalidate()

    def _activate_toolbar_button(self) -> None:
        idx = self._toolbar_index
        state = self._controller.transcript_view_state
        if idx == 0:
            self._controller.toggle_transcript_open()
        elif idx == 1:
            self._controller.toggle_transcript_maximized()
        elif idx == 2:
            self._controller.set_transcript_filter("all" if state.filter_mode == "chat" else "chat")
        elif idx == 3:
            self._controller.set_transcript_filter("all" if state.filter_mode == "tools" else "tools")
        elif idx == 4:
            self._controller.set_transcript_filter("all" if state.filter_mode == "search" else "search")
        elif idx == 5:
            self._controller.toggle_transcript_sort()
        self._invalidate()

    def _invalidate(self) -> None:
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
        self._cursor_col = "delete"

    def _build_render_context(self) -> TranscriptRenderContext:
        tool_call_index = self._controller.tool_call_index()
        focused_col = self._cursor_col if not self._toolbar_focused else None
        toolbar_idx = self._toolbar_index if self._toolbar_focused else None
        return TranscriptRenderContext(
            width=self._content_width(),
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
            toggle_maximize=self._controller.toggle_transcript_maximized,
            open_url=self._controller.open_transcript_url,
            copy_text=self._controller.copy_text_to_clipboard,
            copy_cached_fetch=self._controller.copy_cached_fetch_url,
            focused_col=focused_col,
            toolbar_focused_index=toolbar_idx,
        )

    def _render_toolbar(self) -> StyleAndTextTuples:
        return render_filter_toolbar(self._controller.transcript_view_state, self._build_render_context())

    def _render_body(self) -> StyleAndTextTuples:
        rows = self._controller.visible_transcript_rows()
        prev_selection = self._selected_row_number
        self.ensure_selection()
        if self._selected_row_number != prev_selection:
            self._scroll_to_selection = True
        tool_call_index = self._controller.tool_call_index()
        context = self._build_render_context()
        fragments: StyleAndTextTuples = []
        scroll_to = self._scroll_to_selection
        self._scroll_to_selection = False

        line_count = 0
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

        max_scroll = max(0, line_count - self._visible_body_lines())
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
            "width": self._content_width(),
            "cached_fetch_urls": sorted(self._controller.cached_fetch_urls()),
            "filter_mode": self._controller.transcript_view_state.filter_mode,
            "sort_order": self._controller.transcript_view_state.sort_order,
            "focused_col": self._cursor_col if (row.row_number == self._selected_row_number and not self._toolbar_focused) else None,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    def _content_width(self) -> int:
        if self._content_width_override is not None:
            return self._content_width_override
        return max(20, self._width() - 2)

    def _on_body_width_changed(self, width: int) -> None:
        width = max(20, width)
        if width == self._content_width_override:
            return
        self._content_width_override = width
        try:
            get_app().invalidate()
        except Exception:
            pass

    def _delete_row_from_mouse(self, row_number: int) -> None:
        app = get_app()
        app.create_background_task(self._controller.delete_transcript_row(row_number))

    def _delete_search_result_from_mouse(self, row_number: int, result_index: int) -> None:
        app = get_app()
        app.create_background_task(self._controller.delete_search_result(row_number, result_index))

    def _on_scroll(self, direction: int) -> None:
        scroll_lines = 3
        new_scroll = self._scroll_pos + direction * scroll_lines
        max_scroll = max(0, self._last_line_count - self._visible_body_lines())
        self._scroll_pos = max(0, min(new_scroll, max_scroll))
        try:
            get_app().invalidate()
        except Exception:
            pass

    def _visible_body_lines(self) -> int:
        render_info = self.window.render_info
        if render_info is None:
            return 2
        return max(1, render_info.window_height)

    def _is_blank_body_mouse_event(self, mouse_event: MouseEvent) -> bool:
        visible_content_lines = max(0, self._last_line_count - self._scroll_pos)
        return mouse_event.position.y >= visible_content_lines

    def copy_selected_row(self) -> None:
        """Copy the keyboard-selected transcript row's content to the clipboard."""
        self.ensure_selection()
        if self._selected_row_number is None:
            return
        rows = self._controller.visible_transcript_rows()
        row = next((r for r in rows if r.row_number == self._selected_row_number), None)
        if row is None:
            return
        text = extract_row_text(row, self._controller.tool_call_index())
        if text:
            self._controller.copy_text_to_clipboard(text)

    def _on_right_click_line(self, content_row: int) -> None:
        """Find the transcript row that owns content_row and copy it to the clipboard."""
        row_start_lines = self._row_start_lines
        if not row_start_lines:
            return
        best_row_number: int | None = None
        best_start = -1
        for row_number, start in row_start_lines.items():
            if start <= content_row and start > best_start:
                best_start = start
                best_row_number = row_number
        if best_row_number is None:
            return
        rows = self._controller.visible_transcript_rows()
        row = next((r for r in rows if r.row_number == best_row_number), None)
        if row is None:
            return
        text = extract_row_text(row, self._controller.tool_call_index())
        if text:
            self._controller.copy_text_to_clipboard(text)


class _ScrollableFormattedTextControl(FormattedTextControl):
    def __init__(
        self,
        *args,
        scroll_callback: Callable[[int], None] | None = None,
        blank_area_callback: Callable[[MouseEvent], bool] | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._scroll_callback = scroll_callback
        self._blank_area_callback = blank_area_callback

    def mouse_handler(self, mouse_event: MouseEvent):
        if self._scroll_callback is not None:
            if mouse_event.event_type == MouseEventType.SCROLL_UP:
                self._scroll_callback(-1)
                return None
            if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
                self._scroll_callback(1)
                return None
        if self._blank_area_callback is not None and self._blank_area_callback(mouse_event):
            return None
        return super().mouse_handler(mouse_event)


class _TranscriptBodyWindow(Window):
    def __init__(
        self,
        *args,
        content_width_callback: Callable[[int], None] | None = None,
        right_click_row_callback: Callable[[int], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._content_width_callback = content_width_callback
        self._right_click_row_callback = right_click_row_callback

    def write_to_screen(
        self,
        screen,
        mouse_handlers,
        write_position,
        parent_style,
        erase_bg,
        z_index,
    ) -> None:
        super().write_to_screen(
            screen,
            mouse_handlers,
            write_position,
            parent_style,
            erase_bg,
            z_index,
        )

        render_info = self.render_info
        effective_write_position = screen.visible_windows_to_write_positions.get(self)
        if render_info is None or effective_write_position is None:
            return
        rowcol_to_yx = getattr(render_info, "rowcol_to_yx", None)
        if rowcol_to_yx is None:
            rowcol_to_yx = getattr(render_info, "_rowcol_to_yx", None)
        visible_line_to_row_col = getattr(render_info, "visible_line_to_row_col", None)
        if rowcol_to_yx is None or visible_line_to_row_col is None:
            return

        left_margin_widths = [self._get_margin_width(m) for m in self.left_margins]
        right_margin_widths = [self._get_margin_width(m) for m in self.right_margins]
        total_margin_width = sum(left_margin_widths + right_margin_widths)
        if self._content_width_callback is not None:
            self._content_width_callback(max(1, effective_write_position.width - total_margin_width - 2))

        mouse_handlers.set_mouse_handler_for_range(
            x_min=effective_write_position.xpos + sum(left_margin_widths),
            x_max=effective_write_position.xpos + effective_write_position.width - total_margin_width,
            y_min=effective_write_position.ypos,
            y_max=effective_write_position.ypos + effective_write_position.height,
            handler=self._build_mouse_handler(
                rowcol_to_yx=rowcol_to_yx,
                visible_line_to_row_col=visible_line_to_row_col,
                write_position=effective_write_position,
            ),
        )

    def _build_mouse_handler(
        self,
        *,
        rowcol_to_yx: dict[tuple[int, int], tuple[int, int]],
        visible_line_to_row_col: dict[int, tuple[int, int]],
        write_position,
    ) -> Callable[[MouseEvent], object]:
        right_click_row_callback = self._right_click_row_callback

        def mouse_handler(mouse_event: MouseEvent):
            if self not in get_app().layout.walk_through_modal_area():
                return NotImplemented

            yx_to_rowcol = {v: k for k, v in rowcol_to_yx.items()}
            y = mouse_event.position.y
            x = mouse_event.position.x

            # Right-click: copy the transcript row at the clicked position.
            if (
                mouse_event.event_type == MouseEventType.MOUSE_UP
                and mouse_event.button == MouseButton.RIGHT
                and right_click_row_callback is not None
            ):
                x_try = x
                while x_try >= 0:
                    try:
                        content_row, _ = yx_to_rowcol[y, x_try]
                        right_click_row_callback(content_row)
                        break
                    except KeyError:
                        x_try -= 1
                return None

            if not visible_line_to_row_col:
                return None

            max_y = write_position.ypos + len(visible_line_to_row_col) - 1
            if mouse_event.event_type not in {MouseEventType.SCROLL_UP, MouseEventType.SCROLL_DOWN} and y > max_y:
                return None

            y = min(max_y, y)
            result = NotImplemented

            while x >= 0:
                try:
                    row, col = yx_to_rowcol[y, x]
                except KeyError:
                    x -= 1
                else:
                    result = self.content.mouse_handler(
                        MouseEvent(
                            position=Point(x=col, y=row),
                            event_type=mouse_event.event_type,
                            button=mouse_event.button,
                            modifiers=mouse_event.modifiers,
                        )
                    )
                    break
            else:
                result = self.content.mouse_handler(
                    MouseEvent(
                        position=Point(x=0, y=0),
                        event_type=mouse_event.event_type,
                        button=mouse_event.button,
                        modifiers=mouse_event.modifiers,
                    )
                )

            if result == NotImplemented:
                result = self._mouse_handler(mouse_event)

            return result

        return mouse_handler


def _line_count(fragments: StyleAndTextTuples) -> int:
    return max(1, sum(fragment[1].count("\n") for fragment in fragments))


def _row_has_action(row) -> bool:
    """Returns True if the row has a secondary action button (toggle/open link)."""
    return row.tool_name in {"bash", "web_search", "web_fetch"} and row.type in {"tool_result", "tool_error"}
