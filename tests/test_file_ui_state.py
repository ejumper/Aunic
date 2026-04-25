from __future__ import annotations

from pathlib import Path

import pytest

import aunic.file_ui_state as file_ui_state
from aunic.file_ui_state import (
    IncludeEntry,
    ProjectIncludeState,
    load_project_include_state,
    resolve_project_context_paths,
    resolve_project_included_files,
    save_project_include_state,
)


@pytest.fixture(autouse=True)
def _tmp_tui_prefs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(file_ui_state, "_TUI_PREFS_PATH", tmp_path / "tui_prefs.json")


def test_project_include_state_round_trips_entries_and_child_overrides(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    source.write_text("body", encoding="utf-8")

    save_project_include_state(
        source,
        ProjectIncludeState(
            include_entries=(
                IncludeEntry(path="./docs/", is_dir=True, recursive=True, active=True),
                IncludeEntry(path="./appendix.md", is_dir=False, recursive=False, active=False),
            ),
            inactive_children=("./docs/skip.md",),
            active_plan_id="plan-123",
        ),
    )

    loaded = load_project_include_state(source)

    assert loaded == ProjectIncludeState(
        include_entries=(
            IncludeEntry(path="./docs/", is_dir=True, recursive=True, active=True),
            IncludeEntry(path="./appendix.md", is_dir=False, recursive=False, active=False),
        ),
        inactive_children=("./docs/skip.md",),
        active_plan_id="plan-123",
    )


def test_resolve_project_included_files_honors_inactive_children(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    docs = tmp_path / "docs"
    docs.mkdir()
    keep = docs / "keep.md"
    skip = docs / "skip.md"
    source.write_text("body", encoding="utf-8")
    keep.write_text("keep", encoding="utf-8")
    skip.write_text("skip", encoding="utf-8")

    resolved = resolve_project_included_files(
        source,
        (IncludeEntry(path="./docs/", is_dir=True, recursive=False, active=True),),
        inactive_children=("./docs/skip.md",),
    )

    assert resolved == (keep.resolve(),)


def test_resolve_project_context_paths_splits_text_and_images(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    docs = tmp_path / "docs"
    docs.mkdir()
    keep = docs / "keep.md"
    image = docs / "diagram.png"
    source.write_text("body", encoding="utf-8")
    keep.write_text("keep", encoding="utf-8")
    image.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
        )
    )

    resolved = resolve_project_context_paths(
        source,
        (IncludeEntry(path="./docs/", is_dir=True, recursive=False, active=True),),
    )

    assert resolved.text_files == (keep.resolve(),)
    assert resolved.image_files == (image.resolve(),)
