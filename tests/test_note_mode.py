from __future__ import annotations

from pathlib import Path

import pytest

from aunic.context import ContextBuildResult, ContextBuildRequest, ContextEngine, FileManager
from aunic.context.types import ParseWarning, PromptRun
from aunic.domain import HealthCheck, TranscriptRow
from aunic.errors import NoteModeError
from aunic.loop import LoopEvent, LoopMetrics, LoopRunResult
from aunic.modes import NoteModeRunRequest, NoteModeRunner
from aunic.providers.base import LLMProvider


class DummyProvider(LLMProvider):
    name = "dummy"

    async def healthcheck(self) -> HealthCheck:
        return HealthCheck(provider=self.name, ok=True, message="ok")

    async def generate(self, request):
        raise AssertionError("Provider.generate should not be called directly in note-mode tests.")


class FakeContextEngine:
    def __init__(self, result: ContextBuildResult) -> None:
        self._result = result
        self.requests = []

    async def build_context(self, request: ContextBuildRequest) -> ContextBuildResult:
        self.requests.append(request)
        return self._result


class FakeToolLoop:
    def __init__(self, results: list[LoopRunResult], *, mutate_file=None) -> None:
        self._results = list(results)
        self._mutate_file = mutate_file
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        if self._mutate_file is not None:
            self._mutate_file(len(self.requests), request)
        return self._results.pop(0)


def _finished_loop_result(
    snapshot,
    *,
    events: tuple[LoopEvent, ...] = (),
    run_log: tuple[TranscriptRow, ...] = (),
    run_log_new_start: int = 0,
) -> LoopRunResult:
    return LoopRunResult(
        stop_reason="finished",
        events=events,
        metrics=LoopMetrics(
            valid_turn_count=1,
            successful_edit_count=0,
            stop_reason="finished",
        ),
        tool_failures=(),
        final_file_snapshots=(snapshot,),
        run_log=run_log,
        run_log_new_start=run_log_new_start,
    )


def _stopped_loop_result(snapshot, stop_reason: str) -> LoopRunResult:
    return LoopRunResult(
        stop_reason=stop_reason,
        events=(),
        metrics=LoopMetrics(stop_reason=stop_reason),
        tool_failures=(),
        final_file_snapshots=(snapshot,),
    )


def _prompt_run(
    *,
    index: int,
    prompt_text: str,
    mode: str,
    model_input_text: str,
    source_path: Path | None = None,
) -> PromptRun:
    return PromptRun(
        index=index,
        prompt_text=prompt_text,
        mode=mode,
        per_prompt_budget=4,
        target_map_text="TARGET MAP",
        model_input_text=model_input_text,
        source_path=source_path,
    )


