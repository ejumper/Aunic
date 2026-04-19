from __future__ import annotations

from pathlib import Path

import pytest

from aunic.context.file_manager import FileManager
from aunic.research.types import ResearchState
from aunic.tools.note_edit import build_note_tool_registry
from aunic.tools.plan import PlanCreateArgs, execute_plan_create
from aunic.tools.runtime import RunToolContext, ToolSessionState


def test_planning_registry_excludes_mutating_tools_and_keeps_plan_tools() -> None:
    registry = build_note_tool_registry(work_mode="work", planning_status="drafting")
    names = {definition.spec.name for definition in registry}

    assert {"plan_create", "plan_write", "plan_edit", "exit_plan"} <= names
    assert {"read", "grep", "glob", "list"} <= names
    assert "note_edit" not in names
    assert "note_write" not in names
    assert "edit" not in names
    assert "write" not in names
    assert "bash" not in names


@pytest.mark.asyncio
async def test_note_snapshot_includes_plan_draft_only_while_planning(tmp_path: Path) -> None:
    note = tmp_path / "task.md"
    note.write_text("# Task\n\nContext.\n", encoding="utf-8")
    runtime = await RunToolContext.create(
        file_manager=FileManager(),
        context_result=None,
        prompt_run=None,
        active_file=note,
        session_state=ToolSessionState(cwd=tmp_path),
        search_service=object(),
        fetch_service=object(),
        research_state=ResearchState(),
        progress_sink=None,
        work_mode="read",
        permission_handler=None,
        metadata={"cwd": str(tmp_path)},
    )
    await execute_plan_create(runtime, PlanCreateArgs(title="Plan", content="# Plan\n\nDraft.\n"))

    snapshot = runtime.note_snapshot_text()
    runtime.set_planning_status("none")
    non_planning_snapshot = runtime.note_snapshot_text()

    assert "NOTE SNAPSHOT" in snapshot
    assert "PLAN DRAFT" in snapshot
    assert "Draft." in snapshot
    assert "PLAN DRAFT" not in non_planning_snapshot
