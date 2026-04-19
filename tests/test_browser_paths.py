from __future__ import annotations

from pathlib import Path

import pytest

from aunic.browser.paths import WorkspacePathError, resolve_workspace_path, workspace_relative_path


def test_resolve_workspace_path_accepts_relative_posix_path(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()

    resolved = resolve_workspace_path("notes/file.md", workspace_root=root)

    assert resolved == root.resolve() / "notes" / "file.md"


@pytest.mark.parametrize("subpath", ["", "/etc/passwd", "../outside.md", "notes/../outside.md", "./note.md"])
def test_resolve_workspace_path_rejects_unsafe_paths(tmp_path: Path, subpath: str) -> None:
    root = tmp_path / "workspace"
    root.mkdir()

    with pytest.raises(WorkspacePathError):
        resolve_workspace_path(subpath, workspace_root=root)


def test_resolve_workspace_path_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspacePathError):
        resolve_workspace_path("escape/secret.md", workspace_root=root)


def test_workspace_relative_path_returns_posix_path(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    target = root / "nested" / "note.md"
    target.parent.mkdir(parents=True)
    target.write_text("body", encoding="utf-8")

    assert workspace_relative_path(target, workspace_root=root) == "nested/note.md"
