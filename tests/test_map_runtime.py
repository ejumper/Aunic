from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from aunic.map.builder import build_map, ensure_map_ready, ensure_map_ready_shared
from aunic.map.runtime import resolve_map_location


def _aunic_note(directory: Path, name: str = "note.md", content: str = "Hello") -> Path:
    note = directory / name
    note.write_text(content, encoding="utf-8")
    (directory / ".aunic").mkdir(exist_ok=True)
    return note


def test_resolve_map_location_prefers_highest_ancestor_aunic(tmp_path: Path) -> None:
    (tmp_path / ".aunic").mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / ".aunic").mkdir()
    nested = project / "docs"
    nested.mkdir()
    note = nested / "note.md"
    note.write_text("hello", encoding="utf-8")

    location = resolve_map_location(note, fallback_root=project)

    assert location.anchor_root == tmp_path.resolve()
    assert location.map_path == (tmp_path / ".aunic" / "map.md").resolve()


def test_resolve_map_location_falls_back_and_creates_map_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    note = workspace / "note.md"
    note.write_text("hello", encoding="utf-8")

    location = resolve_map_location(note, fallback_root=workspace, create=True)

    assert location.anchor_root == workspace.resolve()
    assert location.map_dir.exists()
    assert location.map_path == (workspace / ".aunic" / "map.md").resolve()


def test_ensure_map_ready_builds_missing_canonical_map(tmp_path: Path) -> None:
    note = _aunic_note(tmp_path, "note.md", "BGP notes")

    result = ensure_map_ready(note, fallback_root=tmp_path)

    assert result is not None
    assert (tmp_path / ".aunic" / "map.md").exists()


def test_ensure_map_ready_noops_when_map_is_fresh(tmp_path: Path) -> None:
    note = _aunic_note(tmp_path, "note.md", "BGP notes")
    build_map(tmp_path, fallback_root=tmp_path)

    result = ensure_map_ready(note, fallback_root=tmp_path)

    assert result is None


@pytest.mark.asyncio
async def test_ensure_map_ready_shared_collapses_concurrent_callers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _aunic_note(tmp_path, "note.md", "BGP notes")
    calls: list[Path] = []

    def fake_ensure(subject_path: Path, *, fallback_root: Path | None = None):
        calls.append(subject_path)
        time.sleep(0.05)
        return None

    monkeypatch.setattr("aunic.map.builder.ensure_map_ready", fake_ensure)

    await asyncio.gather(
        ensure_map_ready_shared(note, fallback_root=tmp_path),
        ensure_map_ready_shared(note, fallback_root=tmp_path),
    )

    assert calls == [note]
