from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def usage_log_path_for_cwd(cwd: Path, *, when: datetime | None = None) -> Path:
    timestamp = when or datetime.now().astimezone()
    return cwd.expanduser().resolve() / ".aunic" / "usage" / f"{timestamp.date().isoformat()}.jsonl"


def append_usage_record(cwd: Path, record: dict[str, Any]) -> Path:
    path = usage_log_path_for_cwd(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True))
        handle.write("\n")
    return path
