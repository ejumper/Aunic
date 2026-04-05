from __future__ import annotations

from pathlib import Path

import pytest

from aunic.context.types import ContextBuildResult, FileSnapshot, PromptRun, TextSpan
from aunic.domain import ProviderResponse
from aunic.loop.types import LoopEvent, LoopMetrics, LoopRunResult
from aunic.modes import ChatModeRunner, ChatModeRunRequest, NoteModeRunner, NoteModeRunRequest
from aunic.progress import ProgressEvent


def _snapshot(path: Path, text: str) -> FileSnapshot:
    return FileSnapshot(
        path=path,
        raw_text=text,
        revision_id="rev-1",
        content_hash="hash",
        mtime_ns=1,
        size_bytes=len(text.encode("utf-8")),
    )


def _context_result(path: Path) -> ContextBuildResult:
    prompt_run = PromptRun(
        index=0,
        prompt_text="Do the thing",
        mode="direct",
        per_prompt_budget=4,
        target_map_text="view",
        model_input_text="input",
        source_path=path,
        source_raw_span=TextSpan(0, 1),
    )
    snapshot = _snapshot(path, "body\n")
    return ContextBuildResult(
        prompt_runs=(prompt_run,),
        file_snapshots=(snapshot,),
        parsed_files=(),
        structural_nodes=(),
        parsed_note_text="body",
        target_map_text="view",
        read_only_map_text="",
        model_input_text="input",
        warnings=(),
    )


class _FakeContextEngine:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def build_context(self, request) -> ContextBuildResult:
        return _context_result(self.path)


class _FakeToolLoop:
    async def run(self, request) -> LoopRunResult:
        if request.progress_sink is not None:
            await request.progress_sink(
                ProgressEvent(
                    kind="status",
                    message="Loop status update.",
                    path=request.active_file,
                )
            )
        return LoopRunResult(
            stop_reason="finished",
            events=(),
            metrics=LoopMetrics(stop_reason="finished"),
            tool_failures=(),
            final_file_snapshots=(),
        )


class _FakeToolLoopWithConfirmation:
    async def run(self, request) -> LoopRunResult:
        return LoopRunResult(
            stop_reason="finished",
            events=(
                LoopEvent(
                    kind="stop",
                    message="Run completed (note updated).",
                    details={"tool_name": "note_write"},
                ),
            ),
            metrics=LoopMetrics(stop_reason="finished"),
            tool_failures=(),
            final_file_snapshots=(),
        )


class _FakeProvider:
    name = "fake"

    async def healthcheck(self):
        raise NotImplementedError

    async def generate(self, request):
        return ProviderResponse(text="Hello from chat mode.")


@pytest.mark.asyncio
async def test_note_mode_runner_emits_progress_events(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body\n", encoding="utf-8")
    events: list[ProgressEvent] = []
    runner = NoteModeRunner(
        context_engine=_FakeContextEngine(note),
        tool_loop=_FakeToolLoop(),
    )

    await runner.run(
        NoteModeRunRequest(
            active_file=note,
            provider=_FakeProvider(),
            user_prompt="Do the thing",
            progress_sink=events.append,
        )
    )

    assert [event.kind for event in events] == [
        "run_started",
        "prompt_submitted",
        "status",
        "run_finished",
    ]


@pytest.mark.asyncio
async def test_note_mode_runner_persists_usage_log(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body\n", encoding="utf-8")
    runner = NoteModeRunner(
        context_engine=_FakeContextEngine(note),
        tool_loop=_FakeToolLoop(),
    )

    result = await runner.run(
        NoteModeRunRequest(
            active_file=note,
            provider=_FakeProvider(),
            user_prompt="Do the thing",
            metadata={"cwd": str(tmp_path)},
        )
    )

    assert result.usage_log_path is not None
    assert Path(result.usage_log_path).exists()


@pytest.mark.asyncio
async def test_note_mode_runner_prefers_confirmation_text_in_final_progress_event(
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("body\n", encoding="utf-8")
    events: list[ProgressEvent] = []
    runner = NoteModeRunner(
        context_engine=_FakeContextEngine(note),
        tool_loop=_FakeToolLoopWithConfirmation(),
    )

    await runner.run(
        NoteModeRunRequest(
            active_file=note,
            provider=_FakeProvider(),
            user_prompt="Do the thing",
            progress_sink=events.append,
        )
    )

    assert events[-1].kind == "run_finished"
    assert "Note updated." in events[-1].message
    assert events[-1].details["confirmation_text"] == "Note updated."


@pytest.mark.asyncio
async def test_chat_mode_runner_emits_progress_for_prompt_append_and_completion(tmp_path: Path) -> None:
    note = tmp_path / "chat.md"
    note.write_text("body\n", encoding="utf-8")
    events: list[ProgressEvent] = []

    result = await ChatModeRunner().run(
        ChatModeRunRequest(
            active_file=note,
            provider=_FakeProvider(),
            user_prompt="hello",
            progress_sink=events.append,
        )
    )

    assert result.stop_reason == "finished"
    assert [event.kind for event in events][0:3] == [
        "run_started",
        "file_written",
        "prompt_submitted",
    ]
    assert events[-1].kind == "run_finished"
