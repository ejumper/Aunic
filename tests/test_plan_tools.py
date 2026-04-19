from __future__ import annotations

from pathlib import Path

import pytest

from aunic.context.file_manager import FileManager
from aunic.research.types import ResearchState
from aunic.tools.plan import (
    ExitPlanArgs,
    PlanCreateArgs,
    PlanEditArgs,
    PlanWriteArgs,
    execute_exit_plan,
    execute_plan_create,
    execute_plan_edit,
    execute_plan_write,
)
from aunic.tools.runtime import PermissionRequest, RunToolContext, ToolSessionState


async def _runtime(
    project_root: Path,
    *,
    permission_handler=None,
) -> RunToolContext:
    note = project_root / "task.md"
    note.write_text("# Task\n\nContext.\n", encoding="utf-8")
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
        work_mode="read",
        permission_handler=permission_handler,
        metadata={"cwd": str(project_root)},
    )


@pytest.mark.asyncio
async def test_plan_create_sets_active_plan_and_writes_file(tmp_path: Path) -> None:
    runtime = await _runtime(tmp_path)

    result = await execute_plan_create(runtime, PlanCreateArgs(title="Migrate Runner"))

    assert result.status == "completed"
    assert runtime.active_plan_id is not None
    assert runtime.active_plan_path == tmp_path / ".aunic" / "plans" / "migrate-runner.md"
    assert runtime.planning_status == "drafting"
    assert runtime.active_plan_path.exists()
    assert result.transcript_content["event"] == "created"


@pytest.mark.asyncio
async def test_plan_write_and_edit_update_active_plan(tmp_path: Path) -> None:
    runtime = await _runtime(tmp_path)
    await execute_plan_create(runtime, PlanCreateArgs(title="Plan", content="# Plan\n\nOld step.\n"))

    write_result = await execute_plan_write(
        runtime,
        PlanWriteArgs(content="# Plan\n\n- First\n- Second\n"),
    )
    edit_result = await execute_plan_edit(
        runtime,
        PlanEditArgs(old_string="- Second", new_string="- Verified second"),
    )

    assert write_result.status == "completed"
    assert edit_result.status == "completed"
    assert runtime.active_plan_path is not None
    content = runtime.active_plan_path.read_text(encoding="utf-8")
    assert "- Verified second" in content
    assert write_result.transcript_content is None
    assert edit_result.transcript_content is None


@pytest.mark.asyncio
async def test_plan_write_conflicts_when_live_plan_changed(tmp_path: Path) -> None:
    runtime = await _runtime(tmp_path)
    await execute_plan_create(runtime, PlanCreateArgs(title="Plan", content="# Plan\n\nOld step.\n"))
    assert runtime.active_plan_path is not None
    runtime.active_plan_path.write_text(
        runtime.active_plan_path.read_text(encoding="utf-8").replace("Old step", "User edit"),
        encoding="utf-8",
    )

    result = await execute_plan_write(runtime, PlanWriteArgs(content="# Plan\n\nModel edit.\n"))

    assert result.status == "tool_error"
    assert result.tool_failure is not None
    assert result.tool_failure.category == "conflict"


@pytest.mark.asyncio
async def test_exit_plan_reads_disk_content_for_approval(tmp_path: Path) -> None:
    captured_requests: list[PermissionRequest] = []

    async def approve(request: PermissionRequest) -> str:
        captured_requests.append(request)
        return "once"

    runtime = await _runtime(tmp_path, permission_handler=approve)
    await execute_plan_create(runtime, PlanCreateArgs(title="Plan", content="# Plan\n\nModel draft.\n"))
    assert runtime.active_plan_path is not None
    runtime.active_plan_path.write_text(
        runtime.active_plan_path.read_text(encoding="utf-8").replace("Model draft", "User edited draft"),
        encoding="utf-8",
    )

    result = await execute_exit_plan(runtime, ExitPlanArgs())

    assert result.status == "completed"
    assert runtime.planning_status == "approved"
    assert runtime.work_mode == "work"
    assert "User edited draft" in captured_requests[0].details["plan_markdown"]
    assert "User edited draft" in result.in_memory_content["plan_markdown"]
    assert result.transcript_content["event"] == "approved"


@pytest.mark.asyncio
async def test_exit_plan_dismiss_keeps_drafting(tmp_path: Path) -> None:
    async def reject(request: PermissionRequest) -> str:
        return "reject"

    runtime = await _runtime(tmp_path, permission_handler=reject)
    await execute_plan_create(runtime, PlanCreateArgs(title="Plan"))

    result = await execute_exit_plan(runtime, ExitPlanArgs())

    assert result.status == "tool_error"
    assert result.tool_failure is not None
    assert result.tool_failure.category == "user_cancel"
    assert runtime.planning_status == "drafting"
