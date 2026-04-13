from __future__ import annotations

from pathlib import Path

import pytest

from aunic.domain import TranscriptRow
from aunic.transcript.search import (
    TranscriptSearchService,
    _Pair,
    _encode,
    _filter_pairs,
    _pair_rows,
    _truncate,
    _SNIPPET_MAX,
    _TRUNCATION_SUFFIX,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "aunic_notes"


# ---------------------------------------------------------------------------
# _pair_rows
# ---------------------------------------------------------------------------


def _call(row_number: int, tool_name: str, tool_id: str, content: object) -> TranscriptRow:
    return TranscriptRow(
        row_number=row_number,
        role="assistant",
        type="tool_call",
        tool_name=tool_name,
        tool_id=tool_id,
        content=content,
    )


def _result(row_number: int, tool_name: str, tool_id: str, content: object) -> TranscriptRow:
    return TranscriptRow(
        row_number=row_number,
        role="tool",
        type="tool_result",
        tool_name=tool_name,
        tool_id=tool_id,
        content=content,
    )


def _error(row_number: int, tool_name: str, tool_id: str, content: object) -> TranscriptRow:
    return TranscriptRow(
        row_number=row_number,
        role="tool",
        type="tool_error",
        tool_name=tool_name,
        tool_id=tool_id,
        content=content,
    )


def _message(row_number: int, role: str, content: object) -> TranscriptRow:
    return TranscriptRow(
        row_number=row_number,
        role=role,  # type: ignore[arg-type]
        type="message",
        content=content,
    )


def test_pair_rows_groups_call_and_result() -> None:
    rows = [
        _call(1, "bash", "c1", {"command": "ls"}),
        _call(2, "bash", "c2", {"command": "pwd"}),
        _result(3, "bash", "c1", "file1.txt"),
        _result(4, "bash", "c2", "/home/user"),
    ]
    pairs = _pair_rows(rows)
    assert len(pairs) == 2
    assert pairs[0].call is not None and pairs[0].call.tool_id == "c1"
    assert pairs[0].result is not None and pairs[0].result.tool_id == "c1"
    assert pairs[1].call is not None and pairs[1].call.tool_id == "c2"
    assert pairs[1].result is not None and pairs[1].result.tool_id == "c2"


def test_pair_rows_handles_orphaned_call() -> None:
    rows = [_call(1, "bash", "c1", {"command": "ls"})]
    pairs = _pair_rows(rows)
    assert len(pairs) == 1
    assert pairs[0].call is not None
    assert pairs[0].result is None


def test_pair_rows_handles_orphaned_result() -> None:
    rows = [_result(1, "bash", "c1", "output")]
    pairs = _pair_rows(rows)
    # Orphaned result is emitted as a pair
    assert len(pairs) == 1
    assert pairs[0].call is None
    assert pairs[0].result is not None


def test_pair_rows_message_rows_emitted() -> None:
    rows = [_message(1, "user", "Hello")]
    pairs = _pair_rows(rows)
    assert len(pairs) == 1
    assert pairs[0].message_row is not None


# ---------------------------------------------------------------------------
# _filter_pairs
# ---------------------------------------------------------------------------


def _note_path() -> Path:
    return Path("/tmp/fake-note.md")


def test_filter_pairs_no_filter_returns_all_tool_pairs() -> None:
    rows = [
        _call(1, "bash", "c1", {"command": "ls"}),
        _result(2, "bash", "c1", "output"),
    ]
    pairs = _pair_rows(rows)
    hits = _filter_pairs(pairs, note_path=_note_path(), query=None, tool=None)
    assert len(hits) == 1
    assert hits[0].result_status == "ok"


def test_filter_pairs_tool_filter() -> None:
    rows = [
        _call(1, "bash", "c1", {"command": "ls"}),
        _result(2, "bash", "c1", "output"),
        _call(3, "web_search", "c2", {"queries": ["test"]}),
        _result(4, "web_search", "c2", [{"title": "T"}]),
    ]
    pairs = _pair_rows(rows)
    hits = _filter_pairs(pairs, note_path=_note_path(), query=None, tool="bash")
    assert len(hits) == 1
    assert hits[0].tool == "bash"


def test_filter_pairs_query_in_args() -> None:
    rows = [
        _call(1, "bash", "c1", {"command": "docker compose up"}),
        _result(2, "bash", "c1", "started"),
        _call(3, "bash", "c2", {"command": "ls"}),
        _result(4, "bash", "c2", "output"),
    ]
    pairs = _pair_rows(rows)
    hits = _filter_pairs(pairs, note_path=_note_path(), query="docker", tool=None)
    assert len(hits) == 1
    assert "docker" in (hits[0].args_snippet or "")


def test_filter_pairs_query_in_result() -> None:
    rows = [
        _call(1, "bash", "c1", {"command": "ls"}),
        _result(2, "bash", "c1", "docker.sock found"),
    ]
    pairs = _pair_rows(rows)
    hits = _filter_pairs(pairs, note_path=_note_path(), query="docker.sock", tool=None)
    assert len(hits) == 1


def test_filter_pairs_orphaned_call_result_status_missing() -> None:
    rows = [_call(1, "bash", "c1", {"command": "ls"})]
    pairs = _pair_rows(rows)
    hits = _filter_pairs(pairs, note_path=_note_path(), query=None, tool=None)
    assert len(hits) == 1
    assert hits[0].result_status == "missing"


def test_filter_pairs_tool_error_result_status_error() -> None:
    rows = [
        _call(1, "bash", "c1", {"command": "bad_cmd"}),
        _error(2, "bash", "c1", {"error": "not found"}),
    ]
    pairs = _pair_rows(rows)
    hits = _filter_pairs(pairs, note_path=_note_path(), query=None, tool=None)
    assert len(hits) == 1
    assert hits[0].result_status == "error"


def test_filter_pairs_message_excluded_without_query() -> None:
    rows = [_message(1, "user", "Hello, world")]
    pairs = _pair_rows(rows)
    hits = _filter_pairs(pairs, note_path=_note_path(), query=None, tool=None)
    assert len(hits) == 0


def test_filter_pairs_message_included_when_query_matches() -> None:
    rows = [_message(1, "user", "Hello, world")]
    pairs = _pair_rows(rows)
    hits = _filter_pairs(pairs, note_path=_note_path(), query="Hello", tool=None)
    assert len(hits) == 1
    assert hits[0].result_status == "message"


# ---------------------------------------------------------------------------
# Snippet truncation
# ---------------------------------------------------------------------------


def test_truncate_short_string_unchanged() -> None:
    assert _truncate("hello") == "hello"


def test_truncate_long_string_appends_suffix() -> None:
    long = "x" * (_SNIPPET_MAX + 100)
    result = _truncate(long)
    assert result.endswith(_TRUNCATION_SUFFIX)
    assert len(result) == _SNIPPET_MAX + len(_TRUNCATION_SUFFIX)


# ---------------------------------------------------------------------------
# TranscriptSearchService
# ---------------------------------------------------------------------------


def test_search_filters_by_tool_name() -> None:
    service = TranscriptSearchService()
    result = service.search(tool="bash", scope=FIXTURE_ROOT)
    assert result.total_matches > 0
    assert all(hit.tool == "bash" for hit in result.hits)


def test_search_filters_by_query_in_args() -> None:
    service = TranscriptSearchService()
    result = service.search(query="docker compose", scope=FIXTURE_ROOT)
    assert result.total_matches > 0
    # All hits should contain the query in args or result
    for hit in result.hits:
        combined = (hit.args_snippet or "") + (hit.result_snippet or "")
        assert "docker compose" in combined.lower() or "docker compose" in combined


def test_search_filters_by_query_in_result() -> None:
    service = TranscriptSearchService()
    result = service.search(query="192.168", scope=FIXTURE_ROOT)
    assert result.total_matches > 0


def test_search_filters_by_scope_subtree(tmp_path: Path) -> None:
    sub_a = tmp_path / "a"
    sub_b = tmp_path / "b"
    sub_a.mkdir()
    sub_b.mkdir()

    note_a = sub_a / "note.md"
    note_a.write_text(
        "# A\n\n---\n# Transcript\n\n"
        "| 1 | assistant | tool_call | bash | c1 | {\"command\":\"echo alpha\"} |\n"
        "| 2 | tool | tool_result | bash | c1 | \"alpha output\" |\n"
    )
    note_b = sub_b / "note.md"
    note_b.write_text(
        "# B\n\n---\n# Transcript\n\n"
        "| 1 | assistant | tool_call | bash | c2 | {\"command\":\"echo beta\"} |\n"
        "| 2 | tool | tool_result | bash | c2 | \"beta output\" |\n"
    )

    service = TranscriptSearchService()
    result = service.search(scope=sub_a)
    paths = {hit.path for hit in result.hits}
    assert all(str(sub_a) in p for p in paths)
    assert not any(str(sub_b) in p for p in paths)


def test_search_truncates_snippets_at_600_chars(tmp_path: Path) -> None:
    huge_arg = "x" * 2000
    note = tmp_path / "note.md"
    import json
    content_json = json.dumps({"command": huge_arg}, separators=(",", ":"))
    note.write_text(
        f"# Big\n\n---\n# Transcript\n\n"
        f"| 1 | assistant | tool_call | bash | c1 | {content_json} |\n"
        f"| 2 | tool | tool_result | bash | c1 | \"done\" |\n"
    )
    service = TranscriptSearchService()
    result = service.search(scope=tmp_path)
    assert result.total_matches > 0
    for hit in result.hits:
        if hit.args_snippet:
            assert hit.args_snippet.endswith(_TRUNCATION_SUFFIX)


def test_search_paginates_with_limit_and_offset(tmp_path: Path) -> None:
    # Create a note with 30 tool calls
    rows = []
    for i in range(1, 31):
        rows.append(
            f"| {i*2-1} | assistant | tool_call | bash | c{i} | "
            f'{{\"command\":\"echo {i}\"}} |'
        )
        rows.append(
            f"| {i*2} | tool | tool_result | bash | c{i} | \"output {i}\" |"
        )
    transcript = "\n".join(rows)
    note = tmp_path / "note.md"
    note.write_text(f"# Many\n\n---\n# Transcript\n\n{transcript}\n")

    service = TranscriptSearchService()
    result = service.search(scope=tmp_path, limit=10, offset=10)
    assert result.total_matches == 30
    assert result.returned == 10
    assert result.offset == 10
    assert result.limit == 10
    assert result.truncated is True


def test_search_emits_narrow_hint_when_truncated(tmp_path: Path) -> None:
    rows = []
    for i in range(1, 26):
        rows.append(
            f"| {i*2-1} | assistant | tool_call | bash | c{i} | "
            f'{{\"command\":\"echo {i}\"}} |'
        )
        rows.append(
            f"| {i*2} | tool | tool_result | bash | c{i} | \"output {i}\" |"
        )
    note = tmp_path / "note.md"
    note.write_text(f"# T\n\n---\n# Transcript\n\n" + "\n".join(rows) + "\n")

    service = TranscriptSearchService()
    result = service.search(scope=tmp_path, limit=10, offset=0)
    assert result.truncated is True
    assert result.narrow_hint is not None
    assert "25" in result.narrow_hint


def test_search_no_matches_returns_empty_hits_no_hint(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        "# X\n\n---\n# Transcript\n\n"
        "| 1 | assistant | tool_call | bash | c1 | {\"command\":\"ls\"} |\n"
        "| 2 | tool | tool_result | bash | c1 | \"output\" |\n"
    )
    service = TranscriptSearchService()
    result = service.search(query="XYZZY_NOT_FOUND", scope=tmp_path)
    assert result.hits == []
    assert result.total_matches == 0
    assert result.narrow_hint is None


def test_search_includes_message_rows_when_query_matches(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        "# X\n\n---\n# Transcript\n\n"
        '| 1 | user | message |  |  | "What about docker?" |\n'
    )
    service = TranscriptSearchService()
    result = service.search(query="docker", scope=tmp_path)
    assert result.total_matches > 0
    assert any(hit.result_status == "message" for hit in result.hits)


def test_search_uses_fixture_tree() -> None:
    service = TranscriptSearchService()
    result = service.search(scope=FIXTURE_ROOT)
    # Each fixture note should contribute at least one hit
    hit_paths = {hit.path for hit in result.hits}
    bgp = str((FIXTURE_ROOT / "networking" / "bgp-notes.md").resolve())
    docker = str((FIXTURE_ROOT / "projects" / "homelab" / "docker-setup.md").resolve())
    assert bgp in hit_paths
    assert docker in hit_paths