@pytest.mark.asyncio
async def test_note_mode_runner_direct_mode_runs_once_and_refreshes_final_snapshot(
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("Editable text.\n", encoding="utf-8")
    file_manager = FileManager()
    snapshot = await file_manager.read_snapshot(note)
    context_result = ContextBuildResult(
        prompt_runs=(
            _prompt_run(
                index=0,
                prompt_text="Rewrite the note.",
                mode="direct",
                model_input_text="USER PROMPT\nRewrite the note.",
            ),
        ),
        file_snapshots=(snapshot,),
        parsed_files=(),
        structural_nodes=(),
        parsed_note_text="PARSED NOTE TEXT",
        target_map_text="TARGET MAP",
        read_only_map_text="",
        model_input_text="MODEL INPUT",
        warnings=(),
    )

    def mutate_file(call_index: int, request) -> None:
        assert call_index == 1
        note.write_text("Updated text.\n", encoding="utf-8")

    runner = NoteModeRunner(
        context_engine=FakeContextEngine(context_result),
        tool_loop=FakeToolLoop([_finished_loop_result(snapshot)], mutate_file=mutate_file),
        file_manager=file_manager,
    )

    result = await runner.run(
        NoteModeRunRequest(
            active_file=note,
            provider=DummyProvider(),
            user_prompt="Rewrite the note.",
            metadata={"cwd": str(note.parent)},
        )
    )

    assert result.stop_reason == "finished"
    assert result.completed_prompt_runs == 1
    assert result.completed_all_prompts is True
    assert result.final_file_snapshots[0].raw_text == "Updated text.\n"


@pytest.mark.asyncio
async def test_note_mode_runner_uses_initial_prompt_runs_after_live_change(
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("Editable text.\n", encoding="utf-8")
    file_manager = FileManager()
    snapshot = await file_manager.read_snapshot(note)
    context_result = ContextBuildResult(
        prompt_runs=(
            _prompt_run(
                index=0,
                prompt_text="First prompt",
                mode="direct",
                model_input_text="USER PROMPT\nFirst prompt\nPARSED NOTE TEXT\nEditable text.",
                source_path=note,
            ),
            _prompt_run(
                index=1,
                prompt_text="Second prompt",
                mode="direct",
                model_input_text="USER PROMPT\nSecond prompt\nPARSED NOTE TEXT\nEditable text.",
                source_path=note,
            ),
        ),
        file_snapshots=(snapshot,),
        parsed_files=(),
        structural_nodes=(),
        parsed_note_text="PARSED NOTE TEXT",
        target_map_text="TARGET MAP",
        read_only_map_text="",
        model_input_text="MODEL INPUT",
        warnings=(ParseWarning(path=note, code="demo", message="warning", line=1, column=1, offset=0),),
    )

    def mutate_file(call_index: int, request) -> None:
        if call_index == 1:
            note.write_text("Changed live text.\n", encoding="utf-8")

    fake_loop = FakeToolLoop(
        [_finished_loop_result(snapshot), _finished_loop_result(snapshot)],
        mutate_file=mutate_file,
    )
    runner = NoteModeRunner(
        context_engine=FakeContextEngine(context_result),
        tool_loop=fake_loop,
        file_manager=file_manager,
    )

    result = await runner.run(
        NoteModeRunRequest(
            active_file=note,
            provider=DummyProvider(),
            user_prompt="First prompt",
            metadata={"cwd": str(note.parent)},
        )
    )

    assert result.completed_prompt_runs == 2
    assert result.completed_all_prompts is True
    assert len(fake_loop.requests) == 2
    assert "Editable text." in fake_loop.requests[1].prompt_run.model_input_text
    assert "Changed live text." not in fake_loop.requests[1].prompt_run.model_input_text


@pytest.mark.asyncio
async def test_note_mode_runner_stops_on_first_non_finished_prompt_run(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("Editable text.\n", encoding="utf-8")
    file_manager = FileManager()
    snapshot = await file_manager.read_snapshot(note)
    context_result = ContextBuildResult(
        prompt_runs=(
            _prompt_run(index=0, prompt_text="One", mode="direct", model_input_text="one"),
            _prompt_run(index=1, prompt_text="Two", mode="direct", model_input_text="two"),
            _prompt_run(index=2, prompt_text="Three", mode="direct", model_input_text="three"),
        ),
        file_snapshots=(snapshot,),
        parsed_files=(),
        structural_nodes=(),
        parsed_note_text="PARSED NOTE TEXT",
        target_map_text="TARGET MAP",
        read_only_map_text="",
        model_input_text="MODEL INPUT",
        warnings=(),
    )
    fake_loop = FakeToolLoop(
        [
            _finished_loop_result(snapshot),
            _stopped_loop_result(snapshot, "turn_cap_reached"),
            _finished_loop_result(snapshot),
        ]
    )
    runner = NoteModeRunner(
        context_engine=FakeContextEngine(context_result),
        tool_loop=fake_loop,
        file_manager=file_manager,
    )

    result = await runner.run(
        NoteModeRunRequest(
            active_file=note,
            provider=DummyProvider(),
            user_prompt="One",
        )
    )

    assert result.stop_reason == "turn_cap_reached"
    assert result.completed_prompt_runs == 1
    assert result.completed_all_prompts is False
    assert len(result.prompt_results) == 2
    assert len(fake_loop.requests) == 2


@pytest.mark.asyncio
async def test_note_mode_runner_rejects_empty_direct_prompt_before_context_build() -> None:
    context_result = ContextBuildResult(
        prompt_runs=(),
        file_snapshots=(),
        parsed_files=(),
        structural_nodes=(),
        parsed_note_text="",
        target_map_text="",
        read_only_map_text="",
        model_input_text="",
        warnings=(),
    )
    fake_engine = FakeContextEngine(context_result)
    fake_loop = FakeToolLoop([])
    runner = NoteModeRunner(context_engine=fake_engine, tool_loop=fake_loop)

    with pytest.raises(NoteModeError, match="non-empty prompt"):
        await runner.run(
            NoteModeRunRequest(
                active_file=Path("/tmp/demo.md"),
                provider=DummyProvider(),
                user_prompt="",
            )
        )

    assert fake_engine.requests == []
    assert fake_loop.requests == []


@pytest.mark.asyncio
async def test_note_mode_runner_rejects_when_context_returns_no_prompt_runs(
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("No prompts here.\n", encoding="utf-8")
    file_manager = FileManager()
    snapshot = await file_manager.read_snapshot(note)
    context_result = ContextBuildResult(
        prompt_runs=(),
        file_snapshots=(snapshot,),
        parsed_files=(),
        structural_nodes=(),
        parsed_note_text="No prompts here.",
        target_map_text="TARGET MAP",
        read_only_map_text="",
        model_input_text="MODEL INPUT",
        warnings=(),
    )
    fake_loop = FakeToolLoop([])
    runner = NoteModeRunner(
        context_engine=FakeContextEngine(context_result),
        tool_loop=fake_loop,
        file_manager=file_manager,
    )

    with pytest.raises(NoteModeError, match="requires a prompt run"):
        await runner.run(
            NoteModeRunRequest(
                active_file=note,
                provider=DummyProvider(),
                user_prompt="Rewrite the note.",
            )
        )

    assert fake_loop.requests == []


@pytest.mark.asyncio
async def test_note_mode_runner_forwards_context_and_loop_request_fields(
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    included = tmp_path / "included.md"
    note.write_text("Editable text.\n", encoding="utf-8")
    included.write_text("Reference.\n", encoding="utf-8")
    file_manager = FileManager()
    snapshot = await file_manager.read_snapshot(note)
    context_result = ContextBuildResult(
        prompt_runs=(
            _prompt_run(index=0, prompt_text="Do the thing", mode="direct", model_input_text="input"),
        ),
        file_snapshots=(snapshot,),
        parsed_files=(),
        structural_nodes=(),
        parsed_note_text="PARSED NOTE TEXT",
        target_map_text="TARGET MAP",
        read_only_map_text="",
        model_input_text="MODEL INPUT",
        warnings=(),
    )
    fake_engine = FakeContextEngine(context_result)
    fake_loop = FakeToolLoop([_finished_loop_result(snapshot)])
    runner = NoteModeRunner(
        context_engine=fake_engine,
        tool_loop=fake_loop,
        file_manager=file_manager,
    )

    await runner.run(
        NoteModeRunRequest(
            active_file=note,
            included_files=(included,),
            provider=DummyProvider(),
            user_prompt="Do the thing",
            total_turn_budget=12,
            model="gpt-5.4",
            reasoning_effort="high",
            display_root=tmp_path,
            metadata={"cwd": str(tmp_path)},
        )
    )

    context_request = fake_engine.requests[0]
    loop_request = fake_loop.requests[0]
    assert context_request.active_file == note
    assert context_request.included_files == (included,)
    assert context_request.user_prompt == "Do the thing"
    assert context_request.total_turn_budget == 12
    assert context_request.display_root == tmp_path
    assert loop_request.model == "gpt-5.4"
    assert loop_request.reasoning_effort == "high"
    assert loop_request.metadata["cwd"] == str(tmp_path)
    assert loop_request.persist_message_rows is False


@pytest.mark.asyncio
async def test_note_mode_runner_runs_synthesis_pass_after_successful_outside_note_work(
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("Editable text.\n", encoding="utf-8")
    file_manager = FileManager()
    snapshot = await file_manager.read_snapshot(note)
    context_result = ContextBuildResult(
        prompt_runs=(
            _prompt_run(index=0, prompt_text="Do the thing", mode="direct", model_input_text="input"),
        ),
        file_snapshots=(snapshot,),
        parsed_files=(),
        structural_nodes=(),
        parsed_note_text="PARSED NOTE TEXT",
        target_map_text="TARGET MAP",
        read_only_map_text="",
        model_input_text="MODEL INPUT",
        warnings=(),
    )
    main_run_log = (
        TranscriptRow(1, "user", "message", content="Do the thing"),
        TranscriptRow(2, "assistant", "tool_call", "read", "call_1", {"file_path": "README.md"}),
        TranscriptRow(3, "tool", "tool_result", "read", "call_1", {"type": "text", "content": "details"}),
        TranscriptRow(4, "assistant", "message", content="Done."),
    )
    fake_loop = FakeToolLoop(
        [
            _finished_loop_result(
                snapshot,
                events=(
                    LoopEvent(
                        kind="tool_result",
                        message="read finished",
                        details={"tool_name": "read", "status": "completed"},
                    ),
                ),
                run_log=main_run_log,
                run_log_new_start=0,
            ),
            _finished_loop_result(snapshot),
        ]
    )
    runner = NoteModeRunner(
        context_engine=FakeContextEngine(context_result),
        tool_loop=fake_loop,
        file_manager=file_manager,
    )

    result = await runner.run(
        NoteModeRunRequest(
            active_file=note,
            provider=DummyProvider(),
            user_prompt="Do the thing",
            work_mode="read",
            metadata={"cwd": str(note.parent)},
        )
    )

    assert result.stop_reason == "finished"
    assert result.synthesis_ran is True
    assert result.synthesis_error is None
    assert len(fake_loop.requests) == 2
    assert fake_loop.requests[0].persist_message_rows is False
    synthesis_request = fake_loop.requests[1]
    assert synthesis_request.work_mode == "off"
    assert synthesis_request.persist_message_rows is False
    assert synthesis_request.system_prompt is not None
    assert "synthesis pass" in synthesis_request.system_prompt
    assert {definition.spec.name for definition in synthesis_request.tool_registry} == {
        "note_edit",
        "note_write",
    }
    assert "RUN LOG" in synthesis_request.prompt_run.user_prompt_text
    assert "tool_call read" in synthesis_request.prompt_run.user_prompt_text


@pytest.mark.asyncio
async def test_note_mode_runner_skips_synthesis_when_only_web_tools_were_used(
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("Editable text.\n", encoding="utf-8")
    file_manager = FileManager()
    snapshot = await file_manager.read_snapshot(note)
    context_result = ContextBuildResult(
        prompt_runs=(
            _prompt_run(index=0, prompt_text="Do the thing", mode="direct", model_input_text="input"),
        ),
        file_snapshots=(snapshot,),
        parsed_files=(),
        structural_nodes=(),
        parsed_note_text="PARSED NOTE TEXT",
        target_map_text="TARGET MAP",
        read_only_map_text="",
        model_input_text="MODEL INPUT",
        warnings=(),
    )
    fake_loop = FakeToolLoop(
        [
            _finished_loop_result(
                snapshot,
                events=(
                    LoopEvent(
                        kind="tool_result",
                        message="web search finished",
                        details={"tool_name": "web_search", "status": "completed"},
                    ),
                ),
            ),
        ]
    )
    runner = NoteModeRunner(
        context_engine=FakeContextEngine(context_result),
        tool_loop=fake_loop,
        file_manager=file_manager,
    )

    result = await runner.run(
        NoteModeRunRequest(
            active_file=note,
            provider=DummyProvider(),
            user_prompt="Do the thing",
            work_mode="read",
        )
    )

    assert result.synthesis_ran is False
    assert len(fake_loop.requests) == 1


@pytest.mark.asyncio
async def test_note_mode_runner_reports_synthesis_error_without_failing_main_run(
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("Editable text.\n", encoding="utf-8")
    file_manager = FileManager()
    snapshot = await file_manager.read_snapshot(note)
    context_result = ContextBuildResult(
        prompt_runs=(
            _prompt_run(index=0, prompt_text="Do the thing", mode="direct", model_input_text="input"),
        ),
        file_snapshots=(snapshot,),
        parsed_files=(),
        structural_nodes=(),
        parsed_note_text="PARSED NOTE TEXT",
        target_map_text="TARGET MAP",
        read_only_map_text="",
        model_input_text="MODEL INPUT",
        warnings=(),
    )

    class ExplodingSynthesisLoop(FakeToolLoop):
        async def run(self, request):
            self.requests.append(request)
            if len(self.requests) == 2:
                raise RuntimeError("boom")
            return self._results.pop(0)

    fake_loop = ExplodingSynthesisLoop(
        [
            _finished_loop_result(
                snapshot,
                events=(
                    LoopEvent(
                        kind="tool_result",
                        message="read finished",
                        details={"tool_name": "read", "status": "completed"},
                    ),
                ),
                run_log=(
                    TranscriptRow(1, "user", "message", content="Do the thing"),
                    TranscriptRow(2, "assistant", "message", content="Done."),
                ),
                run_log_new_start=0,
            ),
        ]
    )
    runner = NoteModeRunner(
        context_engine=FakeContextEngine(context_result),
        tool_loop=fake_loop,
        file_manager=file_manager,
    )

    result = await runner.run(
        NoteModeRunRequest(
            active_file=note,
            provider=DummyProvider(),
            user_prompt="Do the thing",
            work_mode="read",
        )
    )

    assert result.stop_reason == "finished"
    assert result.synthesis_ran is True
    assert result.synthesis_error == "Synthesis pass failed: boom"
    assert len(fake_loop.requests) == 2
