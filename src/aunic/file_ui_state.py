from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from aunic.image_inputs import is_supported_image_path

_TUI_PREFS_PATH = Path.home() / ".aunic" / "tui_prefs.json"
_MAX_FILE_STATE_ENTRIES = 100
_PROJECT_INACTIVE_CHILDREN_KEY = "project_inactive_children"
_PROJECT_ACTIVE_PLAN_ID_KEY = "project_active_plan_id"


@dataclass(frozen=True)
class IncludeEntry:
    path: str
    is_dir: bool
    recursive: bool
    active: bool = True


@dataclass(frozen=True)
class ProjectIncludeState:
    include_entries: tuple[IncludeEntry, ...] = ()
    inactive_children: tuple[str, ...] = ()
    active_plan_id: str | None = None


@dataclass(frozen=True)
class ProjectResolvedContext:
    text_files: tuple[Path, ...] = ()
    image_files: tuple[Path, ...] = ()


def load_file_ui_state(file_path: Path) -> dict[str, Any]:
    data = _read_tui_prefs()
    file_state = data.get("file_state")
    if not isinstance(file_state, dict):
        return {}
    key = str(file_path.resolve())
    entry = file_state.get(key)
    return entry if isinstance(entry, dict) else {}


def save_file_ui_state(file_path: Path, state: dict[str, Any]) -> None:
    data = _read_tui_prefs()
    file_state = data.get("file_state")
    if not isinstance(file_state, dict):
        file_state = {}
    key = str(file_path.resolve())
    file_state[key] = state
    if len(file_state) > _MAX_FILE_STATE_ENTRIES:
        keys = list(file_state)
        for old_key in keys[: len(keys) - _MAX_FILE_STATE_ENTRIES]:
            del file_state[old_key]
    data["file_state"] = file_state
    _write_tui_prefs(data)


def load_project_include_state(file_path: Path) -> ProjectIncludeState:
    state = load_file_ui_state(file_path)
    raw_includes = state.get("includes")
    inactive_children = state.get(_PROJECT_INACTIVE_CHILDREN_KEY)
    active_plan_id = state.get(_PROJECT_ACTIVE_PLAN_ID_KEY)
    return ProjectIncludeState(
        include_entries=tuple(deserialize_include_entries(raw_includes)),
        inactive_children=tuple(_deserialize_inactive_children(inactive_children)),
        active_plan_id=active_plan_id if isinstance(active_plan_id, str) and active_plan_id.strip() else None,
    )


def save_project_include_state(file_path: Path, project_state: ProjectIncludeState) -> None:
    state = load_file_ui_state(file_path)
    state["includes"] = serialize_include_entries(project_state.include_entries)
    if project_state.inactive_children:
        state[_PROJECT_INACTIVE_CHILDREN_KEY] = list(project_state.inactive_children)
    else:
        state.pop(_PROJECT_INACTIVE_CHILDREN_KEY, None)
    if project_state.active_plan_id:
        state[_PROJECT_ACTIVE_PLAN_ID_KEY] = project_state.active_plan_id
    else:
        state.pop(_PROJECT_ACTIVE_PLAN_ID_KEY, None)
    save_file_ui_state(file_path, state)


def serialize_include_entries(entries: Iterable[IncludeEntry]) -> list[dict[str, Any]]:
    return [
        {
            "path": entry.path,
            "is_dir": entry.is_dir,
            "recursive": entry.recursive,
            "active": entry.active,
        }
        for entry in entries
    ]


def deserialize_include_entries(raw: Any) -> list[IncludeEntry]:
    if not isinstance(raw, list):
        return []
    result: list[IncludeEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not isinstance(path, str):
            continue
        result.append(
            IncludeEntry(
                path=path,
                is_dir=bool(item.get("is_dir", False)),
                recursive=bool(item.get("recursive", False)),
                active=bool(item.get("active", True)),
            )
        )
    return result


def resolve_project_included_files(
    source_file: Path,
    include_entries: Iterable[IncludeEntry],
    *,
    inactive_children: Iterable[str] = (),
) -> tuple[Path, ...]:
    return resolve_project_context_paths(
        source_file,
        include_entries,
        inactive_children=inactive_children,
    ).text_files


def resolve_project_context_paths(
    source_file: Path,
    include_entries: Iterable[IncludeEntry],
    *,
    inactive_children: Iterable[str] = (),
) -> ProjectResolvedContext:
    source = source_file.expanduser().resolve()
    inactive = {
        resolve_project_relative_path(source, raw).resolve()
        for raw in inactive_children
        if isinstance(raw, str) and raw.strip()
    }
    seen: set[Path] = set()
    text_files: list[Path] = []
    image_files: list[Path] = []
    for entry in include_entries:
        if not entry.active:
            continue
        target = resolve_include_entry_path(source, entry)
        if entry.is_dir:
            if not target.is_dir():
                continue
            iterator = target.rglob("*") if entry.recursive else target.iterdir()
            candidates = sorted((item for item in iterator if item.is_file()), key=str)
            for candidate in candidates:
                item = candidate.resolve()
                if item == source or item in seen or item in inactive:
                    continue
                if not _is_supported_project_context_file(item):
                    continue
                seen.add(item)
                _append_project_context_file(item, text_files=text_files, image_files=image_files)
            continue
        item = target.resolve()
        if item == source or item in seen or not item.exists() or not item.is_file():
            continue
        if not _is_supported_project_context_file(item):
            continue
        seen.add(item)
        _append_project_context_file(item, text_files=text_files, image_files=image_files)
    return ProjectResolvedContext(
        text_files=tuple(text_files),
        image_files=tuple(image_files),
    )


def resolve_include_entry_path(source_file: Path, entry: IncludeEntry) -> Path:
    return resolve_project_relative_path(source_file, entry.path)


def resolve_project_relative_path(source_file: Path, raw_path: str) -> Path:
    base = source_file.expanduser().resolve().parent
    raw = Path(raw_path)
    return (base / raw).resolve() if not raw.is_absolute() else raw.expanduser().resolve()


def normalize_project_path_for_storage(source_file: Path, target_path: Path, *, is_dir: bool) -> str:
    source_parent = source_file.expanduser().resolve().parent
    target = target_path.expanduser().resolve()
    try:
        relative = os.path.relpath(target, start=source_parent)
    except ValueError:
        normalized = str(target)
    else:
        normalized = relative.replace(os.sep, "/")
        if not normalized.startswith((".", "..")):
            normalized = f"./{normalized}"
    if is_dir and not normalized.endswith("/"):
        normalized = f"{normalized}/"
    return normalized


def _deserialize_inactive_children(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item.strip()]


def _is_supported_project_context_file(path: Path) -> bool:
    return path.suffix.lower() == ".md" or is_supported_image_path(path)


def _append_project_context_file(
    path: Path,
    *,
    text_files: list[Path],
    image_files: list[Path],
) -> None:
    if path.suffix.lower() == ".md":
        text_files.append(path)
        return
    if is_supported_image_path(path):
        image_files.append(path)


def _read_tui_prefs() -> dict[str, Any]:
    try:
        return json.loads(_TUI_PREFS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_tui_prefs(data: dict[str, Any]) -> None:
    try:
        _TUI_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TUI_PREFS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass
