from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import aunic.tools.sleep as sleep_module
from aunic.config import AppSettings, SleepSettings
from aunic.context import ContextBuildRequest, ContextEngine
from aunic.context.file_manager import FileManager
from aunic.domain import HealthCheck, ProviderResponse, ToolCall
from aunic.loop import LoopRunRequest, ToolLoop
from aunic.providers.base import LLMProvider
from aunic.research.types import ResearchState
from aunic.tools.runtime import RunToolContext, ToolSessionState
from aunic.tools.sleep import (
    SleepArgs,
    build_sleep_tool_registry,
    execute_sleep,
    parse_sleep_args,
)
from aunic.transcript.parser import parse_transcript_rows


class _SequenceProvider(LLMProvider):
    name = "sequence"

    def __init__(self, responses: list[ProviderResponse]) -> None:
        self._responses = list(responses)

    async def healthcheck(self) -> HealthCheck:
        return HealthCheck(provider=self.name, ok=True, message="ok")

    async def generate(self, request):
        if not self._responses:
            raise AssertionError("Provider received more turns than expected.")
        return self._responses.pop(0)


async def _runtime(
    project_root: Path,
    *,
    progress_sink=None,
) -> RunToolContext:
    note = project_root / "note.md"
    note.write_text("# Note\n\nBody.\n", encoding="utf-8")
    return await RunToolContext.create(
        file_manager=FileManager(),
        context_result=None,
        prompt_run=None,
        active_file=note,
        session_state=ToolSessionState(cwd=project_root),
        search_service=object(),
        fetch_service=object(),
        research_state=ResearchState(),
        progress_sink=progress_sink,
        work_mode="read",
        permission_handler=None,
        metadata={"cwd": str(project_root)},
    )


def _fast_settings(
    *,
    min_ms: int = 1,
    max_ms: int = 1_000,
    reason_after_ms: int = 30_000,
) -> AppSettings:
    return AppSettings(
        sleep=SleepSettings(
            sleep_min_ms=min_ms,
            sleep_max_ms=max_ms,
            sleep_default_poll_ms=1,
            sleep_require_reason_after_ms=reason_after_ms,
        )
    )


def test_parse_sleep_args_accepts_minimal_duration() -> None:
    assert parse_sleep_args({"duration_ms": 100}) == SleepArgs(duration_ms=100, reason=None)


@pytest.mark.parametrize(
    "payload",
    [
        {"duration_ms": -5},
        {"duration_ms": 0},
        {"duration_ms": 1.5},
        {"duration_ms": "100"},
        {"duration_ms": True},
        {"duration_ms": 100, "extra": "nope"},
        {"duration_ms": 100, "reason": ""},
        {"duration_ms": 100, "reason": 123},
    ],
)
def test_parse_sleep_args_rejects_invalid_payloads(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        parse_sleep_args(payload)


def test_parse_sleep_args_requires_reason_for_long_sleep() -> None:
    with pytest.raises(ValueError, match="reason"):
        parse_sleep_args({"duration_ms": 60_000})

    assert parse_sleep_args({"duration_ms": 60_000, "reason": "rate-limit cooldown"}) == SleepArgs(
        duration_ms=60_000,
        reason="rate-limit cooldown",
    )


@pytest.mark.asyncio
async def test_execute_sleep_completes_and_emits_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sleep_module, "SETTINGS", _fast_settings())
    events = []
    runtime = await _runtime(tmp_path, progress_sink=events.append)

    result = await execute_sleep(runtime, SleepArgs(duration_ms=10))

    assert result.status == "completed"
    assert result.in_memory_content["type"] == "sleep_result"
    assert result.in_memory_content["status"] == "completed"
    assert result.in_memory_content["woke_because"] == "timer"
    assert result.in_memory_content["slept_ms"] >= 1
    assert [event.kind for event in events] == ["sleep_started", "sleep_ended"]
    assert events[0].details["duration_ms"] == 10


@pytest.mark.asyncio
async def test_execute_sleep_reports_clamped_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sleep_module, "SETTINGS", _fast_settings(max_ms=5))
    runtime = await _runtime(tmp_path)

    result = await execute_sleep(runtime, SleepArgs(duration_ms=50, reason="test clamp"))

    assert result.status == "completed"
    assert result.in_memory_content["status"] == "clamped"
    assert result.in_memory_content["duration_ms"] == 5
    assert result.in_memory_content["woke_because"] == "max_duration"


@pytest.mark.asyncio
async def test_execute_sleep_reports_clamped_minimum(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sleep_module, "SETTINGS", _fast_settings(min_ms=5))
    runtime = await _runtime(tmp_path)

    result = await execute_sleep(runtime, SleepArgs(duration_ms=1))

    assert result.in_memory_content["status"] == "clamped"
    assert result.in_memory_content["duration_ms"] == 5
    assert result.in_memory_content["woke_because"] == "timer"


@pytest.mark.asyncio
async def test_execute_sleep_re_raises_cancellation_after_end_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sleep_module, "SETTINGS", _fast_settings())
    events = []
    started = asyncio.Event()

    def _sink(event):
        events.append(event)
        if event.kind == "sleep_started":
            started.set()

    runtime = await _runtime(tmp_path, progress_sink=_sink)
    task = asyncio.create_task(execute_sleep(runtime, SleepArgs(duration_ms=500, reason="interrupt test")))
    await started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert [event.kind for event in events] == ["sleep_started", "sleep_ended"]
    assert events[-1].details["status"] == "interrupted"
    assert events[-1].details["woke_because"] == "cancelled"


def test_sleep_tool_is_ephemeral() -> None:
    registry = build_sleep_tool_registry()

    assert registry[0].spec.name == "sleep"
    assert registry[0].persistence == "ephemeral"


@pytest.mark.asyncio
async def test_sleep_tool_result_does_not_persist_to_note_transcript(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sleep_module, "SETTINGS", _fast_settings())
    note = tmp_path / "note.md"
    note.write_text("# Note\n\nOriginal note.\n", encoding="utf-8")
    context_result = await ContextEngine().build_context(
        ContextBuildRequest(active_file=note, user_prompt="Sleep briefly, then update the note.")
    )
    provider = _SequenceProvider(
        [
            ProviderResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        name="sleep",
                        arguments={"duration_ms": 1, "reason": "testing ephemeral persistence"},
                        id="call_1",
                    )
                ],
            ),
            ProviderResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        name="note_write",
                        arguments={"content": "Original note.\n\nDone.\n"},
                        id="call_2",
                    )
                ],
            ),
        ]
    )

    result = await ToolLoop().run(
        LoopRunRequest(
            provider=provider,
            prompt_run=context_result.prompt_runs[0],
            context_result=context_result,
            active_file=note,
            persist_message_rows=False,
        )
    )

    assert result.stop_reason == "finished"
    assert [row.tool_name for row in result.run_log if row.tool_name] == [
        "sleep",
        "sleep",
        "note_write",
        "note_write",
    ]
    rows = parse_transcript_rows(note.read_text(encoding="utf-8"))
    assert all(row.tool_name != "sleep" for row in rows)
