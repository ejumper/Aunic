from __future__ import annotations

from pathlib import Path

import pytest

from aunic.tools.grep_notes import (
    GrepNotesArgs,
    build_grep_notes_tool_registry,
    execute_grep_notes,
    parse_grep_notes_args,
)
from aunic.tools.memory_tools import build_memory_tool_registry
from aunic.tools.note_edit import build_chat_tool_registry, build_note_tool_registry

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "aunic_notes"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeSessionState:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd


class _FakeRuntime:
    def __init__(self, cwd: Path) -> None:
        self.session_state = _FakeSessionState(cwd)


def _note_with_transcript(content: str, transcript_rows: str) -> str:
    """Build a minimal Aunic note string."""
    return f"{content}\n\n---\n# Transcript\n\n{transcript_rows}"


# ---------------------------------------------------------------------------
# parse_grep_notes_args — argument parsing
# ---------------------------------------------------------------------------


def test_parse_args_requires_pattern() -> None:
    with pytest.raises((ValueError, KeyError)):
        parse_grep_notes_args({})


def test_parse_args_defaults() -> None:
    args = parse_grep_notes_args({"pattern": "foo"})
    assert args.pattern == "foo"
    assert args.section == "all"
    assert args.scope is None
    assert args.case_sensitive is False
    assert args.literal_text is False
    assert args.context == 2
    assert args.limit == 20
    assert args.offset == 0


def test_parse_args_rejects_extra_keys() -> None:
    with pytest.raises(ValueError, match="Unexpected fields"):
        parse_grep_notes_args({"pattern": "x", "foo": 1})


def test_parse_args_rejects_invalid_section() -> None:
    with pytest.raises(ValueError, match="section"):
        parse_grep_notes_args({"pattern": "x", "section": "transcript-only"})


def test_parse_args_rejects_limit_over_100() -> None:
    with pytest.raises(ValueError, match="limit"):
        parse_grep_notes_args({"pattern": "x", "limit": 500})


def test_parse_args_rejects_limit_zero() -> None:
    with pytest.raises(ValueError, match="limit"):
        parse_grep_notes_args({"pattern": "x", "limit": 0})


def test_parse_args_rejects_context_out_of_range() -> None:
    with pytest.raises(ValueError, match="context"):
        parse_grep_notes_args({"pattern": "x", "context": 50})


def test_parse_args_rejects_negative_offset() -> None:
    with pytest.raises(ValueError, match="offset"):
        parse_grep_notes_args({"pattern": "x", "offset": -1})


def test_parse_args_full_payload() -> None:
    args = parse_grep_notes_args({
        "pattern": "docker",
        "section": "transcript",
        "scope": "/home/user",
        "case_sensitive": True,
        "literal_text": True,
        "context": 3,
        "limit": 50,
        "offset": 10,
    })
    assert args.pattern == "docker"
    assert args.section == "transcript"
    assert args.scope == "/home/user"
    assert args.case_sensitive is True
    assert args.literal_text is True
    assert args.context == 3
    assert args.limit == 50
    assert args.offset == 10


# ---------------------------------------------------------------------------
# Section splitting + line-number mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_section_all_returns_both_halves(tmp_path: Path) -> None:
    """Pattern matching in both note-content and transcript → hits with correct section attr."""
    note = tmp_path / "note.md"
    # "TARGET" appears in note-content (line 1) and in the transcript (row line)
    note.write_text(
        "TARGET appears here\n"
        "\n"
        "---\n"
        "# Transcript\n"
        "\n"
        "| 1 | user | message |  |  | \"TARGET also here\" |\n"
    )
    runtime = _FakeRuntime(cwd=tmp_path)
    args = GrepNotesArgs(pattern="TARGET", section="all", scope=str(tmp_path))

    result = await execute_grep_notes(runtime, args)  # type: ignore[arg-type]

    assert result.status == "completed"
    hits = result.in_memory_content["hits"]
    sections = {h["section"] for h in hits}
    assert "note-content" in sections
    assert "transcript" in sections


@pytest.mark.asyncio
async def test_section_note_content_excludes_transcript_matches(tmp_path: Path) -> None:
    """Pattern only in transcript → zero hits when section='note-content'."""
    note = tmp_path / "note.md"
    note.write_text(
        "Some prose without the keyword\n"
        "\n"
        "---\n"
        "# Transcript\n"
        "\n"
        "| 1 | assistant | tool_call | bash | c1 | {\"command\":\"TRANSCRIPT_ONLY\"} |\n"
    )
    runtime = _FakeRuntime(cwd=tmp_path)
    args = GrepNotesArgs(pattern="TRANSCRIPT_ONLY", section="note-content", scope=str(tmp_path))

    result = await execute_grep_notes(runtime, args)  # type: ignore[arg-type]

    assert result.status == "completed"
    assert result.in_memory_content["hits"] == []


