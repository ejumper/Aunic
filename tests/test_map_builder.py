from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from aunic.map.builder import (
    MAP_PATH,
    BuildResult,
    build_map,
    clear_summary,
    mark_map_entry_stale,
    refresh_map_entry_if_stale,
    set_summary,
)
from aunic.map.manifest import load_meta, meta_path_for, save_meta, NoteMetadata
from aunic.map.render import parse_map


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TRANSCRIPT_MARKER = "---\n# Transcript\n\n"


def _aunic_note(tmp_path: Path, name: str = "note.md", content: str = "Hello world") -> Path:
    """Create a minimal Aunic note (with .aunic/ sibling so is_aunic_note returns True)."""
    note = tmp_path / name
    note.write_text(content)
    aunic_dir = tmp_path / ".aunic"
    aunic_dir.mkdir(exist_ok=True)
    return note


# ---------------------------------------------------------------------------
# build_map — basic
# ---------------------------------------------------------------------------


def test_build_map_creates_map_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aunic.map.builder.MAP_PATH", tmp_path / ".aunic" / "map.md")
    _aunic_note(tmp_path, "bgp.md", "BGP configuration notes")

    result = build_map(tmp_path)

    map_path = tmp_path / ".aunic" / "map.md"
    assert map_path.exists()
    assert isinstance(result, BuildResult)
    assert result.entry_count == 1
    assert result.entries_added == 1


def test_build_map_snippet_in_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aunic.map.builder.MAP_PATH", tmp_path / ".aunic" / "map.md")
    _aunic_note(tmp_path, "note.md", "Docker compose setup for homelab")

    build_map(tmp_path)

    text = (tmp_path / ".aunic" / "map.md").read_text()
    assert "Docker compose setup for homelab" in text


def test_build_map_multiple_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aunic.map.builder.MAP_PATH", tmp_path / ".aunic" / "map.md")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / ".aunic").mkdir()
    (sub / "a.md").write_text("Note A")
    (sub / "b.md").write_text("Note B")

    result = build_map(tmp_path)

    assert result.entry_count == 2


def test_build_map_incremental_reuses_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    map_path = tmp_path / ".aunic" / "map.md"
    monkeypatch.setattr("aunic.map.builder.MAP_PATH", map_path)
    note = _aunic_note(tmp_path, "note.md", "Some content")

    # First build
    build_map(tmp_path)

    # Second build with unchanged mtime → should reuse
    result2 = build_map(tmp_path)
    assert result2.entries_reused_from_cache == 1
    assert result2.entries_updated == 0
    assert result2.entries_added == 0


def test_build_map_updates_changed_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    map_path = tmp_path / ".aunic" / "map.md"
    monkeypatch.setattr("aunic.map.builder.MAP_PATH", map_path)
    note = _aunic_note(tmp_path, "note.md", "Original content")

    build_map(tmp_path)

    # Touch the note (new content)
    time.sleep(0.01)  # ensure mtime changes
    note.write_text("Updated content")

    result2 = build_map(tmp_path)
    assert result2.entries_updated == 1
    assert "Updated content" in map_path.read_text()


def test_build_map_subtree_preserves_out_of_scope_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    map_path = tmp_path / ".aunic" / "map.md"
    monkeypatch.setattr("aunic.map.builder.MAP_PATH", map_path)

    # Two subdirs
    sub_a = tmp_path / "a"
    sub_a.mkdir()
    (sub_a / ".aunic").mkdir()
    (sub_a / "note_a.md").write_text("Note in A")

    sub_b = tmp_path / "b"
    sub_b.mkdir()
    (sub_b / ".aunic").mkdir()
    (sub_b / "note_b.md").write_text("Note in B")

    # Full build first
    build_map(tmp_path)
    assert "note_a.md" in map_path.read_text()
    assert "note_b.md" in map_path.read_text()

    # Subtree build on sub_a only
    build_map(sub_a)
    text = map_path.read_text()
    # note_b should still be present (preserved from previous map)
    assert "note_b.md" in text


# ---------------------------------------------------------------------------
# Pinned summary
# ---------------------------------------------------------------------------


def test_build_map_uses_locked_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    map_path = tmp_path / ".aunic" / "map.md"
    monkeypatch.setattr("aunic.map.builder.MAP_PATH", map_path)
    note = _aunic_note(tmp_path, "note.md", "Some long actual content that should not appear")

    meta = NoteMetadata(summary="Pinned summary text", summary_locked=True)
    save_meta(note, meta)

    build_map(tmp_path)
    text = map_path.read_text()
    assert "Pinned summary text" in text
    assert "locked=true" in text


