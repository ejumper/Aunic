from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aunic.tasks import get_task, list_tasks
from aunic.tools.note_edit import build_chat_tool_registry, build_note_tool_registry
from aunic.tools.task_tools import (
    build_task_tool_registry,
    execute_task_create,
    execute_task_get,
    execute_task_list,
    execute_task_update,
    parse_task_create_args,
    parse_task_get_args,
    parse_task_list_args,
    parse_task_update_args,
)


TASK_TOOL_NAMES = {"task_create", "task_get", "task_list", "task_update"}


@pytest.fixture
def note(tmp_path: Path) -> Path:
    path = tmp_path / "note.md"
    path.write_text("# Note\n", encoding="utf-8")
    return path


def _runtime(active_file: Path) -> Any:
    return SimpleNamespace(active_file=active_file)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def test_parse_create_requires_subject_and_description() -> None:
    with pytest.raises(ValueError):
        parse_task_create_args({})
    with pytest.raises(ValueError):
        parse_task_create_args({"subject": "hi"})
    with pytest.raises(ValueError):
        parse_task_create_args({"subject": "", "description": ""})


def test_parse_create_accepts_optional_fields() -> None:
    args = parse_task_create_args(
        {
            "subject": "Do thing",
            "description": "",
            "active_form": "Doing thing",
            "metadata": {"a": 1},
        }
    )
    assert args.subject == "Do thing"
    assert args.active_form == "Doing thing"
    assert args.metadata == {"a": 1}


def test_parse_create_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        parse_task_create_args({"subject": "x", "description": "", "owner": "dan"})


def test_parse_get_requires_task_id() -> None:
    with pytest.raises(ValueError):
        parse_task_get_args({})


def test_parse_list_rejects_params() -> None:
    assert parse_task_list_args({}) is not None
    with pytest.raises(ValueError):
        parse_task_list_args({"status": "pending"})


def test_parse_update_validates_status_enum() -> None:
    with pytest.raises(ValueError):
        parse_task_update_args({"task_id": "1", "status": "bogus"})
    # All legal status values accepted
    for status in ("pending", "in_progress", "completed", "deleted"):
        parse_task_update_args({"task_id": "1", "status": status})


def test_parse_update_string_arrays() -> None:
    args = parse_task_update_args(
        {"task_id": "1", "add_blocks": ["2", "3"], "add_blocked_by": []}
    )
    assert args.add_blocks == ("2", "3")
    assert args.add_blocked_by == ()


def test_parse_update_rejects_empty_string_in_array() -> None:
    with pytest.raises(ValueError):
        parse_task_update_args({"task_id": "1", "add_blocks": ["2", ""]})


# ---------------------------------------------------------------------------
# Execute — roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_create_persists_task(note: Path) -> None:
    result = await execute_task_create(
        _runtime(note),
        parse_task_create_args({"subject": "Write docs", "description": "all of them"}),
    )
    assert result.status == "completed"
    assert result.in_memory_content["task"]["id"] == "1"
    assert result.transcript_content == "Task #1 created: Write docs"

    persisted = get_task(note, "1")
    assert persisted is not None
    assert persisted.subject == "Write docs"
    assert persisted.status == "pending"


@pytest.mark.asyncio
async def test_task_get_roundtrip(note: Path) -> None:
    await execute_task_create(
        _runtime(note),
        parse_task_create_args({"subject": "A", "description": "body"}),
    )
    result = await execute_task_get(_runtime(note), parse_task_get_args({"task_id": "1"}))
    payload = result.in_memory_content
    assert payload["task"]["id"] == "1"
    assert payload["task"]["subject"] == "A"
    assert payload["task"]["status"] == "pending"


@pytest.mark.asyncio
async def test_task_get_missing_returns_null(note: Path) -> None:
    result = await execute_task_get(_runtime(note), parse_task_get_args({"task_id": "99"}))
    assert result.in_memory_content["task"] is None


