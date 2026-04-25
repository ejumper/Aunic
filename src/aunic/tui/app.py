from __future__ import annotations

import asyncio
import contextlib
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
from aunic.file_ui_state import (
    load_file_ui_state,
    load_project_include_state,
    save_file_ui_state,
    serialize_include_entries,
)
from aunic.modes import ChatModeRunner, NoteModeRunner
from aunic.tui.controller import TuiController
from aunic.tui.types import IncludeEntry, SleepStatusState
from aunic.tui.web_search_view import WebSearchView
from aunic.tui.transcript_view import TranscriptView
from aunic.tui.note_tables import NoteTablePreviewBufferControl
from aunic.tui.controller import _open_url_focused
from aunic.tui.rendering import (
    AunicMarkdownLexer,
    MarkdownLinkProcessor,
    PromptLexer,
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
        file_ui_state = load_file_ui_state(active_file)
        if isinstance(file_ui_state.get("transcript_open"), bool):
            self.controller.state.transcript_open = file_ui_state["transcript_open"]
        if isinstance(file_ui_state.get("transcript_maximized"), bool):
            self.controller.transcript_view_state.maximized = file_ui_state["transcript_maximized"]
        if isinstance(file_ui_state.get("mode"), str):
            self.controller.state.mode = file_ui_state["mode"]  # type: ignore[assignment]
        if isinstance(file_ui_state.get("work_mode"), str):
            self.controller.state.work_mode = file_ui_state["work_mode"]  # type: ignore[assignment]
        project_state = load_project_include_state(active_file)
        self.controller.state.include_entries = list(project_state.include_entries)
        self.controller.state.include_inactive_children = project_state.inactive_children
        if project_state.include_entries:
            self.controller._rebuild_available_files()

        def _save_file_ui_state(path: Path) -> None:
            save_file_ui_state(
                path,
                {
                    "transcript_open": self.controller.state.transcript_open,
                    "transcript_maximized": self.controller.transcript_view_state.maximized,
                    "mode": self.controller.state.mode,
                    "work_mode": self.controller.state.work_mode,
                    "includes": serialize_include_entries(self.controller.state.include_entries),
                    "project_inactive_children": list(self.controller.state.include_inactive_children),
                },
            )

        def _on_file_switched(new_path: Path) -> None:
            new_state = load_file_ui_state(new_path)
            self.controller.state.transcript_open = bool(new_state.get("transcript_open", True))
            self.controller.transcript_view_state.maximized = bool(new_state.get("transcript_maximized", False))
            if isinstance(new_state.get("mode"), str):
                self.controller.state.mode = new_state["mode"]  # type: ignore[assignment]
            if isinstance(new_state.get("work_mode"), str):
                self.controller.state.work_mode = new_state["work_mode"]  # type: ignore[assignment]
            project_state = load_project_include_state(new_path)
            self.controller.state.include_entries = list(project_state.include_entries)
            self.controller.state.include_inactive_children = project_state.inactive_children
            self.controller._rebuild_available_files()

        self.controller._on_transcript_open_changed = lambda _open_state: _save_file_ui_state(self.controller.state.context_file or self.controller.state.active_file)
        self.controller._on_transcript_maximized_changed = lambda _maximized: _save_file_ui_state(self.controller.state.context_file or self.controller.state.active_file)
        self.controller._on_includes_changed = lambda: _save_file_ui_state(self.controller.state.context_file or self.controller.state.active_file)
        self.controller._on_mode_changed = lambda: _save_file_ui_state(self.controller.state.context_file or self.controller.state.active_file)
        self.controller._on_file_switched = _on_file_switched

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

            lexer=PromptLexer(),
        )
        self.prompt_field.window.right_margins = [ScrollbarMargin(display_arrows=False)]
        self.find_field = TextArea(
            multiline=False,
            wrap_lines=False,
            focus_on_click=True,
        )
        self.replace_field = TextArea(
            multiline=False,
            wrap_lines=False,
            focus_on_click=True,
        )
        self.find_controls_window = Window(
            FormattedTextControl(
                text=self._find_controls_fragments,
                focusable=True,
                show_cursor=False,
            ),
            height=1,
        )
        self.controller.attach_buffers(
            editor_buffer=self.editor.buffer,
            prompt_buffer=self.prompt_field.buffer,
        )
        self.editor.buffer.on_text_changed += self._coalesce_buffer_undo_history
        self.prompt_field.buffer.on_text_changed += self._coalesce_buffer_undo_history
        self.find_field.buffer.on_text_changed += self._coalesce_buffer_undo_history
        self.replace_field.buffer.on_text_changed += self._coalesce_buffer_undo_history
        self.find_field.buffer.on_text_changed += self._handle_find_buffer_changed
        self.replace_field.buffer.on_text_changed += self._handle_replace_buffer_changed
        original_editor_control = self.editor.control
        self.editor.control = NoteTablePreviewBufferControl(
            buffer=self.editor.buffer,
            lexer=original_editor_control.lexer,
            input_processors=[
                ThematicBreakProcessor(width=lambda: self._editor_width()),
                RecentChangeProcessor(spans=self.controller.recent_display_change_spans),
                RecentChangeProcessor(
                    spans=self.controller.model_insert_display_change_spans,
                    style="class:md.model_insert",
                ),
                MarkdownLinkProcessor(
                    open_target=_open_url_focused,
                    active_file=lambda: self.controller.state.display_file
                    or self.controller.state.context_file
                    or self.controller.state.active_file,
                ),
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
            [(self.controller.state.context_file or self.controller.state.active_file, (self.controller.state.context_file or self.controller.state.active_file).name)]
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
        self._attachment_control_window = self._control_window(
            self._attachment_control_fragments,
            lambda: self._background(self.controller.attach_prompt_images_via_picker()),
        )
        self._model_control_window = self._control_window(self._model_control_fragments, self._open_model_picker)
        self._work_control_window = self._control_window(self._work_control_fragments, self.controller.toggle_work_mode)
        self._mode_control_window = self._control_window(self._mode_control_fragments, self._toggle_mode_background)
        self._send_control_window = self._control_window(self._send_control_fragments, self._send_background)
        self.control_row = DynamicContainer(self._control_row_body)
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
        self.application.ttimeoutlen = 0.05
        self.application.pre_run_callables.append(self._invalidate)
        self.controller.set_invalidator(self._invalidate)
        set_title("Aunic")
        self._last_edit_action_by_buffer: dict[int, str] = {}
        self._last_edit_at_by_buffer: dict[int, float] = {}
        self._last_text_by_buffer: dict[int, str] = {
            id(self.editor.buffer): self.editor.buffer.text,
            id(self.prompt_field.buffer): self.prompt_field.buffer.text,
            id(self.find_field.buffer): self.find_field.buffer.text,
            id(self.replace_field.buffer): self.replace_field.buffer.text,
        }
        self._suspend_undo_coalescing = False
        self._syncing_find = False
        self._syncing_replace = False
        self._sleep_refresh_task: asyncio.Task[None] | None = None

    async def run(self) -> int:
        await self.controller.initialize()
        await self.controller.start_watch_task()
        self._sleep_refresh_task = asyncio.create_task(self._sleep_refresh_loop())
        self._refresh_dimensions()
        try:
            await self.application.run_async()
        finally:
            if self._sleep_refresh_task is not None:
                self._sleep_refresh_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._sleep_refresh_task
                self._sleep_refresh_task = None
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
            if self.controller.state.find_ui.active:
                return
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
        @bindings.add("c-e", eager=True)
        def _toggle_focus(_event) -> None:
            self._toggle_focus_between_editor_and_prompt()

        @bindings.add("escape", "e", eager=True)
        def _focus_transcript(_event) -> None:
            if self.controller.state.active_dialog is not None:
                return
            if not self.controller.has_transcript():
                return
            if not self.controller.state.transcript_open:
                self.controller.toggle_transcript_open()
            self._transcript_view.ensure_selection()
            self._transcript_view._cursor_col = "delete"
            self._transcript_view._toolbar_focused = False
            self.application.layout.focus(self._transcript_view.window)

        @bindings.add("escape", eager=True, filter=Condition(self._sleeping))
        @bindings.add("c-c", eager=True, filter=Condition(self._sleeping))
        def _wake_sleep(_event) -> None:
            self.controller.force_stop_run()

        @bindings.add("c-f", eager=True, filter=Condition(self._can_toggle_find_ui))
        def _open_find(_event) -> None:
            self._handle_find_shortcut()

        @bindings.add("up", eager=True, filter=Condition(lambda: self._editing_text_area_has_focus() and not self._find_text_field_has_focus()))
        def _move_visual_up(_event) -> None:
            self._move_active_text_area_visual(-1)

        @bindings.add("down", eager=True, filter=Condition(lambda: self._editing_text_area_has_focus() and not self._find_text_field_has_focus()))
        def _move_visual_down(_event) -> None:
            self._move_active_text_area_visual(1)

        @bindings.add("c-m", eager=True, filter=Condition(self._find_field_has_focus))
        @bindings.add("enter", eager=True, filter=Condition(self._find_field_has_focus))
        def _find_next(_event) -> None:
            self.controller.find_next_match()

        @bindings.add("up", eager=True, filter=Condition(self._find_text_field_has_focus))
        def _find_prev(_event) -> None:
            self.controller.find_previous_match()

        @bindings.add("down", eager=True, filter=Condition(self._find_text_field_has_focus))
        def _find_next_from_arrow(_event) -> None:
            self.controller.find_next_match()

        @bindings.add("c-m", eager=True, filter=Condition(self._replace_field_has_focus))
        @bindings.add("enter", eager=True, filter=Condition(self._replace_field_has_focus))
        def _replace_current(_event) -> None:
            self._replace_current_find_match()

        @bindings.add("c-i", eager=True, filter=Condition(self._find_ui_active))
        @bindings.add("tab", eager=True, filter=Condition(self._find_ui_active))
        def _find_cycle_forward(_event) -> None:
            self._cycle_find_focus(forward=True)

        @bindings.add("s-tab", eager=True, filter=Condition(self._find_ui_active))
        def _find_cycle_backward(_event) -> None:
            self._cycle_find_focus(forward=False)

        @bindings.add("left", eager=True, filter=Condition(self._find_buttons_have_focus))
        def _find_button_left(_event) -> None:
            self._move_find_button_selection(-1)

        @bindings.add("right", eager=True, filter=Condition(self._find_buttons_have_focus))
        def _find_button_right(_event) -> None:
            self._move_find_button_selection(1)

        @bindings.add("c-m", eager=True, filter=Condition(self._find_buttons_have_focus))
        @bindings.add("enter", eager=True, filter=Condition(self._find_buttons_have_focus))
        def _find_button_activate(_event) -> None:
            self._activate_selected_find_button()

        @bindings.add("tab", eager=True, filter=Condition(lambda: self._editing_text_area_has_focus() and not self._find_ui_active()))
        def _indent(_event) -> None:
            self._indent_active_text_area()

        @bindings.add("s-tab", eager=True, filter=Condition(lambda: self._editing_text_area_has_focus() and not self._find_ui_active()))
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

        @bindings.add("escape", eager=True, filter=Condition(self._find_ui_control_has_focus))
        def _close_find(_event) -> None:
            self.controller.close_find_ui()

        @bindings.add("escape")
        def _close_dialog(_event) -> None:
            self.controller.close_dialog()

        @bindings.add("escape", filter=Condition(lambda: self._find_ui_active() and not self._find_ui_control_has_focus()))
        def _close_find_from_editor(_event) -> None:
            self.controller.close_find_ui()

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

        @bindings.add("escape", "up", eager=True, filter=Condition(self._editing_text_area_has_focus))
        def _move_line_up(_event) -> None:
            self._move_line_active_text_area(-1)

        @bindings.add("escape", "down", eager=True, filter=Condition(self._editing_text_area_has_focus))
        def _move_line_down(_event) -> None:
            self._move_line_active_text_area(1)

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
            self._web_view.on_cursor_moved()

        @bindings.add("down", eager=True, filter=Condition(self._in_web_mode))
        def _web_down(_event) -> None:
            self.controller.web_move_cursor(1)
            self._web_view.on_cursor_moved()

        @bindings.add("left", eager=True, filter=Condition(self._in_web_results))
        @bindings.add("right", eager=True, filter=Condition(self._in_web_results))
        @bindings.add("left", eager=True, filter=Condition(self._in_web_chunks))
        @bindings.add("right", eager=True, filter=Condition(self._in_web_chunks))
        def _web_toggle_expand(_event) -> None:
            self.controller.web_toggle_expand()
            self._web_view.on_cursor_moved()

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
            self._transcript_view.move_up()

        @bindings.add("down", eager=True, filter=Condition(self._transcript_has_focus))
        def _transcript_down(_event) -> None:
            self._transcript_view.move_down()

        @bindings.add("left", eager=True, filter=Condition(self._transcript_has_focus))
        def _transcript_left(_event) -> None:
            self._transcript_view.move_left()

        @bindings.add("right", eager=True, filter=Condition(self._transcript_has_focus))
        def _transcript_right(_event) -> None:
            self._transcript_view.move_right()

        @bindings.add("enter", eager=True, filter=Condition(self._transcript_has_focus))
        def _transcript_activate(_event) -> None:
            self._transcript_view.activate()

        @bindings.add("home", eager=True, filter=Condition(self._transcript_has_focus))
        def _transcript_home(_event) -> None:
            self._transcript_view.go_home()

        @bindings.add("end", eager=True, filter=Condition(self._transcript_has_focus))
        def _transcript_end(_event) -> None:
            self._transcript_view.go_end()

        @bindings.add("delete", eager=True, filter=Condition(self._transcript_has_focus))
        @bindings.add("backspace", eager=True, filter=Condition(self._transcript_has_focus))
        def _transcript_delete(_event) -> None:
            self._background(self._transcript_view.delete_selected_row())

        @bindings.add("y", eager=True, filter=Condition(self._transcript_has_focus))
        def _transcript_copy(_event) -> None:
            self._transcript_view.copy_selected_row()

        return bindings

    def _invalidate(self) -> None:
        self._sync_find_buffers_from_state()
        self._refresh_dimensions()
        self._refresh_web_view_height()
        self._refresh_transcript_dimensions()
        self._update_web_focus()
        self._update_find_focus()
        self._update_dialog_focus()
        self._update_transcript_focus()
        self.application.invalidate()

    def _refresh_dimensions(self) -> None:
        self.prompt_field.window.height = self._compute_prompt_visual_height()
        self.find_field.window.height = 1
        self.replace_field.window.height = 1
        self.indicator_window.height = self.controller.indicator_height()

    def _compute_prompt_visual_height(self) -> int:
        render_info = self.prompt_field.window.render_info
        if render_info is not None:
            width = render_info.window_width
        else:
            try:
                width = self.application.output.get_size().columns - 4  # frame borders + scrollbar
            except Exception:
                return self.controller.prompt_height()
        if width <= 0:
            return self.controller.prompt_height()
        lines = self.prompt_field.buffer.document.text.split("\n")
        total_rows = sum(max(1, (len(line) + width - 1) // width) for line in lines)
        return max(1, min(10, total_rows))

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
        if self.controller.state.find_ui.active:
            return self._find_ui_container()
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
        elif not self.controller.state.find_ui.active:
            self.application.layout.focus(self.prompt_field)

    def _update_find_focus(self) -> None:
        state = self.controller.state.find_ui
        if not state.active:
            if (
                self.application.layout.has_focus(self.find_field)
                or self.application.layout.has_focus(self.replace_field)
                or self.application.layout.has_focus(self.find_controls_window)
            ):
                self.application.layout.focus(self.prompt_field)
            return
        if not self._find_ui_control_has_focus():
            if self.application.layout.has_focus(self.prompt_field):
                self._focus_find_active_field()
            return
        self._focus_find_active_field()

    def _focus_find_active_field(self) -> None:
        state = self.controller.state.find_ui
        if state.active_field == "buttons":
            if not self.application.layout.has_focus(self.find_controls_window):
                self.application.layout.focus(self.find_controls_window)
            return
        if state.active_field == "replace" and state.replace_mode:
            if not self.application.layout.has_focus(self.replace_field):
                self.application.layout.focus(self.replace_field)
            return
        if not self.application.layout.has_focus(self.find_field):
            self.application.layout.focus(self.find_field)

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
        if self.controller.state.find_ui.active:
            prompt_body_height = 1 + (1 if self.controller.state.find_ui.replace_mode else 0)
            return prompt_body_height + 1 + 1 + 2
        prompt_body_height = _dimension_value(self._prompt_area_body(), 3)
        return prompt_body_height + 1 + 1 + 2

    def _find_ui_container(self):
        lines = [self._find_input_row("find")]
        if self.controller.state.find_ui.replace_mode:
            lines.append(self._find_input_row("replace"))
        return HSplit(lines)

    def _find_input_row(self, field: str):
        label = "find: " if field == "find" else "replace: "
        area = self.find_field if field == "find" else self.replace_field
        return VSplit(
            [
                Window(
                    FormattedTextControl(
                        text=lambda label=label: [("class:prompt.find.label", label)]
                    ),
                    width=len(label),
                    dont_extend_width=True,
                    height=1,
                ),
                area,
            ]
        )

    def _control_row_body(self):
        if self.controller.state.find_ui.active:
            return self._find_control_row()
        return VSplit(
            [
                VSplit(
                    [
                        self._attachment_control_window,
                        self._model_control_window,
                        self._work_control_window,
                        self._mode_control_window,
                    ],
                    padding=1,
                ),
                Window(),
                self._send_control_window,
            ],
            padding=1,
        )

    def _find_control_row(self):
        return self.find_controls_window

    def _cycle_find_focus(self, *, forward: bool) -> None:
        state = self.controller.state.find_ui
        if not state.active:
            return
        order = ["find"]
        if state.replace_mode:
            order.append("replace")
        order.append("buttons")
        try:
            current_index = order.index(state.active_field)
        except ValueError:
            current_index = 0
        delta = 1 if forward else -1
        next_field = order[(current_index + delta) % len(order)]
        self.controller.set_find_active_field(next_field)

    def _handle_find_shortcut(self) -> None:
        state = self.controller.state.find_ui
        if not state.active:
            self.controller.open_find_ui()
            self.application.layout.focus(self.find_field)
            return
        if self._find_text_field_has_focus():
            self.controller.set_find_replace_mode(not state.replace_mode)
            self.application.layout.focus(self.find_field)
            return
        self.controller.set_find_active_field("find")
        self.application.layout.focus(self.find_field)

    def _find_button_specs(self) -> list[tuple[str, bool, Callable[[], object]]]:
        specs: list[tuple[str, bool, Callable[[], object]]] = [
            ("[ X ]", True, self._close_find_ui),
            ("[ Aa: on ]" if self.controller.state.find_ui.case_sensitive else "[ Aa: off ]", True, self.controller.toggle_find_case_sensitive),
            ("[ next ]", bool(self.controller.state.find_ui.find_text), self.controller.find_next_match),
            ("[ prev ]", bool(self.controller.state.find_ui.find_text), self.controller.find_previous_match),
        ]
        if self.controller.state.find_ui.replace_mode:
            specs.extend(
                [
                    ("[ repl. ]", bool(self.controller.state.find_ui.match_count), self._replace_current_find_match),
                    ("[ repl. all ]", bool(self.controller.state.find_ui.match_count), self._replace_all_find_matches),
                    ("[ find ]", True, self._open_find_mode),
                ]
            )
        else:
            specs.append(("[ replace ]", True, self._open_replace_mode))
        return specs

    def _find_controls_fragments(self):
        specs = self._find_button_specs()
        selected_index = self._valid_find_button_index(self.controller.state.find_ui.button_index, specs=specs) if specs else 0
        self.controller.state.find_ui.button_index = selected_index
        focused = self.controller.state.find_ui.active_field == "buttons"
        left_specs = specs[:-1]
        right_spec = specs[-1] if specs else None
        fragments: StyleAndTextTuples = []

        def _style_for(index: int, enabled: bool) -> str:
            if focused and index == selected_index and enabled:
                return "class:control.active"
            if not enabled:
                return "class:control.disabled"
            return "class:control"

        left_width = 0
        for index, (label, enabled, _callback) in enumerate(left_specs):
            if fragments:
                fragments.append(("", " "))
                left_width += 1
            fragments.append((_style_for(index, enabled), label, lambda event, index=index: self._click_find_button(event, index)))
            left_width += len(label)

        right_width = len(right_spec[0]) if right_spec is not None else 0
        row_width = self._find_controls_width()
        gap = max(1, row_width - left_width - right_width)
        fragments.append(("", " " * gap))
        if right_spec is not None:
            index = len(specs) - 1
            label, enabled, _callback = right_spec
            fragments.append((_style_for(index, enabled), label, lambda event, index=index: self._click_find_button(event, index)))
        return fragments

    def _find_controls_width(self) -> int:
        try:
            return max(20, self.application.output.get_size().columns - 4)
        except Exception:
            return 76

    def _move_find_button_selection(self, delta: int) -> None:
        specs = self._find_button_specs()
        if not specs:
            return
        state = self.controller.state.find_ui
        state.active_field = "buttons"
        state.button_index = self._valid_find_button_index(state.button_index + delta, delta=delta, specs=specs)
        self._invalidate()

    def _activate_selected_find_button(self) -> None:
        specs = self._find_button_specs()
        if not specs:
            return
        index = self._valid_find_button_index(self.controller.state.find_ui.button_index, specs=specs)
        self.controller.state.find_ui.button_index = index
        _label, enabled, callback = specs[index]
        if not enabled:
            return
        result = callback()
        if asyncio.iscoroutine(result):
            self._background(result)

    def _click_find_button(self, mouse_event, index: int) -> None:
        self.controller.state.find_ui.active_field = "buttons"
        self.controller.state.find_ui.button_index = index
        self.application.layout.focus(self.find_controls_window)
        if mouse_event.event_type != MouseEventType.MOUSE_UP:
            self._invalidate()
            return
        self._activate_selected_find_button()

    def _valid_find_button_index(
        self,
        index: int,
        *,
        delta: int = 1,
        specs: list[tuple[str, bool, Callable[[], object]]] | None = None,
    ) -> int:
        specs = self._find_button_specs() if specs is None else specs
        if not specs:
            return 0
        direction = 1 if delta >= 0 else -1
        index %= len(specs)
        for _ in range(len(specs)):
            if specs[index][1]:
                return index
            index = (index + direction) % len(specs)
        return 0

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
        prompt_target = self.prompt_field
        if self.controller.state.find_ui.active:
            if self.controller.state.find_ui.active_field == "buttons":
                prompt_target = self.find_controls_window
            else:
                prompt_target = self.replace_field if (
                    self.controller.state.find_ui.replace_mode and self.controller.state.find_ui.active_field == "replace"
                ) else self.find_field
        if self.application.layout.has_focus(self.editor):
            if self.controller.has_transcript() and self.controller.state.transcript_open:
                self._transcript_view.ensure_selection()
                self.application.layout.focus(self._transcript_view.window)
                return
            self.application.layout.focus(prompt_target)
            return
        if self.application.layout.has_focus(self._transcript_view.window):
            self.application.layout.focus(prompt_target)
            return
        if (
            self.application.layout.has_focus(self.prompt_field)
            or self.application.layout.has_focus(self.find_field)
            or self.application.layout.has_focus(self.replace_field)
        ):
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
            or self.application.layout.has_focus(self.find_field)
            or self.application.layout.has_focus(self.replace_field)
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

    def _in_web_chunks(self) -> bool:
        return self.controller.state.web_mode == "chunks"

    def _sleeping(self) -> bool:
        return self.controller.state.run_in_progress and self.controller.state.sleep_status is not None

    def _find_ui_active(self) -> bool:
        return self.controller.state.find_ui.active

    def _find_ui_replace_mode_active(self) -> bool:
        return self.controller.state.find_ui.active and self.controller.state.find_ui.replace_mode

    def _find_field_has_focus(self) -> bool:
        return self.controller.state.find_ui.active and self.application.layout.has_focus(self.find_field)

    def _replace_field_has_focus(self) -> bool:
        return (
            self.controller.state.find_ui.active
            and self.controller.state.find_ui.replace_mode
            and self.application.layout.has_focus(self.replace_field)
        )

    def _find_text_field_has_focus(self) -> bool:
        return self._find_field_has_focus() or self._replace_field_has_focus()

    def _find_buttons_have_focus(self) -> bool:
        return self.controller.state.find_ui.active and self.application.layout.has_focus(self.find_controls_window)

    def _find_ui_control_has_focus(self) -> bool:
        return (
            self._find_field_has_focus()
            or self._replace_field_has_focus()
            or self._find_buttons_have_focus()
        )

    def _can_toggle_find_ui(self) -> bool:
        return self.controller.state.active_dialog is None and self.controller.state.web_mode == "idle"

    def _active_text_area(self) -> TextArea | None:
        if self.application.layout.has_focus(self.editor):
            return self.editor
        if self.application.layout.has_focus(self.prompt_field):
            return self.prompt_field
        if self.application.layout.has_focus(self.find_field):
            return self.find_field
        if self.application.layout.has_focus(self.replace_field):
            return self.replace_field
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
        self._rebuild_file_menu()
        self.controller.open_file_menu()
        self.application.layout.focus(self._file_radio)

    def _open_model_picker(self) -> None:
        if self.controller.state.web_mode != "idle" or self.controller.state.run_in_progress:
            return
        if self.controller.state.active_dialog == "model_picker":
            self.controller.close_dialog()
            self.application.layout.focus(self.prompt_field)
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

    async def _sleep_refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if self.controller.state.sleep_status is not None:
                self._invalidate()

    def _handle_find_buffer_changed(self, _event) -> None:
        if self._syncing_find:
            return
        self.controller.set_find_field_text("find", self.find_field.text)

    def _handle_replace_buffer_changed(self, _event) -> None:
        if self._syncing_replace:
            return
        self.controller.set_find_field_text("replace", self.replace_field.text)

    def _sync_find_buffers_from_state(self) -> None:
        state = self.controller.state.find_ui
        if self.find_field.text != state.find_text:
            self._syncing_find = True
            try:
                self.find_field.buffer.set_document(
                    Document(text=state.find_text, cursor_position=len(state.find_text)),
                    bypass_readonly=True,
                )
            finally:
                self._syncing_find = False
        if self.replace_field.text != state.replace_text:
            self._syncing_replace = True
            try:
                self.replace_field.buffer.set_document(
                    Document(text=state.replace_text, cursor_position=len(state.replace_text)),
                    bypass_readonly=True,
                )
            finally:
                self._syncing_replace = False

    def _close_find_ui(self) -> None:
        self.controller.close_find_ui()

    def _open_replace_mode(self) -> None:
        self.controller.set_find_replace_mode(True)

    def _open_find_mode(self) -> None:
        self.controller.set_find_replace_mode(False)

    def _replace_current_find_match(self) -> None:
        if self.controller.state.find_ui.current_match_index is None:
            self.controller.find_next_match()
        if self.controller.state.find_ui.current_match_index is None:
            return
        self._reset_undo_coalescing_for_buffer(self.editor.buffer)
        self.editor.buffer.save_to_undo_stack()
        if self.controller.replace_current_find_match():
            self._reset_undo_coalescing_for_buffer(self.editor.buffer)
            self.application.layout.focus(self.editor)

    def _replace_all_find_matches(self) -> None:
        if self.controller.state.find_ui.match_count <= 0:
            self.controller.replace_all_find_matches()
            return
        self._reset_undo_coalescing_for_buffer(self.editor.buffer)
        self.editor.buffer.save_to_undo_stack()
        if self.controller.replace_all_find_matches() > 0:
            self._reset_undo_coalescing_for_buffer(self.editor.buffer)
            self.application.layout.focus(self.editor)

    def _active_text_buffer(self):
        if self.application.layout.has_focus(self.editor):
            return self.editor.buffer
        if self.application.layout.has_focus(self.prompt_field):
            return self.prompt_field.buffer
        if self.application.layout.has_focus(self.find_field):
            return self.find_field.buffer
        if self.application.layout.has_focus(self.replace_field):
            return self.replace_field.buffer
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
        if self.application.layout.has_focus(self.find_field) or self.application.layout.has_focus(self.replace_field):
            return False
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
        if buffer is self.find_field.buffer:
            return self._syncing_find
        if buffer is self.replace_field.buffer:
            return self._syncing_replace
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

    def _move_line_active_text_area(self, direction: int) -> None:
        """Move the current line up (direction=-1) or down (direction=1)."""
        buffer = self._active_text_buffer()
        if buffer is None or self._active_text_area_is_read_only():
            return
        lines = buffer.text.splitlines(keepends=True)
        if not lines:
            return
        row = buffer.document.cursor_position_row
        col = buffer.document.cursor_position_col
        swap = row + direction
        if swap < 0 or swap >= len(lines):
            return
        self._reset_undo_coalescing_for_buffer(buffer)
        buffer.save_to_undo_stack()
        lines[row], lines[swap] = lines[swap], lines[row]
        updated_text = "".join(lines)
        new_cursor = _cursor_position_for_row_col(updated_text, swap, col)
        buffer.set_document(
            Document(text=updated_text, cursor_position=new_cursor),
            bypass_readonly=True,
        )

    def _control_window(self, text_getter, callback) -> Window:
        return Window(
            FormattedTextControl(
                text=text_getter,
                focusable=False,
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
        if self.controller.state.run_in_progress and self.controller.state.sleep_status is not None:
            return [("class:indicator.status", _sleep_banner(self.controller.state.sleep_status))]
        style = "class:indicator.error" if self.controller.state.indicator_kind == "error" else "class:indicator.status"
        fragments = [(style, self.controller.state.indicator_message)]
        for index, attachment in enumerate(self.controller.state.prompt_image_attachments):
            fragments.extend(
                [
                    ("", " "),
                    ("class:indicator.attachment", "["),
                    (
                        "class:indicator.attachment.remove",
                        "x",
                        lambda event, idx=index: self._fragment_click(
                            event,
                            lambda: self.controller.remove_prompt_image_attachment(idx),
                        ),
                    ),
                    ("class:indicator.attachment", f" {_truncate_attachment_name(attachment.name)}]"),
                ]
            )
        return fragments

    def _send_control_fragments(self):
        if self.controller.state.run_in_progress:
            return [("class:control.send", "[x]", lambda event: self._fragment_click(event, self.controller.force_stop_run))]
        return [("class:control.send", "[↑]", lambda event: self._fragment_click(event, self._send_background))]

    def _find_close_fragments(self):
        return [("class:control", "[ X ]", lambda event: self._fragment_click(event, self._close_find_ui))]

    def _find_case_fragments(self):
        state = self.controller.state.find_ui
        return [("class:control", f"[ Aa: {'on' if state.case_sensitive else 'off'} ]", lambda event: self._fragment_click(event, self.controller.toggle_find_case_sensitive))]

    def _find_next_fragments(self):
        style = "class:control" if self.controller.state.find_ui.find_text else "class:control.disabled"
        return [(style, "[ next ]", lambda event: self._fragment_click(event, self.controller.find_next_match))]

    def _find_prev_fragments(self):
        style = "class:control" if self.controller.state.find_ui.find_text else "class:control.disabled"
        return [(style, "[ prev ]", lambda event: self._fragment_click(event, self.controller.find_previous_match))]

    def _find_open_replace_fragments(self):
        return [("class:control", "[ replace ]", lambda event: self._fragment_click(event, self._open_replace_mode))]

    def _find_open_find_fragments(self):
        return [("class:control", "[ find ]", lambda event: self._fragment_click(event, self._open_find_mode))]

    def _replace_current_fragments(self):
        style = "class:control" if self.controller.state.find_ui.match_count else "class:control.disabled"
        return [(style, "[ repl. ]", lambda event: self._fragment_click(event, self._replace_current_find_match))]

    def _replace_all_fragments(self):
        style = "class:control" if self.controller.state.find_ui.match_count else "class:control.disabled"
        return [(style, "[ repl. all ]", lambda event: self._fragment_click(event, self._replace_all_find_matches))]

    def _mode_control_fragments(self):
        if self.controller.state.web_mode != "idle":
            return [("class:control.disabled", f"[ Mode: {self.controller.state.mode} ]")]
        return [("class:control", f"[ Mode: {self.controller.state.mode} ]", lambda event: self._fragment_click(event, self._toggle_mode_background))]

    def _work_control_fragments(self):
        if self.controller.state.web_mode != "idle":
            return [("class:control.disabled", f"[ Agent: {self.controller.state.work_mode} ]")]
        return [("class:control", f"[ Agent: {self.controller.state.work_mode} ]", lambda event: self._fragment_click(event, self.controller.toggle_work_mode))]

    def _model_control_fragments(self):
        if self.controller.state.web_mode != "idle":
            return [("class:control.disabled", f"[ {self.controller.state.selected_model.label} ]")]
        return [("class:control", f"[ {self.controller.state.selected_model.label} ]", lambda event: self._fragment_click(event, self._open_model_picker))]

    def _attachment_control_fragments(self):
        if not self.controller.state.selected_model.supports_images:
            return [("class:control.disabled", "[ + ]")]
        return [
            (
                "class:control",
                "[ + ]",
                lambda event: self._fragment_click(
                    event,
                    lambda: self._background(self.controller.attach_prompt_images_via_picker()),
                ),
            )
        ]

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
        entries = self.controller.state.include_entries
        sections: list = [self._file_radio]
        if entries:
            include_rows: list = []
            for i, entry in enumerate(entries):
                idx = i  # capture for lambda
                label = _disambiguate_include_label(entry.path, entries)
                active_marker = "[*]" if entry.active else "[ ]"
                dim = not entry.active

                def make_toggle(ix):
                    return lambda: (self.controller.toggle_include_active(ix), self._sync_file_radio(), self._invalidate())

                def make_remove(ix):
                    return lambda: (self.controller.remove_include(ix), self._rebuild_file_menu(), self._invalidate())

                row = VSplit([
                    Button(text="X", handler=make_remove(idx), width=3),
                    Button(text=active_marker, handler=make_toggle(idx), width=5),
                    Label(text=(" " if dim else "") + label),
                ])
                include_rows.append(row)
            include_section = HSplit([
                Label(text="\nIncludes:"),
                *include_rows,
            ])
            sections.append(include_section)
        plans = self.controller.plan_menu_entries()
        if plans:
            plan_rows: list = []
            for plan in plans:
                label = f"[{plan.status}] {plan.title}"
                plan_rows.append(
                    Button(
                        text=label,
                        handler=lambda plan_id=plan.id: self._background(self.controller.open_plan(plan_id)),
                    )
                )
            sections.append(HSplit([Label(text="\nPlans:"), *plan_rows]))
        body = Box(HSplit(sections, padding=1), padding=1)
        return Dialog(
            title="Open File",
            body=body,
            buttons=[
                Button(text="Open", handler=lambda: self._background(self.controller.request_file_switch(self._file_radio.current_value))),
                Button(text="Cancel", handler=self.controller.close_dialog),
            ],
        )

    def _rebuild_file_menu(self) -> None:
        self._file_menu = self._build_file_menu_dialog()
        self._sync_file_radio()

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
        paths = self.controller.state.available_files
        labels = _disambiguate_path_labels(paths)
        self._file_radio.values = list(zip(paths, labels))
        self._file_radio.current_value = self.controller.state.context_file or self.controller.state.active_file


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

        is_plan_approval = prompt.details.get("kind") == "plan_approval"
        fragments.append(("class:control.active", "Plan Approval\n" if is_plan_approval else "Tool Permission\n"))
        fragments.append(("", f"{prompt.message}\n"))
        fragments.append(("class:transcript.tool.name", f"Tool: {prompt.tool_name}\n"))
        fragments.append(("class:transcript.tool.content", f"Target: {prompt.target}\n"))
        if is_plan_approval:
            plan_markdown = str(prompt.details.get("plan_markdown") or "")
            preview = "\n".join(plan_markdown.splitlines()[:12])
            if preview:
                fragments.append(("class:transcript.tool.content", f"\n{preview}\n"))
                if len(plan_markdown.splitlines()) > 12:
                    fragments.append(("class:transcript.tool.content", "..."))
                fragments.append(("", "\n"))
        fragments.append(("", "\n"))

        options = (("once", "Approve & implement"), ("reject", "Keep planning")) if is_plan_approval else self._OPTIONS
        for index, (value, label) in enumerate(options):
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


def _disambiguate_path_labels(paths: tuple[Path, ...]) -> list[str]:
    """Return display labels for a list of paths, using minimal path depth to disambiguate."""
    names = [p.name for p in paths]
    labels = list(names)
    # Find pairs that share a name and progressively add parent components
    depth = 1
    while True:
        seen: dict[str, list[int]] = {}
        for i, name in enumerate(labels):
            seen.setdefault(name, []).append(i)
        conflicts = [indices for indices in seen.values() if len(indices) > 1]
        if not conflicts:
            break
        depth += 1
        for indices in conflicts:
            for i in indices:
                parts = paths[i].parts
                label_parts = parts[max(0, len(parts) - depth):]
                labels[i] = "/".join(label_parts)
        if depth > 20:
            break
    return labels


def _disambiguate_include_label(path: str, entries: list[IncludeEntry]) -> str:
    """Return a display label for one include entry, disambiguating if another shares the same name."""
    names = [Path(e.path).name for e in entries]
    my_name = Path(path).name
    if names.count(my_name) <= 1:
        return my_name
    # Same name found elsewhere — show path as-is
    return path


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


def _truncate_attachment_name(name: str, *, max_length: int = 28) -> str:
    if len(name) <= max_length:
        return name
    head = max_length - 3
    return f"{name[:head]}..."


def _sleep_banner(status: SleepStatusState) -> str:
    remaining_seconds = max(0, int(status.deadline_monotonic - time.monotonic() + 0.999))
    reason = f" - {status.reason}" if status.reason else ""
    return f"Sleeping {_format_remaining(remaining_seconds)} remaining{reason}  [Esc to wake]"


def _format_remaining(total_seconds: int) -> str:
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


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
