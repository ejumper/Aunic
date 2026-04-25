from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

TaskStatus = Literal["pending", "in_progress", "completed"]
TASK_STATUSES: frozenset[str] = frozenset({"pending", "in_progress", "completed"})

HIGH_WATER_MARK_FILE = ".highwatermark"


@dataclass
class Task:
    id: str
    subject: str
    description: str
    active_form: str | None = None
    status: TaskStatus = "pending"
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "status": self.status,
            "blocks": list(self.blocks),
            "blockedBy": list(self.blocked_by),
        }
        if self.active_form is not None:
            payload["activeForm"] = self.active_form
        if self.metadata is not None:
            payload["metadata"] = dict(self.metadata)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        status = data.get("status", "pending")
        if status not in TASK_STATUSES:
            raise ValueError(f"Unknown task status: {status!r}")
        task_id = data.get("id")
        subject = data.get("subject")
        description = data.get("description")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("Task `id` must be a non-empty string.")
        if not isinstance(subject, str):
            raise ValueError("Task `subject` must be a string.")
        if not isinstance(description, str):
            raise ValueError("Task `description` must be a string.")
        blocks = data.get("blocks", [])
        blocked_by = data.get("blockedBy", [])
        if not isinstance(blocks, list) or not all(isinstance(x, str) for x in blocks):
            raise ValueError("Task `blocks` must be a list of strings.")
        if not isinstance(blocked_by, list) or not all(isinstance(x, str) for x in blocked_by):
            raise ValueError("Task `blockedBy` must be a list of strings.")
        active_form = data.get("activeForm")
        if active_form is not None and not isinstance(active_form, str):
            raise ValueError("Task `activeForm` must be a string or omitted.")
        metadata = data.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("Task `metadata` must be an object or omitted.")
        return cls(
            id=task_id,
            subject=subject,
            description=description,
            active_form=active_form,
            status=status,
            blocks=list(blocks),
            blocked_by=list(blocked_by),
            metadata=dict(metadata) if metadata is not None else None,
        )


def tasks_dir_for(note_path: Path) -> Path:
    """Return the `.aunic/tasks/` directory for the given note path."""
    return note_path.parent / ".aunic" / "tasks"


def task_path(note_path: Path, task_id: str) -> Path:
    return tasks_dir_for(note_path) / f"{task_id}.json"


def high_water_mark_path(note_path: Path) -> Path:
    return tasks_dir_for(note_path) / HIGH_WATER_MARK_FILE


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _read_high_water_mark(note_path: Path) -> int:
    path = high_water_mark_path(note_path)
    try:
        content = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return 0
    except OSError:
        return 0
    try:
        value = int(content)
    except ValueError:
        return 0
    return value if value >= 0 else 0


def _write_high_water_mark(note_path: Path, value: int) -> None:
    _atomic_write_text(high_water_mark_path(note_path), str(value))


def _find_highest_task_id(note_path: Path) -> int:
    directory = tasks_dir_for(note_path)
    highest = 0
    try:
        entries = list(directory.iterdir())
    except FileNotFoundError:
        return 0
    for entry in entries:
        if not entry.is_file() or entry.suffix != ".json":
            continue
        stem = entry.stem
        if not stem.isdigit():
            continue
        value = int(stem)
        if value > highest:
            highest = value
    return highest


def _write_task_file(note_path: Path, task: Task) -> None:
    path = task_path(note_path, task.id)
    text = json.dumps(task.to_dict(), indent=2, ensure_ascii=False) + "\n"
    _atomic_write_text(path, text)


def _read_task_file(path: Path) -> Task | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return Task.from_dict(data)
    except ValueError:
        return None


@dataclass(frozen=True)
class TaskDraft:
    subject: str
    description: str
    active_form: str | None = None
    metadata: dict[str, Any] | None = None


def create_task(note_path: Path, draft: TaskDraft) -> Task:
    """Create a new task with status='pending' and return it."""
    tasks_dir_for(note_path).mkdir(parents=True, exist_ok=True)

    highest_on_disk = _find_highest_task_id(note_path)
    hwm = _read_high_water_mark(note_path)
    next_id_int = max(highest_on_disk, hwm) + 1
    next_id = str(next_id_int)

    task = Task(
        id=next_id,
        subject=draft.subject,
        description=draft.description,
        active_form=draft.active_form,
        status="pending",
        blocks=[],
        blocked_by=[],
        metadata=dict(draft.metadata) if draft.metadata is not None else None,
    )
    _write_task_file(note_path, task)
    _write_high_water_mark(note_path, next_id_int)
    return task