@pytest.mark.asyncio
async def test_task_list_filters_internal_and_strips_completed_blockers(note: Path) -> None:
    # id 1 done, id 2 blocked by 1 (should report empty blocked_by), id 3 is internal
    await execute_task_create(
        _runtime(note),
        parse_task_create_args({"subject": "first", "description": ""}),
    )
    await execute_task_create(
        _runtime(note),
        parse_task_create_args({"subject": "second", "description": ""}),
    )
    await execute_task_create(
        _runtime(note),
        parse_task_create_args(
            {"subject": "hidden", "description": "", "metadata": {"_internal": True}}
        ),
    )
    # 2 blocked by 1
    await execute_task_update(
        _runtime(note),
        parse_task_update_args({"task_id": "2", "add_blocked_by": ["1"]}),
    )
    await execute_task_update(
        _runtime(note),
        parse_task_update_args({"task_id": "1", "status": "completed"}),
    )

    result = await execute_task_list(_runtime(note), parse_task_list_args({}))
    ids = [t["id"] for t in result.in_memory_content["tasks"]]
    assert ids == ["1", "2"]
    task2 = next(t for t in result.in_memory_content["tasks"] if t["id"] == "2")
    assert task2["blocked_by"] == []  # stripped because #1 is completed


@pytest.mark.asyncio
async def test_task_update_changes_status_and_reports_change(note: Path) -> None:
    await execute_task_create(
        _runtime(note),
        parse_task_create_args({"subject": "A", "description": ""}),
    )
    result = await execute_task_update(
        _runtime(note),
        parse_task_update_args({"task_id": "1", "status": "in_progress"}),
    )
    payload = result.in_memory_content
    assert payload["success"] is True
    assert payload["status_change"] == {"from": "pending", "to": "in_progress"}
    assert "status" in payload["updated_fields"]


@pytest.mark.asyncio
async def test_task_update_delete_removes_task(note: Path) -> None:
    await execute_task_create(
        _runtime(note),
        parse_task_create_args({"subject": "A", "description": ""}),
    )
    await execute_task_update(
        _runtime(note),
        parse_task_update_args({"task_id": "1", "status": "deleted"}),
    )
    assert list_tasks(note) == []


@pytest.mark.asyncio
async def test_task_update_missing_returns_error_payload(note: Path) -> None:
    result = await execute_task_update(
        _runtime(note),
        parse_task_update_args({"task_id": "99", "status": "completed"}),
    )
    payload = result.in_memory_content
    assert payload["success"] is False
    assert payload["error"] == "Task not found."


# ---------------------------------------------------------------------------
# Mode gating
# ---------------------------------------------------------------------------


def _tool_names(registry: tuple) -> set[str]:
    return {tool.spec.name for tool in registry}


def test_task_tools_absent_in_off_mode() -> None:
    registry = build_note_tool_registry(work_mode="off")
    assert TASK_TOOL_NAMES.isdisjoint(_tool_names(registry))


def test_task_tools_present_in_read_mode() -> None:
    registry = build_note_tool_registry(work_mode="read")
    assert TASK_TOOL_NAMES.issubset(_tool_names(registry))


def test_task_tools_present_in_work_mode() -> None:
    registry = build_note_tool_registry(work_mode="work")
    assert TASK_TOOL_NAMES.issubset(_tool_names(registry))


def test_task_tools_present_in_chat_read_and_work_modes() -> None:
    assert TASK_TOOL_NAMES.isdisjoint(_tool_names(build_chat_tool_registry(work_mode="off")))
    assert TASK_TOOL_NAMES.issubset(_tool_names(build_chat_tool_registry(work_mode="read")))
    assert TASK_TOOL_NAMES.issubset(_tool_names(build_chat_tool_registry(work_mode="work")))


def test_build_task_tool_registry_returns_four_tools() -> None:
    registry = build_task_tool_registry()
    assert _tool_names(registry) == TASK_TOOL_NAMES
