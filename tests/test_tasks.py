from __future__ import annotations

import json
from pathlib import Path

import pytest

from aunic.tasks import (
    Task,
    TaskDraft,
    TaskUpdates,
    block_task,
    create_task,
    delete_task,
    get_active_task_label,
    get_task,
    high_water_mark_path,
    list_tasks,
    tasks_dir_for,
    update_task,
)


@pytest.fixture
def note(tmp_path: Path) -> Path:
    path = tmp_path / "note.md"
    path.write_text("# Note\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_task_writes_file_and_bumps_hwm(note: Path) -> None:
    task = create_task(
        note,
        TaskDraft(subject="Fix login", description="Broken", active_form="Fixing login"),
    )
    assert task.id == "1"
    assert task.status == "pending"
    assert (tasks_dir_for(note) / "1.json").is_file()
    assert high_water_mark_path(note).read_text(encoding="utf-8").strip() == "1"


def test_create_task_sequential_ids(note: Path) -> None:
    a = create_task(note, TaskDraft(subject="A", description=""))
    b = create_task(note, TaskDraft(subject="B", description=""))
    c = create_task(note, TaskDraft(subject="C", description=""))
    assert [a.id, b.id, c.id] == ["1", "2", "3"]


def test_create_task_serialises_expected_shape(note: Path) -> None:
    create_task(
        note,
        TaskDraft(
            subject="Run tests",
            description="All of them",
            active_form="Running tests",
            metadata={"priority": "high"},
        ),
    )
    data = json.loads((tasks_dir_for(note) / "1.json").read_text(encoding="utf-8"))
    assert data == {
        "id": "1",
        "subject": "Run tests",
        "description": "All of them",
        "status": "pending",
        "blocks": [],
        "blockedBy": [],
        "activeForm": "Running tests",
        "metadata": {"priority": "high"},
    }


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def test_get_task_returns_none_for_missing(note: Path) -> None:
    assert get_task(note, "42") is None


def test_list_tasks_empty_when_no_dir(note: Path) -> None:
    assert list_tasks(note) == []


def test_list_tasks_sorted_by_numeric_id(note: Path) -> None:
    # Create in non-sequential order by manually writing task files.
    for task_id, subject in [("3", "C"), ("1", "A"), ("2", "B")]:
        create_task(note, TaskDraft(subject=subject, description=""))
    tasks = list_tasks(note)
    assert [t.id for t in tasks] == ["1", "2", "3"]


def test_list_tasks_ignores_non_task_files(note: Path) -> None:
    create_task(note, TaskDraft(subject="A", description=""))
    # hwm is a hidden file — should be skipped
    assert [t.id for t in list_tasks(note)] == ["1"]


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_task_mutates_fields(note: Path) -> None:
    create_task(note, TaskDraft(subject="Subject", description="Body"))
    updated = update_task(
        note,
        "1",
        TaskUpdates(status="in_progress", active_form="Doing it"),
    )
    assert updated is not None
    assert updated.status == "in_progress"
    assert updated.active_form == "Doing it"

    reread = get_task(note, "1")
    assert reread is not None
    assert reread.status == "in_progress"
    assert reread.active_form == "Doing it"


def test_update_task_metadata_merge_and_delete(note: Path) -> None:
    create_task(note, TaskDraft(subject="Subject", description="", metadata={"a": 1, "b": 2}))
    updated = update_task(note, "1", TaskUpdates(metadata={"a": None, "c": 3}))
    assert updated is not None
    assert updated.metadata == {"b": 2, "c": 3}


def test_update_task_missing_returns_none(note: Path) -> None:
    assert update_task(note, "99", TaskUpdates(status="completed")) is None


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete_task_removes_file(note: Path) -> None:
    create_task(note, TaskDraft(subject="A", description=""))
    assert delete_task(note, "1") is True
    assert get_task(note, "1") is None
    assert (tasks_dir_for(note) / "1.json").exists() is False


def test_delete_task_cascades_blocks_and_blocked_by(note: Path) -> None:
    create_task(note, TaskDraft(subject="A", description=""))  # id 1
    create_task(note, TaskDraft(subject="B", description=""))  # id 2
    create_task(note, TaskDraft(subject="C", description=""))  # id 3
    # 1 blocks 2, 3 blocks 1
    block_task(note, "1", "2")
    block_task(note, "3", "1")

    assert delete_task(note, "1") is True

    remaining = {t.id: t for t in list_tasks(note)}
    assert "1" not in remaining
    assert remaining["2"].blocked_by == []
    assert remaining["3"].blocks == []


def test_delete_missing_returns_false(note: Path) -> None:
    assert delete_task(note, "99") is False


def test_delete_preserves_hwm_no_id_reuse(note: Path) -> None:
    a = create_task(note, TaskDraft(subject="A", description=""))
    delete_task(note, a.id)
    b = create_task(note, TaskDraft(subject="B", description=""))
    assert b.id == "2"


# ---------------------------------------------------------------------------
# Block / Dependency
# ---------------------------------------------------------------------------


def test_block_task_sets_symmetric_edges(note: Path) -> None:
    create_task(note, TaskDraft(subject="A", description=""))  # id 1
    create_task(note, TaskDraft(subject="B", description=""))  # id 2
    assert block_task(note, "1", "2") is True
    task_1 = get_task(note, "1")
    task_2 = get_task(note, "2")
    assert task_1 is not None and task_2 is not None
    assert task_1.blocks == ["2"]
    assert task_2.blocked_by == ["1"]


def test_block_task_idempotent(note: Path) -> None:
    create_task(note, TaskDraft(subject="A", description=""))
    create_task(note, TaskDraft(subject="B", description=""))
    block_task(note, "1", "2")
    block_task(note, "1", "2")
    task_1 = get_task(note, "1")
    task_2 = get_task(note, "2")
    assert task_1 is not None and task_2 is not None
    assert task_1.blocks == ["2"]
    assert task_2.blocked_by == ["1"]


def test_block_task_rejects_missing_target(note: Path) -> None:
    create_task(note, TaskDraft(subject="A", description=""))
    assert block_task(note, "1", "99") is False


# ---------------------------------------------------------------------------
# get_active_task_label
# ---------------------------------------------------------------------------


def test_active_task_label_none_when_empty(note: Path) -> None:
    assert get_active_task_label(note) is None


def test_active_task_label_none_when_no_in_progress(note: Path) -> None:
    create_task(note, TaskDraft(subject="A", description=""))
    assert get_active_task_label(note) is None


def test_active_task_label_prefers_active_form(note: Path) -> None:
    create_task(note, TaskDraft(subject="Fix bug", description="", active_form="Fixing bug"))
    update_task(note, "1", TaskUpdates(status="in_progress"))
    assert get_active_task_label(note) == "Fixing bug"


def test_active_task_label_falls_back_to_subject(note: Path) -> None:
    create_task(note, TaskDraft(subject="Fix bug", description=""))
    update_task(note, "1", TaskUpdates(status="in_progress"))
    assert get_active_task_label(note) == "Fix bug"


def test_active_task_label_returns_lowest_id_in_progress(note: Path) -> None:
    create_task(note, TaskDraft(subject="First", description=""))
    create_task(note, TaskDraft(subject="Second", description="", active_form="Second-ing"))
    update_task(note, "2", TaskUpdates(status="in_progress"))
    update_task(note, "1", TaskUpdates(status="in_progress"))
    # Lowest ID wins (list_tasks sorts by numeric id).
    assert get_active_task_label(note) == "First"


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_from_dict_rejects_invalid_status() -> None:
    with pytest.raises(ValueError):
        Task.from_dict(
            {
                "id": "1",
                "subject": "s",
                "description": "d",
                "status": "bogus",
                "blocks": [],
                "blockedBy": [],
            }
        )


def test_get_task_tolerates_corrupt_json(note: Path, tmp_path: Path) -> None:
    directory = tasks_dir_for(note)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "1.json").write_text("not-json", encoding="utf-8")
    assert get_task(note, "1") is None
    assert list_tasks(note) == []
