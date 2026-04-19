from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from aunic.context.markers import analyze_note_file
from aunic.context.types import FileSnapshot
from aunic.tools.note_edit import (
    NoteEditArgs,
    NoteWriteArgs,
    OUTSIDE_NOTE_TOOL_NAMES,
    build_chat_tool_registry,
    build_note_only_registry,
    build_note_tool_registry,
    execute_note_edit,
    execute_note_write,
)


def test_build_note_tool_registry_defaults_to_note_and_research_tools() -> None:
    registry = build_note_tool_registry()
    tool_names = {definition.spec.name for definition in registry}

    assert tool_names == {
        "enter_plan_mode",
        "plan_create",
        "plan_write",
        "plan_edit",
        "exit_plan",
        "sleep",
        "stop_process",
        "note_edit",
        "note_write",
        "web_search",
        "web_fetch",
        "search_transcripts",
        "grep_notes",
        "read_map",
    }


def test_tool_registries_expand_with_work_mode() -> None:
    note_names = {definition.spec.name for definition in build_note_tool_registry(work_mode="work")}
    chat_names = {definition.spec.name for definition in build_chat_tool_registry(work_mode="work")}

    assert {"read", "grep", "glob", "list", "edit", "write", "bash"} <= note_names
    assert {"note_edit", "note_write"} <= note_names
    assert "sleep" in note_names
    assert "sleep" in chat_names
    assert "stop_process" in note_names
    assert "stop_process" in chat_names
    assert {"read", "grep", "glob", "list", "edit", "write", "bash"} <= chat_names
    assert "note_edit" not in chat_names
    assert "note_write" not in chat_names


def test_build_note_only_registry_and_outside_note_tool_constants() -> None:
    note_only_names = {definition.spec.name for definition in build_note_only_registry()}

    assert note_only_names == {"note_edit", "note_write"}
    assert OUTSIDE_NOTE_TOOL_NAMES == {
        "read",
        "grep",
        "glob",
        "list",
        "edit",
        "write",
        "bash",
    }


@dataclass(frozen=True)
class _FakeSnapshot:
    revision_id: str = "rev-1"


@dataclass
class _FakeContextResult:
    parsed_files: list


class _FakeRuntime:
    def __init__(self, note_content: str, *, context_result=None, active_file: Path | None = None) -> None:
        self.working_note_content = note_content
        self.note_baseline_content = note_content
        self._live_note_content = note_content
        self.writes: list[tuple[str, str | None]] = []
        self.context_result = context_result
        self.active_file = active_file or Path("/fake/note.md")

    async def read_live_note(self):
        return _FakeSnapshot(), self._live_note_content, None

    async def write_live_note_content(self, new_note_content: str, *, expected_revision: str | None = None):
        self._live_note_content = new_note_content
        self.working_note_content = new_note_content
        self.note_baseline_content = new_note_content
        self.writes.append((new_note_content, expected_revision))
        return _FakeSnapshot(revision_id=expected_revision or "rev-write")


def _runtime_for(note_content: str, tmp_path: Path) -> _FakeRuntime:
    """Build a _FakeRuntime with a real ParsedNoteFile derived from note_content."""
    path = tmp_path / "note.md"
    snapshot = FileSnapshot(
        path=path,
        raw_text=note_content,
        revision_id="rev-1",
        content_hash=hashlib.sha256(note_content.encode()).hexdigest(),
        mtime_ns=1,
        size_bytes=len(note_content.encode()),
    )
    analysis = analyze_note_file(snapshot, "note.md")
    ctx = _FakeContextResult(parsed_files=[analysis.parsed_file])
    return _FakeRuntime(note_content, context_result=ctx, active_file=path)


@pytest.mark.asyncio
async def test_execute_note_write_normalizes_markdown_tables() -> None:
    runtime = _FakeRuntime("")
    content = (
        "| A | Long Header |\n"
        "| --- | --- |\n"
        "| 1 | 22 |\n"
    )

    result = await execute_note_write(runtime, NoteWriteArgs(content=content))

    expected = (
        "| A | Long Header |\n"
        "| :-- | :---------- |\n"
        "| 1 | 22          |\n"
    )
    assert result.status == "completed"
    assert runtime.writes == [(expected, "rev-1")]
    assert runtime.working_note_content == expected
    assert result.in_memory_content["content"] == expected


