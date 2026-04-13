from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aunic.domain import ToolSpec
from aunic.tools.base import ToolDefinition
from aunic.tools.memory_manifest import MEMORY_TOOL_HINTS, build_memory_manifest
from aunic.tools.memory_tools import build_memory_tool_registry
from aunic.tools.search_transcripts import build_search_transcripts_tool_registry
from aunic.tools.research import build_research_tool_registry


def _make_tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        spec=ToolSpec(name=name, description="", input_schema={}),
        parse_arguments=lambda p: p,
        execute=lambda r, a: ...,  # type: ignore[arg-type, return-value]
    )


# ---------------------------------------------------------------------------
# build_memory_manifest
# ---------------------------------------------------------------------------


def test_manifest_returns_none_for_empty_registry() -> None:
    assert build_memory_manifest(()) is None


def test_manifest_returns_none_for_non_memory_tools() -> None:
    registry = build_research_tool_registry()
    assert build_memory_manifest(registry) is None


def test_manifest_returns_string_when_search_transcripts_present() -> None:
    registry = build_memory_tool_registry()
    manifest = build_memory_manifest(registry)
    assert manifest is not None
    assert "search_transcripts" in manifest
    assert "Memory tools." in manifest


def test_manifest_contains_preamble() -> None:
    registry = build_memory_tool_registry()
    manifest = build_memory_manifest(registry)
    assert manifest is not None
    assert "Before proposing destructive bash commands" in manifest


def test_manifest_only_mentions_present_tools() -> None:
    # A registry containing only search_transcripts should NOT mention grep_notes or future tools
    registry = build_search_transcripts_tool_registry()
    manifest = build_memory_manifest(registry)
    assert manifest is not None
    assert "search_transcripts" in manifest
    assert "grep_notes" not in manifest
    assert "read_map" not in manifest
    assert "rag_search" not in manifest


def test_manifest_omits_absent_tool_bullets() -> None:
    """Alias for test_manifest_only_mentions_present_tools: confirms per-tool presence is dynamic."""
    registry = build_search_transcripts_tool_registry()
    manifest = build_memory_manifest(registry)
    assert manifest is not None
    assert "grep_notes:" not in manifest


def test_manifest_includes_both_when_both_present() -> None:
    """Full memory registry → manifest mentions both search_transcripts and grep_notes."""
    registry = build_memory_tool_registry()
    manifest = build_memory_manifest(registry)
    assert manifest is not None
    assert "search_transcripts" in manifest
    assert "grep_notes" in manifest
    # Preamble appears first, then both bullets
    preamble_pos = manifest.index("Memory tools.")
    st_pos = manifest.index("search_transcripts")
    gn_pos = manifest.index("grep_notes")
    assert preamble_pos < st_pos
    assert preamble_pos < gn_pos


# ---------------------------------------------------------------------------
# Chat system prompt splice
# ---------------------------------------------------------------------------


def test_chat_system_prompt_splices_manifest() -> None:
    from aunic.modes.chat import _build_chat_system_prompt

    registry = tuple(build_memory_tool_registry())
    prompt = _build_chat_system_prompt(
        work_mode="off",
        registry=registry,
        protected_paths=(),
    )
    assert "Memory tools." in prompt


def test_chat_system_prompt_omits_manifest_without_memory_tools() -> None:
    from aunic.modes.chat import _build_chat_system_prompt

    registry = tuple(build_research_tool_registry())
    prompt = _build_chat_system_prompt(
        work_mode="off",
        registry=registry,
        protected_paths=(),
    )
    assert "Memory tools." not in prompt


# ---------------------------------------------------------------------------
# Note loop system prompt splice
# ---------------------------------------------------------------------------


def test_note_loop_system_prompt_splices_manifest() -> None:
    from aunic.loop.runner import _build_system_prompt
    from aunic.tools.note_edit import build_note_tool_registry

    registry = build_note_tool_registry(work_mode="off")
    prompt = _build_system_prompt(
        None,
        work_mode="off",
        registry=registry,
        protected_paths=(),
    )
    assert "Memory tools." in prompt


def test_note_loop_system_prompt_omits_manifest_without_memory_tools() -> None:
    from aunic.loop.runner import _build_system_prompt

    # Only research tools, no memory tools
    registry = tuple(build_research_tool_registry())
    prompt = _build_system_prompt(
        None,
        work_mode="off",
        registry=registry,
        protected_paths=(),
    )
    assert "Memory tools." not in prompt
