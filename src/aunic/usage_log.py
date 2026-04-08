from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def usage_log_path_for_cwd(cwd: Path, *, when: datetime | None = None) -> Path:
    timestamp = when or datetime.now().astimezone()
    base_dir = resolve_usage_root(cwd)
    return base_dir / "usage" / f"{timestamp.date().isoformat()}.jsonl"


def append_usage_record(cwd: Path, record: dict[str, Any]) -> Path:
    path = usage_log_path_for_cwd(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True))
        handle.write("\n")
    return path


def resolve_usage_root(cwd: Path) -> Path:
    search_root = _normalized_search_root(cwd)
    for ancestor in (search_root, *search_root.parents):
        candidate = ancestor / ".aunic"
        if candidate.exists() and candidate.is_dir():
            return candidate

    home_candidate = Path.home().expanduser().resolve() / ".aunic"
    if home_candidate.exists() and home_candidate.is_dir():
        return home_candidate

    return search_root / ".aunic"


def _normalized_search_root(cwd: Path) -> Path:
    resolved = cwd.expanduser().resolve()
    if resolved.exists() and resolved.is_file():
        return resolved.parent
    if not resolved.exists() and resolved.suffix:
        return resolved.parent
    return resolved
