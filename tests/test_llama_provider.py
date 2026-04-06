from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from aunic.config import LlamaCppSettings
from aunic.domain import HealthCheck, Message, ProviderRequest, ToolSpec, TranscriptRow
from aunic.providers.llama_cpp import LlamaCppProvider, OpenAICompatibleProvider


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

    async def always_unhealthy(_profile=None) -> bool:
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


@pytest.mark.asyncio
async def test_llama_generate_compacts_old_tool_results_before_translation(
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
                [{"title": f"title-{index}", "url": f"https://example.com/{index}", "snippet": f"snippet-{index}"}],
            )
        )

    await provider.generate(
        ProviderRequest(
            messages=[],
            transcript_messages=rows,
            note_snapshot="# Note",
            user_prompt="Finish.",
            tools=[ECHO_TOOL],
        )
    )

    messages = captured["messages"]
    assert isinstance(messages, list)
    tool_messages = [message for message in messages if message.get("role") == "tool"]
    assert tool_messages[0]["content"] == "[Old tool result content cleared]"
    assert "title-7" in tool_messages[-1]["content"]


@pytest.mark.asyncio
async def test_llama_generate_drops_incomplete_tool_pairs_before_translation(
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
        TranscriptRow(1, "assistant", "tool_call", "read", "orphan_call", {"file_path": "/tmp/a.txt"}),
        TranscriptRow(2, "assistant", "tool_call", "web_search", "call_1", {"queries": ["weather"]}),
        TranscriptRow(3, "tool", "tool_result", "web_search", "call_1", [{"url": "https://example.com"}]),
    ]

    await provider.generate(
        ProviderRequest(
            messages=[],
            transcript_messages=rows,
            note_snapshot="# Note",
            user_prompt="Finish.",
            tools=[ECHO_TOOL],
        )
    )

    messages = captured["messages"]
    assert isinstance(messages, list)
    assistant_messages = [message for message in messages if message.get("role") == "assistant"]
    assert len(assistant_messages) == 1
    assert assistant_messages[0]["tool_calls"][0]["id"] == "call_1"


@pytest.mark.asyncio
async def test_openai_compatible_provider_uses_proto_profile_base_url_and_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_dir = tmp_path / ".aunic"
    settings_dir.mkdir()
    (settings_dir / "proto-settings.json").write_text(
        (
            "{\n"
            '  "selected_openai_compatible_profile": "openrouter_nemo",\n'
            '  "openai_compatible_profiles": {\n'
            '    "openrouter_nemo": {\n'
            '      "provider_label": "OpenRouter",\n'
            '      "custom_model_name": "Nemo",\n'
            '      "model": "nvidia/nemotron-3-super-120b-a12b",\n'
            '      "base_url": "https://openrouter.ai/api/v1",\n'
            '      "chat_endpoint": "/chat/completions",\n'
            '      "api_key": "placeholder-key"\n'
            "    }\n"
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.content.decode("utf-8"))
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

    provider = OpenAICompatibleProvider(
        project_root=tmp_path,
        client_factory=lambda _timeout: _client_factory(handler)(),
    )

    async def ready() -> HealthCheck:
        return HealthCheck(provider="openai_compatible", ok=True, message="ready", details={})

    monkeypatch.setattr(provider, "ensure_ready", ready)
    response = await provider.generate(
        ProviderRequest(messages=[Message(role="user", content="hi")])
    )

    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["authorization"] == "Bearer placeholder-key"
    assert response.provider_metadata["profile_id"] == "openrouter_nemo"
    assert response.provider_metadata["profile_label"] == "OpenRouter Nemo"


@pytest.mark.asyncio
async def test_openai_compatible_provider_enables_reasoning_split_and_extracts_patch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_dir = tmp_path / ".aunic"
    settings_dir.mkdir()
    (settings_dir / "proto-settings.json").write_text(
        (
            "{\n"
            '  "selected_openai_compatible_profile": "openrouter_minimax",\n'
            '  "openai_compatible_profiles": {\n'
            '    "openrouter_minimax": {\n'
            '      "provider_label": "OpenRouter",\n'
            '      "custom_model_name": "MiniMax",\n'
            '      "model": "minimax/minimax-m2.7",\n'
            '      "base_url": "https://openrouter.ai/api/v1",\n'
            '      "chat_endpoint": "/chat/completions",\n'
            '      "api_key": "placeholder-key",\n'
            '      "replay_reasoning_details": true,\n'
            '      "reasoning_replay_turns": 1\n'
            "    }\n"
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        captured["payload"] = payload
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "reasoning": "Need to call the tool.",
                            "reasoning_details": [
                                {
                                    "type": "reasoning.text",
                                    "text": "Need to call the tool.",
                                }
                            ],
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
                ]
            },
        )

    provider = OpenAICompatibleProvider(
        project_root=tmp_path,
        client_factory=lambda _timeout: _client_factory(handler)(),
    )

    async def ready() -> HealthCheck:
        return HealthCheck(provider="openai_compatible", ok=True, message="ready", details={})

    monkeypatch.setattr(provider, "ensure_ready", ready)
    response = await provider.generate(
        ProviderRequest(messages=[Message(role="user", content="hi")], tools=[ECHO_TOOL])
    )

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["extra_body"] == {"reasoning_split": True}
    assert response.assistant_message_patch == {
        "reasoning": "Need to call the tool.",
        "reasoning_details": [
            {
                "type": "reasoning.text",
                "text": "Need to call the tool.",
            }
        ],
    }
    assert response.provider_metadata["reasoning_replay_enabled"] is True


