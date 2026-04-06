from __future__ import annotations

import json
from pathlib import Path

import pytest

from aunic.config import CodexSettings
from aunic.domain import Message, ProviderRequest, TranscriptRow
from aunic.providers.codex import (
    CodexProvider,
    build_codex_history_items,
    extract_assistant_text,
    extract_generated_rows_from_thread_items,
    normalize_codex_reasoning_effort,
    usage_from_codex_token_usage,
)
from aunic.providers.codex_client import CodexTurnResult


def test_usage_from_codex_token_usage_maps_fields() -> None:
    usage = usage_from_codex_token_usage(
        {
            "last": {
                "totalTokens": 12,
                "inputTokens": 8,
                "cachedInputTokens": 2,
                "outputTokens": 4,
                "reasoningOutputTokens": 1,
            },
            "modelContextWindow": 128000,
        }
    )

    assert usage is not None
    assert usage.total_tokens == 12
    assert usage.input_tokens == 8
    assert usage.cached_input_tokens == 2
    assert usage.output_tokens == 4
    assert usage.reasoning_output_tokens == 1
    assert usage.model_context_window == 128000


def test_codex_reasoning_effort_normalizes_minimal() -> None:
    assert normalize_codex_reasoning_effort("minimal") == "low"
    assert normalize_codex_reasoning_effort("medium") == "medium"


def test_build_codex_history_items_preserves_tool_call_and_result_shape() -> None:
    rows = [
        TranscriptRow(1, "user", "message", content="Search weather"),
        TranscriptRow(2, "assistant", "message", content="Looking."),
        TranscriptRow(3, "assistant", "tool_call", "web_search", "call_1", {"queries": ["weather"]}),
        TranscriptRow(4, "tool", "tool_result", "web_search", "call_1", [{"url": "https://example.com"}]),
    ]

    history = build_codex_history_items(rows)

    assert history[0]["type"] == "message"
    assert history[1]["type"] == "message"
    assert history[2]["type"] == "function_call"
    assert history[3]["type"] == "function_call_output"


def test_extract_generated_rows_from_thread_items_uses_structured_mcp_result() -> None:
    generated = extract_generated_rows_from_thread_items(
        [
            {
                "type": "mcpToolCall",
                "id": "call_1",
                "server": "aunic",
                "tool": "web_search",
                "status": "completed",
                "arguments": {"queries": ["weather"]},
                "result": {
                    "content": [{"type": "text", "text": "ok"}],
                    "structuredContent": {
                        "_aunic_result": {
                            "tool_name": "web_search",
                            "status": "completed",
                            "in_memory_content": [{"url": "https://example.com"}],
                            "transcript_content": [{"url": "https://example.com"}],
                            "tool_failure": None,
                            "metadata": {},
                        }
                    },
                },
                "error": None,
            }
        ]
    )

    assert len(generated) == 2
    assert generated[0].row.type == "tool_call"
    assert generated[1].row.type == "tool_result"


def test_extract_assistant_text_prefers_thread_items() -> None:
    result = CodexTurnResult(
        thread_id="thread-1",
        turn_id="turn-1",
        status="completed",
        raw_items=[],
        thread_items=[{"type": "agentMessage", "id": "msg_1", "text": "done", "phase": None, "memoryCitation": None}],
        token_usage=None,
        error_message=None,
        stderr_lines=[],
    )

    assert extract_assistant_text(result) == "done"


@pytest.mark.asyncio
async def test_codex_provider_reuses_transport_thread_within_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {
        "enter": 0,
        "exit": 0,
        "start_thread": 0,
        "resume_thread": 0,
        "run_turn": 0,
    }

    class FakeSession:
        def __init__(self, settings, cwd, *, config_overrides=()):
            self.cwd = cwd

        async def __aenter__(self):
            calls["enter"] += 1
            return self

        async def __aexit__(self, *_):
            calls["exit"] += 1

        async def start_thread(self, **kwargs):
            calls["start_thread"] += 1
            return {"thread": {"id": "thread-1"}}

        async def resume_thread_with_history(self, **kwargs):
            calls["resume_thread"] += 1
            return {"thread": {"id": "thread-1"}}

        async def run_turn(self, **kwargs):
            calls["run_turn"] += 1
            return CodexTurnResult(
                thread_id="thread-1",
                turn_id=f"turn-{calls['run_turn']}",
                status="completed",
                raw_items=[
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"text": "done"}],
                    }
                ],
                thread_items=[],
                token_usage=None,
                error_message=None,
                stderr_lines=[],
            )

    monkeypatch.setattr("aunic.providers.codex.CodexAppServerSession", FakeSession)

    provider = CodexProvider(CodexSettings())
    metadata = {
        "cwd": str(Path.cwd()),
        "run_session_id": "run-1",
        "active_file": str(Path.cwd() / "note.md"),
        "mode": "note",
        "work_mode": "off",
    }

    await provider.generate(ProviderRequest(messages=[Message(role="user", content="one")], metadata=metadata))
    await provider.generate(ProviderRequest(messages=[Message(role="user", content="two")], metadata=metadata))
    await provider.close_run("run-1")

    assert calls["enter"] == 1
    assert calls["start_thread"] == 1
    assert calls["run_turn"] == 2
    assert calls["exit"] == 1


