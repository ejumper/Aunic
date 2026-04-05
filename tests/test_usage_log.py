from __future__ import annotations

import json
from pathlib import Path

from aunic.usage_log import append_usage_record


def test_append_usage_record_writes_jsonl_under_dot_aunic(tmp_path: Path) -> None:
    path = append_usage_record(
        tmp_path,
        {
            "mode": "prompt",
            "provider": "codex",
            "usage": {"total_tokens": 10},
        },
    )

    assert path == tmp_path / ".aunic" / "usage" / f"{path.stem}.jsonl"
    payload = json.loads(path.read_text(encoding="utf-8").strip())
    assert payload["mode"] == "prompt"
    assert payload["usage"]["total_tokens"] == 10
