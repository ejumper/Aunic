from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.data_structures import Point
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.output import DummyOutput

from aunic.loop.types import LoopMetrics, LoopRunResult
from aunic.modes.types import NoteModePromptResult, NoteModeRunResult
from aunic.progress import ProgressEvent
from aunic.tui.app import AunicTuiApp
from aunic.tui.note_tables import NoteTablePreviewBufferControl
from aunic.tui.types import PermissionPromptState
import aunic.tui.transcript_view as transcript_view_module


class _FakeNoteRunner:
    def __init__(self, note: Path) -> None:
        self.note = note
        self.called = asyncio.Event()
        self.requests = []

    async def run(self, request) -> NoteModeRunResult:
        self.requests.append(request)
        updated_text = self.note.read_text(encoding="utf-8") + "\nModel output.\n"
        self.note.write_text(updated_text, encoding="utf-8")
        if request.progress_sink is not None:
            await request.progress_sink(
                ProgressEvent(
                    kind="file_written",
                    message="Model wrote the file.",
                    path=request.active_file,
                    details={"reason": "edit_applied"},
                )
            )
        self.called.set()
        return NoteModeRunResult(
            initial_warnings=(),
            prompt_results=(
                NoteModePromptResult(
                    prompt_index=0,
                    prompt_run=request and _prompt_run(),
                    loop_result=LoopRunResult(
                        stop_reason="finished",
                        events=(),
                        metrics=LoopMetrics(stop_reason="finished"),
                        tool_failures=(),
                        final_file_snapshots=(),
                    ),
                ),
            ),
            completed_prompt_runs=1,
            completed_all_prompts=True,
            final_file_snapshots=(),
            stop_reason="finished",
        )


class _FakeChatRunner:
    async def run(self, request):
        raise AssertionError("chat runner should not be used in this test")


class _QuietFileManager:
    async def read_snapshot(self, path):
        raw = Path(path).read_text(encoding="utf-8")
        return _snapshot(Path(path), raw)

    async def write_text(self, path, new_text, expected_revision=None):
        Path(path).write_text(new_text, encoding="utf-8")
        return await self.read_snapshot(path)

    async def watch(self, paths):
        while True:
            await asyncio.sleep(60)
            if False:
                yield ()


def _snapshot(path: Path, text: str):
    from aunic.context.types import FileSnapshot

    return FileSnapshot(
        path=path,
        raw_text=text,
        revision_id=f"rev:{hash(text)}",
        content_hash=f"hash:{hash(text)}",
        mtime_ns=1,
        size_bytes=len(text.encode("utf-8")),
    )


def _prompt_run():
    from aunic.context.types import PromptRun

    return PromptRun(
        index=0,
        prompt_text="Do the thing",
        mode="direct",
        per_prompt_budget=4,
        target_map_text="view",
        model_input_text="input",
    )