@pytest.mark.asyncio
async def test_codex_provider_seeds_history_once_and_returns_generated_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeSession:
        def __init__(self, settings, cwd, *, config_overrides=()):
            captured["config_overrides"] = config_overrides

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def start_thread(self, **kwargs):
            raise AssertionError("history path should use resume_thread_with_history")

        async def resume_thread_with_history(self, **kwargs):
            captured["history"] = kwargs["history"]
            return {"thread": {"id": "thread-1"}}

        async def run_turn(self, **kwargs):
            captured["input_text"] = kwargs["input_text"]
            return CodexTurnResult(
                thread_id="thread-1",
                turn_id="turn-1",
                status="completed",
                raw_items=[
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"text": "done"}],
                    }
                ],
                thread_items=[
                    {
                        "type": "mcpToolCall",
                        "id": "call_1",
                        "server": "aunic",
                        "tool": "web_search",
                        "status": "completed",
                        "arguments": {"queries": ["weather"]},
                        "result": {
                            "content": [{"type": "text", "text": "ok"}],
                            "structuredContent": {
                                "_aunic_result": {
                                    "tool_name": "web_search",
                                    "status": "completed",
                                    "in_memory_content": [{"url": "https://example.com"}],
                                    "transcript_content": [{"url": "https://example.com"}],
                                    "tool_failure": None,
                                    "metadata": {},
                                }
                            },
                        },
                        "error": None,
                    }
                ],
                token_usage=None,
                error_message=None,
                stderr_lines=[],
            )

    monkeypatch.setattr("aunic.providers.codex.CodexAppServerSession", FakeSession)

    provider = CodexProvider(CodexSettings())
    rows = [
        TranscriptRow(1, "user", "message", content="Earlier question"),
        TranscriptRow(2, "assistant", "tool_call", "web_search", "old_1", {"queries": ["earlier"]}),
        TranscriptRow(3, "tool", "tool_result", "web_search", "old_1", [{"url": "https://example.com"}]),
    ]
    response = await provider.generate(
        ProviderRequest(
            messages=[],
            transcript_messages=rows,
            note_snapshot="# Note",
            user_prompt="Current prompt",
            tools=[],
            metadata={
                "cwd": str(Path.cwd()),
                "active_file": str(Path.cwd() / "note.md"),
                "mode": "note",
                "work_mode": "off",
            },
        )
    )

    history = captured["history"]
    assert isinstance(history, list)
    assert response.text == "done"
    assert len(response.generated_rows) == 2
    assert response.provider_metadata["transport"] == "codex_sdk"
    assert response.provider_metadata["history_seeded"] is True


@pytest.mark.asyncio
async def test_codex_provider_compacts_old_tool_results_before_history_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeSession:
        def __init__(self, settings, cwd, *, config_overrides=()):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def start_thread(self, **kwargs):
            raise AssertionError("history path should use resume_thread_with_history")

        async def resume_thread_with_history(self, **kwargs):
            captured["history"] = kwargs["history"]
            return {"thread": {"id": "thread-1"}}

        async def run_turn(self, **kwargs):
            return CodexTurnResult(
                thread_id="thread-1",
                turn_id="turn-1",
                status="completed",
                raw_items=[
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"text": "done"}],
                    }
                ],
                thread_items=[],
                token_usage=None,
                error_message=None,
                stderr_lines=[],
            )

    monkeypatch.setattr("aunic.providers.codex.CodexAppServerSession", FakeSession)

    provider = CodexProvider(CodexSettings())
    rows: list[TranscriptRow] = []
    for index in range(1, 8):
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
                [{"title": f"title-{index}", "url": f"https://example.com/{index}"}],
            )
        )

    await provider.generate(
        ProviderRequest(
            messages=[],
            transcript_messages=rows,
            note_snapshot="# Note",
            user_prompt="Current prompt",
            tools=[],
            metadata={
                "cwd": str(Path.cwd()),
                "active_file": str(Path.cwd() / "note.md"),
                "mode": "note",
                "work_mode": "off",
            },
        )
    )

    history = captured["history"]
    assert isinstance(history, list)
    outputs = [item["output"] for item in history if item.get("type") == "function_call_output"]
    assert outputs[0] == "[Old tool result content cleared]"
    assert "title-7" in outputs[-1]


@pytest.mark.asyncio
async def test_codex_provider_drops_incomplete_tool_pairs_before_history_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeSession:
        def __init__(self, settings, cwd, *, config_overrides=()):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def start_thread(self, **kwargs):
            raise AssertionError("history path should use resume_thread_with_history")

        async def resume_thread_with_history(self, **kwargs):
            captured["history"] = kwargs["history"]
            return {"thread": {"id": "thread-1"}}

        async def run_turn(self, **kwargs):
            return CodexTurnResult(
                thread_id="thread-1",
                turn_id="turn-1",
                status="completed",
                raw_items=[
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"text": "done"}],
                    }
                ],
                thread_items=[],
                token_usage=None,
                error_message=None,
                stderr_lines=[],
            )

    monkeypatch.setattr("aunic.providers.codex.CodexAppServerSession", FakeSession)

    provider = CodexProvider(CodexSettings())
    rows = [
        TranscriptRow(1, "assistant", "tool_call", "read", "orphan_call", {"file_path": "/tmp/a.txt"}),
        TranscriptRow(2, "assistant", "tool_call", "web_search", "call_1", {"queries": ["weather"]}),
        TranscriptRow(3, "tool", "tool_result", "web_search", "call_1", [{"url": "https://example.com"}]),
    ]

    await provider.generate(
        ProviderRequest(
            messages=[],
            transcript_messages=rows,
            note_snapshot="# Note",
            user_prompt="Current prompt",
            tools=[],
            metadata={
                "cwd": str(Path.cwd()),
                "active_file": str(Path.cwd() / "note.md"),
                "mode": "note",
                "work_mode": "off",
            },
        )
    )

    history = captured["history"]
    assert isinstance(history, list)
    function_calls = [item for item in history if item.get("type") == "function_call"]
    assert len(function_calls) == 1
    assert function_calls[0]["call_id"] == "call_1"