@pytest.mark.asyncio
async def test_openai_compatible_provider_replays_assistant_reasoning_patch_into_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_dir = tmp_path / ".aunic"
    settings_dir.mkdir()
    (settings_dir / "proto-settings.json").write_text(
        (
            "{\n"
            '  "selected_openai_compatible_profile": "openrouter_minimax",\n'
            '  "openai_compatible_profiles": {\n'
            '    "openrouter_minimax": {\n'
            '      "provider_label": "OpenRouter",\n'
            '      "custom_model_name": "MiniMax",\n'
            '      "model": "minimax/minimax-m2.7",\n'
            '      "base_url": "https://openrouter.ai/api/v1",\n'
            '      "chat_endpoint": "/chat/completions",\n'
            '      "api_key": "placeholder-key",\n'
            '      "replay_reasoning_details": true,\n'
            '      "reasoning_replay_turns": 1\n'
            "    }\n"
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        captured["payload"] = payload
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

    provider = OpenAICompatibleProvider(
        project_root=tmp_path,
        client_factory=lambda _timeout: _client_factory(handler)(),
    )

    async def ready() -> HealthCheck:
        return HealthCheck(provider="openai_compatible", ok=True, message="ready", details={})

    monkeypatch.setattr(provider, "ensure_ready", ready)
    rows = [
        TranscriptRow(1, "user", "message", content="Search weather"),
        TranscriptRow(2, "assistant", "tool_call", "echo_tool", "call_1", {"value": "ok"}),
        TranscriptRow(3, "tool", "tool_result", "echo_tool", "call_1", {"message": "done"}),
    ]

    await provider.generate(
        ProviderRequest(
            messages=[],
            transcript_messages=rows,
            assistant_message_patches=[
                {
                    "reasoning": "Need to call the tool.",
                    "reasoning_details": [
                        {
                            "type": "reasoning.text",
                            "text": "Need to call the tool.",
                        }
                    ],
                }
            ],
            note_snapshot="# Note",
            user_prompt="Finish.",
            tools=[ECHO_TOOL],
        )
    )

    payload = captured["payload"]
    assert isinstance(payload, dict)
    messages = payload["messages"]
    assert isinstance(messages, list)
    assistant_messages = [message for message in messages if message.get("role") == "assistant"]
    assert assistant_messages[0]["reasoning"] == "Need to call the tool."
    assert assistant_messages[0]["reasoning_details"][0]["text"] == "Need to call the tool."


@pytest.mark.asyncio
async def test_openai_compatible_provider_auto_enables_reasoning_replay_for_minimax_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_dir = tmp_path / ".aunic"
    settings_dir.mkdir()
    (settings_dir / "proto-settings.json").write_text(
        (
            "{\n"
            '  "selected_openai_compatible_profile": "openrouter_nemo",\n'
            '  "openai_compatible_profiles": {\n'
            '    "openrouter_nemo": {\n'
            '      "provider_label": "OpenRouter",\n'
            '      "custom_model_name": "Nemo",\n'
            '      "model": "qwen/qwen3.5-122b-a10b",\n'
            '      "base_url": "https://openrouter.ai/api/v1",\n'
            '      "chat_endpoint": "/chat/completions",\n'
            '      "api_key": "placeholder-key"\n'
            "    }\n"
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": "done"}}]},
        )

    provider = OpenAICompatibleProvider(
        project_root=tmp_path,
        client_factory=lambda _timeout: _client_factory(handler)(),
    )

    async def ready() -> HealthCheck:
        return HealthCheck(provider="openai_compatible", ok=True, message="ready", details={})

    monkeypatch.setattr(provider, "ensure_ready", ready)
    response = await provider.generate(
        ProviderRequest(
            messages=[Message(role="user", content="hi")],
            model="minimax/minimax-m2.7",
        )
    )

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["extra_body"] == {"reasoning_split": True}
    assert response.provider_metadata["reasoning_replay_enabled"] is True
