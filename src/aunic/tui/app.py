from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition, has_focus
from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
from prompt_toolkit.input.base import Input
from prompt_toolkit.key_binding.bindings.named_commands import get_by_name
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Float, FloatContainer, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.containers import WindowAlign, ScrollOffsets
from prompt_toolkit.layout.containers import ConditionalContainer, DynamicContainer
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.output.base import Output
from prompt_toolkit.shortcuts import set_title
from prompt_toolkit.widgets import Box, Button, Dialog, Frame, Label, RadioList, TextArea
from prompt_toolkit.document import Document

from aunic.context import FileManager
from aunic.modes import ChatModeRunner, NoteModeRunner
from aunic.tui.controller import TuiController
from aunic.tui.web_search_view import WebSearchView
from aunic.tui.transcript_view import TranscriptView
from aunic.tui.note_tables import NoteTablePreviewBufferControl
from aunic.tui.rendering import (
    AunicMarkdownLexer,
    RecentChangeProcessor,
    ThematicBreakProcessor,
    build_tui_style,
)


_CTRL_BACKSPACE_SEQUENCES = (
    "\x1b[127;5u",
    "\x1b[8;5u",
    "\x1b[27;5;8~",
)

for _sequence in _CTRL_BACKSPACE_SEQUENCES:
    ANSI_SEQUENCES.setdefault(_sequence, (Keys.Escape, Keys.ControlH))


