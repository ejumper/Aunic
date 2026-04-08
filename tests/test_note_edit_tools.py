from __future__ import annotations

from dataclasses import dataclass

import pytest

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

    assert tool_names == {"note_edit", "note_write", "web_search", "web_fetch"}


def test_tool_registries_expand_with_work_mode() -> None:
    note_names = {definition.spec.name for definition in build_note_tool_registry(work_mode="work")}
    chat_names = {definition.spec.name for definition in build_chat_tool_registry(work_mode="work")}

    assert {"read", "grep", "glob", "list", "edit", "write", "bash"} <= note_names
    assert {"note_edit", "note_write"} <= note_names
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


class _FakeRuntime:
    def __init__(self, note_content: str) -> None:
        self.working_note_content = note_content
        self.note_baseline_content = note_content
        self._live_note_content = note_content
        self.writes: list[tuple[str, str | None]] = []

    async def read_live_note(self):
        return _FakeSnapshot(), self._live_note_content, None

    async def write_live_note_content(self, new_note_content: str, *, expected_revision: str | None = None):
        self._live_note_content = new_note_content
        self.working_note_content = new_note_content
        self.note_baseline_content = new_note_content
        self.writes.append((new_note_content, expected_revision))
        return _FakeSnapshot(revision_id=expected_revision or "rev-write")


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
