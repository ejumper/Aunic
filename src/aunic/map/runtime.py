from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

GLOBAL_MAP_STALENESS_SECONDS = 120 * 3600  # 120 hours


@dataclass(frozen=True)
class MapLocation:
    anchor_root: Path
    map_dir: Path
    map_path: Path


def resolve_map_location(
    subject_path: Path,
    *,
    fallback_root: Path | None = None,
    create: bool = False,
) -> MapLocation:
    resolved_subject = subject_path.expanduser().resolve()
    subject_dir = resolved_subject if resolved_subject.is_dir() else resolved_subject.parent

    resolved_fallback = _resolve_root(fallback_root or subject_dir)

    highest_anchor: Path | None = None
    current = subject_dir
    while True:
        if (current / ".aunic").is_dir():
            highest_anchor = current
        if current.parent == current:
            break
        current = current.parent

    anchor_root = highest_anchor or resolved_fallback
    map_dir = anchor_root / ".aunic"
    if create:
        map_dir.mkdir(parents=True, exist_ok=True)
    return MapLocation(
        anchor_root=anchor_root,
        map_dir=map_dir,
        map_path=map_dir / "map.md",
    )


def is_map_globally_stale(map_path: Path) -> bool:
    try:
        stat = map_path.stat()
    except OSError:
        return True
    return (time.time() - stat.st_mtime) >= GLOBAL_MAP_STALENESS_SECONDS


def _resolve_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    return resolved if resolved.is_dir() else resolved.parent
