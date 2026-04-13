from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from aunic.discovery import DEFAULT_SKIP_DIRS, _NOTE_CACHE, is_aunic_note, walk_aunic_notes

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "aunic_notes"


# ---------------------------------------------------------------------------
# is_aunic_note
# ---------------------------------------------------------------------------


def test_is_aunic_note_via_sibling_aunic_dir(tmp_path: Path) -> None:
    note = tmp_path / "my-note.md"
    note.write_text("# Hello\n\nSome content.")
    aunic_dir = tmp_path / ".aunic"
    aunic_dir.mkdir()

    assert is_aunic_note(note) is True


def test_is_aunic_note_via_transcript_header_in_content(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("# Notes\n\nSome content.\n\n---\n# Transcript\n\n| 1 | user | message |  |  | \"hi\" |")

    assert is_aunic_note(note) is True


def test_is_aunic_note_returns_false_for_plain_markdown(tmp_path: Path) -> None:
    plain = tmp_path / "readme.md"
    plain.write_text("# README\n\nJust a regular markdown file.")

    assert is_aunic_note(plain) is False


def test_is_aunic_note_fixture_notes_detected() -> None:
    bgp = FIXTURE_ROOT / "networking" / "bgp-notes.md"
    docker = FIXTURE_ROOT / "projects" / "homelab" / "docker-setup.md"
    assert is_aunic_note(bgp) is True
    assert is_aunic_note(docker) is True


def test_is_aunic_note_plain_fixture_not_detected() -> None:
    plain = FIXTURE_ROOT / "plain-markdown.md"
    assert is_aunic_note(plain) is False


def test_is_aunic_note_caches_on_mtime(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("# Notes\n\n---\n# Transcript\n")
    _NOTE_CACHE.clear()

    read_count = 0
    original_open = Path.open

    def counting_open(self: Path, *args: object, **kwargs: object):  # type: ignore[override]
        nonlocal read_count
        if self == note.resolve():
            read_count += 1
        return original_open(self, *args, **kwargs)

    with patch.object(Path, "open", counting_open):
        is_aunic_note(note)
        is_aunic_note(note)

    # Second call should hit cache, so only one file open
    assert read_count == 1


def test_is_aunic_note_cache_invalidates_on_mtime_change(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("# Notes\n\n---\n# Transcript\n")
    _NOTE_CACHE.clear()

    result1 = is_aunic_note(note)

    # Overwrite to change mtime
    note.write_text("# Changed\n\nNo transcript here.")
    # Force mtime to differ by using os.utime
    import os
    stat = note.stat()
    os.utime(note, (stat.st_atime + 1, stat.st_mtime + 1))

    result2 = is_aunic_note(note)

    assert result1 is True
    assert result2 is False


# ---------------------------------------------------------------------------
# walk_aunic_notes
# ---------------------------------------------------------------------------


def test_walk_aunic_notes_finds_fixture_notes() -> None:
    notes = walk_aunic_notes(FIXTURE_ROOT)
    note_names = {p.name for p in notes}
    assert "bgp-notes.md" in note_names
    assert "docker-setup.md" in note_names


def test_walk_aunic_notes_excludes_plain_markdown() -> None:
    notes = walk_aunic_notes(FIXTURE_ROOT)
    note_names = {p.name for p in notes}
    assert "plain-markdown.md" not in note_names


def test_walk_aunic_notes_skips_default_noise_dirs(tmp_path: Path) -> None:
    for skip_dir in [".git", "node_modules", ".venv", "__pycache__"]:
        d = tmp_path / skip_dir
        d.mkdir()
        note = d / "note.md"
        note.write_text("# Note\n\n---\n# Transcript\n")

    notes = walk_aunic_notes(tmp_path)
    assert notes == []


def test_walk_aunic_notes_respects_scope(tmp_path: Path) -> None:
    sub1 = tmp_path / "sub1"
    sub2 = tmp_path / "sub2"
    sub1.mkdir()
    sub2.mkdir()

    note1 = sub1 / "note1.md"
    note1.write_text("# Note 1\n\n---\n# Transcript\n")
    note2 = sub2 / "note2.md"
    note2.write_text("# Note 2\n\n---\n# Transcript\n")

    notes = walk_aunic_notes(sub1)
    note_names = {p.name for p in notes}
    assert "note1.md" in note_names
    assert "note2.md" not in note_names


def test_walk_aunic_notes_skips_symlinks(tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    note = real_dir / "note.md"
    note.write_text("# Note\n\n---\n# Transcript\n")

    link = tmp_path / "link.md"
    link.symlink_to(note)

    notes = walk_aunic_notes(tmp_path)
    note_names = [p.name for p in notes]
    # The real note is found once, the symlink file is skipped
    assert "note.md" in note_names
    assert "link.md" not in note_names


def test_walk_aunic_notes_uses_home_by_default(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("# Note\n\n---\n# Transcript\n")

    with patch("aunic.discovery.Path.home", return_value=tmp_path):
        notes = walk_aunic_notes()

    assert any(p.name == "note.md" for p in notes)


def test_default_skip_dirs_contains_expected_entries() -> None:
    assert ".git" in DEFAULT_SKIP_DIRS
    assert "node_modules" in DEFAULT_SKIP_DIRS
    assert ".aunic" in DEFAULT_SKIP_DIRS
    assert ".venv" in DEFAULT_SKIP_DIRS
