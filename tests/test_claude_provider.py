from __future__ import annotations

from pathlib import Path

import pytest

from aunic.config import ClaudeSettings
from aunic.domain import Message, ProviderRequest, TranscriptRow
from aunic.providers.claude import ClaudeProvider, build_claude_seed_messages
from aunic.providers.claude_client import ClaudeTurnResult


def test_build_claude_seed_messages_serializes_transcript_history() -> None:
    rows = [
        TranscriptRow(1, "user", "message", content="Earlier prompt"),
        TranscriptRow(2, "assistant", "tool_call", "web_search", "call_1", {"queries": ["weather"]}),
        TranscriptRow(3, "tool", "tool_result", "web_search", "call_1", [{"url": "https://example.com"}]),
    ]

    messages = build_claude_seed_messages(
        ProviderRequest(
            messages=[],
            transcript_messages=rows,
            note_snapshot="# Note",
            user_prompt="Current prompt",
        ),
        model="claude-haiku",
    )

    assert messages[0]["type"] == "user"
    assert messages[1]["type"] == "assistant"
    assert messages[-1]["message"]["role"] == "user"


@pytest.mark.asyncio
async def test_claude_provider_reuses_session_and_returns_generated_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"enter": 0, "exit": 0, "query": 0}

    class FakeSession:
        def __init__(self, settings, cwd, model, *, system_prompt, bridge_config=None, effort=None):
            self.cwd = cwd
            self.model = model
            self.system_prompt = system_prompt

        async def __aenter__(self):
            calls["enter"] += 1
            return self

        async def __aexit__(self, *_):
            calls["exit"] += 1

        async def query(self, *, prompt_text=None, seeded_messages=None, session_id="default"):
            calls["query"] += 1
            return ClaudeTurnResult(
                text="done",
                usage=None,
                finish_reason="stop",
                generated_rows=[],
                raw_messages=[],
            )

    monkeypatch.setattr("aunic.providers.claude.ClaudeSession", FakeSession)

    provider = ClaudeProvider(ClaudeSettings())
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
    assert calls["query"] == 2
    assert calls["exit"] == 1


@pytest.mark.asyncio
async def test_claude_provider_seeds_history_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeSession:
        def __init__(self, settings, cwd, model, *, system_prompt, bridge_config=None, effort=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def query(self, *, prompt_text=None, seeded_messages=None, session_id="default"):
            captured["prompt_text"] = prompt_text
            captured["seeded_messages"] = seeded_messages
            return ClaudeTurnResult(
                text="done",
                usage=None,
                finish_reason="stop",
                generated_rows=[],
                raw_messages=[],
            )

    monkeypatch.setattr("aunic.providers.claude.ClaudeSession", FakeSession)

    provider = ClaudeProvider(ClaudeSettings())
    rows = [TranscriptRow(1, "user", "message", content="Earlier prompt")]
    response = await provider.generate(
        ProviderRequest(
            messages=[],
            transcript_messages=rows,
            note_snapshot="# Note",
            user_prompt="Current prompt",
            metadata={
                "cwd": str(Path.cwd()),
                "active_file": str(Path.cwd() / "note.md"),
                "mode": "note",
                "work_mode": "off",
            },
        )
    )

    assert captured["seeded_messages"] is not None
    assert response.provider_metadata["transport"] == "claude_code_sdk"
    assert response.provider_metadata["history_seeded"] is True