@pytest.mark.asyncio
async def test_section_transcript_excludes_note_content_matches(tmp_path: Path) -> None:
    """Pattern only in note-content → zero hits when section='transcript'."""
    note = tmp_path / "note.md"
    note.write_text(
        "PROSE_ONLY keyword lives here in note content\n"
        "\n"
        "---\n"
        "# Transcript\n"
        "\n"
        "| 1 | user | message |  |  | \"nothing special\" |\n"
    )
    runtime = _FakeRuntime(cwd=tmp_path)
    args = GrepNotesArgs(pattern="PROSE_ONLY", section="transcript", scope=str(tmp_path))

    result = await execute_grep_notes(runtime, args)  # type: ignore[arg-type]

    assert result.status == "completed"
    assert result.in_memory_content["hits"] == []


@pytest.mark.asyncio
async def test_transcript_section_line_numbers_are_absolute(tmp_path: Path) -> None:
    """Hit inside transcript returns a line number referencing the full file, not offset-within-transcript."""
    # Structure:
    # line 1: # Note
    # line 2: (empty)
    # line 3: prose
    # line 4: (empty)
    # line 5: ---           <- transcript_start_line = 5
    # line 6: # Transcript
    # line 7: (empty)
    # line 8: | 1 | ...     <- "FINDME" is on line 8 in the original file
    note = tmp_path / "note.md"
    note.write_text(
        "# Note\n"
        "\n"
        "prose\n"
        "\n"
        "---\n"
        "# Transcript\n"
        "\n"
        "| 1 | user | message |  |  | \"FINDME\" |\n"
    )
    runtime = _FakeRuntime(cwd=tmp_path)
    args = GrepNotesArgs(pattern="FINDME", section="transcript", scope=str(tmp_path))

    result = await execute_grep_notes(runtime, args)  # type: ignore[arg-type]

    assert result.status == "completed"
    hits = result.in_memory_content["hits"]
    assert len(hits) == 1
    assert hits[0]["line"] == 8


@pytest.mark.asyncio
async def test_context_clipped_to_section_bounds(tmp_path: Path) -> None:
    """Context lines do not spill across the section boundary.

    When section='note-content', a hit on the very first line of note-content
    has an empty context_before (no lines above it in the note-content haystack).
    When section='transcript', context_before is bounded by the start of the
    transcript haystack — it never includes note-content prose lines.
    """
    note = tmp_path / "note.md"
    note.write_text(
        "MATCH_FIRST_NOTE_LINE\n"
        "prose line 2\n"
        "prose line 3\n"
        "\n"
        "---\n"
        "# Transcript\n"
        "\n"
        "| 1 | user | message |  |  | \"hi\" |\n"
    )
    runtime = _FakeRuntime(cwd=tmp_path)

    # Hit on the first line of note-content: context_before should be empty
    args = GrepNotesArgs(
        pattern="MATCH_FIRST_NOTE_LINE", section="note-content", scope=str(tmp_path), context=3
    )
    result = await execute_grep_notes(runtime, args)  # type: ignore[arg-type]

    assert result.status == "completed"
    hits = result.in_memory_content["hits"]
    assert len(hits) == 1
    # First line of note-content → no lines above in the haystack
    assert hits[0]["context_before"] == []


@pytest.mark.asyncio
async def test_section_all_attribution_at_boundary(tmp_path: Path) -> None:
    """The --- line itself is attributed to 'transcript' (>= transcript_start_line)."""
    note = tmp_path / "note.md"
    note.write_text(
        "prose\n"
        "\n"
        "---\n"
        "# Transcript\n"
        "\n"
        "| 1 | user | message |  |  | \"hi\" |\n"
    )
    runtime = _FakeRuntime(cwd=tmp_path)
    # Search for "---" with section="all": should find the --- line attributed as "transcript"
    args = GrepNotesArgs(pattern=r"^---$", section="all", scope=str(tmp_path))

    result = await execute_grep_notes(runtime, args)  # type: ignore[arg-type]

    assert result.status == "completed"
    hits = result.in_memory_content["hits"]
    assert len(hits) >= 1
    # The first hit on "---" is the transcript separator line
    separator_hits = [h for h in hits if h["match"] == "---"]
    assert all(h["section"] == "transcript" for h in separator_hits)


