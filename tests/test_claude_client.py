from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from aunic.config import ClaudeSettings
from aunic.providers.claude_client import ClaudeSession
from aunic.providers.sdk_tools import STRUCTURED_RESULT_KEY
from aunic.tools.base import ToolExecutionResult


@pytest.mark.asyncio
async def test_post_tool_use_hook_accepts_dict_payload() -> None:
    session = ClaudeSession(
        ClaudeSettings(),
        Path.cwd(),
        "claude-haiku",
        system_prompt="test",
    )

    output = await session._post_tool_use_hook(  # noqa: SLF001
        {
            "tool_name": "note_write",
            "tool_input": {"content": "done"},
            "tool_use_id": "toolu_1",
            "tool_response": {
                STRUCTURED_RESULT_KEY: {
                    "tool_name": "note_write",
                    "status": "completed",
                    "in_memory_content": {"type": "note_content_write", "content": "done"},
                    "transcript_content": None,
                    "tool_failure": None,
                    "metadata": {},
                }
            },
        }
    )

    assert output == {"continue_": True}
    assert len(session._generated_rows) == 2  # noqa: SLF001
    assert session._generated_rows[0].row.type == "tool_call"  # noqa: SLF001
    assert session._generated_rows[1].row.type == "tool_result"  # noqa: SLF001


@pytest.mark.asyncio
async def test_post_tool_use_failure_hook_accepts_dict_payload() -> None:
    session = ClaudeSession(
        ClaudeSettings(),
        Path.cwd(),
        "claude-haiku",
        system_prompt="test",
    )

    output = await session._post_tool_use_failure_hook(  # noqa: SLF001
        {
            "tool_name": "note_write",
            "tool_input": {"content": "done"},
            "tool_use_id": "toolu_1",
            "error": "boom",
        }
    )

    assert output == {"continue_": True}
    assert len(session._generated_rows) == 2  # noqa: SLF001
    assert session._generated_rows[1].row.type == "tool_error"  # noqa: SLF001


@pytest.mark.asyncio
async def test_post_tool_use_hook_uses_recorded_bridge_result_when_sdk_drops_structured_content() -> None:
    session = ClaudeSession(
        ClaudeSettings(),
        Path.cwd(),
        "claude-haiku",
        system_prompt="test",
    )
    session._bridge = SimpleNamespace(  # noqa: SLF001
        consume_sdk_tool_execution=lambda **_: ToolExecutionResult(
            tool_name="note_write",
            status="completed",
            in_memory_content={"type": "note_content_write", "content": "done"},
            transcript_content=None,
        )
    )

    output = await session._post_tool_use_hook(  # noqa: SLF001
        {
            "tool_name": "mcp__aunic__note_write",
            "tool_input": {"content": "done"},
            "tool_use_id": "toolu_1",
            "tool_response": {"content": [{"type": "text", "text": "done"}]},
        }
    )

    assert output == {"continue_": True}
    assert len(session._generated_rows) == 2  # noqa: SLF001
    assert session._generated_rows[0].row.tool_name == "note_write"  # noqa: SLF001
    assert session._generated_rows[1].row.type == "tool_result"  # noqa: SLF001
