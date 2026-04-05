from __future__ import annotations

from aunic.domain import TranscriptRow
from aunic.transcript.flattening import flatten_tool_result_for_provider


def test_flatten_tool_result_for_provider_formats_search_results() -> None:
    row = TranscriptRow(
        row_number=1,
        role="tool",
        type="tool_result",
        tool_name="web_search",
        tool_id="call_1",
        content=[
            {
                "title": "Python",
                "url": "https://www.python.org/",
                "snippet": "Official Python website.",
            }
        ],
    )

    assert flatten_tool_result_for_provider(row) == (
        "Python | https://www.python.org/ | Official Python website."
    )


def test_flatten_tool_result_for_provider_formats_fetch_summary() -> None:
    row = TranscriptRow(
        row_number=1,
        role="tool",
        type="tool_result",
        tool_name="web_fetch",
        tool_id="call_1",
        content={
            "title": "Python",
            "url": "https://www.python.org/",
            "snippet": "Official Python website.",
        },
    )

    assert flatten_tool_result_for_provider(row) == (
        "Title: Python\n"
        "URL: https://www.python.org/\n"
        "Snippet: Official Python website."
    )


def test_flatten_tool_result_for_provider_passthroughs_string_content() -> None:
    row = TranscriptRow(
        row_number=1,
        role="tool",
        type="tool_error",
        tool_name="web_search",
        tool_id="call_1",
        content="Error: timeout",
    )

    assert flatten_tool_result_for_provider(row) == "Error: timeout"
