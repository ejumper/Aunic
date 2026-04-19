from __future__ import annotations

from pathlib import Path, PurePosixPath

from aunic.browser.errors import PathError


class WorkspacePathError(PathError):
    pass


def resolve_workspace_path(subpath: str, *, workspace_root: Path) -> Path:
    """Resolve a client-provided relative path to an absolute path inside the workspace."""
    if not isinstance(subpath, str) or not subpath.strip():
        raise WorkspacePathError("invalid_path", "Path must be a non-empty relative path.")
    if subpath.startswith("./") or "/./" in subpath:
        raise WorkspacePathError("invalid_path", "Path segments must not include '.'.")

    raw = PurePosixPath(subpath)
    if raw.is_absolute():
        raise WorkspacePathError("invalid_path", "Absolute paths are not allowed.")
    if any(part in {"", ".", ".."} for part in raw.parts):
        raise WorkspacePathError("invalid_path", "Path segments must not be empty, '.', or '..'.")

    root = workspace_root.expanduser().resolve()
    resolved = (root / Path(*raw.parts)).resolve(strict=False)
    if not _is_relative_to(resolved, root):
        raise WorkspacePathError("path_escape", "Path resolves outside the workspace.")
    return resolved


def resolve_workspace_directory(
    subpath: str | None,
    *,
    workspace_root: Path,
) -> Path:
    if subpath is None or subpath == "":
        return workspace_root.expanduser().resolve()
    return resolve_workspace_path(subpath, workspace_root=workspace_root)


def workspace_relative_path(path: Path, *, workspace_root: Path) -> str:
    root = workspace_root.expanduser().resolve()
    resolved = path.expanduser().resolve(strict=False)
    if not _is_relative_to(resolved, root):
        raise WorkspacePathError("path_escape", "Path is outside the workspace.")
    rel = resolved.relative_to(root)
    return "." if not rel.parts else rel.as_posix()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