@pytest.mark.asyncio
async def test_tui_ctrl_r_sends_prompt_and_refreshes_editor(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("Start\n", encoding="utf-8")
    note_runner = _FakeNoteRunner(note)

    with create_pipe_input() as pipe:
        app = AunicTuiApp(
            active_file=note,
            note_runner=note_runner,
            chat_runner=_FakeChatRunner(),
            file_manager=_QuietFileManager(),
            input=pipe,
            output=DummyOutput(),
        )
        task = asyncio.create_task(app.run())
        await asyncio.sleep(0.1)
        app.application.layout.focus(app.prompt_field)
        pipe.send_text("hello from tui")
        pipe.send_bytes(b"\x12")

        await asyncio.wait_for(note_runner.called.wait(), timeout=5)
        await asyncio.sleep(0.1)
        app.application.exit(result=0)
        await task

    assert note_runner.requests[0].user_prompt == "hello from tui"
    assert "Model output." in app.editor.text


@pytest.mark.asyncio
async def test_tui_ctrl_z_and_ctrl_y_use_grouped_undo_redo(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("Start\n", encoding="utf-8")

    with create_pipe_input() as pipe:
        app = AunicTuiApp(
            active_file=note,
            note_runner=_FakeNoteRunner(note),
            chat_runner=_FakeChatRunner(),
            file_manager=_QuietFileManager(),
            input=pipe,
            output=DummyOutput(),
        )
        task = asyncio.create_task(app.run())
        await asyncio.sleep(0.1)

        pipe.send_text("snail")
        await asyncio.sleep(0.05)
        pipe.send_bytes(b"\x1a")
        await asyncio.sleep(0.05)
        assert app.prompt_field.text == ""

        pipe.send_bytes(b"\x19")
        await asyncio.sleep(0.05)
        assert app.prompt_field.text == "snail"

        app.application.exit(result=0)
        await task


@pytest.mark.asyncio
async def test_tui_ctrl_f_opens_find_ui_and_close_restores_prompt_draft(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("alpha beta gamma\n", encoding="utf-8")

    with create_pipe_input() as pipe:
        app = AunicTuiApp(
            active_file=note,
            note_runner=_FakeNoteRunner(note),
            chat_runner=_FakeChatRunner(),
            file_manager=_QuietFileManager(),
            input=pipe,
            output=DummyOutput(),
        )
        task = asyncio.create_task(app.run())
        await asyncio.sleep(0.1)

        app.application.layout.focus(app.prompt_field)
        pipe.send_text("draft prompt")
        await asyncio.sleep(0.05)

        pipe.send_bytes(b"\x06")
        await asyncio.sleep(0.05)

        assert app.controller.state.find_ui.active is True
        assert app.application.layout.has_focus(app.find_field)
        assert app.prompt_field.text == "draft prompt"

        pipe.send_bytes(b"\x1b")
        await asyncio.sleep(0.1)

        assert app.controller.state.find_ui.active is False
        assert app.application.layout.has_focus(app.prompt_field)
        assert app.prompt_field.text == "draft prompt"

        app.application.exit(result=0)
        await task


@pytest.mark.asyncio
async def test_tui_find_ui_does_not_steal_focus_back_from_editor_after_typing(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("alpha beta\n", encoding="utf-8")

    with create_pipe_input() as pipe:
        app = AunicTuiApp(
            active_file=note,
            note_runner=_FakeNoteRunner(note),
            chat_runner=_FakeChatRunner(),
            file_manager=_QuietFileManager(),
            input=pipe,
            output=DummyOutput(),
        )
        task = asyncio.create_task(app.run())
        await asyncio.sleep(0.1)

        app.controller.open_find_ui(find_text="beta")
        app.application.layout.focus(app.editor)
        app.editor.buffer.cursor_position = len(app.editor.buffer.text)
        app._invalidate()
        await asyncio.sleep(0.05)

        app.editor.buffer.insert_text("!")
        await asyncio.sleep(0.05)

        assert app.application.layout.has_focus(app.editor)
        assert app.editor.buffer.text.endswith("!\n") or app.editor.buffer.text.endswith("\n!")

        app.application.exit(result=0)
        await task


def test_tui_ctrl_f_focuses_existing_replace_ui_without_closing_it_from_editor(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("alpha beta\n", encoding="utf-8")
    app = AunicTuiApp(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        file_manager=_QuietFileManager(),
        output=DummyOutput(),
    )

    app.controller.open_find_ui(replace_mode=True, find_text="beta", replace_text="BETA")
    app.application.layout.focus(app.editor)

    app._handle_find_shortcut()

    assert app.controller.state.find_ui.replace_mode is True
    assert app.application.layout.has_focus(app.find_field)


def test_tui_ctrl_f_closes_replace_mode_from_find_or_replace_field(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("alpha beta\n", encoding="utf-8")
    app = AunicTuiApp(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        file_manager=_QuietFileManager(),
        output=DummyOutput(),
    )

    app.controller.open_find_ui(replace_mode=True, find_text="beta", replace_text="BETA")
    app.application.layout.focus(app.replace_field)

    app._handle_find_shortcut()

    assert app.controller.state.find_ui.active is True
    assert app.controller.state.find_ui.replace_mode is False
    assert app.application.layout.has_focus(app.find_field)


def test_tui_ctrl_f_opens_replace_mode_from_find_field(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("alpha beta\n", encoding="utf-8")
    app = AunicTuiApp(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        file_manager=_QuietFileManager(),
        output=DummyOutput(),
    )

    app.controller.open_find_ui(find_text="beta")
    app.application.layout.focus(app.find_field)

    app._handle_find_shortcut()

    assert app.controller.state.find_ui.active is True
    assert app.controller.state.find_ui.replace_mode is True
    assert app.application.layout.has_focus(app.find_field)


@pytest.mark.asyncio
async def test_tui_find_field_uses_grouped_undo_redo(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("alpha beta gamma\n", encoding="utf-8")

    with create_pipe_input() as pipe:
        app = AunicTuiApp(
            active_file=note,
            note_runner=_FakeNoteRunner(note),
            chat_runner=_FakeChatRunner(),
            file_manager=_QuietFileManager(),
            input=pipe,
            output=DummyOutput(),
        )
        task = asyncio.create_task(app.run())
        await asyncio.sleep(0.1)

        app.controller.open_find_ui()
        app._invalidate()
        await asyncio.sleep(0.05)

        pipe.send_text("beta")
        await asyncio.sleep(0.05)
        assert app.find_field.text == "beta"

        pipe.send_bytes(b"\x1a")
        await asyncio.sleep(0.05)
        assert app.find_field.text == ""

        pipe.send_bytes(b"\x19")
        await asyncio.sleep(0.05)
        assert app.find_field.text == "beta"

        app.application.exit(result=0)
        await task


@pytest.mark.asyncio
async def test_tui_replace_current_focuses_editor_and_ctrl_z_undoes_note_change(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("alpha beta\n", encoding="utf-8")

    with create_pipe_input() as pipe:
        app = AunicTuiApp(
            active_file=note,
            note_runner=_FakeNoteRunner(note),
            chat_runner=_FakeChatRunner(),
            file_manager=_QuietFileManager(),
            input=pipe,
            output=DummyOutput(),
        )
        task = asyncio.create_task(app.run())
        await asyncio.sleep(0.1)

        app.controller.open_find_ui(replace_mode=True, find_text="beta", replace_text="BETA")
        app.controller.set_find_active_field("replace")
        app.application.layout.focus(app.replace_field)
        app._invalidate()
        await asyncio.sleep(0.05)

        app._replace_current_find_match()
        await asyncio.sleep(0.05)

        assert app.application.layout.has_focus(app.editor)
        assert app.editor.buffer.text == "alpha BETA\n"

        pipe.send_bytes(b"\x1a")
        await asyncio.sleep(0.05)
        assert app.editor.buffer.text == "alpha beta\n"

        app.application.exit(result=0)
        await task


@pytest.mark.asyncio
async def test_tui_find_ui_tab_cycles_to_button_row_and_enter_activates_selected_button(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("alpha beta\n", encoding="utf-8")

    with create_pipe_input() as pipe:
        app = AunicTuiApp(
            active_file=note,
            note_runner=_FakeNoteRunner(note),
            chat_runner=_FakeChatRunner(),
            file_manager=_QuietFileManager(),
            input=pipe,
            output=DummyOutput(),
        )
        task = asyncio.create_task(app.run())
        await asyncio.sleep(0.1)

        app.controller.open_find_ui(replace_mode=True, find_text="beta", replace_text="BETA")
        app._invalidate()
        await asyncio.sleep(0.05)

        assert app.application.layout.has_focus(app.find_field)

        pipe.send_bytes(b"\t")
        await asyncio.sleep(0.05)
        assert app.application.layout.has_focus(app.replace_field)

        pipe.send_bytes(b"\t")
        await asyncio.sleep(0.05)
        assert app.application.layout.has_focus(app.find_controls_window)
        assert app.controller.state.find_ui.button_index == 0

        pipe.send_bytes(b"\x1b[C")
        pipe.send_bytes(b"\x1b[C")
        pipe.send_bytes(b"\x1b[C")
        pipe.send_bytes(b"\x1b[C")
        await asyncio.sleep(0.05)
        assert app.controller.state.find_ui.button_index == 4

        pipe.send_bytes(b"\r")
        await asyncio.sleep(0.05)

        assert app.application.layout.has_focus(app.editor)
        assert app.editor.buffer.text == "alpha BETA\n"

        app.application.exit(result=0)
        await task


def test_tui_find_button_keyboard_navigation_skips_disabled_buttons(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("alpha beta\n", encoding="utf-8")
    app = AunicTuiApp(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        file_manager=_QuietFileManager(),
        output=DummyOutput(),
    )

    app.controller.open_find_ui()
    app.controller.set_find_active_field("buttons")

    app._move_find_button_selection(1)
    assert app.controller.state.find_ui.button_index == 1

    app._move_find_button_selection(1)
    assert app.controller.state.find_ui.button_index == 4

    app._move_find_button_selection(-1)
    assert app.controller.state.find_ui.button_index == 1


def test_tui_find_ui_mouse_click_on_button_works(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("alpha beta\n", encoding="utf-8")
    app = AunicTuiApp(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        file_manager=_QuietFileManager(),
        output=DummyOutput(),
    )

    app.controller.open_find_ui(find_text="beta")
    fragments = app._find_controls_fragments()
    replace_fragment = next(fragment for fragment in fragments if len(fragment) == 3 and fragment[1] == "[ replace ]")
    handler = replace_fragment[2]
    handler(
        MouseEvent(
            position=Point(x=0, y=0),
            event_type=MouseEventType.MOUSE_UP,
            button=MouseButton.LEFT,
            modifiers=(),
        )
    )

    assert app.controller.state.find_ui.replace_mode is True


@pytest.mark.asyncio
async def test_tui_arrow_keys_move_through_wrapped_rows_in_editor(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("This is a very long line that should wrap across multiple visual rows in the editor. " * 4, encoding="utf-8")

    with create_pipe_input() as pipe:
        app = AunicTuiApp(
            active_file=note,
            note_runner=_FakeNoteRunner(note),
            chat_runner=_FakeChatRunner(),
            file_manager=_QuietFileManager(),
            input=pipe,
            output=DummyOutput(),
        )
        task = asyncio.create_task(app.run())
        await asyncio.sleep(0.1)

        app.application.layout.focus(app.editor)
        app.editor.buffer.cursor_position = 0
        app.application.invalidate()
        await asyncio.sleep(0.1)

        pipe.send_bytes(b"\x1b[B")
        await asyncio.sleep(0.1)
        assert app.editor.buffer.document.cursor_position_row == 0
        assert app.editor.buffer.document.cursor_position_col > 0

        pipe.send_bytes(b"\x1b[A")
        await asyncio.sleep(0.1)
        assert app.editor.buffer.document.cursor_position_row == 0
        assert app.editor.buffer.document.cursor_position_col == 0

        app.application.exit(result=0)
        await task


@pytest.mark.asyncio
async def test_tui_home_end_follow_wrapped_segments_in_editor(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        "This is a very long line that should wrap across multiple visual rows in the editor. " * 6,
        encoding="utf-8",
    )

    with create_pipe_input() as pipe:
        app = AunicTuiApp(
            active_file=note,
            note_runner=_FakeNoteRunner(note),
            chat_runner=_FakeChatRunner(),
            file_manager=_QuietFileManager(),
            input=pipe,
            output=DummyOutput(),
        )
        task = asyncio.create_task(app.run())
        await asyncio.sleep(0.1)

        app.application.layout.focus(app.editor)
        app.application.invalidate()
        await asyncio.sleep(0.1)

        render_info = app.editor.window.render_info
        assert render_info is not None
        assert render_info.wrap_lines is True
        segment_starts = render_info.visible_line_to_row_col
        assert 1 in segment_starts
        assert 2 in segment_starts
        current_row, current_start = segment_starts[1]
        next_row, next_start = segment_starts[2]
        assert current_row == 0
        assert next_row == current_row

        processed_line = app.editor.control._last_get_processed_line(current_row)
        target_display_col = min(current_start + 5, next_start - 1)
        target_source_col = processed_line.display_to_source(target_display_col)
        app.editor.buffer.cursor_position = app.editor.buffer.document.translate_row_col_to_index(
            current_row, target_source_col
        )
        app.application.invalidate()
        await asyncio.sleep(0.1)

        pipe.send_bytes(b"\x1b[H")
        await asyncio.sleep(0.1)
        expected_home = app.editor.buffer.document.translate_row_col_to_index(
            current_row, processed_line.display_to_source(current_start)
        )
        assert app.editor.buffer.cursor_position == expected_home

        pipe.send_bytes(b"\x1b[F")
        await asyncio.sleep(0.1)
        expected_end = app.editor.buffer.document.translate_row_col_to_index(
            current_row, processed_line.display_to_source(next_start)
        )
        assert app.editor.buffer.cursor_position == expected_end
        assert expected_end < len(app.editor.buffer.text)

        app.application.exit(result=0)
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("sequence", [b"\x1b\x7f", b"\x1b[127;5u"])
async def test_tui_ctrl_backspace_variants_delete_previous_word_in_prompt(
    tmp_path: Path, sequence: bytes
) -> None:
    note = tmp_path / "note.md"
    note.write_text("body\n", encoding="utf-8")

    with create_pipe_input() as pipe:
        app = AunicTuiApp(
            active_file=note,
            note_runner=_FakeNoteRunner(note),
            chat_runner=_FakeChatRunner(),
            file_manager=_QuietFileManager(),
            input=pipe,
            output=DummyOutput(),
        )
        task = asyncio.create_task(app.run())
        await asyncio.sleep(0.1)

        app.application.layout.focus(app.prompt_field)
        pipe.send_text("hello world")
        await asyncio.sleep(0.05)
        pipe.send_bytes(sequence)
        await asyncio.sleep(0.05)

        assert app.prompt_field.text == "hello "

        app.application.exit(result=0)
        await task


@pytest.mark.asyncio
async def test_tui_ctrl_backspace_deletes_previous_word_in_editor(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("alpha beta", encoding="utf-8")

    with create_pipe_input() as pipe:
        app = AunicTuiApp(
            active_file=note,
            note_runner=_FakeNoteRunner(note),
            chat_runner=_FakeChatRunner(),
            file_manager=_QuietFileManager(),
            input=pipe,
            output=DummyOutput(),
        )
        task = asyncio.create_task(app.run())
        await asyncio.sleep(0.1)

        app.application.layout.focus(app.editor)
        app.editor.buffer.cursor_position = len(app.editor.buffer.text)
        pipe.send_bytes(b"\x1b[127;5u")
        await asyncio.sleep(0.05)

        assert app.editor.buffer.text == "alpha "

        app.application.exit(result=0)
        await task


def test_tui_title_click_opens_file_menu(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body\n", encoding="utf-8")
    app = AunicTuiApp(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        file_manager=_QuietFileManager(),
        output=DummyOutput(),
    )

    app._title_mouse_handler(
        MouseEvent(
            position=None,
            event_type=MouseEventType.MOUSE_UP,
            button=MouseButton.LEFT,
            modifiers=(),
        )
    )

    assert app.controller.state.active_dialog == "file_menu"


def test_tui_focus_toggle_switches_between_prompt_and_editor(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body\n", encoding="utf-8")
    app = AunicTuiApp(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        file_manager=_QuietFileManager(),
        output=DummyOutput(),
    )

    assert app.application.layout.has_focus(app.prompt_field)

    app._toggle_focus_between_editor_and_prompt()
    assert app.application.layout.has_focus(app.editor)

    app._toggle_focus_between_editor_and_prompt()
    assert app.application.layout.has_focus(app.prompt_field)


def test_tui_permission_prompt_replaces_prompt_area(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body\n", encoding="utf-8")
    app = AunicTuiApp(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        file_manager=_QuietFileManager(),
        output=DummyOutput(),
    )

    app.controller.state.permission_prompt = PermissionPromptState(
        message="Tool wants permission.",
        target="/tmp/file.txt",
        tool_name="read",
    )
    app.controller.state.active_dialog = "permission_prompt"

    assert app._prompt_area_body() is app._permission_prompt_view.window
    assert app._dialog_container().height == 0


@pytest.mark.asyncio
async def test_tui_focus_toggle_cycles_through_transcript_when_present(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        "body\n\n"
        "---\n"
        "# Transcript\n"
        "| # | role      | type        | tool_name  | tool_id  | content\n"
        "|---|-----------|-------------|------------|----------|-------------------------------\n"
        '| 1 | user      | message     |            |          | "Hello"\n',
        encoding="utf-8",
    )
    app = AunicTuiApp(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        file_manager=_QuietFileManager(),
        output=DummyOutput(),
    )
    await app.controller.initialize()

    assert app.application.layout.has_focus(app.prompt_field)

    app._toggle_focus_between_editor_and_prompt()
    assert app.application.layout.has_focus(app.editor)

    app._toggle_focus_between_editor_and_prompt()
    assert app.application.layout.has_focus(app._transcript_view.window)

    app._toggle_focus_between_editor_and_prompt()
    assert app.application.layout.has_focus(app.prompt_field)


@pytest.mark.asyncio
async def test_transcript_toolbar_renders_separately_from_scroll_body(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        "body\n\n"
        "---\n"
        "# Transcript\n"
        "| # | role      | type        | tool_name  | tool_id  | content\n"
        "|---|-----------|-------------|------------|----------|-------------------------------\n"
        '| 1 | user      | message     |            |          | "Hello"\n',
        encoding="utf-8",
    )
    app = AunicTuiApp(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        file_manager=_QuietFileManager(),
        output=DummyOutput(),
    )
    await app.controller.initialize()

    toolbar_text = "".join(fragment[1] for fragment in app._transcript_view._render_toolbar())
    body_text = "".join(fragment[1] for fragment in app._transcript_view._render_body())

    assert "[ Chat ]" in toolbar_text
    assert "[ Tools ]" in toolbar_text
    assert "[ Search ]" in toolbar_text
    assert "[ Descending ]" in toolbar_text
    assert "[ Chat ]" not in body_text
    assert "[ Tools ]" not in body_text
    assert "Hello" in body_text


@pytest.mark.asyncio
async def test_transcript_maximize_expands_height_and_persists_across_open_toggle(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        "body\n\n"
        "---\n"
        "# Transcript\n"
        "| # | role      | type        | tool_name  | tool_id  | content\n"
        "|---|-----------|-------------|------------|----------|-------------------------------\n"
        '| 1 | user      | message     |            |          | "Hello"\n',
        encoding="utf-8",
    )
    app = AunicTuiApp(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        file_manager=_QuietFileManager(),
        output=DummyOutput(),
    )
    await app.controller.initialize()

    app.controller.transcript_view_state.maximized = False
    app._refresh_transcript_dimensions()
    normal_height = app._transcript_view.window.height
    normal_preferred = normal_height.preferred if hasattr(normal_height, "preferred") else normal_height

    app.application.layout.focus(app.editor)
    app.controller.toggle_transcript_maximized()
    app._refresh_transcript_dimensions()
    expanded_height = app._transcript_view.window.height
    expanded_preferred = expanded_height.preferred if hasattr(expanded_height, "preferred") else expanded_height

    assert app.controller.transcript_view_state.maximized is True
    assert app._transcript_fills_editor_area() is True
    assert app.application.layout.has_focus(app._transcript_view.window)
    assert expanded_preferred > normal_preferred
    assert "[ - ]" in "".join(fragment[1] for fragment in app._transcript_view._render_toolbar())

    app.controller.toggle_transcript_open()
    assert app.controller.state.transcript_open is False
    assert app.controller.transcript_view_state.maximized is True
    assert app._transcript_fills_editor_area() is False

    app.controller.toggle_transcript_open()
    assert app.controller.state.transcript_open is True
    assert app.controller.transcript_view_state.maximized is True
    assert app._transcript_fills_editor_area() is True


def test_tui_registers_pre_run_layout_refresh() -> None:
    note = Path("/tmp/aunic-pre-run-layout-refresh.md")
    app = AunicTuiApp(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        file_manager=_QuietFileManager(),
        output=DummyOutput(),
    )

    assert any(
        getattr(callback, "__self__", None) is app
        and getattr(callback, "__func__", None) is app._invalidate.__func__
        for callback in app.application.pre_run_callables
    )


@pytest.mark.asyncio
async def test_tui_editor_uses_note_table_preview_buffer_control(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("| A | B |\n| --- | --- |\n| 1 | 2 |\n", encoding="utf-8")
    app = AunicTuiApp(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        file_manager=_QuietFileManager(),
        output=DummyOutput(),
    )
    await app.controller.initialize()

    assert isinstance(app.editor.control, NoteTablePreviewBufferControl)


@pytest.mark.asyncio
async def test_tui_editor_renders_boxed_table_preview_when_cursor_is_outside_table(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        "# Heading\n\n"
        "| Protocol | Time |\n"
        "| --- | --- |\n"
        "| **STP** | *30-50 seconds* |\n",
        encoding="utf-8",
    )
    app = AunicTuiApp(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        file_manager=_QuietFileManager(),
        output=DummyOutput(),
    )
    await app.controller.initialize()

    app.editor.buffer.cursor_position = 0
    app.editor.buffer.load_history_if_not_yet_loaded = lambda: None
    content = app.editor.control.create_content(width=80, height=40)
    rendered = "\n".join(
        "".join(fragment[1] for fragment in content.get_line(i)).rstrip()
        for i in range(content.line_count)
    )

    assert "┌" in rendered
    assert "┬" in rendered
    assert "│ Protocol " in rendered
    assert "STP" in rendered
    assert "30-50 seconds" in rendered
    assert "**STP**" not in rendered
    assert "*30-50 seconds*" not in rendered
    assert content.line_count > app.editor.buffer.document.line_count


@pytest.mark.asyncio
async def test_tui_editor_shows_raw_table_when_cursor_enters_table(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        "# Heading\n\n"
        "| Protocol | Time |\n"
        "| --- | --- |\n"
        "| STP | 30-50 seconds |\n",
        encoding="utf-8",
    )
    app = AunicTuiApp(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        file_manager=_QuietFileManager(),
        output=DummyOutput(),
    )
    await app.controller.initialize()

    table_row_index = 2
    app.editor.buffer.cursor_position = app.editor.buffer.document.translate_row_col_to_index(table_row_index, 0)
    app.editor.buffer.load_history_if_not_yet_loaded = lambda: None
    content = app.editor.control.create_content(width=80, height=40)
    rendered = "\n".join(
        "".join(fragment[1] for fragment in content.get_line(i)).rstrip()
        for i in range(content.line_count)
    )

    assert "| Protocol | Time |" in rendered
    assert "| --- | --- |" in rendered
    assert "┌" not in rendered
    assert content.line_count == app.editor.buffer.document.line_count


@pytest.mark.asyncio
async def test_transcript_blank_area_click_is_non_interactive(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        "body\n\n"
        "---\n"
        "# Transcript\n"
        "| # | role      | type        | tool_name  | tool_id  | content\n"
        "|---|-----------|-------------|------------|----------|-------------------------------\n"
        '| 1 | user      | message     |            |          | "Hello"\n',
        encoding="utf-8",
    )
    app = AunicTuiApp(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        file_manager=_QuietFileManager(),
        output=DummyOutput(),
    )
    await app.controller.initialize()
    app._transcript_view._render_body()

    class _Layout:
        def walk_through_modal_area(self):
            return [app._transcript_view.window]

    class _App:
        layout = _Layout()

    original_get_app = transcript_view_module.get_app
    transcript_view_module.get_app = lambda: _App()
    try:
        handler = app._transcript_view.window._build_mouse_handler(
            rowcol_to_yx={(0, 0): (0, 0)},
            visible_line_to_row_col={0: (0, 0)},
            write_position=type("WP", (), {"ypos": 0})(),
        )

        blank_click = MouseEvent(
            position=Point(x=0, y=50),
            event_type=MouseEventType.MOUSE_UP,
            button=MouseButton.LEFT,
            modifiers=(),
        )

        assert app._transcript_view._is_blank_body_mouse_event(blank_click) is True
        assert handler(blank_click) is None
    finally:
        transcript_view_module.get_app = original_get_app


def test_transcript_view_uses_tracked_body_width_for_render_context(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body\n", encoding="utf-8")
    app = AunicTuiApp(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        file_manager=_QuietFileManager(),
        output=DummyOutput(),
    )

    class _App:
        def invalidate(self):
            return None

    original_get_app = transcript_view_module.get_app
    transcript_view_module.get_app = lambda: _App()
    try:
        app._transcript_view._on_body_width_changed(37)
    finally:
        transcript_view_module.get_app = original_get_app

    assert app._transcript_view._build_render_context().width == 37


def test_tui_indent_and_unindent_prompt_field(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body\n", encoding="utf-8")
    app = AunicTuiApp(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        file_manager=_QuietFileManager(),
        output=DummyOutput(),
    )

    app.application.layout.focus(app.prompt_field)
    app.prompt_field.buffer.text = "hello"
    app.prompt_field.buffer.cursor_position = 0

    app._indent_active_text_area()
    assert app.prompt_field.buffer.text == "    hello"

    app._unindent_active_text_area()
    assert app.prompt_field.buffer.text == "hello"


def test_tui_undo_and_redo_prompt_field(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body\n", encoding="utf-8")
    app = AunicTuiApp(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        file_manager=_QuietFileManager(),
        output=DummyOutput(),
    )

    app.application.layout.focus(app.prompt_field)
    app.prompt_field.buffer.text = "snails"
    app.prompt_field.buffer.cursor_position = len("snails")
    app.prompt_field.buffer.save_to_undo_stack()
    app.prompt_field.buffer.delete_before_cursor(count=len("snails"))

    assert app.prompt_field.buffer.text == ""

    app._undo_active_text_area()
    assert app.prompt_field.buffer.text == "snails"

    app._redo_active_text_area()
    assert app.prompt_field.buffer.text == ""


def test_tui_indent_is_undoable(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body\n", encoding="utf-8")
    app = AunicTuiApp(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        file_manager=_QuietFileManager(),
        output=DummyOutput(),
    )

    app.application.layout.focus(app.prompt_field)
    app.prompt_field.buffer.text = "hello"
    app.prompt_field.buffer.cursor_position = 0

    app._indent_active_text_area()
    assert app.prompt_field.buffer.text == "    hello"

    app._undo_active_text_area()
    assert app.prompt_field.buffer.text == "hello"

    app._redo_active_text_area()
    assert app.prompt_field.buffer.text == "    hello"