# ---------------------------------------------------------------------------
# Matcher behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_case_insensitive_by_default(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        "docker compose up\n"
        "\n"
        "---\n# Transcript\n\n| 1 | user | message |  |  | \"hi\" |\n"
    )
    runtime = _FakeRuntime(cwd=tmp_path)
    args = GrepNotesArgs(pattern="DOCKER", section="note-content", scope=str(tmp_path))

    result = await execute_grep_notes(runtime, args)  # type: ignore[arg-type]

    assert result.status == "completed"
    assert len(result.in_memory_content["hits"]) == 1


@pytest.mark.asyncio
async def test_case_sensitive_flag_respects_case(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        "docker compose up\n"
        "\n"
        "---\n# Transcript\n\n| 1 | user | message |  |  | \"hi\" |\n"
    )
    runtime = _FakeRuntime(cwd=tmp_path)
    args = GrepNotesArgs(
        pattern="DOCKER", section="note-content", scope=str(tmp_path), case_sensitive=True
    )

    result = await execute_grep_notes(runtime, args)  # type: ignore[arg-type]

    assert result.status == "completed"
    assert result.in_memory_content["hits"] == []


@pytest.mark.asyncio
async def test_literal_text_escapes_regex(tmp_path: Path) -> None:
    """With literal_text=True, '.' matches a literal dot, not every character."""
    note = tmp_path / "note.md"
    note.write_text(
        "10.0.0.1 is the server\n"
        "anything can match here\n"
        "\n"
        "---\n# Transcript\n\n| 1 | user | message |  |  | \"hi\" |\n"
    )
    runtime = _FakeRuntime(cwd=tmp_path)
    # Without literal_text, "." matches every non-empty line
    args_re = GrepNotesArgs(pattern=".", section="note-content", scope=str(tmp_path))
    result_re = await execute_grep_notes(runtime, args_re)  # type: ignore[arg-type]

    # With literal_text, "." only matches lines containing a literal dot
    args_lit = GrepNotesArgs(
        pattern=".", section="note-content", scope=str(tmp_path), literal_text=True
    )
    result_lit = await execute_grep_notes(runtime, args_lit)  # type: ignore[arg-type]

    assert result_re.status == "completed"
    assert result_lit.status == "completed"
    # literal should return fewer hits than the regex "."
    assert len(result_lit.in_memory_content["hits"]) < len(result_re.in_memory_content["hits"])
    # literal only matches the IP-address line
    assert all("." in h["match"] for h in result_lit.in_memory_content["hits"])


@pytest.mark.asyncio
async def test_invalid_regex_returns_tool_error(tmp_path: Path) -> None:
    runtime = _FakeRuntime(cwd=tmp_path)
    args = GrepNotesArgs(pattern="[oops", scope=str(tmp_path))

    result = await execute_grep_notes(runtime, args)  # type: ignore[arg-type]

    assert result.status == "tool_error"
    assert result.in_memory_content["reason"] == "invalid_regex"


# ---------------------------------------------------------------------------
# Scope + discovery
# ---------------------------------------------------------------------------


def test_scope_walks_only_subtree() -> None:
    """Pointing scope at networking/ → hits only from bgp-notes.md."""
    from aunic.tools.grep_notes import _grep_note, GrepNotesArgs
    import re

    networking_dir = FIXTURE_ROOT / "networking"
    notes_in_scope = [networking_dir / "bgp-notes.md"]

    args = GrepNotesArgs(pattern="ip route", section="transcript", scope=str(networking_dir))
    compiled = re.compile("ip route", re.IGNORECASE)

    all_hits = []
    for note_path in notes_in_scope:
        text = note_path.read_text(encoding="utf-8")
        all_hits.extend(_grep_note(text, note_path=note_path, args=args, compiled=compiled))

    assert len(all_hits) >= 1
    assert all("bgp-notes" in h["path"] for h in all_hits)


@pytest.mark.asyncio
async def test_scope_rejects_nonexistent_path(tmp_path: Path) -> None:
    runtime = _FakeRuntime(cwd=tmp_path)
    args = GrepNotesArgs(pattern="x", scope=str(tmp_path / "no_such_dir"))

    result = await execute_grep_notes(runtime, args)  # type: ignore[arg-type]

    assert result.status == "tool_error"
    assert result.in_memory_content["reason"] == "scope_not_found"