# ---------------------------------------------------------------------------
# mark_map_entry_stale
# ---------------------------------------------------------------------------


def test_mark_stale_sets_flag(tmp_path: Path) -> None:
    note = _aunic_note(tmp_path, "note.md", "Content")
    mark_map_entry_stale(note)
    meta = load_meta(note)
    assert meta.auto_snippet_stale is True


def test_mark_stale_no_op_if_not_aunic_note(tmp_path: Path) -> None:
    plain = tmp_path / "plain.md"
    plain.write_text("# Hello")
    # Should not raise and meta file should not be created
    mark_map_entry_stale(plain)
    meta_path = meta_path_for(plain)
    assert not meta_path.exists()


def test_mark_stale_no_op_if_summary_locked(tmp_path: Path) -> None:
    note = _aunic_note(tmp_path, "note.md", "Content")
    meta = NoteMetadata(summary="Locked", summary_locked=True)
    save_meta(note, meta)

    mark_map_entry_stale(note)

    meta2 = load_meta(note)
    assert meta2.auto_snippet_stale is False  # unchanged


# ---------------------------------------------------------------------------
# refresh_map_entry_if_stale
# ---------------------------------------------------------------------------


def test_refresh_no_op_when_map_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aunic.map.builder.MAP_PATH", tmp_path / ".aunic" / "map.md")
    note = _aunic_note(tmp_path, "note.md", "Content")
    # No map file → refresh should silently do nothing
    refresh_map_entry_if_stale(note)  # no exception


def test_refresh_no_op_when_not_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    map_path = tmp_path / ".aunic" / "map.md"
    monkeypatch.setattr("aunic.map.builder.MAP_PATH", map_path)
    note = _aunic_note(tmp_path, "note.md", "Content")
    build_map(tmp_path)

    original_text = map_path.read_text()
    refresh_map_entry_if_stale(note)  # stale=False → no-op
    assert map_path.read_text() == original_text


def test_refresh_updates_map_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    map_path = tmp_path / ".aunic" / "map.md"
    monkeypatch.setattr("aunic.map.builder.MAP_PATH", map_path)
    note = _aunic_note(tmp_path, "note.md", "Original content")
    build_map(tmp_path)

    # Update file and mark stale
    time.sleep(0.01)
    note.write_text("Refreshed content")
    mark_map_entry_stale(note)

    refresh_map_entry_if_stale(note)

    text = map_path.read_text()
    assert "Refreshed content" in text


def test_refresh_clears_stale_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    map_path = tmp_path / ".aunic" / "map.md"
    monkeypatch.setattr("aunic.map.builder.MAP_PATH", map_path)
    note = _aunic_note(tmp_path, "note.md", "Content")
    build_map(tmp_path)

    time.sleep(0.01)
    note.write_text("New content")
    mark_map_entry_stale(note)
    refresh_map_entry_if_stale(note)

    meta = load_meta(note)
    assert meta.auto_snippet_stale is False


# ---------------------------------------------------------------------------
# set_summary / clear_summary
# ---------------------------------------------------------------------------


def test_set_summary_locks_meta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aunic.map.builder.MAP_PATH", tmp_path / ".aunic" / "map.md")
    note = _aunic_note(tmp_path, "note.md", "Content")
    set_summary(note, "My custom summary")
    meta = load_meta(note)
    assert meta.summary == "My custom summary"
    assert meta.summary_locked is True


def test_set_summary_updates_map_if_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    map_path = tmp_path / ".aunic" / "map.md"
    monkeypatch.setattr("aunic.map.builder.MAP_PATH", map_path)
    note = _aunic_note(tmp_path, "note.md", "Content")
    build_map(tmp_path)

    set_summary(note, "My custom summary")
    text = map_path.read_text()
    assert "My custom summary" in text
    assert "locked=true" in text


def test_clear_summary_unlocks_meta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aunic.map.builder.MAP_PATH", tmp_path / ".aunic" / "map.md")
    note = _aunic_note(tmp_path, "note.md", "Content")
    set_summary(note, "Pinned")
    clear_summary(note)
    meta = load_meta(note)
    assert meta.summary is None
    assert meta.summary_locked is False


def test_clear_summary_regenerates_auto_snippet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    map_path = tmp_path / ".aunic" / "map.md"
    monkeypatch.setattr("aunic.map.builder.MAP_PATH", map_path)
    note = _aunic_note(tmp_path, "note.md", "Auto snippet content")
    build_map(tmp_path)

    set_summary(note, "Pinned")
    clear_summary(note)

    text = map_path.read_text()
    assert "Auto snippet content" in text
    assert "Pinned" not in text
