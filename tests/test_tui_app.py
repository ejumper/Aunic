from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.output import DummyOutput

from aunic.loop.types import LoopMetrics, LoopRunResult
from aunic.modes.types import NoteModePromptResult, NoteModeRunResult
from aunic.progress import ProgressEvent
from aunic.tui.app import AunicTuiApp
from aunic.tui.types import PermissionPromptState


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