@pytest.mark.asyncio
async def test_scope_rejects_file_path(tmp_path: Path) -> None:
    f = tmp_path / "afile.txt"
    f.write_text("hello")
    runtime = _FakeRuntime(cwd=tmp_path)
    args = GrepNotesArgs(pattern="x", scope=str(f))

    result = await execute_grep_notes(runtime, args)  # type: ignore[arg-type]

    assert result.status == "tool_error"
    assert result.in_memory_content["reason"] == "scope_not_directory"


@pytest.mark.asyncio
async def test_plain_markdown_file_is_skipped() -> None:
    """plain-markdown.md in the fixture should never appear in results."""
    import tempfile
    from pathlib import Path as P

    # We need a runtime with a real cwd
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime = _FakeRuntime(cwd=P(tmpdir))
        # Search across the whole fixture root; plain-markdown.md contains "Plain Markdown"
        args = GrepNotesArgs(pattern="Plain Markdown", scope=str(FIXTURE_ROOT))

        result = await execute_grep_notes(runtime, args)  # type: ignore[arg-type]

        assert result.status == "completed"
        paths = {h["path"] for h in result.in_memory_content["hits"]}
        assert not any("plain-markdown" in p for p in paths)


# ---------------------------------------------------------------------------
# Pagination + truncation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pagination_returns_correct_window(tmp_path: Path) -> None:
    """Generate a note with many matches; limit=3 offset=3 → 3 hits, correct total."""
    lines = "\n".join(f"MATCH line {i}" for i in range(20))
    note = tmp_path / "note.md"
    note.write_text(f"{lines}\n\n---\n# Transcript\n\n| 1 | user | message |  |  | \"hi\" |\n")

    runtime = _FakeRuntime(cwd=tmp_path)
    args = GrepNotesArgs(pattern="MATCH", section="note-content", scope=str(tmp_path), limit=3, offset=3)

    result = await execute_grep_notes(runtime, args)  # type: ignore[arg-type]

    assert result.status == "completed"
    payload = result.in_memory_content
    assert payload["returned"] == 3
    assert payload["offset"] == 3
    assert payload["total_matches"] == 20


@pytest.mark.asyncio
async def test_narrow_hint_populated_when_truncated(tmp_path: Path) -> None:
    lines = "\n".join(f"MATCH line {i}" for i in range(30))
    note = tmp_path / "note.md"
    note.write_text(f"{lines}\n\n---\n# Transcript\n\n| 1 | user | message |  |  | \"hi\" |\n")

    runtime = _FakeRuntime(cwd=tmp_path)
    args = GrepNotesArgs(pattern="MATCH", section="note-content", scope=str(tmp_path), limit=10)

    result = await execute_grep_notes(runtime, args)  # type: ignore[arg-type]

    payload = result.in_memory_content
    assert payload["truncated"] is True
    assert payload["narrow_hint"] is not None
    assert "30" in payload["narrow_hint"]


@pytest.mark.asyncio
async def test_collection_cap_at_10x_limit(tmp_path: Path) -> None:
    """Pattern '.' with limit=5 should cap collection at 50 and set narrow_hint."""
    # Write a note with 100 lines, all matching "."
    lines = "\n".join(f"line {i}" for i in range(100))
    note = tmp_path / "note.md"
    note.write_text(f"{lines}\n\n---\n# Transcript\n\n| 1 | user | message |  |  | \"hi\" |\n")

    runtime = _FakeRuntime(cwd=tmp_path)
    args = GrepNotesArgs(
        pattern="line", section="note-content", scope=str(tmp_path), limit=5, literal_text=True
    )

    result = await execute_grep_notes(runtime, args)  # type: ignore[arg-type]

    payload = result.in_memory_content
    # total_matches capped at 10 * 5 = 50
    assert payload["total_matches"] <= 50
    # narrow_hint should mention the cap
    assert payload["narrow_hint"] is not None
    assert "capped" in payload["narrow_hint"].lower()


@pytest.mark.asyncio
async def test_no_matches_returns_empty_hits_no_hint(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        "some prose\n\n---\n# Transcript\n\n| 1 | user | message |  |  | \"hi\" |\n"
    )
    runtime = _FakeRuntime(cwd=tmp_path)
    args = GrepNotesArgs(pattern="NEVERMATCHES12345", scope=str(tmp_path))

    result = await execute_grep_notes(runtime, args)  # type: ignore[arg-type]

    payload = result.in_memory_content
    assert result.status == "completed"
    assert payload["hits"] == []
    assert payload["total_matches"] == 0
    assert payload["narrow_hint"] is None