def get_task(note_path: Path, task_id: str) -> Task | None:
    return _read_task_file(task_path(note_path, task_id))


def list_tasks(note_path: Path) -> list[Task]:
    directory = tasks_dir_for(note_path)
    try:
        entries = list(directory.iterdir())
    except FileNotFoundError:
        return []
    tasks: list[Task] = []
    for entry in entries:
        if not entry.is_file() or entry.suffix != ".json":
            continue
        if entry.name.startswith("."):
            continue
        task = _read_task_file(entry)
        if task is not None:
            tasks.append(task)
    tasks.sort(key=lambda t: (_id_sort_key(t.id), t.id))
    return tasks


def _id_sort_key(task_id: str) -> tuple[int, int]:
    if task_id.isdigit():
        return (0, int(task_id))
    return (1, 0)


@dataclass(frozen=True)
class TaskUpdates:
    subject: str | None = None
    description: str | None = None
    active_form: str | None = None
    status: TaskStatus | None = None
    metadata: dict[str, Any] | None = None

    def has_changes(self) -> bool:
        return any(
            value is not None
            for value in (
                self.subject,
                self.description,
                self.active_form,
                self.status,
                self.metadata,
            )
        )


def update_task(note_path: Path, task_id: str, updates: TaskUpdates) -> Task | None:
    task = get_task(note_path, task_id)
    if task is None:
        return None
    changed = False
    if updates.subject is not None and updates.subject != task.subject:
        task.subject = updates.subject
        changed = True
    if updates.description is not None and updates.description != task.description:
        task.description = updates.description
        changed = True
    if updates.active_form is not None and updates.active_form != task.active_form:
        task.active_form = updates.active_form
        changed = True
    if updates.status is not None and updates.status != task.status:
        task.status = updates.status
        changed = True
    if updates.metadata is not None:
        merged = dict(task.metadata or {})
        for key, value in updates.metadata.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        task.metadata = merged if merged else None
        changed = True
    if changed:
        _write_task_file(note_path, task)
    return task


def delete_task(note_path: Path, task_id: str) -> bool:
    """Delete a task; cascade-remove its id from every other task's blocks/blockedBy."""
    path = task_path(note_path, task_id)
    if not path.exists():
        return False

    # Bump the high-water mark BEFORE deleting so a subsequent create_task doesn't
    # reuse this id even if no other tasks exist afterward.
    try:
        numeric_id = int(task_id)
    except ValueError:
        numeric_id = 0
    if numeric_id > 0:
        hwm = _read_high_water_mark(note_path)
        if numeric_id > hwm:
            _write_high_water_mark(note_path, numeric_id)

    try:
        path.unlink()
    except FileNotFoundError:
        return False

    # Cascade: remove this id from every other task's blocks / blocked_by.
    for other in list_tasks(note_path):
        mutated = False
        if task_id in other.blocks:
            other.blocks = [b for b in other.blocks if b != task_id]
            mutated = True
        if task_id in other.blocked_by:
            other.blocked_by = [b for b in other.blocked_by if b != task_id]
            mutated = True
        if mutated:
            _write_task_file(note_path, other)
    return True


def block_task(note_path: Path, from_id: str, to_id: str) -> bool:
    """Record that `from_id` blocks `to_id`. Idempotent; returns True on success."""
    if from_id == to_id:
        return False
    from_task = get_task(note_path, from_id)
    to_task = get_task(note_path, to_id)
    if from_task is None or to_task is None:
        return False
    mutated_from = False
    mutated_to = False
    if to_id not in from_task.blocks:
        from_task.blocks.append(to_id)
        mutated_from = True
    if from_id not in to_task.blocked_by:
        to_task.blocked_by.append(from_id)
        mutated_to = True
    if mutated_from:
        _write_task_file(note_path, from_task)
    if mutated_to:
        _write_task_file(note_path, to_task)
    return True


def get_active_task_label(note_path: Path) -> str | None:
    """Return the `active_form` (or `subject`) of the first in-progress task, if any."""
    try:
        tasks = list_tasks(note_path)
    except OSError:
        return None
    for task in tasks:
        if task.status == "in_progress":
            label = (task.active_form or task.subject or "").strip()
            return label or None
    return None