class AunicTuiApp:
    _INDENT_TEXT = "    "
    _UNDO_GROUP_WINDOW_SECONDS = 0.75

    def __init__(
        self,
        *,
        active_file: Path,
        included_files: tuple[Path, ...] = (),
        initial_provider: str = "codex",
        initial_model: str | None = None,
        initial_profile_id: str | None = None,
        reasoning_effort=None,
        display_root: Path | None = None,
        initial_mode: str = "note",
        cwd: Path | None = None,
        allow_missing_active_file: bool = False,
        create_missing_parents_on_save: bool = False,
        file_manager: FileManager | None = None,
        note_runner: NoteModeRunner | None = None,
        chat_runner: ChatModeRunner | None = None,
        input: Input | None = None,
        output: Output | None = None,
    ) -> None:
        self.controller = TuiController(
            active_file=active_file,
            included_files=included_files,
            initial_provider=initial_provider,
            initial_model=initial_model,
            initial_profile_id=initial_profile_id,
            reasoning_effort=reasoning_effort,
            display_root=display_root,
            cwd=cwd,
            allow_missing_active_file=allow_missing_active_file,
            create_missing_parents_on_save=create_missing_parents_on_save,
            file_manager=file_manager,
            note_runner=note_runner,
            chat_runner=chat_runner,
        )
        self.controller.state.mode = initial_mode  # type: ignore[assignment]
        file_ui_state = _load_tui_file_state(active_file)
        if isinstance(file_ui_state.get("transcript_open"), bool):
            self.controller.state.transcript_open = file_ui_state["transcript_open"]
        if isinstance(file_ui_state.get("transcript_maximized"), bool):
            self.controller.transcript_view_state.maximized = file_ui_state["transcript_maximized"]

        def _save_transcript_ui_state() -> None:
            _save_tui_file_state(
                active_file,
                {
                    "transcript_open": self.controller.state.transcript_open,
                    "transcript_maximized": self.controller.transcript_view_state.maximized,
                },
            )

        self.controller._on_transcript_open_changed = lambda _open_state: _save_transcript_ui_state()
        self.controller._on_transcript_maximized_changed = lambda _maximized: _save_transcript_ui_state()

        self.editor = TextArea(
            multiline=True,
            scrollbar=True,
            wrap_lines=True,
            lexer=AunicMarkdownLexer(),
            focus_on_click=True,
            read_only=Condition(lambda: self.controller.editor_is_read_only() or self.controller.state.web_mode != "idle"),
        )
        self.editor.window.right_margins = [ScrollbarMargin(display_arrows=False)]
        self.prompt_field = TextArea(
            multiline=True,
            scrollbar=True,
            wrap_lines=True,
            focus_on_click=True,
            read_only=Condition(lambda: self.controller.state.run_in_progress),
        )
        self.prompt_field.window.right_margins = [ScrollbarMargin(display_arrows=False)]
        self.controller.attach_buffers(
            editor_buffer=self.editor.buffer,
            prompt_buffer=self.prompt_field.buffer,
        )
        self.editor.buffer.on_text_changed += self._coalesce_buffer_undo_history
        self.prompt_field.buffer.on_text_changed += self._coalesce_buffer_undo_history
        original_editor_control = self.editor.control
        self.editor.control = NoteTablePreviewBufferControl(
            buffer=self.editor.buffer,
            lexer=original_editor_control.lexer,
            input_processors=[
                ThematicBreakProcessor(width=lambda: self._editor_width()),
                RecentChangeProcessor(spans=self.controller.recent_display_change_spans),
            ],
            search_buffer_control=original_editor_control.search_buffer_control,
            preview_search=original_editor_control.preview_search,
            focusable=original_editor_control.focusable,
            focus_on_click=original_editor_control.focus_on_click,
            key_bindings=original_editor_control.key_bindings,
        )
        self.editor.window.content = self.editor.control
        self.editor.window.get_line_prefix = self._editor_line_prefix
        self._web_view = WebSearchView(self.controller, width=self._editor_width)
        self._transcript_view = TranscriptView(self.controller, width=self._editor_width)
        self._last_web_mode: str = "idle"
        self._model_picker_view = ModelPickerView(self.controller, select=self._select_model_from_picker)
        self._permission_prompt_view = PermissionPromptView(self.controller)

        self._file_radio = RadioList(
            [(self.controller.state.active_file, self.controller.state.active_file.name)]
        )
        self._file_menu = self._build_file_menu_dialog()

        self.top_bar_window = Window(
            FormattedTextControl(
                text=self._top_bar_fragments,
                focusable=True,
                show_cursor=False,
            ),
            height=1,
            align=WindowAlign.CENTER,
            style="class:topbar",
        )
        self.indicator_window = Window(
            FormattedTextControl(text=self._indicator_fragments, focusable=False),
            height=1,
        )
        self.control_row = VSplit(
            [
                VSplit(
                    [
                        self._control_window(self._model_control_fragments, self._open_model_picker),
                        self._control_window(self._work_control_fragments, self.controller.toggle_work_mode),
                        self._control_window(self._mode_control_fragments, self._toggle_mode_background),
                    ],
                    padding=1,
                ),
                Window(),
                self._control_window(self._send_control_fragments, self._send_background),
            ],
            padding=1,
        )
        self.prompt_box = Frame(
            HSplit(
                [
                    DynamicContainer(self._prompt_area_body),
                    _ContextSeparatorWindow(self.controller),
                    self.control_row,
                ]
            ),
        )
        self._closed_transcript_bar = Window(
            FormattedTextControl(
                text=self._closed_transcript_fragments,
                focusable=True,
                show_cursor=False,
            ),
            height=1,
        )
        self.note_and_transcript = HSplit(
            [
                ConditionalContainer(
                    content=self.editor,
                    filter=Condition(lambda: not self._transcript_fills_editor_area()),
                ),
                ConditionalContainer(
                    content=HSplit(
                        [
                            Window(height=1, char="─", style="class:md.thematic"),
                            self._transcript_view.toolbar_window,
                            self._transcript_view.window,
                        ]
                    ),
                    filter=Condition(lambda: self.controller.has_transcript() and self.controller.state.transcript_open),
                ),
                ConditionalContainer(
                    content=HSplit(
                        [
                            Window(height=1, char="─", style="class:md.thematic"),
                            self._closed_transcript_bar,
                        ]
                    ),
                    filter=Condition(lambda: self.controller.has_transcript() and not self.controller.state.transcript_open),
                ),
            ]
        )
        self.root = FloatContainer(
            content=HSplit(
                [
                    self.top_bar_window,
                    self.note_and_transcript,
                    self.indicator_window,
                    self.prompt_box,
                ]
            ),
            floats=[
                Float(
                    content=ConditionalContainer(
                        content=DynamicContainer(self._dialog_container),
                        filter=Condition(lambda: (
                            self.controller.state.active_dialog is not None
                            and self.controller.state.active_dialog not in {"model_picker", "permission_prompt"}
                        )),
                    ),
                    top=2,
                    left=2,
                    right=2,
                    bottom=2,
                )
            ],
        )
        self.bindings = self._build_key_bindings()
        self.application = Application(
            layout=Layout(self.root, focused_element=self.prompt_field),
            key_bindings=self.bindings,
            full_screen=True,
            mouse_support=True,
            style=build_tui_style(),
            input=input,
            output=output,
        )
        self.application.pre_run_callables.append(self._invalidate)
        self.controller.set_invalidator(self._invalidate)
        set_title("Aunic")
        self._last_edit_action_by_buffer: dict[int, str] = {}
        self._last_edit_at_by_buffer: dict[int, float] = {}
        self._last_text_by_buffer: dict[int, str] = {
            id(self.editor.buffer): self.editor.buffer.text,
            id(self.prompt_field.buffer): self.prompt_field.buffer.text,
        }
        self._suspend_undo_coalescing = False

    async def run(self) -> int:
        await self.controller.initialize()
        await self.controller.start_watch_task()
        self._refresh_dimensions()
        try:
            await self.application.run_async()
        finally:
            await self.controller.shutdown()
        return 0

    def _build_key_bindings(self) -> KeyBindings:
        bindings = KeyBindings()

        @bindings.add("c-c", eager=True, filter=Condition(self._editing_text_area_has_selection))
        def _copy(_event) -> None:
            area = self._active_text_area()
            if area is None:
                return
            doc = area.buffer.document
            if doc.selection is None:
                return
            from_pos, to_pos = doc.selection_range()
            self.controller.copy_text_to_clipboard(doc.text[from_pos:to_pos])

        @bindings.add("c-r", eager=True)
        def _send(_event) -> None:
            self._background(self.controller.send_prompt())

        @bindings.add("c-s", eager=True)
        def _save(_event) -> None:
            self._background(self.controller.save_active_file())

        @bindings.add("f2")
        def _file_menu(_event) -> None:
            self._open_file_menu()

        @bindings.add("f3")
        def _model_menu(_event) -> None:
            self._open_model_picker()

        @bindings.add("f4")
        def _mode_toggle(_event) -> None:
            self._toggle_mode_background()

        @bindings.add("f6")
        @bindings.add("c-_", eager=True)
        def _toggle_focus(_event) -> None:
            self._toggle_focus_between_editor_and_prompt()

        @bindings.add("up", eager=True, filter=Condition(self._editing_text_area_has_focus))
        def _move_visual_up(_event) -> None:
            self._move_active_text_area_visual(-1)

        @bindings.add("down", eager=True, filter=Condition(self._editing_text_area_has_focus))
        def _move_visual_down(_event) -> None:
            self._move_active_text_area_visual(1)

        @bindings.add("tab", eager=True, filter=Condition(self._editing_text_area_has_focus))
        def _indent(_event) -> None:
            self._indent_active_text_area()

        @bindings.add("s-tab", eager=True, filter=Condition(self._editing_text_area_has_focus))
        def _unindent(_event) -> None:
            self._unindent_active_text_area()

        @bindings.add(
            "c-z",
            eager=True,
            filter=Condition(self._editing_text_area_has_focus),
            save_before=lambda _event: False,
        )
        def _undo(_event) -> None:
            self._undo_active_text_area()

        @bindings.add(
            "c-y",
            eager=True,
            filter=Condition(self._editing_text_area_has_focus),
            save_before=lambda _event: False,
        )
        def _redo(_event) -> None:
            self._redo_active_text_area()

        @bindings.add("c-up", eager=True, filter=has_focus(self.editor))
        def _fold(_event) -> None:
            self.controller.fold_at_cursor()

        @bindings.add("c-down", eager=True, filter=has_focus(self.editor))
        def _unfold(_event) -> None:
            self.controller.unfold_at_cursor()

        @bindings.add("escape")
        def _close_dialog(_event) -> None:
            self.controller.close_dialog()

        @bindings.add("c-a", eager=True, filter=Condition(self._editing_text_area_has_focus))
        def _select_all(_event) -> None:
            area = self._active_text_area()
            if area is None:
                return
            buf = area.buffer
            buf.cursor_position = 0
            buf.start_selection()
            buf.cursor_position = len(buf.text)

        @bindings.add("escape", "backspace", eager=True, filter=Condition(self._editing_text_area_has_focus))
        @bindings.add("escape", "c-h", eager=True, filter=Condition(self._editing_text_area_has_focus))
        def _backward_kill_word(event) -> None:
            get_by_name("backward-kill-word").handler(event)

        @bindings.add("home", eager=True, filter=Condition(self._editing_text_area_has_focus))
        def _visual_home(_event) -> None:
            area = self._active_text_area()
            if area is None:
                return
            pos = _visual_home_end_position(text_area=area, go_end=False)
            if pos is not None:
                area.buffer.cursor_position = pos
            else:
                area.buffer.cursor_position += area.buffer.document.get_start_of_line_position()

        @bindings.add("end", eager=True, filter=Condition(self._editing_text_area_has_focus))
        def _visual_end(_event) -> None:
            area = self._active_text_area()
            if area is None:
                return
            pos = _visual_home_end_position(text_area=area, go_end=True)
            if pos is not None:
                area.buffer.cursor_position = pos
            else:
                area.buffer.cursor_position += area.buffer.document.get_end_of_line_position()

        @bindings.add("c-q")
        def _quit(event) -> None:
            event.app.exit(result=0)

        @bindings.add("up", eager=True, filter=Condition(self._in_web_mode))
        def _web_up(_event) -> None:
            self.controller.web_move_cursor(-1)

        @bindings.add("down", eager=True, filter=Condition(self._in_web_mode))
        def _web_down(_event) -> None:
            self.controller.web_move_cursor(1)

        @bindings.add("left", eager=True, filter=Condition(self._in_web_results))
        @bindings.add("right", eager=True, filter=Condition(self._in_web_results))
        def _web_toggle_expand(_event) -> None:
            self.controller.web_toggle_expand()

        @bindings.add("space", eager=True, filter=Condition(self._in_web_mode))
        def _web_space(_event) -> None:
            self.controller.web_space_pressed()

        @bindings.add("enter", eager=True, filter=Condition(self._in_web_results))
        def _web_open_url(_event) -> None:
            self.controller.web_open_url()

        @bindings.add("escape", filter=Condition(self._in_web_mode))
        def _web_escape(_event) -> None:
            self.controller.web_escape()

        @bindings.add("c-c", filter=Condition(self._in_web_mode))
        def _web_cancel_key(_event) -> None:
            self.controller._web_cancel()

        @bindings.add("up", eager=True, filter=Condition(self._in_model_picker))
        def _model_up(_event) -> None:
            self.controller.move_dialog_selection(-1)
            self._model_picker_view.on_cursor_moved()

        @bindings.add("down", eager=True, filter=Condition(self._in_model_picker))
        def _model_down(_event) -> None:
            self.controller.move_dialog_selection(1)
            self._model_picker_view.on_cursor_moved()

        @bindings.add("enter", eager=True, filter=Condition(self._in_model_picker))
        def _model_select(_event) -> None:
            self._select_model_from_picker()

        @bindings.add("escape", filter=Condition(self._in_model_picker))
        def _model_escape(_event) -> None:
            self.controller.close_dialog()
            self.application.layout.focus(self.prompt_field)

        @bindings.add("up", eager=True, filter=Condition(self._in_permission_prompt))
        def _permission_up(_event) -> None:
            self.controller.move_dialog_selection(-1)

        @bindings.add("down", eager=True, filter=Condition(self._in_permission_prompt))
        def _permission_down(_event) -> None:
            self.controller.move_dialog_selection(1)

        @bindings.add("left", eager=True, filter=Condition(self._in_permission_prompt))
        @bindings.add("s-tab", eager=True, filter=Condition(self._in_permission_prompt))
        def _permission_left(_event) -> None:
            self.controller.move_dialog_selection(-1)

        @bindings.add("right", eager=True, filter=Condition(self._in_permission_prompt))
        @bindings.add("tab", eager=True, filter=Condition(self._in_permission_prompt))
        def _permission_right(_event) -> None:
            self.controller.move_dialog_selection(1)

        @bindings.add("enter", eager=True, filter=Condition(self._in_permission_prompt))
        @bindings.add("space", eager=True, filter=Condition(self._in_permission_prompt))
        def _permission_select(_event) -> None:
            self._background(self.controller.activate_dialog_selection())

        @bindings.add("escape", filter=Condition(self._in_permission_prompt))
        def _permission_escape(_event) -> None:
            self.controller.close_dialog()

        @bindings.add("up", eager=True, filter=Condition(self._transcript_has_focus))
        def _transcript_up(_event) -> None:
            self._transcript_view.move_selection(-1)

        @bindings.add("down", eager=True, filter=Condition(self._transcript_has_focus))
        def _transcript_down(_event) -> None:
            self._transcript_view.move_selection(1)

        @bindings.add("enter", eager=True, filter=Condition(self._transcript_has_focus))
        @bindings.add("space", eager=True, filter=Condition(self._transcript_has_focus))
        def _transcript_toggle(_event) -> None:
            self._transcript_view.toggle_selected_expand()

        @bindings.add("delete", eager=True, filter=Condition(self._transcript_has_focus))
        @bindings.add("backspace", eager=True, filter=Condition(self._transcript_has_focus))
        def _transcript_delete(_event) -> None:
            self._background(self._transcript_view.delete_selected_row())

        @bindings.add("f", eager=True, filter=Condition(self._transcript_has_focus))
        def _transcript_filter(_event) -> None:
            self.controller.cycle_transcript_filter()

        @bindings.add("s", eager=True, filter=Condition(self._transcript_has_focus))
        def _transcript_sort(_event) -> None:
            self.controller.toggle_transcript_sort()

        return bindings

    def _invalidate(self) -> None:
        self._refresh_dimensions()
        self._refresh_web_view_height()
        self._refresh_transcript_dimensions()
        self._update_web_focus()
        self._update_dialog_focus()
        self._update_transcript_focus()
        self.application.invalidate()

    def _refresh_dimensions(self) -> None:
        self.prompt_field.window.height = self.controller.prompt_height()
        self.indicator_window.height = self.controller.indicator_height()

    def _refresh_web_view_height(self) -> None:
        if self.controller.state.web_mode == "idle":
            return
        h = min(self.controller.web_view_preferred_height(), 20)
        self._web_view.window.height = Dimension(preferred=h, max=20, min=3)

    def _prompt_area_body(self):
        if self.controller.state.active_dialog == "model_picker":
            return self._model_picker_view.window
        if self.controller.state.active_dialog == "permission_prompt":
            return self._permission_prompt_view.window
        if self.controller.state.web_mode == "idle":
            return self.prompt_field
        return self._web_view.window

    def _update_web_focus(self) -> None:
        current = self.controller.state.web_mode
        if current == self._last_web_mode:
            return
        self._last_web_mode = current
        if current != "idle":
            self.application.layout.focus(self._web_view.window)
        else:
            self.application.layout.focus(self.prompt_field)

    def _update_dialog_focus(self) -> None:
        dialog = self.controller.state.active_dialog
        if dialog == "model_picker" and not self.application.layout.has_focus(self._model_picker_view.window):
            self.application.layout.focus(self._model_picker_view.window)
        elif dialog == "permission_prompt" and not self.application.layout.has_focus(self._permission_prompt_view.window):
            self.application.layout.focus(self._permission_prompt_view.window)

    def _update_transcript_focus(self) -> None:
        transcript_visible = self.controller.has_transcript() and self.controller.state.transcript_open
        if not transcript_visible and self.application.layout.has_focus(self._transcript_view.window):
            self.application.layout.focus(self.editor)
            return
        if self._transcript_fills_editor_area() and self.application.layout.has_focus(self.editor):
            self._transcript_view.ensure_selection()
            self.application.layout.focus(self._transcript_view.window)

    def _editor_width(self) -> int:
        render_info = self.editor.window.render_info
        if render_info is None:
            render_info = self._transcript_view.window.render_info
        if render_info is None:
            return 60
        return max(3, render_info.window_width - 2)

    def _refresh_transcript_dimensions(self) -> None:
        try:
            app_height = self.application.output.get_size().rows
        except Exception:
            app_height = 40
        if self.controller.transcript_view_state.maximized:
            note_and_transcript_rows = max(
                3,
                app_height
                - 1  # top bar
                - _dimension_value(self.indicator_window.height, 1)
                - self._prompt_box_height(),
            )
            max_height = note_and_transcript_rows
        else:
            max_height = max(6, app_height // 3)
        body_max_height = max(2, max_height - self._transcript_view.toolbar_height - 1)  # separator row
        if self.controller.transcript_view_state.maximized:
            preferred = body_max_height
        else:
            preferred = self._transcript_view.preferred_height() - self._transcript_view.toolbar_height
        self._transcript_view.window.height = Dimension(
            preferred=min(max(2, preferred), body_max_height),
            max=body_max_height,
            min=2,
        )

    def _prompt_box_height(self) -> int:
        prompt_body_height = _dimension_value(self._prompt_area_body(), 3)
        return prompt_body_height + 1 + 1 + 2

    def _editor_line_prefix(self, line_number: int, wrap_count: int):
        lines = self.controller.current_display_lines()
        source_line_number = _source_row_for_display_row(self.editor.control, line_number)
        if source_line_number >= len(lines):
            return [("", "")]
        prefix = self.controller.line_prefix(
            source_line_number,
            wrap_count,
            in_code_block=_line_is_in_fenced_code_block(lines, source_line_number),
        )
        return [("", prefix)]

    def _title_mouse_handler(self, mouse_event) -> None:
        if mouse_event.event_type == MouseEventType.MOUSE_UP:
            self._open_file_menu()

    def _send_background(self) -> None:
        self._background(self.controller.send_prompt())

    def _toggle_mode_background(self) -> None:
        self._background(self.controller.toggle_mode())

    def _toggle_focus_between_editor_and_prompt(self) -> None:
        if self.controller.state.active_dialog is not None:
            return
        if self.application.layout.has_focus(self.editor):
            if self.controller.has_transcript() and self.controller.state.transcript_open:
                self._transcript_view.ensure_selection()
                self.application.layout.focus(self._transcript_view.window)
                return
            self.application.layout.focus(self.prompt_field)
            return
        if self.application.layout.has_focus(self._transcript_view.window):
            self.application.layout.focus(self.prompt_field)
            return
        if self.application.layout.has_focus(self.prompt_field):
            if self._transcript_fills_editor_area():
                self._transcript_view.ensure_selection()
                self.application.layout.focus(self._transcript_view.window)
                return
            self.application.layout.focus(self.editor)
            return
        self.application.layout.focus(self.editor)

    def _editing_text_area_has_focus(self) -> bool:
        if self.controller.state.active_dialog is not None:
            return False
        if self.controller.state.web_mode != "idle":
            return False
        return (
            self.application.layout.has_focus(self.editor)
            or self.application.layout.has_focus(self.prompt_field)
        )

    def _editing_text_area_has_selection(self) -> bool:
        if not self._editing_text_area_has_focus():
            return False
        area = self._active_text_area()
        return area is not None and area.buffer.selection_state is not None

    def _transcript_has_focus(self) -> bool:
        if self.controller.state.active_dialog is not None:
            return False
        if self.controller.state.web_mode != "idle":
            return False
        return self.application.layout.has_focus(self._transcript_view.window)

    def _transcript_fills_editor_area(self) -> bool:
        return (
            self.controller.has_transcript()
            and self.controller.state.transcript_open
            and self.controller.transcript_view_state.maximized
        )

    def _in_model_picker(self) -> bool:
        return self.controller.state.active_dialog == "model_picker"

    def _in_permission_prompt(self) -> bool:
        return self.controller.state.active_dialog == "permission_prompt"

    def _in_web_mode(self) -> bool:
        return self.controller.state.web_mode != "idle"

    def _in_web_results(self) -> bool:
        return self.controller.state.web_mode == "results"

    def _active_text_area(self) -> TextArea | None:
        if self.application.layout.has_focus(self.editor):
            return self.editor
        if self.application.layout.has_focus(self.prompt_field):
            return self.prompt_field
        return None

    def _indent_active_text_area(self) -> None:
        buffer = self._active_text_buffer()
        if buffer is None:
            return
        if self._active_text_area_is_read_only():
            return
        self._reset_undo_coalescing_for_buffer(buffer)
        buffer.save_to_undo_stack()
        document = buffer.document
        updated_text = (
            buffer.text[:document.cursor_position]
            + self._INDENT_TEXT
            + buffer.text[document.cursor_position:]
        )
        buffer.set_document(
            Document(
                text=updated_text,
                cursor_position=document.cursor_position + len(self._INDENT_TEXT),
                selection=document.selection,
            ),
            bypass_readonly=True,
        )

    def _unindent_active_text_area(self) -> None:
        buffer = self._active_text_buffer()
        if buffer is None:
            return
        if self._active_text_area_is_read_only():
            return

        document = buffer.document
        row = document.cursor_position_row
        col = document.cursor_position_col
        line = document.current_line
        remove_count = 0
        if line.startswith("\t"):
            remove_count = 1
        else:
            leading_spaces = len(line) - len(line.lstrip(" "))
            remove_count = min(4, leading_spaces)
        if remove_count == 0:
            return

        self._reset_undo_coalescing_for_buffer(buffer)
        buffer.save_to_undo_stack()
        lines = buffer.text.splitlines(keepends=True)
        current = lines[row]
        stripped = current.rstrip("\r\n")
        newline = current[len(stripped):]
        lines[row] = stripped[remove_count:] + newline
        updated_text = "".join(lines)
        new_col = max(0, col - remove_count)
        new_cursor = _cursor_position_for_row_col(updated_text, row, new_col)
        buffer.set_document(
            Document(text=updated_text, cursor_position=new_cursor),
            bypass_readonly=True,
        )

    def _open_file_menu(self) -> None:
        self._sync_file_radio()
        self.controller.open_file_menu()
        self.application.layout.focus(self._file_radio)

    def _open_model_picker(self) -> None:
        if self.controller.state.web_mode != "idle" or self.controller.state.run_in_progress:
            return
        self.controller.open_model_picker()
        self.application.layout.focus(self._model_picker_view.window)

    def _select_model_from_picker(self) -> None:
        idx = self.controller.state.dialog_selection_index
        self.controller.state.selected_model_index = idx
        selected = self.controller.state.selected_model
        _save_tui_model_pref(selected.provider_name, selected.model, selected.profile_id)
        self.controller._set_status(f"Selected model: {selected.label}.")
        self.controller.close_dialog()
        self.application.layout.focus(self.prompt_field)

    def _background(self, coroutine) -> None:
        self.application.create_background_task(coroutine)

    def _active_text_buffer(self):
        if self.application.layout.has_focus(self.editor):
            return self.editor.buffer
        if self.application.layout.has_focus(self.prompt_field):
            return self.prompt_field.buffer
        return None

    def _move_active_text_area_visual(self, direction: int) -> None:
        text_area = self._active_text_area()
        if text_area is None:
            return

        new_position = _visual_cursor_position_for_wrapped_move(
            text_area=text_area,
            direction=direction,
        )
        if new_position is not None:
            text_area.buffer.cursor_position = new_position
            return

        if direction < 0:
            text_area.buffer.cursor_up(count=1)
        else:
            text_area.buffer.cursor_down(count=1)

    def _active_text_area_is_read_only(self) -> bool:
        if self.application.layout.has_focus(self.editor):
            return self.controller.editor_is_read_only()
        if self.application.layout.has_focus(self.prompt_field):
            return self.controller.state.run_in_progress
        return True

    def _coalesce_buffer_undo_history(self, buffer) -> None:
        key = id(buffer)
        current_text = buffer.text
        if self._suspend_undo_coalescing or self._buffer_change_is_controller_sync(buffer):
            self._reset_undo_coalescing_for_buffer(buffer, text=current_text)
            return

        previous_text = self._last_text_by_buffer.get(key, current_text)
        action = _classify_text_change(previous_text, current_text)
        now = time.monotonic()
        if (
            action in {"insert", "delete"}
            and self._last_edit_action_by_buffer.get(key) == action
            and (last_at := self._last_edit_at_by_buffer.get(key)) is not None
            and (now - last_at) <= self._UNDO_GROUP_WINDOW_SECONDS
            and buffer._undo_stack
        ):
            buffer._undo_stack.pop()
        self._last_edit_action_by_buffer[key] = action or "other"
        self._last_edit_at_by_buffer[key] = now
        self._last_text_by_buffer[key] = current_text

    def _buffer_change_is_controller_sync(self, buffer) -> bool:
        if buffer is self.editor.buffer:
            return self.controller._syncing_editor
        if buffer is self.prompt_field.buffer:
            return self.controller._syncing_prompt
        return False

    def _reset_undo_coalescing_for_buffer(self, buffer, *, text: str | None = None) -> None:
        key = id(buffer)
        self._last_edit_action_by_buffer.pop(key, None)
        self._last_edit_at_by_buffer.pop(key, None)
        self._last_text_by_buffer[key] = buffer.text if text is None else text

    def _undo_active_text_area(self) -> None:
        buffer = self._active_text_buffer()
        if buffer is None or self._active_text_area_is_read_only():
            return
        self._suspend_undo_coalescing = True
        try:
            buffer.undo()
        finally:
            self._suspend_undo_coalescing = False
        self._reset_undo_coalescing_for_buffer(buffer)

    def _redo_active_text_area(self) -> None:
        buffer = self._active_text_buffer()
        if buffer is None or self._active_text_area_is_read_only():
            return
        self._suspend_undo_coalescing = True
        try:
            buffer.redo()
        finally:
            self._suspend_undo_coalescing = False
        self._reset_undo_coalescing_for_buffer(buffer)

    def _control_window(self, text_getter, callback) -> Window:
        return Window(
            FormattedTextControl(
                text=text_getter,
                focusable=True,
                show_cursor=False,
            ),
            height=1,
            dont_extend_width=True,
        )

    def _top_bar_fragments(self):
        return [("class:topbar.title", f" {self.controller.active_file_label} ", self._title_mouse_handler)]

    def _closed_transcript_fragments(self):
        def _toggle(mouse_event):
            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                self.controller.toggle_transcript_open()
        return [("class:transcript.filter", "[ ^ ] Open Transcript", _toggle)]

    def _indicator_fragments(self):
        style = "class:indicator.error" if self.controller.state.indicator_kind == "error" else "class:indicator.status"
        return [(style, self.controller.state.indicator_message)]

    def _send_control_fragments(self):
        style = (
            "class:control.send.disabled"
            if self.controller.state.run_in_progress
            else "class:control.send"
        )
        return [(style, "[↑]", lambda event: self._fragment_click(event, self._send_background))]

    def _mode_control_fragments(self):
        if self.controller.state.web_mode != "idle":
            return [("class:control.disabled", f"[ Mode: {self.controller.state.mode} ]")]
        return [("class:control", f"[ Mode: {self.controller.state.mode} ]", lambda event: self._fragment_click(event, self._toggle_mode_background))]

    def _work_control_fragments(self):
        if self.controller.state.web_mode != "idle":
            return [("class:control.disabled", f"[ Work: {self.controller.state.work_mode} ]")]
        return [("class:control", f"[ Work: {self.controller.state.work_mode} ]", lambda event: self._fragment_click(event, self.controller.toggle_work_mode))]

    def _model_control_fragments(self):
        if self.controller.state.web_mode != "idle":
            return [("class:control.disabled", f"[ {self.controller.state.selected_model.label} ]")]
        return [("class:control", f"[ {self.controller.state.selected_model.label} ]", lambda event: self._fragment_click(event, self._open_model_picker))]

    def _fragment_click(self, mouse_event, callback) -> None:
        if mouse_event.event_type != MouseEventType.MOUSE_UP:
            return
        result = callback()
        if asyncio.iscoroutine(result):
            self._background(result)

    def _dialog_container(self):
        dialog = self.controller.state.active_dialog
        if dialog == "file_menu":
            return self._file_menu
        if dialog == "file_switch_confirm":
            return self._build_file_switch_dialog()
        if dialog == "reload_confirm":
            return self._build_reload_confirm_dialog()
        if dialog == "note_conflict":
            return self._build_note_conflict_dialog()
        return Window(height=0)

    def _build_file_menu_dialog(self):
        return Dialog(
            title="Open File",
            body=Box(self._file_radio, padding=1),
            buttons=[
                Button(text="Open", handler=lambda: self._background(self.controller.request_file_switch(self._file_radio.current_value))),
                Button(text="Cancel", handler=self.controller.close_dialog),
            ],
        )

    def _build_file_switch_dialog(self):
        pending = self.controller.state.pending_switch_path
        body = Label(text=f"Save changes before switching to {pending.name if pending else 'that file'}?")
        return Dialog(
            title="Unsaved Changes",
            body=Box(body, padding=1),
            buttons=[
                Button(
                    text="Save",
                    handler=lambda: self._background(self.controller.confirm_file_switch(save_changes=True)),
                ),
                Button(
                    text="Don't Save",
                    handler=lambda: self._background(self.controller.confirm_file_switch(save_changes=False)),
                ),
                Button(text="Cancel", handler=self.controller.close_dialog),
            ],
        )

    def _build_reload_confirm_dialog(self):
        body = Label(text="The active file changed on disk. Reload it or keep your local version?")
        return Dialog(
            title="External File Change",
            body=Box(body, padding=1),
            buttons=[
                Button(
                    text="Reload",
                    handler=lambda: self._background(self.controller.confirm_external_reload(reload_file=True)),
                ),
                Button(
                    text="Ignore",
                    handler=lambda: self._background(self.controller.confirm_external_reload(reload_file=False)),
                ),
            ],
        )

    def _build_note_conflict_dialog(self):
        conflict = self.controller.state.note_conflict
        tool_name = conflict.tool_name if conflict is not None else "note_write"
        body = Label(
            text=(
                "Your note changed while the model was running.\n\n"
                f"The model finished with {tool_name}. Choose whether the model update or your edits should win.\n"
                "The discarded version will be backed up under .aunic/conflicts."
            )
        )
        return Dialog(
            title="Note Conflict",
            body=Box(body, padding=1),
            buttons=[
                Button(
                    text="Model Wins",
                    handler=lambda: self._background(self.controller.confirm_note_conflict(prefer_model=True)),
                ),
                Button(
                    text="User Wins",
                    handler=lambda: self._background(self.controller.confirm_note_conflict(prefer_model=False)),
                ),
            ],
        )

    def _sync_file_radio(self) -> None:
        self._file_radio.values = [
            (path, path.name)
            for path in self.controller.state.available_files
        ]
        self._file_radio.current_value = self.controller.state.active_file


def _mouse(callback):
    def handler(mouse_event) -> None:
        if mouse_event.event_type == MouseEventType.MOUSE_UP:
            callback()

    return handler


class PermissionPromptView:
    _OPTIONS = (
        ("once", "Once"),
        ("always", "Always"),
        ("reject", "Reject"),
    )

    def __init__(self, controller: TuiController) -> None:
        self._controller = controller
        self.window = Window(
            FormattedTextControl(
                text=self._render,
                focusable=True,
                show_cursor=False,
            ),
            height=Dimension(preferred=8, max=12, min=5),
            dont_extend_height=True,
        )

    def _render(self):
        controller = self._controller
        prompt = controller.state.permission_prompt
        cursor = controller.state.dialog_selection_index
        fragments = []
        if prompt is None:
            return [("", "A tool is requesting permission.\n")]

        fragments.append(("class:control.active", "Tool Permission\n"))
        fragments.append(("", f"{prompt.message}\n"))
        fragments.append(("class:transcript.tool.name", f"Tool: {prompt.tool_name}\n"))
        fragments.append(("class:transcript.tool.content", f"Target: {prompt.target}\n\n"))

        for index, (value, label) in enumerate(self._OPTIONS):
            is_focused = index == cursor
            style = "class:control.active" if is_focused else ""
            indicator = "(*)" if is_focused else "( )"
            fragments.append((style, "  ", _mouse(lambda idx=index: self._set_cursor(idx))))
            fragments.append((style, indicator, _mouse(lambda idx=index: self._set_cursor(idx))))
            fragments.append((style, f" {label}", _mouse(lambda choice=value: controller.resolve_permission_prompt(choice))))
            if index < len(self._OPTIONS) - 1:
                fragments.append(("", "\n"))
        return fragments

    def _set_cursor(self, index: int) -> None:
        self._controller.state.dialog_selection_index = index
        self._controller._invalidate()


class _ContextSeparatorWindow(Window):
    """Separator line that fills left-to-right in blue proportional to context window fill."""

    def __init__(self, controller: TuiController) -> None:
        self._controller = controller
        self._width = 1
        super().__init__(
            FormattedTextControl(text=self._render, focusable=False, show_cursor=False),
            height=1,
        )

    def _render(self) -> StyleAndTextTuples:
        w = max(1, self._width)
        fill = self._controller.context_fill_fraction
        if fill is None:
            return [("", "─" * w)]
        filled = max(0, min(w, round(fill * w)))
        if fill < 0.5:
            color = "ansigreen bold"
        elif fill < 0.75:
            color = "ansiyellow bold"
        else:
            color = "ansired bold"
        fragments: StyleAndTextTuples = []
        if filled > 0:
            fragments.append((color, "─" * filled))
        if filled < w:
            fragments.append(("", "─" * (w - filled)))
        return fragments

    def write_to_screen(self, screen, mouse_handlers, write_position,
                        parent_style, erase_bg, z_index) -> None:
        self._width = write_position.width
        super().write_to_screen(screen, mouse_handlers, write_position,
                                parent_style, erase_bg, z_index)


class ModelPickerView:
    def __init__(self, controller: TuiController, *, select: Callable[[], None]) -> None:
        self._controller = controller
        self._select = select
        self._scroll_pos = 0
        self._pending_scroll = False
        self.window = Window(
            FormattedTextControl(
                text=self._render,
                focusable=True,
                show_cursor=False,
                get_cursor_position=lambda: Point(0, self._scroll_pos),
            ),
            height=Dimension(preferred=5, max=20, min=3),
            dont_extend_height=True,
            wrap_lines=False,
            right_margins=[ScrollbarMargin(display_arrows=False)],
            scroll_offsets=ScrollOffsets(top=0, bottom=0),
            get_vertical_scroll=lambda w: self._scroll_pos,
        )

    def on_cursor_moved(self) -> None:
        self._pending_scroll = True

    def _render(self):
        c = self._controller
        options = c.state.model_options
        cursor = c.state.dialog_selection_index
        selected = c.state.selected_model_index

        if self._pending_scroll:
            self._pending_scroll = False
            render_info = self.window.render_info
            visible_height = render_info.window_height if render_info is not None else 5
            if cursor < self._scroll_pos:
                self._scroll_pos = cursor
            elif cursor >= self._scroll_pos + visible_height:
                self._scroll_pos = cursor - visible_height + 1

        max_scroll = max(0, len(options) - 3)
        self._scroll_pos = max(0, min(self._scroll_pos, max_scroll))

        fragments = []
        for i, option in enumerate(options):
            is_focused = i == cursor
            is_selected = i == selected
            row_style = "class:control.active" if is_focused else ""
            indicator = "(*)" if is_selected else "( )"
            ind_style = "class:model.selected" if is_selected else ""
            if ind_style and row_style:
                combined = f"{row_style} {ind_style}"
            elif ind_style:
                combined = ind_style
            else:
                combined = row_style

            idx = i

            def _click(mouse_event, _idx=idx) -> None:
                if mouse_event.event_type == MouseEventType.MOUSE_UP:
                    c.state.dialog_selection_index = _idx
                    self._select()

            fragments.append((row_style, "  ", _click))
            fragments.append((combined, indicator, _click))
            fragments.append((row_style, f" {option.label}\n", _click))
        return fragments


_TUI_PREFS_PATH = Path.home() / ".aunic" / "tui_prefs.json"


_MAX_FILE_STATE_ENTRIES = 100


def _read_tui_prefs() -> dict:
    try:
        return json.loads(_TUI_PREFS_PATH.read_text())
    except Exception:
        return {}


def _write_tui_prefs(data: dict) -> None:
    try:
        _TUI_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TUI_PREFS_PATH.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def _load_tui_model_pref() -> tuple[str, str | None, str | None] | None:
    data = _read_tui_prefs()
    provider = data.get("provider")
    model = data.get("model")
    profile_id = data.get("profile_id")
    normalized_model = model if isinstance(model, str) else None
    normalized_profile_id = profile_id if isinstance(profile_id, str) else None
    if isinstance(provider, str):
        return provider, normalized_model, normalized_profile_id
    return None


def _save_tui_model_pref(provider: str, model: str, profile_id: str | None = None) -> None:
    data = _read_tui_prefs()
    data["provider"] = provider
    data["model"] = model
    if profile_id is None:
        data.pop("profile_id", None)
    else:
        data["profile_id"] = profile_id
    _write_tui_prefs(data)


def _load_tui_file_state(file_path: Path) -> dict:
    data = _read_tui_prefs()
    file_state = data.get("file_state")
    if not isinstance(file_state, dict):
        return {}
    key = str(file_path.resolve())
    entry = file_state.get(key)
    return entry if isinstance(entry, dict) else {}


def _save_tui_file_state(file_path: Path, state: dict) -> None:
    data = _read_tui_prefs()
    file_state = data.get("file_state")
    if not isinstance(file_state, dict):
        file_state = {}
    key = str(file_path.resolve())
    file_state[key] = state
    if len(file_state) > _MAX_FILE_STATE_ENTRIES:
        keys = list(file_state)
        for old_key in keys[: len(keys) - _MAX_FILE_STATE_ENTRIES]:
            del file_state[old_key]
    data["file_state"] = file_state
    _write_tui_prefs(data)


async def run_tui(
    *,
    active_file: Path,
    included_files: tuple[Path, ...] = (),
    initial_provider: str = "codex",
    initial_model: str | None = None,
    initial_profile_id: str | None = None,
    reasoning_effort=None,
    display_root: Path | None = None,
    initial_mode: str = "note",
    cwd: Path | None = None,
    allow_missing_active_file: bool = False,
    create_missing_parents_on_save: bool = False,
    file_manager: FileManager | None = None,
    note_runner: NoteModeRunner | None = None,
    chat_runner: ChatModeRunner | None = None,
    input: Input | None = None,
    output: Output | None = None,
) -> int:
    if initial_provider == "codex" and initial_model is None and initial_profile_id is None:
        saved = _load_tui_model_pref()
        if saved:
            initial_provider, initial_model, initial_profile_id = saved
    app = AunicTuiApp(
        active_file=active_file,
        included_files=included_files,
        initial_provider=initial_provider,
        initial_model=initial_model,
        initial_profile_id=initial_profile_id,
        reasoning_effort=reasoning_effort,
        display_root=display_root,
        initial_mode=initial_mode,
        cwd=cwd,
        allow_missing_active_file=allow_missing_active_file,
        create_missing_parents_on_save=create_missing_parents_on_save,
        file_manager=file_manager,
        note_runner=note_runner,
        chat_runner=chat_runner,
        input=input,
        output=output,
    )
    return await app.run()


def _cursor_position_for_row_col(text: str, row: int, col: int) -> int:
    lines = text.splitlines(keepends=True)
    if not lines:
        return 0
    clamped_row = min(max(row, 0), len(lines) - 1)
    position = sum(len(line) for line in lines[:clamped_row])
    return min(position + col, position + len(lines[clamped_row].rstrip("\r\n")))


def _classify_text_change(previous_text: str, current_text: str) -> str | None:
    if previous_text == current_text:
        return None

    prefix = 0
    max_prefix = min(len(previous_text), len(current_text))
    while prefix < max_prefix and previous_text[prefix] == current_text[prefix]:
        prefix += 1

    previous_suffix_index = len(previous_text)
    current_suffix_index = len(current_text)
    while (
        previous_suffix_index > prefix
        and current_suffix_index > prefix
        and previous_text[previous_suffix_index - 1] == current_text[current_suffix_index - 1]
    ):
        previous_suffix_index -= 1
        current_suffix_index -= 1

    removed = previous_text[prefix:previous_suffix_index]
    added = current_text[prefix:current_suffix_index]

    if added and not removed:
        return "insert"
    if removed and not added:
        return "delete"
    return "other"


def _line_is_in_fenced_code_block(lines: list[str], line_number: int) -> bool:
    in_code = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            if index == line_number:
                return True
            in_code = not in_code
            continue
        if index == line_number:
            return in_code
    return False


def _visual_cursor_position_for_wrapped_move(
    *,
    text_area: TextArea,
    direction: int,
) -> int | None:
    render_info = text_area.window.render_info
    if render_info is None or not render_info.wrap_lines:
        return None

    control = text_area.control
    get_processed_line = getattr(control, "_last_get_processed_line", None)
    rowcol_to_yx = getattr(render_info, "_rowcol_to_yx", None)
    x_offset = getattr(render_info, "_x_offset", 0)
    y_offset = getattr(render_info, "_y_offset", 0)

    if get_processed_line is None or rowcol_to_yx is None:
        return None

    target_y = render_info.cursor_position.y + direction
    current_x = render_info.cursor_position.x
    best_match: tuple[int, int, int] | None = None

    for (row, display_col), (absolute_y, absolute_x) in rowcol_to_yx.items():
        relative_y = absolute_y - y_offset
        if relative_y != target_y:
            continue
        relative_x = absolute_x - x_offset
        distance = abs(relative_x - current_x)
        candidate = (distance, row, display_col)
        if best_match is None or candidate < best_match:
            best_match = candidate

    if best_match is None:
        return None

    _, display_row, display_col = best_match
    source_row, source_col = _display_to_source_position(text_area.control, display_row, display_col)
    return text_area.buffer.document.translate_row_col_to_index(source_row, source_col)


def _visual_home_end_position(*, text_area: TextArea, go_end: bool) -> int | None:
    render_info = text_area.window.render_info
    if render_info is None or not render_info.wrap_lines:
        return None

    control = text_area.control
    get_processed_line = getattr(control, "_last_get_processed_line", None)
    visible_line_to_row_col = getattr(render_info, "visible_line_to_row_col", None)

    if get_processed_line is None or visible_line_to_row_col is None:
        return None

    current_visible_line = render_info.cursor_position.y
    start = visible_line_to_row_col.get(current_visible_line)
    if start is None:
        return None

    display_row, display_col = start
    source_row = _source_row_for_display_row(control, display_row)
    processed_line = get_processed_line(source_row)

    if not go_end:
        _, source_col = _display_to_source_position(control, display_row, display_col)
        return text_area.buffer.document.translate_row_col_to_index(source_row, source_col)

    next_start = visible_line_to_row_col.get(current_visible_line + 1)
    if next_start is not None and _source_row_for_display_row(control, next_start[0]) == source_row:
        next_display_col = next_start[1]
        _, source_col = _display_to_source_position(control, next_start[0], next_display_col)
        return text_area.buffer.document.translate_row_col_to_index(source_row, source_col)

    source_col = len(text_area.buffer.document.lines[source_row])
    return text_area.buffer.document.translate_row_col_to_index(source_row, source_col)


def _source_row_for_display_row(control, display_row: int) -> int:
    mapper = getattr(control, "display_row_to_source_row", None)
    if callable(mapper):
        return mapper(display_row)
    return display_row


def _display_to_source_position(control, display_row: int, display_col: int) -> tuple[int, int]:
    mapper = getattr(control, "display_to_source_position", None)
    if callable(mapper):
        return mapper(display_row, display_col)
    get_processed_line = getattr(control, "_last_get_processed_line", None)
    if get_processed_line is None:
        return display_row, display_col
    processed_line = get_processed_line(display_row)
    return display_row, processed_line.display_to_source(display_col)


def _dimension_value(value, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, Dimension):
        if value.preferred is not None:
            return value.preferred
        if value.max is not None:
            return value.max
        if value.min is not None:
            return value.min
    if value is None:
        return default
    window = getattr(value, "window", None)
    if window is not None:
        return _dimension_value(getattr(window, "height", None), default)
    return _dimension_value(getattr(value, "height", None), default) if hasattr(value, "height") else default
