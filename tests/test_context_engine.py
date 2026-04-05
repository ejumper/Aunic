from __future__ import annotations

from pathlib import Path

import pytest

from aunic.context import ContextBuildRequest, ContextEngine


@pytest.mark.asyncio
async def test_context_engine_builds_direct_mode_context_across_files(tmp_path: Path) -> None:
    active = tmp_path / "active.md"
    included = tmp_path / "included.md"
    active.write_text(
        "# Active\n"
        "Intro paragraph.\n"
        "@>>Editable core<<@\n",
        encoding="utf-8",
    )
    included.write_text(
        "# Included\n"
        "Shared reference note.\n",
        encoding="utf-8",
    )

    result = await ContextEngine().build_context(
        ContextBuildRequest(
            active_file=active,
            included_files=(included,),
            user_prompt="Summarize the editable area.",
        )
    )

    assert [snapshot.path for snapshot in result.file_snapshots] == [
        active.resolve(),
        included.resolve(),
    ]
    assert result.parsed_note_text.startswith("FILE: active.md")
    assert "FILE: included.md" in result.parsed_note_text
    assert "USER PROMPT\nSummarize the editable area." in result.model_input_text
    assert any(
        node.label == "WRITE-EDIT_ALLOWED" and "Editable core" in node.preview
        for node in result.structural_nodes
    )
    assert any(
        node.label == "READ_ONLY-NO_EDITS" and "Intro paragraph." in node.preview
        for node in result.structural_nodes
    )


@pytest.mark.asyncio
async def test_context_engine_treats_inline_prompt_markers_as_plain_text(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        "# Plan\n"
        "Prelude text.\n"
        ">>First prompt<<\n"
        "\n"
        ">>Second prompt<<\n",
        encoding="utf-8",
    )

    result = await ContextEngine().build_context(
        ContextBuildRequest(
            active_file=note,
            user_prompt="Summarize the note.",
            total_turn_budget=9,
        )
    )

    assert len(result.prompt_runs) == 1
    assert result.prompt_runs[0].prompt_text == "Summarize the note."
    assert result.prompt_runs[0].per_prompt_budget == 9
    assert ">>First prompt<<" in result.parsed_note_text
    assert ">>Second prompt<<" in result.parsed_note_text


@pytest.mark.asyncio
async def test_context_engine_preserves_chat_thread_as_single_section(tmp_path: Path) -> None:
    note = tmp_path / "chat.md"
    note.write_text(
        "***\n\n"
        "> what changed?\n\n"
        "We updated the note.\n",
        encoding="utf-8",
    )

    result = await ContextEngine().build_context(
        ContextBuildRequest(
            active_file=note,
            user_prompt="Inspect the chat transcript.",
        )
    )

    chat_nodes = [node for node in result.structural_nodes if node.kind == "chat_thread"]
    assert len(chat_nodes) == 1
    assert any(node.kind == "chat_thread" for node in result.structural_nodes)


@pytest.mark.asyncio
async def test_context_engine_recognizes_marker_wrapped_heading_as_heading(tmp_path: Path) -> None:
    note = tmp_path / "heading.md"
    note.write_text(
        "@>>## Editable Heading<<@\n"
        "Body text.\n",
        encoding="utf-8",
    )

    result = await ContextEngine().build_context(
        ContextBuildRequest(
            active_file=note,
            user_prompt="Inspect headings.",
        )
    )

    heading_nodes = [node for node in result.structural_nodes if node.kind == "heading"]
    assert len(heading_nodes) == 1
    assert heading_nodes[0].heading_id == "h:1:1"
    assert heading_nodes[0].heading_level == 2
    assert heading_nodes[0].label == "WRITE-EDIT_ALLOWED"


@pytest.mark.asyncio
async def test_context_engine_marks_search_results_section_read_only(tmp_path: Path) -> None:
    note = tmp_path / "search-results.md"
    note.write_text(
        "@>>Editable text.<<@\n\n"
        "# Search Results\n\n"
        "## Search Batch 2026-03-21T00:00:00Z\n"
        "- `s1` | [Python](https://www.python.org/) | score=0.900 | rank=1\n",
        encoding="utf-8",
    )

    result = await ContextEngine().build_context(
        ContextBuildRequest(
            active_file=note,
            user_prompt="Inspect the note.",
        )
    )

    assert any(node.label == "WRITE-EDIT_ALLOWED" and "Editable text." in node.preview for node in result.structural_nodes)
    assert any(
        node.label == "READ_ONLY-SEARCH_RESULTS" and "Python" in node.preview
        for node in result.structural_nodes
    )


@pytest.mark.asyncio
async def test_context_engine_adds_top_of_file_anchor_for_empty_note(tmp_path: Path) -> None:
    note = tmp_path / "blank.md"
    note.write_text("", encoding="utf-8")

    result = await ContextEngine().build_context(
        ContextBuildRequest(
            active_file=note,
            user_prompt="Start the note.",
        )
    )

    assert len(result.structural_nodes) == 1
    anchor = result.structural_nodes[0]
    assert anchor.kind == "anchor"
    assert anchor.label == "WRITE-EDIT_ALLOWED"
    assert anchor.anchor_id == "a:1:0"
    assert anchor.preview == "(start of file)"
    assert "a:1:0" in result.target_map_text


@pytest.mark.asyncio
async def test_context_engine_excludes_transcript_from_note_snapshot_and_structure(
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        "# Body\n\n"
        "@>>Editable text<<@\n\n"
        "---\n"
        "# Transcript\n"
        "| # | role      | type        | tool_name  | tool_id  | content\n"
        "|---|-----------|-------------|------------|----------|-------------------------------\n"
        '| 1 | user      | message     |            |          | "hello"\n',
        encoding="utf-8",
    )

    result = await ContextEngine().build_context(
        ContextBuildRequest(
            active_file=note,
            user_prompt="Inspect the note.",
        )
    )

    assert "Editable text" in result.parsed_note_text
    assert "# Transcript" not in result.parsed_note_text
    assert result.transcript_text is not None
    assert "# Transcript" in result.transcript_text
    assert all("# Transcript" not in node.preview for node in result.structural_nodes)
