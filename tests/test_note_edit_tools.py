from __future__ import annotations

from aunic.tools.note_edit import (
    OUTSIDE_NOTE_TOOL_NAMES,
    build_chat_tool_registry,
    build_note_only_registry,
    build_note_tool_registry,
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
