from __future__ import annotations

import asyncio
import os
import shlex
from pathlib import Path

import pytest

from aunic.context.file_manager import FileManager
from aunic.domain import TranscriptRow
from aunic.research.types import ResearchState
from aunic.tools.bash import BashArgs, execute_bash
from aunic.tools.note_edit import build_chat_tool_registry, build_note_tool_registry
from aunic.tools.runtime import PermissionRequest, RunToolContext, ToolSessionState
from aunic.tools.stop_process import (
    StopProcessArgs,
    build_stop_process_tool_registry,
    execute_stop_process,
    parse_stop_process_args,
)
from aunic.transcript.flattening import flatten_tool_result_for_provider
from aunic.transcript.parser import parse_transcript_rows


async def _allow_permission(request: PermissionRequest) -> str:
    return "once"


async def _runtime(project_root: Path) -> RunToolContext:
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
        progress_sink=None,
        work_mode="work",
        permission_handler=_allow_permission,
        metadata={"cwd": str(project_root)},
    )


async def _start_background(runtime: RunToolContext, command: str, *, description: str | None = None):
    result = await execute_bash(
        runtime,
        BashArgs(command=command, run_in_background=True, description=description),
    )
    assert result.status == "completed"
    assert result.in_memory_content["background_id"] == "bg-1"
    return runtime.session_state.shell.get_background_process("bg-1")


async def _cleanup_runtime(runtime: RunToolContext) -> None:
    for state in tuple(runtime.session_state.shell.background_processes.values()):
        if state.process.returncode is None:
            await execute_stop_process(
                runtime,
                StopProcessArgs(background_id=state.background_id, force=True, reason="test cleanup"),
            )


def test_parse_stop_process_args_validates_payload() -> None:
    assert parse_stop_process_args({"background_id": " bg-1 "}) == StopProcessArgs(
        background_id="bg-1"
    )

    invalid_payloads = [
        {},
        {"background_id": ""},
        {"background_id": "bg-1", "force": "yes"},
        {"background_id": "bg-1", "grace_ms": True},
        {"background_id": "bg-1", "grace_ms": 1.5},
        {"background_id": "bg-1", "reason": ""},
        {"background_id": "bg-1", "extra": "nope"},
    ]
    for payload in invalid_payloads:
        with pytest.raises(ValueError):
            parse_stop_process_args(payload)


def test_stop_process_tool_is_persistent() -> None:
    registry = build_stop_process_tool_registry()

    assert registry[0].spec.name == "stop_process"
    assert registry[0].persistence == "persistent"


@pytest.mark.asyncio
async def test_stop_process_round_trip_happy_path(tmp_path: Path) -> None:
    runtime = await _runtime(tmp_path)
    try:
        state = await _start_background(runtime, "sleep 60", description="long sleep")
        assert state is not None
        assert state.background_id == "bg-1"
        assert state.pid == state.pgid
        assert state.command == "sleep 60"
        assert state.description == "long sleep"
        assert state.cwd == tmp_path
        assert state.status == "running"

        result = await execute_stop_process(
            runtime,
            StopProcessArgs(background_id="bg-1", reason="done with test"),
        )

        assert result.status == "completed"
        assert result.in_memory_content["status"] == "stopped"
        assert result.in_memory_content["signals_sent"] == ["SIGTERM"]
        assert result.in_memory_content["forced"] is False
        assert result.in_memory_content["exit_code"] not in {None, 0}
        assert state.status == "stopped"
        assert state.stop_reason == "done with test"
    finally:
        await _cleanup_runtime(runtime)


@pytest.mark.asyncio
async def test_stop_process_force_sends_sigkill_and_reports_clamped_grace(
    tmp_path: Path,
) -> None:
    runtime = await _runtime(tmp_path)
    try:
        await _start_background(runtime, "sleep 60")

        result = await execute_stop_process(
            runtime,
            StopProcessArgs(background_id="bg-1", force=True, grace_ms=99_999),
        )

        assert result.status == "completed"
        assert result.in_memory_content["status"] == "stopped"
        assert result.in_memory_content["signals_sent"] == ["SIGKILL"]
        assert result.in_memory_content["forced"] is True
        assert result.in_memory_content["requested_grace_ms"] == 99_999
        assert result.in_memory_content["grace_ms"] == 30_000
        assert result.in_memory_content["clamped_grace_ms"] == 30_000
    finally:
        await _cleanup_runtime(runtime)


@pytest.mark.asyncio
async def test_stop_process_escalates_when_sigterm_is_ignored(tmp_path: Path) -> None:
    runtime = await _runtime(tmp_path)
    ready_file = tmp_path / "term-ignore-ready"
    script = (
        "import pathlib,signal,time; "
        f"pathlib.Path({str(ready_file)!r}).write_text('ready'); "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(60)"
    )
    try:
        await _start_background(
            runtime,
            f"exec python3 -c {shlex.quote(script)}",
        )
        await _wait_for_path(ready_file)

        result = await execute_stop_process(
            runtime,
            StopProcessArgs(background_id="bg-1", grace_ms=200),
        )

        assert result.status == "completed"
        assert result.in_memory_content["status"] == "stopped"
        assert result.in_memory_content["signals_sent"] == ["SIGTERM", "SIGKILL"]
        assert result.in_memory_content["forced"] is True
        assert result.in_memory_content["elapsed_ms"] >= 190
    finally:
        await _cleanup_runtime(runtime)


