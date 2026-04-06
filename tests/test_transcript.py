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
from aunic.transcript.compaction import (
    MODEL_COMPACTION_ERROR_PLACEHOLDER,
    MODEL_COMPACTION_KEEP_RECENT,
    MODEL_COMPACTION_RESULT_PLACEHOLDER,
    compact_transcript_for_model,
    filter_incomplete_tool_pairs_for_model,
    prepare_transcript_for_model,
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


def test_compact_transcript_for_model_only_compacts_older_matching_tool_results() -> None:
    rows = [
        TranscriptRow(1, "user", "message", content="hello"),
        TranscriptRow(2, "assistant", "tool_call", "web_search", "call_1", {"queries": ["q1"]}),
        TranscriptRow(3, "tool", "tool_result", "web_search", "call_1", [{"title": "r1"}]),
        TranscriptRow(4, "assistant", "tool_call", "web_search", "call_2", {"queries": ["q2"]}),
        TranscriptRow(5, "tool", "tool_result", "web_search", "call_2", [{"title": "r2"}]),
        TranscriptRow(6, "assistant", "tool_call", "web_search", "call_3", {"queries": ["q3"]}),
        TranscriptRow(7, "tool", "tool_result", "web_search", "call_3", [{"title": "r3"}]),
        TranscriptRow(8, "assistant", "tool_call", "web_search", "call_4", {"queries": ["q4"]}),
        TranscriptRow(9, "tool", "tool_result", "web_search", "call_4", [{"title": "r4"}]),
        TranscriptRow(10, "assistant", "tool_call", "web_search", "call_5", {"queries": ["q5"]}),
        TranscriptRow(11, "tool", "tool_result", "web_search", "call_5", [{"title": "r5"}]),
        TranscriptRow(12, "assistant", "tool_call", "web_search", "call_6", {"queries": ["q6"]}),
        TranscriptRow(13, "tool", "tool_result", "web_search", "call_6", [{"title": "r6"}]),
        TranscriptRow(14, "assistant", "tool_call", "non_compactable", "call_7", {"queries": ["q7"]}),
        TranscriptRow(15, "tool", "tool_result", "non_compactable", "call_7", [{"title": "r7"}]),
    ]

    compacted = compact_transcript_for_model(rows)

    assert compacted is not rows
    assert rows[2].content == [{"title": "r1"}]
    assert compacted[2].content == MODEL_COMPACTION_RESULT_PLACEHOLDER
    assert compacted[4].content == [{"title": "r2"}]
    assert compacted[12].content == [{"title": "r6"}]
    assert compacted[13].content == {"queries": ["q7"]}
    assert compacted[14].content == [{"title": "r7"}]


def test_compact_transcript_for_model_compacts_tool_errors_and_keeps_recent_per_tool() -> None:
    rows = [
        TranscriptRow(1, "tool", "tool_error", "bash", "b1", {"message": "boom-1"}),
        TranscriptRow(2, "tool", "tool_error", "bash", "b2", {"message": "boom-2"}),
        TranscriptRow(3, "tool", "tool_error", "bash", "b3", {"message": "boom-3"}),
        TranscriptRow(4, "tool", "tool_error", "bash", "b4", {"message": "boom-4"}),
        TranscriptRow(5, "tool", "tool_error", "bash", "b5", {"message": "boom-5"}),
        TranscriptRow(6, "tool", "tool_error", "bash", "b6", {"message": "boom-6"}),
    ]

    compacted = compact_transcript_for_model(rows)

    assert compacted[0].content == MODEL_COMPACTION_ERROR_PLACEHOLDER
    assert [row.content for row in compacted[1:]] == [
        {"message": "boom-2"},
        {"message": "boom-3"},
        {"message": "boom-4"},
        {"message": "boom-5"},
        {"message": "boom-6"},
    ]


def test_compact_transcript_for_model_respects_keep_recent_override() -> None:
    rows = [
        TranscriptRow(1, "tool", "tool_result", "read", "r1", {"content": "old"}),
        TranscriptRow(2, "tool", "tool_result", "read", "r2", {"content": "new"}),
    ]

    compacted = compact_transcript_for_model(rows, keep_recent=1)

    assert compacted[0].content == MODEL_COMPACTION_RESULT_PLACEHOLDER
    assert compacted[1].content == {"content": "new"}


def test_compact_transcript_for_model_reduces_translated_prompt_size_for_old_search_rows() -> None:
    rows = []
    for index in range(1, MODEL_COMPACTION_KEEP_RECENT + 3):
        tool_id = f"call_{index}"
        rows.append(
            TranscriptRow(index * 2 - 1, "assistant", "tool_call", "web_search", tool_id, {"queries": [f"q{index}"]})
        )
        rows.append(
            TranscriptRow(
                index * 2,
                "tool",
                "tool_result",
                "web_search",
                tool_id,
                [{"title": f"title-{index}", "url": f"https://example.com/{index}", "snippet": "x" * 400}],
            )
        )

    original = translate_for_openai(group_assistant_rows(rows), "# Note", "Prompt")
    compacted = translate_for_openai(
        group_assistant_rows(compact_transcript_for_model(rows)),
        "# Note",
        "Prompt",
    )

    original_size = sum(len(str(message)) for message in original)
    compacted_size = sum(len(str(message)) for message in compacted)

    assert compacted_size < original_size


def test_filter_incomplete_tool_pairs_for_model_drops_orphaned_calls_and_results() -> None:
    rows = [
        TranscriptRow(1, "assistant", "tool_call", "read", "orphan_call", {"file_path": "/tmp/a.txt"}),
        TranscriptRow(2, "assistant", "tool_call", "web_search", "call_1", {"queries": ["weather"]}),
        TranscriptRow(3, "tool", "tool_result", "web_search", "call_1", [{"url": "https://example.com"}]),
        TranscriptRow(4, "tool", "tool_result", "read", "orphan_result", {"content": "orphan"}),
        TranscriptRow(5, "user", "message", content="Continue"),
    ]

    filtered = filter_incomplete_tool_pairs_for_model(rows)

    assert [(row.role, row.type, row.tool_id) for row in filtered] == [
        ("assistant", "tool_call", "call_1"),
        ("tool", "tool_result", "call_1"),
        ("user", "message", None),
    ]
    assert rows[0].tool_id == "orphan_call"
    assert rows[3].tool_id == "orphan_result"


def test_prepare_transcript_for_model_filters_before_compacting() -> None:
    rows = [
        TranscriptRow(1, "assistant", "tool_call", "web_search", "orphan_call", {"queries": ["q0"]}),
        TranscriptRow(2, "assistant", "tool_call", "web_search", "call_1", {"queries": ["q1"]}),
        TranscriptRow(3, "tool", "tool_result", "web_search", "call_1", [{"title": "r1"}]),
        TranscriptRow(4, "assistant", "tool_call", "web_search", "call_2", {"queries": ["q2"]}),
        TranscriptRow(5, "tool", "tool_result", "web_search", "call_2", [{"title": "r2"}]),
        TranscriptRow(6, "assistant", "tool_call", "web_search", "call_3", {"queries": ["q3"]}),
        TranscriptRow(7, "tool", "tool_result", "web_search", "call_3", [{"title": "r3"}]),
        TranscriptRow(8, "assistant", "tool_call", "web_search", "call_4", {"queries": ["q4"]}),
        TranscriptRow(9, "tool", "tool_result", "web_search", "call_4", [{"title": "r4"}]),
        TranscriptRow(10, "assistant", "tool_call", "web_search", "call_5", {"queries": ["q5"]}),
        TranscriptRow(11, "tool", "tool_result", "web_search", "call_5", [{"title": "r5"}]),
        TranscriptRow(12, "assistant", "tool_call", "web_search", "call_6", {"queries": ["q6"]}),
        TranscriptRow(13, "tool", "tool_result", "web_search", "call_6", [{"title": "r6"}]),
    ]

    prepared = prepare_transcript_for_model(rows)

    assert all(row.tool_id != "orphan_call" for row in prepared)
    assert prepared[1].content == MODEL_COMPACTION_RESULT_PLACEHOLDER
    assert prepared[-1].content == [{"title": "r6"}]


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
