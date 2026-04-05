from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from aunic.domain import ProviderRequest, TranscriptRow
from aunic.context import ContextBuildRequest, ContextEngine
from aunic.transcript.parser import (
    find_transcript_section,
    parse_transcript_rows,
    split_note_and_transcript,
)
from aunic.transcript.translation import (
    group_assistant_rows,
    translate_for_anthropic,
    translate_for_openai,
    translate_transcript,
)
from aunic.transcript.writer import (
    append_synthetic_tool_pair,
    append_transcript_row,
    delete_row_by_number,
    delete_rows_by_tool_id,
    ensure_transcript_section,
    next_synthetic_user_tool_id,
    repair_transcript_section,
)


def test_transcript_row_is_frozen_and_can_bridge_to_legacy_message() -> None:
    row = TranscriptRow(
        row_number=1,
        role="assistant",
        type="tool_call",
        tool_name="web_search",
        tool_id="call_1",
        content={"queries": ["weather today"]},
    )

    message = row.to_legacy_message()

    assert message.role == "assistant"
    assert message.name == "web_search"
    assert message.content == '{"queries":["weather today"]}'
    with pytest.raises(FrozenInstanceError):
        row.row_number = 2  # type: ignore[misc]


def test_split_note_and_transcript_returns_note_body_without_separator() -> None:
    text = "# Note\n\nBody\n\n---\n# Transcript\n| # | role      | type        | tool_name  | tool_id  | content\n"

    note_text, transcript_text = split_note_and_transcript(text)

    assert note_text == "# Note\n\nBody"
    assert transcript_text is not None
    assert transcript_text.startswith("---\n# Transcript")
    assert find_transcript_section(text) == (14, len(text))


def test_parse_transcript_rows_handles_strings_objects_arrays_and_pipes() -> None:
    transcript_text = (
        "---\n"
        "# Transcript\n"
        "| # | role      | type        | tool_name  | tool_id  | content\n"
        "|---|-----------|-------------|------------|----------|-------------------------------\n"
        '| 1 | user      | message     |            |          | "hello | world"\n'
        '| 2 | assistant | tool_call   | web_search | call_1   | {"queries":["weather"]}\n'
        '| 3 | tool      | tool_result | web_search | call_1   | [{"url":"https://example.com"}]\n'
    )

    rows = parse_transcript_rows(transcript_text)

    assert [row.row_number for row in rows] == [1, 2, 3]
    assert rows[0].content == "hello | world"
    assert rows[1].content == {"queries": ["weather"]}
    assert rows[2].content == [{"url": "https://example.com"}]
    assert rows[0].tool_name is None
    assert rows[0].tool_id is None


def test_append_delete_and_repair_transcript_rows_round_trip() -> None:
    text = "# Note\n\nBody"
    text, row_1 = append_transcript_row(text, "user", "message", None, None, "hello")
    text, row_2 = append_transcript_row(
        text,
        "assistant",
        "tool_call",
        "web_search",
        "call_1",
        {"queries": ["weather"]},
    )
    text, row_3 = append_transcript_row(
        text,
        "tool",
        "tool_result",
        "web_search",
        "call_1",
        [{"url": "https://example.com"}],
    )

    assert (row_1, row_2, row_3) == (1, 2, 3)
    assert [row.row_number for row in parse_transcript_rows(text)] == [1, 2, 3]

    deleted = delete_rows_by_tool_id(text, "call_1")
    deleted_rows = parse_transcript_rows(deleted)
    assert len(deleted_rows) == 1
    assert deleted_rows[0].row_number == 1
    assert deleted_rows[0].content == "hello"

    damaged = deleted.replace("| # | role      | type        | tool_name  | tool_id  | content\n", "")
    repaired = repair_transcript_section(damaged)
    assert "| # | role" in repaired

    single_deleted = delete_row_by_number(text, 2)
    assert [row.row_number for row in parse_transcript_rows(single_deleted)] == [1]


def test_ensure_transcript_section_initializes_empty_note() -> None:
    initialized = ensure_transcript_section("")

    assert initialized.startswith("---\n# Transcript\n")
    assert parse_transcript_rows(initialized) == []


def test_append_synthetic_tool_pair_allocates_next_user_tool_id() -> None:
    text = "# Note\n\nBody"
    text, tool_id_1, row_numbers_1 = append_synthetic_tool_pair(
        text,
        tool_name="web_search",
        tool_call_content={"queries": ["python homepage"]},
        tool_response_content=[{"url": "https://www.python.org/"}],
    )
    text, tool_id_2, row_numbers_2 = append_synthetic_tool_pair(
        text,
        tool_name="web_fetch",
        tool_call_content={"url": "https://www.python.org/"},
        tool_response_content={"url": "https://www.python.org/", "title": "Python", "snippet": "Official"},
    )

    assert tool_id_1 == "user_001"
    assert tool_id_2 == "user_002"
    assert row_numbers_1 == (1, 2)
    assert row_numbers_2 == (3, 4)
    assert next_synthetic_user_tool_id(text) == "user_003"


