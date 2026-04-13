from __future__ import annotations

import os
import time
from collections import OrderedDict
from pathlib import Path

from aunic.transcript.parser import find_transcript_section

_MAP_STALENESS_SECONDS = 120 * 3600  # 120 hours

DEFAULT_SKIP_DIRS: frozenset[str] = frozenset({
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "target",
    ".next",
    ".cache",
    ".aunic",
})

_MAX_HEAD_BYTES = 65_536  # 64 KiB

# Module-level LRU cache: (resolved_path_str, mtime_ns) -> bool
_NOTE_CACHE: OrderedDict[tuple[str, int], bool] = OrderedDict()
_NOTE_CACHE_MAX = 4096


def is_aunic_note(path: Path) -> bool:
    """Return True if path is an Aunic markdown note."""
    resolved = path.resolve()
    try:
        mtime_ns = resolved.stat().st_mtime_ns
    except OSError:
        return False

    cache_key = (str(resolved), mtime_ns)
    if cache_key in _NOTE_CACHE:
        _NOTE_CACHE.move_to_end(cache_key)
        return _NOTE_CACHE[cache_key]

    result = _check_is_aunic_note(resolved)

    _NOTE_CACHE[cache_key] = result
    _NOTE_CACHE.move_to_end(cache_key)
    if len(_NOTE_CACHE) > _NOTE_CACHE_MAX:
        _NOTE_CACHE.popitem(last=False)

    return result


def _check_is_aunic_note(resolved: Path) -> bool:
    # Rule 1: sibling .aunic/ directory exists
    aunic_sibling = resolved.parent / ".aunic"
    if aunic_sibling.exists() and aunic_sibling.is_dir():
        return True

    # Rule 2: file head contains the transcript section marker
    try:
        with resolved.open("rb") as fh:
            head_bytes = fh.read(_MAX_HEAD_BYTES)
        text = head_bytes.decode("utf-8", errors="replace")
    except OSError:
        return False

    return find_transcript_section(text) is not None


def walk_aunic_notes(root: Path | None = None) -> list[Path]:
    """Walk the filesystem from root and return all Aunic note paths.

    Skips DEFAULT_SKIP_DIRS, all hidden directories (starting with '.'),
    and symlinks. Default root is the user home directory.
    """
    if root is None:
        root = Path.home()
    root = root.resolve()

    notes: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(str(root), followlinks=False):
        # Prune directories in-place so os.walk won't descend into them
        dirnames[:] = [
            d for d in dirnames
            if d not in DEFAULT_SKIP_DIRS and not d.startswith(".")
        ]

        for filename in filenames:
            filepath = Path(dirpath) / filename
            # Skip symlinks
            if filepath.is_symlink():
                continue
            try:
                if is_aunic_note(filepath):
                    notes.append(filepath)
            except OSError:
                pass

    return notes


def resolve_note_set(scope: Path | None) -> list[Path]:
    """Return the list of Aunic notes for scope, using the cached map when fresh.

    If ~/.aunic/map.md is missing or older than _MAP_STALENESS_SECONDS, the map
    is rebuilt before paths are read from it. Falls back to walk_aunic_notes if
    the map is still unreadable after the rebuild attempt (e.g. read-only filesystem).
    """
    map_path = Path.home() / ".aunic" / "map.md"

    try:
        stat = map_path.stat()
        map_is_fresh = (time.time() - stat.st_mtime) < _MAP_STALENESS_SECONDS
    except OSError:
        map_is_fresh = False

    if not map_is_fresh:
        try:
            from aunic.map.builder import build_map
            build_map(scope)
        except Exception:
            pass  # best-effort; fall through to read or walk

    try:
        from aunic.map.render import parse_map
        entries = parse_map(map_path.read_text(encoding="utf-8"))
        paths = [p for p in entries if p.exists()]
        if scope is not None:
            scope_resolved = scope.resolve()
            paths = [p for p in paths if _is_under(p, scope_resolved)]
            if not paths:
                # Map may not cover this directory (e.g. first use of a new path).
                # Fall back to a direct walk so callers get correct results.
                return walk_aunic_notes(scope)
        return paths
    except OSError:
        return walk_aunic_notes(scope)


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent)
        return True
    except ValueError:
        return False
