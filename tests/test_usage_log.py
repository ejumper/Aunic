from __future__ import annotations

import json
from pathlib import Path

import pytest

from aunic.usage_log import append_usage_record, resolve_usage_root


def test_append_usage_record_writes_jsonl_under_dot_aunic(tmp_path: Path) -> None:
    local_root = tmp_path / ".aunic"
    local_root.mkdir()
    path = append_usage_record(
        tmp_path,
        {
            "mode": "prompt",
            "provider": "codex",
            "usage": {"total_tokens": 10},
        },
    )

    assert path == local_root / "usage" / f"{path.stem}.jsonl"
    payload = json.loads(path.read_text(encoding="utf-8").strip())
    assert payload["mode"] == "prompt"
    assert payload["usage"]["total_tokens"] == 10


def test_append_usage_record_prefers_nearest_ancestor_dot_aunic(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    nested = repo_root / "notes" / "deep"
    nested.mkdir(parents=True)
    (repo_root / ".aunic").mkdir()

    path = append_usage_record(
        nested,
        {
            "mode": "prompt",
            "provider": "codex",
        },
    )

    assert path.parent == repo_root / ".aunic" / "usage"
    assert resolve_usage_root(nested) == repo_root / ".aunic"


def test_append_usage_record_falls_back_to_home_dot_aunic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home_aunic = home / ".aunic"
    home_aunic.mkdir(parents=True)
    workspace = tmp_path / "workspace" / "notes"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    path = append_usage_record(
        workspace,
        {
            "mode": "prompt",
            "provider": "codex",
        },
    )

    assert path.parent == home_aunic / "usage"
    assert resolve_usage_root(workspace) == home_aunic


def test_append_usage_record_creates_local_dot_aunic_when_no_existing_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace" / "notes"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    path = append_usage_record(
        workspace,
        {
            "mode": "prompt",
            "provider": "codex",
        },
    )

    assert path.parent == workspace / ".aunic" / "usage"
    assert resolve_usage_root(workspace) == workspace / ".aunic"