@pytest.mark.asyncio
async def test_execute_note_edit_normalizes_only_touched_tables() -> None:
    baseline = (
        "| A | Long Header |\n"
        "| --- | --- |\n"
        "| 1 | 2 |\n"
        "\n"
        "| X | Y |\n"
        "| --- | --- |\n"
        "| left | right |\n"
    )
    runtime = _FakeRuntime(baseline)

    result = await execute_note_edit(runtime, NoteEditArgs(old_string="| 1 | 2 |", new_string="| 22 | 2 |"))

    expected = (
        "| A  | Long Header |\n"
        "| :-- | :---------- |\n"
        "| 22 | 2           |\n"
        "\n"
        "| X | Y |\n"
        "| --- | --- |\n"
        "| left | right |\n"
    )
    assert result.status == "completed"
    assert runtime.writes == [(expected, "rev-1")]
    assert runtime.working_note_content == expected
    assert result.in_memory_content["structured_patch"][-1]["new_end_line"] == 3


# --- Marker-aware note_write tests ---


@pytest.mark.asyncio
async def test_note_write_single_include_only_scopes_to_span(tmp_path: Path) -> None:
    note = "before\n!>>old visible content<<!after\n"
    runtime = _runtime_for(note, tmp_path)

    result = await execute_note_write(runtime, NoteWriteArgs(content="new content"))

    assert result.status == "completed"
    written = runtime.writes[0][0]
    assert written == "before\n!>>new content<<!after\n"
    assert "before" in written
    assert "after" in written
    assert "old visible content" not in written


@pytest.mark.asyncio
async def test_note_write_multiple_include_only_returns_error(tmp_path: Path) -> None:
    note = "!>>section one<<!gap!>>section two<<!end"
    runtime = _runtime_for(note, tmp_path)

    result = await execute_note_write(runtime, NoteWriteArgs(content="anything"))

    assert result.status == "tool_error"
    assert result.tool_failure is not None
    assert result.tool_failure.category == "protected_rejection"
    assert result.tool_failure.reason == "multiple_include_only_markers"
    assert not runtime.writes


@pytest.mark.asyncio
async def test_note_write_exclude_at_top_preserved_above(tmp_path: Path) -> None:
    note = "%>>secret header<<%\nvisible body\n"
    runtime = _runtime_for(note, tmp_path)

    result = await execute_note_write(runtime, NoteWriteArgs(content="new body"))

    assert result.status == "completed"
    written = runtime.writes[0][0]
    assert written.startswith("%>>secret header<<%")
    assert "new body" in written
    assert written.index("%>>secret header<<%") < written.index("new body")


@pytest.mark.asyncio
async def test_note_write_exclude_at_bottom_preserved_below(tmp_path: Path) -> None:
    note = "visible body\n%>>secret footer<<%"
    runtime = _runtime_for(note, tmp_path)

    result = await execute_note_write(runtime, NoteWriteArgs(content="new body"))

    assert result.status == "completed"
    written = runtime.writes[0][0]
    assert "%>>secret footer<<%" in written
    assert written.index("new body") < written.index("%>>secret footer<<%")


@pytest.mark.asyncio
async def test_note_write_exclude_in_middle_returns_error(tmp_path: Path) -> None:
    note = "top\n%>>hidden<<%\nbottom\n"
    runtime = _runtime_for(note, tmp_path)

    result = await execute_note_write(runtime, NoteWriteArgs(content="anything"))

    assert result.status == "tool_error"
    assert result.tool_failure is not None
    assert result.tool_failure.category == "protected_rejection"
    assert result.tool_failure.reason == "middle_exclude_marker"
    assert not runtime.writes


@pytest.mark.asyncio
async def test_note_write_all_hidden_note_treated_as_top(tmp_path: Path) -> None:
    """A note whose entire content is excluded: write is placed below the block."""
    note = "%>>everything is secret<<%"
    runtime = _runtime_for(note, tmp_path)

    result = await execute_note_write(runtime, NoteWriteArgs(content="new content"))

    assert result.status == "completed"
    written = runtime.writes[0][0]
    assert "%>>everything is secret<<%" in written
    assert "new content" in written
    assert written.index("%>>everything is secret<<%") < written.index("new content")


@pytest.mark.asyncio
async def test_note_write_top_and_bottom_exclude_both_preserved(tmp_path: Path) -> None:
    note = "%>>top secret<<%\nvisible\n%>>bottom secret<<%"
    runtime = _runtime_for(note, tmp_path)

    result = await execute_note_write(runtime, NoteWriteArgs(content="replacement"))

    assert result.status == "completed"
    written = runtime.writes[0][0]
    assert "%>>top secret<<%" in written
    assert "%>>bottom secret<<%" in written
    assert "replacement" in written
    top_pos = written.index("%>>top secret<<%")
    rep_pos = written.index("replacement")
    bot_pos = written.index("%>>bottom secret<<%")
    assert top_pos < rep_pos < bot_pos
