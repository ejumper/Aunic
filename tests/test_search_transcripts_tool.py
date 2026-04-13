from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aunic.tools.note_edit import build_chat_tool_registry, build_note_tool_registry
from aunic.tools.memory_tools import build_memory_tool_registry
from aunic.tools.search_transcripts import (
    SearchTranscriptsArgs,
    build_search_transcripts_tool_registry,
    execute_search_transcripts,
    parse_search_transcripts_args,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "aunic_notes"


# ---------------------------------------------------------------------------
# parse_search_transcripts_args
# ---------------------------------------------------------------------------


def test_parse_args_defaults() -> None:
    args = parse_search_transcripts_args({})
    assert args.query is None
    assert args.tool is None
    assert args.scope is None
    assert args.limit == 20
    assert args.offset == 0


def test_parse_args_rejects_extra_keys() -> None:
    with pytest.raises(ValueError, match="Unexpected fields"):
        parse_search_transcripts_args({"foo": 1})


def test_parse_args_rejects_limit_over_100() -> None:
    with pytest.raises(ValueError, match="limit"):
        parse_search_transcripts_args({"limit": 500})


def test_parse_args_rejects_limit_zero() -> None:
    with pytest.raises(ValueError, match="limit"):
        parse_search_transcripts_args({"limit": 0})


def test_parse_args_rejects_negative_offset() -> None:
    with pytest.raises(ValueError, match="offset"):
        parse_search_transcripts_args({"offset": -1})


def test_parse_args_full_payload() -> None:
    args = parse_search_transcripts_args({
        "query": "docker",
        "tool": "bash",
        "scope": "/home/user/notes",
        "limit": 50,
        "offset": 10,
    })
    assert args.query == "docker"
    assert args.tool == "bash"
    assert args.scope == "/home/user/notes"
    assert args.limit == 50
    assert args.offset == 10


# ---------------------------------------------------------------------------
# _FakeRuntime (minimal stub for execute_search_transcripts)
# ---------------------------------------------------------------------------


class _FakeSessionState:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd


class _FakeRuntime:
    def __init__(self, cwd: Path) -> None:
        self.session_state = _FakeSessionState(cwd)


# ---------------------------------------------------------------------------
# execute_search_transcripts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_returns_completed_on_success(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        "# Test\n\n---\n# Transcript\n\n"
        "| 1 | assistant | tool_call | bash | c1 | {\"command\":\"ls\"} |\n"
        "| 2 | tool | tool_result | bash | c1 | \"file.txt\" |\n"
    )
    runtime = _FakeRuntime(cwd=tmp_path)
    args = SearchTranscriptsArgs(scope=str(tmp_path))

    result = await execute_search_transcripts(runtime, args)  # type: ignore[arg-type]

    assert result.status == "completed"
    assert isinstance(result.in_memory_content, dict)
    assert len(result.in_memory_content["hits"]) > 0


@pytest.mark.asyncio
async def test_execute_respects_scope_override(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    note = sub / "note.md"
    note.write_text(
        "# Sub\n\n---\n# Transcript\n\n"
        "| 1 | assistant | tool_call | bash | c1 | {\"command\":\"pwd\"} |\n"
        "| 2 | tool | tool_result | bash | c1 | \"/sub\" |\n"
    )

    runtime = _FakeRuntime(cwd=tmp_path)
    args = SearchTranscriptsArgs(scope=str(sub))

    result = await execute_search_transcripts(runtime, args)  # type: ignore[arg-type]

    assert result.status == "completed"
    paths = {h["path"] for h in result.in_memory_content["hits"]}
    assert all(str(sub) in p for p in paths)


@pytest.mark.asyncio
async def test_execute_returns_tool_error_for_nonexistent_scope(tmp_path: Path) -> None:
    runtime = _FakeRuntime(cwd=tmp_path)
    args = SearchTranscriptsArgs(scope=str(tmp_path / "does_not_exist"))

    result = await execute_search_transcripts(runtime, args)  # type: ignore[arg-type]

    assert result.status == "tool_error"
    assert result.tool_failure is not None


# ---------------------------------------------------------------------------
# build_memory_tool_registry
# ---------------------------------------------------------------------------


def test_build_memory_tool_registry_contains_search_transcripts() -> None:
    registry = build_memory_tool_registry()
    names = {d.spec.name for d in registry}
    assert "search_transcripts" in names


def test_search_transcripts_in_chat_registry_off_mode() -> None:
    registry = build_chat_tool_registry(work_mode="off")
    names = {d.spec.name for d in registry}
    assert "search_transcripts" in names


def test_search_transcripts_in_note_registry_off_mode() -> None:
    registry = build_note_tool_registry(work_mode="off")
    names = {d.spec.name for d in registry}
    assert "search_transcripts" in names


def test_search_transcripts_in_chat_registry_work_mode() -> None:
    registry = build_chat_tool_registry(work_mode="work")
    names = {d.spec.name for d in registry}
    assert "search_transcripts" in names


def test_search_transcripts_in_note_registry_work_mode() -> None:
    registry = build_note_tool_registry(work_mode="work")
    names = {d.spec.name for d in registry}
    assert "search_transcripts" in names
