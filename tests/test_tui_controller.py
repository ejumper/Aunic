from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest
from prompt_toolkit.buffer import Buffer

from aunic.context.types import FileChange, FileSnapshot, PromptRun, TextSpan
from aunic.loop.types import LoopMetrics, LoopRunResult
from aunic.modes.types import ChatModeMetrics, ChatModeRunResult, NoteModePromptResult, NoteModeRunResult
from aunic.progress import ProgressEvent
from aunic.research.types import (
    FetchPacket,
    FetchedChunk,
    PageFetchResult,
    SearchBatch,
    SearchQueryFailure,
    SearchResult,
)
from aunic.tools.runtime import PermissionRequest
from aunic.tui.controller import TuiController
from aunic.transcript.parser import parse_transcript_rows


def _snapshot(path: Path, text: str, revision_id: str = "rev-1") -> FileSnapshot:
    return FileSnapshot(
        path=path,
        raw_text=text,
        revision_id=revision_id,
        content_hash=f"hash-{revision_id}",
        mtime_ns=1,
        size_bytes=len(text.encode("utf-8")),
    )


def _prompt_run() -> PromptRun:
    return PromptRun(
        index=0,
        prompt_text="Do the thing",
        mode="direct",
        per_prompt_budget=4,
        target_map_text="view",
        model_input_text="input",
    )


def _loop_result() -> LoopRunResult:
    return LoopRunResult(
        stop_reason="finished",
        events=(),
        metrics=LoopMetrics(stop_reason="finished"),
        tool_failures=(),
        final_file_snapshots=(),
    )


class _FakeNoteRunner:
    def __init__(self, note_path: Path) -> None:
        self.requests = []
        self.note_path = note_path

    async def run(self, request) -> NoteModeRunResult:
        self.requests.append((request, self.note_path.read_text(encoding="utf-8")))
        if request.progress_sink is not None:
            await request.progress_sink(
                ProgressEvent(
                    kind="file_written",
                    message="backend wrote file",
                    path=request.active_file,
                    details={"reason": "edit_applied"},
                )
            )
        return NoteModeRunResult(
            initial_warnings=(),
            prompt_results=(
                NoteModePromptResult(
                    prompt_index=0,
                    prompt_run=_prompt_run(),
                    loop_result=_loop_result(),
                ),
            ),
            completed_prompt_runs=1,
            completed_all_prompts=True,
            final_file_snapshots=(),
            stop_reason="finished",
        )


class _SynthesisNoteRunner(_FakeNoteRunner):
    async def run(self, request) -> NoteModeRunResult:
        self.requests.append((request, self.note_path.read_text(encoding="utf-8")))
        return NoteModeRunResult(
            initial_warnings=(),
            prompt_results=(),
            completed_prompt_runs=1,
            completed_all_prompts=True,
            final_file_snapshots=(),
            stop_reason="finished",
            synthesis_ran=True,
        )


class _FakeChatRunner:
    def __init__(self) -> None:
        self.requests = []

    async def run(self, request) -> ChatModeRunResult:
        self.requests.append(request)
        return ChatModeRunResult(
            initial_warnings=(),
            response_text="hello",
            assistant_response_appended=True,
            final_file_snapshots=(),
            stop_reason="finished",
            metrics=ChatModeMetrics(stop_reason="finished"),
        )


class _FakeSearchService:
    def __init__(self, batch: SearchBatch | None = None, *, error: Exception | None = None) -> None:
        self.batch = batch
        self.error = error

    async def search(self, **kwargs) -> SearchBatch:
        if self.error is not None:
            raise self.error
        assert self.batch is not None
        return self.batch


