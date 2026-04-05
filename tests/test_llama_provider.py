from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from aunic.config import LlamaCppSettings
from aunic.domain import HealthCheck, Message, ProviderRequest, ToolSpec, TranscriptRow
from aunic.providers.llama_cpp import LlamaCppProvider


def _client_factory(handler):
    transport = httpx.MockTransport(handler)

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, base_url="http://testserver")

    return factory


ECHO_TOOL = ToolSpec(
    name="echo_tool",
    description="Echo one string value.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "required": ["value"],
        "properties": {"value": {"type": "string"}},
    },
)


@pytest.mark.asyncio
async def test_llama_healthcheck_reports_bootable_when_script_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "addie.sh"
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    script.chmod(0o755)

    provider = LlamaCppProvider(
        LlamaCppSettings(
            base_url="http://testserver",
            startup_script=script,
        )
    )

    async def always_unhealthy() -> bool:
        return False

    monkeypatch.setattr(provider, "_is_healthy", always_unhealthy)
    check = await provider.healthcheck()

    assert check.ok is True
    assert check.details["bootable"] is True


@pytest.mark.asyncio
async def test_llama_generate_reads_native_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["tools"][0]["function"]["name"] == "echo_tool"
        assert payload["tool_choice"] == "auto"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "echo_tool",
                                        "arguments": '{"value":"ok"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "total_tokens": 14,
                },
            },
        )

    provider = LlamaCppProvider(
        LlamaCppSettings(base_url="http://testserver"),
        client_factory=_client_factory(handler),
    )

    async def ready() -> HealthCheck:
        return HealthCheck(provider="llama_cpp", ok=True, message="ready", details={})

    monkeypatch.setattr(provider, "ensure_ready", ready)
    response = await provider.generate(
        ProviderRequest(
            messages=[Message(role="user", content="use the tool")],
            tools=[ECHO_TOOL],
        )
    )

    assert response.text == ""
    assert response.tool_calls[0].name == "echo_tool"
    assert response.tool_calls[0].arguments == {"value": "ok"}
    assert response.tool_calls[0].id == "call_1"
    assert response.finish_reason == "tool_calls"
    assert response.usage is not None
    assert response.usage.total_tokens == 14
    assert response.provider_metadata["transport"] == "openai_compatible"
    assert response.provider_metadata["tool_runtime"] == "provider_native"


@pytest.mark.asyncio
async def test_llama_generate_keeps_plain_text_when_no_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        assert "tools" not in payload
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "plain text answer"},
                    }
                ]
            },
        )

    provider = LlamaCppProvider(
        LlamaCppSettings(base_url="http://testserver"),
        client_factory=_client_factory(handler),
    )

    async def ready() -> HealthCheck:
        return HealthCheck(provider="llama_cpp", ok=True, message="ready", details={})

    monkeypatch.setattr(provider, "ensure_ready", ready)
    response = await provider.generate(
        ProviderRequest(messages=[Message(role="user", content="hi")])
    )

    assert response.text == "plain text answer"
    assert response.tool_calls == []
    assert response.provider_metadata["history_seeded"] is False


@pytest.mark.asyncio
async def test_llama_generate_uses_openai_transcript_translation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        captured.update(payload)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "done"},
                    }
                ]
            },
        )

    provider = LlamaCppProvider(
        LlamaCppSettings(base_url="http://testserver"),
        client_factory=_client_factory(handler),
    )

    async def ready() -> HealthCheck:
        return HealthCheck(provider="llama_cpp", ok=True, message="ready", details={})

    monkeypatch.setattr(provider, "ensure_ready", ready)
    rows = [
        TranscriptRow(1, "user", "message", content="Search weather"),
        TranscriptRow(2, "assistant", "tool_call", "echo_tool", "call_1", {"value": "ok"}),
        TranscriptRow(3, "tool", "tool_result", "echo_tool", "call_1", {"message": "done"}),
    ]

    response = await provider.generate(
        ProviderRequest(
            messages=[],
            transcript_messages=rows,
            note_snapshot="# Note",
            user_prompt="Finish the answer.",
            tools=[ECHO_TOOL],
        )
    )

    messages = captured["messages"]
    assert isinstance(messages, list)
    assert messages[1]["role"] == "user"
    assert messages[-1]["role"] == "user"
    assert response.provider_metadata["history_seeded"] is True