@pytest.mark.asyncio
async def test_stop_process_reports_already_exited_for_natural_exit(tmp_path: Path) -> None:
    runtime = await _runtime(tmp_path)
    state = await _start_background(runtime, "true")
    assert state is not None
    await state.process.wait()

    result = await execute_stop_process(runtime, StopProcessArgs(background_id="bg-1"))

    assert result.status == "completed"
    assert result.in_memory_content["status"] == "already_exited"
    assert result.in_memory_content["signals_sent"] == []
    assert result.in_memory_content["exit_code"] == 0
    assert state.status == "exited"


@pytest.mark.asyncio
async def test_stop_process_is_idempotent_after_prior_stop(tmp_path: Path) -> None:
    runtime = await _runtime(tmp_path)
    try:
        await _start_background(runtime, "sleep 60")
        first = await execute_stop_process(runtime, StopProcessArgs(background_id="bg-1"))
        second = await execute_stop_process(runtime, StopProcessArgs(background_id="bg-1"))

        assert first.in_memory_content["status"] == "stopped"
        assert second.status == "completed"
        assert second.in_memory_content["status"] == "already_exited"
        assert second.in_memory_content["signals_sent"] == []
    finally:
        await _cleanup_runtime(runtime)


@pytest.mark.asyncio
async def test_stop_process_unknown_id_is_tool_error(tmp_path: Path) -> None:
    runtime = await _runtime(tmp_path)

    result = await execute_stop_process(runtime, StopProcessArgs(background_id="bg-99"))

    assert result.status == "tool_error"
    assert result.tool_failure is not None
    assert result.tool_failure.category == "validation_error"
    assert result.tool_failure.reason == "not_found"
    assert result.in_memory_content["background_id"] == "bg-99"


@pytest.mark.asyncio
async def test_stop_process_reaps_background_process_group(tmp_path: Path) -> None:
    runtime = await _runtime(tmp_path)
    try:
        state = await _start_background(runtime, "sleep 100 & wait")
        assert state is not None
        pgid = state.pgid

        result = await execute_stop_process(runtime, StopProcessArgs(background_id="bg-1"))

        assert result.status == "completed"
        assert await _process_group_gone(pgid)
    finally:
        await _cleanup_runtime(runtime)


def test_stop_process_is_available_in_all_modes() -> None:
    for work_mode in ("off", "read", "work"):
        note_names = {tool.spec.name for tool in build_note_tool_registry(work_mode=work_mode)}
        chat_names = {tool.spec.name for tool in build_chat_tool_registry(work_mode=work_mode)}
        assert "stop_process" in note_names
        assert "stop_process" in chat_names

    off_note_names = {tool.spec.name for tool in build_note_tool_registry(work_mode="off")}
    assert "bash" not in off_note_names
    work_note_names = {tool.spec.name for tool in build_note_tool_registry(work_mode="work")}
    assert "bash" in work_note_names


@pytest.mark.asyncio
async def test_stop_process_persists_reason_and_flattens_transcript(
    tmp_path: Path,
) -> None:
    runtime = await _runtime(tmp_path)
    try:
        await _start_background(runtime, "sleep 60", description="test sleeper")
        result = await execute_stop_process(
            runtime,
            StopProcessArgs(background_id="bg-1", reason="restarting"),
        )
        assert result.status == "completed"
        await runtime.write_transcript_row(
            "tool",
            "tool_result",
            "stop_process",
            "call_2",
            result.in_memory_content,
        )
    finally:
        await _cleanup_runtime(runtime)

    stop_rows = [
        row
        for row in parse_transcript_rows(runtime.active_file.read_text(encoding="utf-8"))
        if row.tool_name == "stop_process" and row.type == "tool_result"
    ]
    assert len(stop_rows) == 1
    stop_row = stop_rows[0]
    assert stop_row.content["type"] == "process_stop"
    assert stop_row.content["reason"] == "restarting"
    flattened = flatten_tool_result_for_provider(stop_row)
    assert "Stopped background command bg-1 (sleep 60)" in flattened
    assert "reason: restarting" in flattened

    in_memory_stop = TranscriptRow(
        row_number=1,
        role="tool",
        type="tool_result",
        tool_name="stop_process",
        tool_id="call_2",
        content=result.in_memory_content,
    )
    assert isinstance(in_memory_stop, TranscriptRow)
    assert in_memory_stop.content["status"] == "stopped"


async def _process_group_gone(pgid: int, *, timeout_seconds: float = 2.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        await asyncio.sleep(0.05)
    return False


async def _wait_for_path(path: Path, *, timeout_seconds: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if path.exists():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"Timed out waiting for {path}")
