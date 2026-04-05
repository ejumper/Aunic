from __future__ import annotations

from aunic.providers.sdk_tools import (
    build_mcp_tool_definition,
    deserialize_tool_execution_result,
    model_visible_tool_text,
    provider_rows_from_tool_execution,
    serialize_tool_execution_result,
)
from aunic.tools.base import ToolExecutionResult
from aunic.tools.note_edit import build_note_only_registry


def test_tool_execution_result_serialization_round_trips() -> None:
    result = ToolExecutionResult(
        tool_name="web_fetch",
        status="completed",
        in_memory_content={"url": "https://example.com", "title": "Example", "markdown": "# Example"},
        transcript_content={"url": "https://example.com", "title": "Example", "snippet": "Snippet"},
    )

    restored = deserialize_tool_execution_result(serialize_tool_execution_result(result))

    assert restored.tool_name == "web_fetch"
    assert restored.status == "completed"
    assert restored.in_memory_content["markdown"] == "# Example"
    assert restored.transcript_content["snippet"] == "Snippet"


def test_provider_rows_from_tool_execution_builds_call_and_result_pair() -> None:
    result = ToolExecutionResult(
        tool_name="note_write",
        status="completed",
        in_memory_content={"type": "note_content_write", "content": "hello"},
        transcript_content=None,
    )

    rows = provider_rows_from_tool_execution(
        tool_name="note_write",
        tool_id="call_1",
        arguments={"content": "hello"},
        result=result,
    )

    assert len(rows) == 2
    assert rows[0].row.role == "assistant"
    assert rows[0].row.type == "tool_call"
    assert rows[1].row.role == "tool"
    assert rows[1].row.type == "tool_result"


def test_model_visible_tool_text_uses_flattened_result() -> None:
    result = ToolExecutionResult(
        tool_name="note_edit",
        status="completed",
        in_memory_content={"type": "note_content_edit", "old_string": "a", "new_string": "b"},
    )

    text = model_visible_tool_text(result)

    assert "Edit applied" in text


def test_build_mcp_tool_definition_preserves_aunic_input_schema() -> None:
    definition = next(
        tool for tool in build_note_only_registry() if tool.spec.name == "note_write"
    )

    mcp_tool = build_mcp_tool_definition(definition)

    assert mcp_tool.name == "note_write"
    assert mcp_tool.inputSchema["required"] == ["content"]
    assert "content" in mcp_tool.inputSchema["properties"]
