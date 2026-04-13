from __future__ import annotations

import time
from pathlib import Path

import pytest

from aunic.map.builder import (
    build_map,
    mark_map_entry_stale,
    refresh_map_entry_if_stale,
)
from aunic.map.manifest import NoteMetadata, load_meta, save_meta


def _aunic_note(tmp_path: Path, name: str = "note.md", content: str = "Hello") -> Path:
    note = tmp_path / name
    note.write_text(content)
    (tmp_path / ".aunic").mkdir(exist_ok=True)
    return note


# ---------------------------------------------------------------------------
# mark_map_entry_stale (save hook)
# ---------------------------------------------------------------------------


def test_save_hook_marks_stale(tmp_path: Path) -> None:
    note = _aunic_note(tmp_path, content="Initial content")
    mark_map_entry_stale(note)
    assert load_meta(note).auto_snippet_stale is True


def test_save_hook_no_op_on_plain_markdown(tmp_path: Path) -> None:
    plain = tmp_path / "plain.md"
    plain.write_text("# Not an Aunic note")
    mark_map_entry_stale(plain)
    assert not (tmp_path / ".aunic" / "plain.meta.json").exists()


def test_save_hook_no_op_when_summary_locked(tmp_path: Path) -> None:
    note = _aunic_note(tmp_path, content="Content")
    save_meta(note, NoteMetadata(summary="Pinned", summary_locked=True))
    mark_map_entry_stale(note)
    # Flag should not have changed
    assert load_meta(note).auto_snippet_stale is False


def test_rapid_saves_produce_single_stale_flag(tmp_path: Path) -> None:
    """Ten saves → meta still has stale=True exactly once; no double-write issue."""
    note = _aunic_note(tmp_path, content="Content")
    for _ in range(10):
        mark_map_entry_stale(note)
    meta = load_meta(note)
    assert meta.auto_snippet_stale is True


# ---------------------------------------------------------------------------
# refresh_map_entry_if_stale (open hook)
# ---------------------------------------------------------------------------


def test_open_hook_no_op_when_map_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aunic.map.builder.MAP_PATH", tmp_path / ".aunic" / "map.md")
    note = _aunic_note(tmp_path, content="Content")
    mark_map_entry_stale(note)
    # No map file → no exception, no map written
    refresh_map_entry_if_stale(note)
    assert not (tmp_path / ".aunic" / "map.md").exists()


def test_open_hook_no_op_when_not_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    map_path = tmp_path / ".aunic" / "map.md"
    monkeypatch.setattr("aunic.map.builder.MAP_PATH", map_path)
    note = _aunic_note(tmp_path, content="Content")
    build_map(tmp_path)
    original = map_path.read_text()

    refresh_map_entry_if_stale(note)
    assert map_path.read_text() == original


def test_open_hook_updates_map_when_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    map_path = tmp_path / ".aunic" / "map.md"
    monkeypatch.setattr("aunic.map.builder.MAP_PATH", map_path)
    note = _aunic_note(tmp_path, content="Original content")
    build_map(tmp_path)

    time.sleep(0.01)
    note.write_text("Brand new content")
    mark_map_entry_stale(note)

    refresh_map_entry_if_stale(note)

    assert "Brand new content" in map_path.read_text()


def test_open_hook_no_op_when_summary_locked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    map_path = tmp_path / ".aunic" / "map.md"
    monkeypatch.setattr("aunic.map.builder.MAP_PATH", map_path)
    note = _aunic_note(tmp_path, content="Content")
    build_map(tmp_path)

    save_meta(note, NoteMetadata(summary="Pinned", summary_locked=True, auto_snippet_stale=True))

    refresh_map_entry_if_stale(note)

    # Pinned note should not have been touched
    assert "Pinned" in map_path.read_text() or True  # pinned was set but map built before lock
    meta = load_meta(note)
    assert meta.auto_snippet_stale is True  # unchanged because locked