class _FakeFetchService:
    def __init__(
        self,
        packet: FetchPacket | None = None,
        *,
        summary: PageFetchResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.packet = packet
        self.summary = summary
        self.error = error

    async def fetch_for_user_selection(self, *, state, **kwargs) -> FetchPacket:
        if self.error is not None:
            raise self.error
        assert self.packet is not None
        assert self.summary is not None
        state.record_fetched_page(self.summary)
        return self.packet


class _FakeWatchingFileManager:
    def __init__(self, path: Path, initial_text: str, next_text: str) -> None:
        self.path = path
        self.current_text = initial_text
        self.next_text = next_text
        self.write_calls: list[str] = []

    async def read_snapshot(self, path: Path | str) -> FileSnapshot:
        return _snapshot(Path(path), self.current_text)

    async def write_text(self, path: Path | str, new_text: str, expected_revision: str | None = None) -> FileSnapshot:
        self.current_text = new_text
        self.write_calls.append(new_text)
        return _snapshot(Path(path), new_text, "rev-write")

    async def watch(self, paths):
        self.current_text = self.next_text
        yield (
            FileChange(
                path=self.path,
                change="modified",
                exists=True,
                revision_id="rev-2",
            ),
        )


class _NewFileWatchingFileManager:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.current_text: str | None = None
        self.watch_started = 0

    async def read_snapshot(self, path: Path | str) -> FileSnapshot:
        assert self.current_text is not None
        return _snapshot(Path(path), self.current_text, "rev-new")

    async def write_text(self, path: Path | str, new_text: str, expected_revision: str | None = None) -> FileSnapshot:
        self.current_text = new_text
        return _snapshot(Path(path), new_text, "rev-write")

    async def watch(self, paths):
        self.watch_started += 1
        while True:
            await asyncio.sleep(60)
            if False:
                yield ()


@pytest.mark.asyncio
async def test_controller_send_prompt_saves_buffer_and_dispatches_note_mode(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("Original\n", encoding="utf-8")
    note_runner = _FakeNoteRunner(note)
    controller = TuiController(active_file=note, note_runner=note_runner, chat_runner=_FakeChatRunner())
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    controller._editor_buffer.text = "Changed\n"
    controller._prompt_buffer.text = "Please update it."

    await controller.send_prompt()
    await controller._run_task

    request, saved_text = note_runner.requests[0]
    assert request.prompt_mode == "direct"
    assert request.user_prompt == "Please update it."
    assert saved_text == "Changed\n"
    assert controller.state.run_in_progress is False


@pytest.mark.asyncio
async def test_controller_initializes_missing_file_without_creating_it(tmp_path: Path) -> None:
    note = tmp_path / "new-note.md"
    controller = TuiController(
        active_file=note,
        allow_missing_active_file=True,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
    )
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())

    await controller.initialize()

    assert note.exists() is False
    assert controller.state.active_file_missing_on_disk is True
    assert controller.state.editor_dirty is False
    assert controller._editor_buffer.text == ""
    assert controller.state.indicator_message == "New file: will be created on first save."


@pytest.mark.asyncio
async def test_controller_save_new_file_with_missing_parent_requires_parents_flag(tmp_path: Path) -> None:
    note = tmp_path / "new" / "dir" / "note.md"
    controller = TuiController(
        active_file=note,
        allow_missing_active_file=True,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
    )
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    controller._editor_buffer.text = "Body\n"

    assert await controller.save_active_file() is False
    assert note.exists() is False
    assert "Reopen with -p/--parents" in controller.state.indicator_message


@pytest.mark.asyncio
async def test_controller_save_new_file_creates_missing_parents_on_first_save(tmp_path: Path) -> None:
    note = tmp_path / "new" / "dir" / "note.md"
    controller = TuiController(
        active_file=note,
        allow_missing_active_file=True,
        create_missing_parents_on_save=True,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
    )
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    controller._editor_buffer.text = "Body\n"

    assert await controller.save_active_file() is True
    assert note.read_text(encoding="utf-8") == "Body\n"
    assert controller.state.active_file_missing_on_disk is False

    await controller.shutdown()


@pytest.mark.asyncio
async def test_controller_blocks_model_runs_until_new_file_is_saved(tmp_path: Path) -> None:
    note = tmp_path / "new-note.md"
    note_runner = _FakeNoteRunner(note)
    controller = TuiController(
        active_file=note,
        allow_missing_active_file=True,
        note_runner=note_runner,
        chat_runner=_FakeChatRunner(),
    )
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    controller._prompt_buffer.text = "Do the thing."
    await controller.send_prompt()

    assert controller._run_task is None
    assert controller.state.indicator_kind == "error"
    assert controller.state.indicator_message == "Save the new file before running Aunic."
    assert note_runner.requests == []


@pytest.mark.asyncio
async def test_controller_skips_watch_until_new_file_is_saved(tmp_path: Path) -> None:
    note = tmp_path / "new-note.md"
    file_manager = _NewFileWatchingFileManager(note)
    controller = TuiController(
        active_file=note,
        allow_missing_active_file=True,
        file_manager=file_manager,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
    )
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    await controller.start_watch_task()

    assert controller._watch_task is None
    assert file_manager.watch_started == 0

    controller._editor_buffer.text = "Body\n"
    assert await controller.save_active_file() is True
    await asyncio.sleep(0)

    assert controller._watch_task is not None
    assert file_manager.watch_started == 1

    await controller.shutdown()


@pytest.mark.asyncio
async def test_controller_permission_prompt_uses_dialog_selection_and_resolves(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("Original\n", encoding="utf-8")
    controller = TuiController(active_file=note, note_runner=_FakeNoteRunner(note), chat_runner=_FakeChatRunner())
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    task = asyncio.create_task(
        controller.request_tool_permission(
            PermissionRequest(
                tool_name="bash",
                action="execute",
                target="/tmp",
                message="bash wants permission",
                policy="ask",
            )
        )
    )
    await asyncio.sleep(0)

    assert controller.state.active_dialog == "permission_prompt"
    assert controller.state.dialog_selection_index == 0

    controller.move_dialog_selection(1)
    await controller.activate_dialog_selection()

    assert await task == "always"
    assert controller.state.active_dialog is None
    assert controller.state.permission_prompt is None


@pytest.mark.asyncio
async def test_controller_close_dialog_rejects_permission_prompt(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("Original\n", encoding="utf-8")
    controller = TuiController(active_file=note, note_runner=_FakeNoteRunner(note), chat_runner=_FakeChatRunner())
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    task = asyncio.create_task(
        controller.request_tool_permission(
            PermissionRequest(
                tool_name="read",
                action="read",
                target="/tmp/file.txt",
                message="read wants permission",
                policy="ask",
            )
        )
    )
    await asyncio.sleep(0)

    controller.close_dialog()

    assert await task == "reject"
    assert controller.state.active_dialog is None
    assert controller.state.permission_prompt is None


def test_controller_builds_openai_profile_model_options_from_proto_settings(tmp_path: Path) -> None:
    settings_dir = tmp_path / ".aunic"
    settings_dir.mkdir()
    (settings_dir / "proto-settings.json").write_text(
        (
            "{\n"
            '  "selected_openai_compatible_profile": "openrouter_nemo",\n'
            '  "openai_compatible_profiles": {\n'
            '    "llama_addie": {\n'
            '      "provider_label": "Llama",\n'
            '      "custom_model_name": "Addie",\n'
            '      "model": "local-model",\n'
            '      "base_url": "http://127.0.0.1:8080",\n'
            '      "chat_endpoint": "/v1/chat/completions",\n'
            '      "health_endpoint": "/health",\n'
            '      "startup_script": "/tmp/addie.sh"\n'
            "    },\n"
            '    "openrouter_nemo": {\n'
            '      "provider_label": "OpenRouter",\n'
            '      "custom_model_name": "Nemo",\n'
            '      "model": "nvidia/nemotron-3-super-120b-a12b",\n'
            '      "base_url": "https://openrouter.ai/api/v1",\n'
            '      "chat_endpoint": "/chat/completions",\n'
            '      "api_key": "placeholder"\n'
            "    }\n"
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    note = tmp_path / "note.md"
    note.write_text("Original\n", encoding="utf-8")
    controller = TuiController(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        cwd=tmp_path,
    )

    labels = [option.label for option in controller.state.model_options]
    openai_options = [option for option in controller.state.model_options if option.provider_name == "openai_compatible"]

    assert "Llama Addie" in labels
    assert "OpenRouter Nemo" in labels
    assert [option.profile_id for option in openai_options] == ["llama_addie", "openrouter_nemo"]


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["note", "chat"])
@pytest.mark.parametrize("command", ["/prompt-from-note", "/plan outline the work"])
async def test_controller_rejects_removed_slash_commands(
    tmp_path: Path,
    mode: str,
    command: str,
) -> None:
    note = tmp_path / "slash-command.md"
    note.write_text("Prompt target\n", encoding="utf-8")
    note_runner = _FakeNoteRunner(note)
    controller = TuiController(active_file=note, note_runner=note_runner, chat_runner=_FakeChatRunner())
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()
    controller.state.mode = mode  # type: ignore[assignment]

    controller._prompt_buffer.text = command
    await controller.send_prompt()

    assert controller.state.indicator_kind == "error"
    assert "not available in the terminal UI yet" in controller.state.indicator_message
    assert controller._run_task is None
    assert note_runner.requests == []


@pytest.mark.asyncio
async def test_controller_clears_recent_highlight_on_manual_editor_change(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("Original\n", encoding="utf-8")
    manager = _FakeWatchingFileManager(note, "Original\n", "Updated\n")
    controller = TuiController(
        active_file=note,
        file_manager=manager,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
    )
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    manager.current_text = "Updated\n"
    await controller.handle_progress_event(
        ProgressEvent(
            kind="file_written",
            message="Applied a note edit to disk.",
            path=note,
            details={"tool_name": "replace_block"},
        )
    )

    assert controller.recent_display_change_spans()

    controller._editor_buffer.insert_text("!")

    assert controller.recent_display_change_spans() == ()


@pytest.mark.asyncio
async def test_controller_send_prompt_clears_previous_recent_highlight(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("Original\n", encoding="utf-8")
    note_runner = _FakeNoteRunner(note)
    controller = TuiController(active_file=note, note_runner=note_runner, chat_runner=_FakeChatRunner())
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    controller._recent_display_change_spans = (TextSpan(0, 4),)
    controller._prompt_buffer.text = "Please update it."

    await controller.send_prompt()
    await controller._run_task

    assert controller.recent_display_change_spans() == ()


@pytest.mark.asyncio
async def test_controller_note_mode_status_mentions_completed_synthesis(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("Original\n", encoding="utf-8")
    controller = TuiController(
        active_file=note,
        note_runner=_SynthesisNoteRunner(note),
        chat_runner=_FakeChatRunner(),
    )
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    controller._prompt_buffer.text = "Please update it."
    await controller.send_prompt()
    await controller._run_task

    assert "Synthesis complete." in controller.state.indicator_message


@pytest.mark.asyncio
async def test_controller_prompts_on_dirty_file_switch(tmp_path: Path) -> None:
    first = tmp_path / "one.md"
    second = tmp_path / "two.md"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("two\n", encoding="utf-8")
    controller = TuiController(active_file=first, included_files=(second,), note_runner=_FakeNoteRunner(first), chat_runner=_FakeChatRunner())
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    controller._editor_buffer.text = "changed\n"
    await controller.request_file_switch(second)

    assert controller.state.active_dialog == "file_switch_confirm"
    await controller.confirm_file_switch(save_changes=False)
    assert controller.state.active_file == second


@pytest.mark.asyncio
async def test_controller_work_toggle_and_model_cycle(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body\n", encoding="utf-8")
    controller = TuiController(active_file=note, note_runner=_FakeNoteRunner(note), chat_runner=_FakeChatRunner())

    original_label = controller.state.selected_model.label
    await controller.toggle_work_mode()
    controller.cycle_model()

    assert controller.state.work_mode == "read"
    assert "Work mode set to read." in controller.state.indicator_message
    assert controller.state.selected_model.label != original_label


@pytest.mark.asyncio
async def test_controller_fold_and_unfold_at_cursor(tmp_path: Path) -> None:
    note = tmp_path / "fold.md"
    note.write_text("# Heading\n\none\n\ntwo\n", encoding="utf-8")
    controller = TuiController(active_file=note, note_runner=_FakeNoteRunner(note), chat_runner=_FakeChatRunner())
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    controller._editor_buffer.cursor_position = 0
    controller.fold_at_cursor()
    assert "▶ " in controller.current_editor_text()

    controller._editor_buffer.cursor_position = 0
    controller.unfold_at_cursor()
    assert controller.current_editor_text() == "# Heading\n\none\n\ntwo\n"


@pytest.mark.asyncio
async def test_controller_auto_reloads_clean_external_changes(tmp_path: Path) -> None:
    note = tmp_path / "watch.md"
    manager = _FakeWatchingFileManager(note, "old\n", "new\n")
    controller = TuiController(
        active_file=note,
        file_manager=manager,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
    )
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    await controller._watch_files()

    assert controller.current_editor_text() == "new\n"
    assert controller.state.indicator_kind == "status"


@pytest.mark.asyncio
async def test_controller_warns_on_dirty_external_changes(tmp_path: Path) -> None:
    note = tmp_path / "watch-dirty.md"
    manager = _FakeWatchingFileManager(note, "old\n", "new\n")
    controller = TuiController(
        active_file=note,
        file_manager=manager,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
    )
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()
    controller._editor_buffer.text = "dirty\n"

    await controller._watch_files()

    assert controller.state.pending_external_reload is True
    assert controller.state.active_dialog == "reload_confirm"
    assert controller.state.indicator_kind == "error"


@pytest.mark.asyncio
async def test_controller_new_search_results_section_loads_folded_by_default(tmp_path: Path) -> None:
    note = tmp_path / "search-results.md"
    manager = _FakeWatchingFileManager(
        note,
        "# Body\n\nhello\n",
        "# Body\n\nhello\n\n# Search Results\n\nbatch\n",
    )
    controller = TuiController(
        active_file=note,
        file_manager=manager,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
    )
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    await controller._load_active_file(reset_dirty=True)
    assert "▶ " not in controller.current_editor_text()

    manager.current_text = manager.next_text
    await controller._load_active_file(reset_dirty=True)

    assert "▶ " in controller.current_editor_text()


@pytest.mark.asyncio
async def test_controller_existing_unfolded_search_results_stay_unfolded_on_reload(tmp_path: Path) -> None:
    note = tmp_path / "search-results-open.md"
    manager = _FakeWatchingFileManager(
        note,
        "# Search Results\n\nold batch\n",
        "# Search Results\n\nold batch\n\nnew batch\n",
    )
    controller = TuiController(
        active_file=note,
        file_manager=manager,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
    )
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    controller.state.fold_state[note] = set()
    manager.current_text = manager.next_text
    await controller._load_active_file(reset_dirty=True)

    assert "▶ " not in controller.current_editor_text()


@pytest.mark.asyncio
async def test_controller_existing_folded_search_results_stay_folded_on_reload(tmp_path: Path) -> None:
    note = tmp_path / "search-results-folded.md"
    manager = _FakeWatchingFileManager(
        note,
        "# Search Results\n\nold batch\n",
        "# Search Results\n\nold batch\n\nnew batch\n",
    )
    controller = TuiController(
        active_file=note,
        file_manager=manager,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
    )
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    manager.current_text = manager.next_text
    await controller._load_active_file(reset_dirty=True)

    assert "▶ " in controller.current_editor_text()


@pytest.mark.asyncio
async def test_controller_splits_note_content_from_transcript_and_filters_rows(tmp_path: Path) -> None:
    note = tmp_path / "transcript.md"
    note.write_text(
        "# Body\n\nhello\n\n"
        "---\n"
        "# Transcript\n"
        "| # | role      | type        | tool_name  | tool_id  | content\n"
        "|---|-----------|-------------|------------|----------|-------------------------------\n"
        '| 1 | user      | message     |            |          | "Question"\n'
        '| 2 | assistant | tool_call   | web_search | call_1   | {"queries":["python homepage"]}\n'
        '| 3 | tool      | tool_result | web_search | call_1   | [{"url":"https://www.python.org/","title":"Python","snippet":"Official"}]\n'
        '| 4 | assistant | message     |            |          | "Answer"\n',
        encoding="utf-8",
    )
    controller = TuiController(active_file=note, note_runner=_FakeNoteRunner(note), chat_runner=_FakeChatRunner())
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    assert controller.current_editor_text() == "# Body\n\nhello"
    assert controller.has_transcript() is True
    assert [row.row_number for row in controller.visible_transcript_rows()] == [1, 3, 4]

    controller.set_transcript_filter("chat")
    assert [row.row_number for row in controller.visible_transcript_rows()] == [1, 4]

    controller.set_transcript_filter("tools")
    assert [row.row_number for row in controller.visible_transcript_rows()] == [3]

    controller.set_transcript_filter("search")
    assert [row.row_number for row in controller.visible_transcript_rows()] == [3]

    controller.toggle_transcript_sort()
    assert [row.row_number for row in controller.visible_transcript_rows()] == [3]


@pytest.mark.asyncio
async def test_controller_delete_transcript_row_cascades_tool_pair(tmp_path: Path) -> None:
    note = tmp_path / "delete-transcript.md"
    note.write_text(
        "# Body\n\nhello\n\n"
        "---\n"
        "# Transcript\n"
        "| # | role      | type        | tool_name  | tool_id  | content\n"
        "|---|-----------|-------------|------------|----------|-------------------------------\n"
        '| 1 | user      | message     |            |          | "Question"\n'
        '| 2 | assistant | tool_call   | web_search | user_001 | {"queries":["python homepage"]}\n'
        '| 3 | tool      | tool_result | web_search | user_001 | [{"url":"https://www.python.org/","title":"Python","snippet":"Official"}]\n'
        '| 4 | assistant | message     |            |          | "Answer"\n',
        encoding="utf-8",
    )
    controller = TuiController(active_file=note, note_runner=_FakeNoteRunner(note), chat_runner=_FakeChatRunner())
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    await controller.delete_transcript_row(3)

    rows = parse_transcript_rows(note.read_text(encoding="utf-8"))
    assert [(row.row_number, row.type, row.tool_name) for row in rows] == [
        (1, "message", None),
        (2, "message", None),
    ]
    assert [row.row_number for row in controller.visible_transcript_rows()] == [1, 2]


@pytest.mark.asyncio
async def test_controller_editor_changes_preserve_transcript_section(tmp_path: Path) -> None:
    note = tmp_path / "preserve-transcript.md"
    note.write_text(
        "# Body\n\nhello\n\n"
        "---\n"
        "# Transcript\n"
        "| # | role      | type        | tool_name  | tool_id  | content\n"
        "|---|-----------|-------------|------------|----------|-------------------------------\n"
        '| 1 | user      | message     |            |          | "Question"\n',
        encoding="utf-8",
    )
    controller = TuiController(active_file=note, note_runner=_FakeNoteRunner(note), chat_runner=_FakeChatRunner())
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    controller._editor_buffer.text = "# Body\n\nchanged\n"

    assert "# Transcript" in controller._full_text
    assert '"Question"' in controller._full_text


@pytest.mark.asyncio
async def test_controller_web_search_persists_synthetic_transcript_rows(tmp_path: Path) -> None:
    note = tmp_path / "web-search.md"
    note.write_text("# Body\n\nhello\n", encoding="utf-8")
    search_batch = SearchBatch(
        queries=("python homepage",),
        depth="quick",
        freshness="none",
        purpose="python homepage",
        results=(
            SearchResult(
                source_id="s1",
                title="Python",
                url="https://www.python.org/",
                canonical_url="https://www.python.org/",
                snippet="Official Python website",
                rank=1,
                engine_count=2,
            ),
        ),
    )
    controller = TuiController(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        search_service=_FakeSearchService(search_batch),
        fetch_service=_FakeFetchService(),
    )
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    controller._prompt_buffer.text = "@web python homepage"
    await controller.send_prompt()
    await controller._run_task

    rows = parse_transcript_rows(note.read_text(encoding="utf-8"))
    assert [(row.type, row.tool_name, row.tool_id) for row in rows] == [
        ("tool_call", "web_search", "user_001"),
        ("tool_result", "web_search", "user_001"),
    ]
    assert rows[0].content == {"queries": ["python homepage"]}
    assert rows[1].content == [
        {
            "url": "https://www.python.org/",
            "title": "Python",
            "snippet": "Official Python website",
        }
    ]
    assert "# Search Results" not in note.read_text(encoding="utf-8")
    assert controller.state.web_mode == "results"
    assert len(controller._web_results) == 1


@pytest.mark.asyncio
async def test_controller_web_fetch_and_chunk_insertion_preserve_transcript_section(tmp_path: Path) -> None:
    note = tmp_path / "web-fetch.md"
    note.write_text("# Body\n\nhello\n", encoding="utf-8")
    search_batch = SearchBatch(
        queries=("python homepage",),
        depth="quick",
        freshness="none",
        purpose="python homepage",
        results=(
            SearchResult(
                source_id="s1",
                title="Python",
                url="https://www.python.org/",
                canonical_url="https://www.python.org/",
                snippet="Official Python website",
                rank=1,
            ),
        ),
    )
    fetch_packet = FetchPacket(
        source_id="s1",
        title="Python",
        url="https://www.python.org/",
        canonical_url="https://www.python.org/",
        desired_info="python homepage",
        chunks=(
            FetchedChunk(
                source_id="s1",
                title="Python",
                url="https://www.python.org/",
                canonical_url="https://www.python.org/",
                text="First selected chunk.",
                score=1.0,
            ),
            FetchedChunk(
                source_id="s1",
                title="Python",
                url="https://www.python.org/",
                canonical_url="https://www.python.org/",
                text="Second selected chunk.",
                score=0.8,
            ),
        ),
    )
    fetch_summary = PageFetchResult(
        url="https://www.python.org/",
        canonical_url="https://www.python.org/",
        title="Python",
        snippet="Official Python website",
        markdown="# Python\n\nFirst selected chunk.\n\nSecond selected chunk.\n",
    )
    controller = TuiController(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        search_service=_FakeSearchService(search_batch),
        fetch_service=_FakeFetchService(fetch_packet, summary=fetch_summary),
    )
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    controller._prompt_buffer.text = "@web python homepage"
    await controller.send_prompt()
    await controller._run_task

    controller.web_space_pressed()
    controller._handle_web_send()
    await controller._run_task

    rows = parse_transcript_rows(note.read_text(encoding="utf-8"))
    assert [(row.type, row.tool_name, row.tool_id) for row in rows] == [
        ("tool_call", "web_search", "user_001"),
        ("tool_result", "web_search", "user_001"),
        ("tool_call", "web_fetch", "user_002"),
        ("tool_result", "web_fetch", "user_002"),
    ]
    assert rows[3].content == {
        "url": "https://www.python.org/",
        "title": "Python",
        "snippet": "Official Python website",
    }
    assert controller.state.web_mode == "chunks"

    controller.web_space_pressed()
    controller.web_move_cursor(1)
    controller.web_space_pressed()
    controller._handle_web_send()
    await controller._run_task

    text = note.read_text(encoding="utf-8")
    note_text, transcript_text = text.split("\n\n---\n# Transcript\n", maxsplit=1)
    assert note_text == (
        "# Body\n\nhello\n\n# Python\n\nFirst selected chunk.\n\nSecond selected chunk."
    )
    assert "| 1  | assistant | tool_call" in transcript_text
    assert "| 4  | tool      | tool_result | web_fetch" in transcript_text
    assert controller.current_editor_text() == note_text


@pytest.mark.asyncio
async def test_controller_web_search_failure_persists_tool_error(tmp_path: Path) -> None:
    note = tmp_path / "web-search-error.md"
    note.write_text("# Body\n\nhello\n", encoding="utf-8")
    failure_batch = SearchBatch(
        queries=("python homepage",),
        depth="quick",
        freshness="none",
        purpose="python homepage",
        results=(),
        failures=(
            SearchQueryFailure(
                query="python homepage",
                attempted_engines=("google",),
                message="engine timeout",
            ),
        ),
    )
    controller = TuiController(
        active_file=note,
        note_runner=_FakeNoteRunner(note),
        chat_runner=_FakeChatRunner(),
        search_service=_FakeSearchService(failure_batch),
        fetch_service=_FakeFetchService(),
    )
    controller.attach_buffers(editor_buffer=Buffer(), prompt_buffer=Buffer())
    await controller.initialize()

    controller._prompt_buffer.text = "@web python homepage"
    await controller.send_prompt()
    await controller._run_task

    rows = parse_transcript_rows(note.read_text(encoding="utf-8"))
    assert [(row.type, row.tool_name, row.tool_id) for row in rows] == [
        ("tool_call", "web_search", "user_001"),
        ("tool_error", "web_search", "user_001"),
    ]
    assert rows[1].content == {
        "category": "validation_error",
        "reason": "search_failed",
        "message": "engine timeout",
        "queries": ["python homepage"],
    }
    assert controller.state.web_mode == "idle"
    assert "# Search Results" not in note.read_text(encoding="utf-8")