def test_translate_for_anthropic_matches_grouping_and_tool_result_merging() -> None:
    rows = [
        TranscriptRow(1, "user", "message", content="Search weather and news"),
        TranscriptRow(2, "assistant", "tool_call", "web_search", "call_01", {"queries": ["weather today"]}),
        TranscriptRow(3, "assistant", "tool_call", "web_search", "call_02", {"queries": ["top news"]}),
        TranscriptRow(4, "tool", "tool_result", "web_search", "call_01", [{"url": "https://weather.com"}]),
        TranscriptRow(5, "tool", "tool_error", "web_search", "call_02", "timeout"),
        TranscriptRow(6, "assistant", "message", content="Here's what I found..."),
    ]

    translated = translate_for_anthropic(
        group_assistant_rows(rows),
        "# note snapshot",
        "user prompt",
    )

    assert translated[0] == {"role": "user", "content": "Search weather and news"}
    assert translated[1]["role"] == "assistant"
    assert translated[1]["content"][0]["type"] == "tool_use"
    assert translated[2]["role"] == "user"
    assert len(translated[2]["content"]) == 2
    assert translated[2]["content"][0]["content"] == "(untitled) | https://weather.com"
    assert translated[2]["content"][1]["is_error"] is True
    assert translated[-1] == {"role": "user", "content": "# note snapshot\n\n---\n\nuser prompt"}


def test_translate_for_openai_matches_tool_call_and_tool_result_shape() -> None:
    rows = [
        TranscriptRow(1, "user", "message", content="Search weather and news"),
        TranscriptRow(2, "assistant", "message", content="Let me search."),
        TranscriptRow(3, "assistant", "tool_call", "web_search", "call_01", {"queries": ["weather today"]}),
        TranscriptRow(4, "tool", "tool_result", "web_search", "call_01", [{"url": "https://weather.com"}]),
    ]

    translated = translate_for_openai(
        group_assistant_rows(rows),
        "# note snapshot",
        "user prompt",
    )

    assert translated[1]["role"] == "assistant"
    assert translated[1]["content"] == "Let me search."
    assert translated[1]["tool_calls"][0]["function"]["arguments"] == '{"queries":["weather today"]}'
    assert translated[2] == {
        "role": "tool",
        "tool_call_id": "call_01",
        "content": "(untitled) | https://weather.com",
    }


def test_translate_transcript_dispatches_by_provider() -> None:
    rows = [TranscriptRow(1, "user", "message", content="hello")]

    assert translate_transcript(rows, "anthropic", "note", "prompt")[-1]["role"] == "user"
    assert translate_transcript(rows, "openai_compatible", "note", "prompt")[-1]["role"] == "user"
    with pytest.raises(ValueError):
        translate_transcript(rows, "unknown", "note", "prompt")  # type: ignore[arg-type]


def test_provider_request_accepts_transcript_fields() -> None:
    rows = [TranscriptRow(1, "user", "message", content="hello")]

    request = ProviderRequest(
        messages=[],
        transcript_messages=rows,
        note_snapshot="# note",
        user_prompt="prompt",
    )

    assert request.transcript_messages == rows
    assert request.note_snapshot == "# note"
    assert request.user_prompt == "prompt"


@pytest.mark.asyncio
async def test_context_engine_parses_transcript_rows_into_context_result(tmp_path) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        "# Note\n\nBody\n\n"
        "---\n"
        "# Transcript\n"
        "| # | role      | type        | tool_name  | tool_id  | content\n"
        "|---|-----------|-------------|------------|----------|-------------------------------\n"
        '| 1 | user      | message     |            |          | "hello"\n'
        '| 2 | assistant | message     |            |          | "hi"\n',
        encoding="utf-8",
    )

    result = await ContextEngine().build_context(
        ContextBuildRequest(active_file=note, user_prompt="prompt")
    )

    assert result.transcript_rows is not None
    assert [row.content for row in result.transcript_rows] == ["hello", "hi"]
    assert result.prompt_runs[0].note_snapshot_text.startswith("NOTE SNAPSHOT\nFILE: note.md")
    assert result.prompt_runs[0].user_prompt_text == "prompt"
