from __future__ import annotations

from pathlib import Path

import pytest

from aunic.map.builder import build_map
from aunic.tools.memory_manifest import MEMORY_TOOL_HINTS, build_memory_manifest
from aunic.tools.memory_tools import build_memory_tool_registry
from aunic.tools.read_map import (
    ReadMapArgs,
    build_read_map_tool_registry,
    execute_read_map,
    parse_read_map_args,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeSessionState:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd


class _FakeRuntime:
    def __init__(self, cwd: Path) -> None:
        self.session_state = _FakeSessionState(cwd)


def _aunic_note(directory: Path, name: str = "note.md", content: str = "Hello") -> Path:
    note = directory / name
    note.write_text(content)
    (directory / ".aunic").mkdir(exist_ok=True)
    return note


# ---------------------------------------------------------------------------
# parse_read_map_args
# ---------------------------------------------------------------------------


def test_parse_no_args() -> None:
    args = parse_read_map_args({})
    assert args.scope is None


def test_parse_scope() -> None:
    args = parse_read_map_args({"scope": "/home/user/notes"})
    assert args.scope == "/home/user/notes"


def test_parse_rejects_extra_keys() -> None:
    with pytest.raises(ValueError, match="Unexpected"):
        parse_read_map_args({"scope": "/home", "foo": 1})


def test_parse_empty_scope_becomes_none() -> None:
    args = parse_read_map_args({"scope": ""})
    assert args.scope is None


# ---------------------------------------------------------------------------
# execute_read_map — map auto-build
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_missing_map_builds_canonical_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _aunic_note(tmp_path, "note.md", "hello world")

    runtime = _FakeRuntime(cwd=tmp_path)
    args = ReadMapArgs(scope=None)
    result = await execute_read_map(runtime, args)  # type: ignore[arg-type]

    assert result.status == "completed"
    assert result.in_memory_content["entry_count"] == 1
    assert result.in_memory_content["map_path"] == str((tmp_path / ".aunic" / "map.md").resolve())


# ---------------------------------------------------------------------------
# execute_read_map — full map
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_returns_full_map(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _aunic_note(tmp_path, "bgp.md", "BGP notes content")
    build_map(tmp_path)

    runtime = _FakeRuntime(cwd=tmp_path)
    args = ReadMapArgs(scope=None)
    result = await execute_read_map(runtime, args)  # type: ignore[arg-type]

    assert result.status == "completed"
    payload = result.in_memory_content
    assert payload["entry_count"] == 1
    assert "bgp.md" in payload["content"]
    assert payload["scope_applied"] is None


# ---------------------------------------------------------------------------
# execute_read_map — scope filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_scope_filters_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sub_a = tmp_path / "work"
    sub_a.mkdir()
    (sub_a / ".aunic").mkdir()
    (sub_a / "work_note.md").write_text("Work content")

    sub_b = tmp_path / "personal"
    sub_b.mkdir()
    (sub_b / ".aunic").mkdir()
    (sub_b / "personal_note.md").write_text("Personal content")

    build_map(tmp_path)

    runtime = _FakeRuntime(cwd=tmp_path)
    args = ReadMapArgs(scope=str(sub_a))
    result = await execute_read_map(runtime, args)  # type: ignore[arg-type]

    assert result.status == "completed"
    payload = result.in_memory_content
    assert payload["entry_count"] == 1
    assert "work_note.md" in payload["content"]
    assert "personal_note.md" not in payload["content"]


@pytest.mark.asyncio
async def test_execute_scope_not_found_returns_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _aunic_note(tmp_path, "note.md", "content")
    build_map(tmp_path)

    runtime = _FakeRuntime(cwd=tmp_path)
    args = ReadMapArgs(scope=str(tmp_path / "nonexistent"))
    result = await execute_read_map(runtime, args)  # type: ignore[arg-type]

    assert result.status == "tool_error"
    assert result.in_memory_content["reason"] == "scope_not_found"


@pytest.mark.asyncio
async def test_execute_scope_not_directory_returns_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _aunic_note(tmp_path, "note.md", "content")
    build_map(tmp_path)

    runtime = _FakeRuntime(cwd=tmp_path)
    # Pass the note file itself as scope (not a directory)
    args = ReadMapArgs(scope=str(note))
    result = await execute_read_map(runtime, args)  # type: ignore[arg-type]

    assert result.status == "tool_error"
    assert result.in_memory_content["reason"] == "scope_not_directory"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_has_read_map_tool() -> None:
    registry = build_read_map_tool_registry()
    assert len(registry) == 1
    assert registry[0].spec.name == "read_map"


def test_read_map_in_memory_tool_registry() -> None:
    registry = build_memory_tool_registry()
    names = {d.spec.name for d in registry}
    assert "read_map" in names


# ---------------------------------------------------------------------------
# Memory manifest
# ---------------------------------------------------------------------------


def test_manifest_includes_read_map_hint() -> None:
    assert "read_map" in MEMORY_TOOL_HINTS


def test_manifest_includes_read_map_when_present() -> None:
    registry = build_memory_tool_registry()
    manifest = build_memory_manifest(registry)
    assert manifest is not None
    assert "read_map" in manifest