# ---------------------------------------------------------------------------
# Execution wrapper / integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_returns_completed_on_success(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        "docker compose info\n\n---\n# Transcript\n\n"
        "| 1 | assistant | tool_call | bash | c1 | {\"command\":\"docker ps\"} |\n"
        "| 2 | tool | tool_result | bash | c1 | \"running\" |\n"
    )
    runtime = _FakeRuntime(cwd=tmp_path)
    args = GrepNotesArgs(pattern="docker", scope=str(tmp_path))

    result = await execute_grep_notes(runtime, args)  # type: ignore[arg-type]

    assert result.status == "completed"
    assert isinstance(result.in_memory_content, dict)
    assert len(result.in_memory_content["hits"]) > 0


@pytest.mark.asyncio
async def test_execute_fixture_notes_have_hits() -> None:
    """Integration: fixture notes return hits for known patterns in each file."""
    import tempfile
    from pathlib import Path as P

    with tempfile.TemporaryDirectory() as tmpdir:
        runtime = _FakeRuntime(cwd=P(tmpdir))

        # bgp-notes.md contains "ip route" in transcript
        args_bgp = GrepNotesArgs(
            pattern="ip route", section="transcript", scope=str(FIXTURE_ROOT / "networking")
        )
        result_bgp = await execute_grep_notes(runtime, args_bgp)  # type: ignore[arg-type]
        assert result_bgp.status == "completed"
        assert len(result_bgp.in_memory_content["hits"]) >= 1

        # docker-setup.md contains "docker compose" in transcript
        args_docker = GrepNotesArgs(
            pattern="docker compose", section="transcript",
            scope=str(FIXTURE_ROOT / "projects")
        )
        result_docker = await execute_grep_notes(runtime, args_docker)  # type: ignore[arg-type]
        assert result_docker.status == "completed"
        assert len(result_docker.in_memory_content["hits"]) >= 1


@pytest.mark.asyncio
async def test_execute_normalizes_tilde_scope(tmp_path: Path) -> None:
    """scope='~/nonexistent-path-xyz' resolves and fails cleanly."""
    runtime = _FakeRuntime(cwd=tmp_path)
    args = GrepNotesArgs(pattern="x", scope="~/nonexistent-path-aunic-test-xyz")

    result = await execute_grep_notes(runtime, args)  # type: ignore[arg-type]

    assert result.status == "tool_error"
    assert result.in_memory_content["reason"] == "scope_not_found"


# ---------------------------------------------------------------------------
# Registry + manifest
# ---------------------------------------------------------------------------


def test_build_grep_notes_tool_registry_returns_one_tool() -> None:
    registry = build_grep_notes_tool_registry()
    assert len(registry) == 1
    assert registry[0].spec.name == "grep_notes"


def test_grep_notes_in_chat_registry_off_mode() -> None:
    registry = build_chat_tool_registry(work_mode="off")
    names = {d.spec.name for d in registry}
    assert "grep_notes" in names


def test_grep_notes_in_chat_registry_read_mode() -> None:
    registry = build_chat_tool_registry(work_mode="read")
    names = {d.spec.name for d in registry}
    assert "grep_notes" in names


def test_grep_notes_in_chat_registry_work_mode() -> None:
    registry = build_chat_tool_registry(work_mode="work")
    names = {d.spec.name for d in registry}
    assert "grep_notes" in names


def test_grep_notes_in_note_registry_off_mode() -> None:
    registry = build_note_tool_registry(work_mode="off")
    names = {d.spec.name for d in registry}
    assert "grep_notes" in names


def test_grep_notes_in_note_registry_read_mode() -> None:
    registry = build_note_tool_registry(work_mode="read")
    names = {d.spec.name for d in registry}
    assert "grep_notes" in names


def test_grep_notes_in_note_registry_work_mode() -> None:
    registry = build_note_tool_registry(work_mode="work")
    names = {d.spec.name for d in registry}
    assert "grep_notes" in names


def test_memory_manifest_includes_grep_notes_bullet() -> None:
    from aunic.tools.memory_manifest import build_memory_manifest

    registry = build_memory_tool_registry()
    manifest = build_memory_manifest(registry)

    assert manifest is not None
    assert "grep_notes:" in manifest


def test_memory_tool_registry_contains_both_memory_tools() -> None:
    registry = build_memory_tool_registry()
    names = {d.spec.name for d in registry}
    assert "search_transcripts" in names
    assert "grep_notes" in names
