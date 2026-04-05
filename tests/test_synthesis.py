from __future__ import annotations

from pathlib import Path

import pytest

from aunic.context.types import ContextBuildResult, FileSnapshot, PromptRun
from aunic.domain import HealthCheck, TranscriptRow
from aunic.loop import LoopEvent, LoopMetrics, LoopRunResult
from aunic.modes.synthesis import (
    format_run_log_for_synthesis,
    run_synthesis_pass,
    work_read_tools_were_used,
)
from aunic.providers.base import LLMProvider


class _DummyProvider(LLMProvider):
    name = "dummy"

    async def healthcheck(self) -> HealthCheck:
        return HealthCheck(provider=self.name, ok=True, message="ok")

    async def generate(self, request):
        raise AssertionError("The fake tool loop handles synthesis in these tests.")


class _CapturingToolLoop:
    def __init__(self, result: LoopRunResult) -> None:
        self.result = result
        self.requests = []

    async def run(self, request) -> LoopRunResult:
        self.requests.append(request)
        return self.result


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
        per_prompt_budget=8,
        target_map_text="TARGET MAP",
        model_input_text="MODEL INPUT",
        read_only_map_text="READ-ONLY MAP",
        note_snapshot_text="NOTE SNAPSHOT\nbody",
        user_prompt_text="Do the thing",
        source_path=path,
    )
    snapshot = _snapshot(path, "body\n")
    return ContextBuildResult(
        prompt_runs=(prompt_run,),
        file_snapshots=(snapshot,),
        parsed_files=(),
        structural_nodes=(),
        parsed_note_text="body",
        target_map_text="TARGET MAP",
        read_only_map_text="READ-ONLY MAP",
        model_input_text="MODEL INPUT",
        warnings=(),
        transcript_rows=[],
    )


def test_work_read_tools_were_used_detects_successful_outside_note_tools() -> None:
    events = (
        LoopEvent(
            kind="tool_result",
            message="read finished",
            details={"tool_name": "read", "status": "completed"},
        ),
    )

    assert work_read_tools_were_used(events) is True
    assert work_read_tools_were_used(
        (
            LoopEvent(
                kind="tool_result",
                message="search finished",
                details={"tool_name": "web_search", "status": "completed"},
            ),
        )
    ) is False
    assert work_read_tools_were_used(
        (
            LoopEvent(
                kind="tool_result",
                message="edit failed",
                details={"tool_name": "edit", "status": "tool_error"},
            ),
        )
    ) is False


def test_format_run_log_for_synthesis_renders_messages_and_tool_rows() -> None:
    rows = (
        TranscriptRow(1, "user", "message", content="Plan the update."),
        TranscriptRow(2, "assistant", "tool_call", "read", "call_1", {"file_path": "README.md"}),
        TranscriptRow(3, "tool", "tool_result", "read", "call_1", {"type": "text", "content": "details"}),
        TranscriptRow(4, "assistant", "message", content="Done."),
        TranscriptRow(5, "tool", "tool_error", "bash", "call_2", {"message": "denied"}),
    )

    rendered = format_run_log_for_synthesis(rows)

    assert "[user] Plan the update." in rendered
    assert "[assistant] tool_call read: {\"file_path\":\"README.md\"}" in rendered
    assert "[tool_result read] {\"content\":\"details\",\"type\":\"text\"}" in rendered
    assert "[assistant] Done." in rendered
    assert "[tool_error bash] {\"message\":\"denied\"}" in rendered


@pytest.mark.asyncio
async def test_run_synthesis_pass_builds_note_only_in_memory_request(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body\n", encoding="utf-8")
    context_result = _context_result(note)
    loop_result = LoopRunResult(
        stop_reason="finished",
        events=(),
        metrics=LoopMetrics(stop_reason="finished"),
        tool_failures=(),
        final_file_snapshots=(context_result.file_snapshots[0],),
    )
    tool_loop = _CapturingToolLoop(loop_result)

    result = await run_synthesis_pass(
        tool_loop=tool_loop,
        provider=_DummyProvider(),
        context_result=context_result,
        prompt_run=context_result.prompt_runs[0],
        active_file=note,
        included_files=(),
        model="gpt-5.4",
        reasoning_effort="high",
        progress_sink=None,
        metadata={"cwd": str(tmp_path)},
        note_snapshot_text="body",
        run_log_rows=(
            TranscriptRow(1, "user", "message", content="Do the thing"),
            TranscriptRow(2, "assistant", "message", content="Done."),
        ),
        permission_handler=None,
    )

    assert result.ran is True
    request = tool_loop.requests[0]
    assert request.work_mode == "off"
    assert request.persist_message_rows is False
    assert request.system_prompt is not None
    assert "synthesis pass" in request.system_prompt
    assert {definition.spec.name for definition in request.tool_registry} == {
        "note_edit",
        "note_write",
    }
    assert request.prompt_run.per_prompt_budget == 4
    assert "RUN LOG" in request.prompt_run.user_prompt_text
    assert request.prompt_run.note_snapshot_text == "body"
