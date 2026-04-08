from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from aunic.config import LlamaCppSettings, SETTINGS
from aunic.domain import (
    HealthCheck,
    ProviderRequest,
    ProviderResponse,
    ToolCall,
    ToolSpec,
    Usage,
)
from aunic.errors import ConfigurationError, ServiceUnavailableError, StructuredOutputError
from aunic.proto_settings import (
    OpenAICompatibleProfile,
    resolve_openai_compatible_profile,
)
from aunic.providers.base import LLMProvider
from aunic.providers.envelope import (
    build_llama_native_messages,
    build_llama_structured_messages,
)
from aunic.transcript.compaction import prepare_transcript_for_model
from aunic.transcript.translation import group_assistant_rows, translate_for_openai


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(
        self,
        *,
        project_root: Path | None = None,
        profile_id: str | None = None,
        fallback_profile: OpenAICompatibleProfile | None = None,
        client_factory: Callable[[float], httpx.AsyncClient] | None = None,
        launcher: Callable[[Path], subprocess.Popen[Any]] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._project_root = (project_root or Path.cwd()).expanduser().resolve()
        self._profile_id = profile_id
        self._fallback_profile = fallback_profile or legacy_llama_profile()
        self._client_factory = client_factory or self._build_client
        self._launcher = launcher or self._launch_startup_script
        self._sleep = sleep or asyncio.sleep

    async def healthcheck(self) -> HealthCheck:
        profile = self._resolve_profile()
        if profile.health_endpoint is None:
            return HealthCheck(
                provider=self.name,
                ok=True,
                message=f"{profile.display_label} is configured (no health endpoint).",
                details=_profile_details(profile, running=None, bootable=None),
            )

        if await self._is_healthy(profile):
            return HealthCheck(
                provider=self.name,
                ok=True,
                message=f"{profile.display_label} is responding.",
                details=_profile_details(profile, running=True, bootable=None),
            )

        startup_script = profile.startup_script
        bootable = (
            startup_script is not None
            and startup_script.exists()
            and os.access(startup_script, os.X_OK)
        )
        if bootable:
            return HealthCheck(
                provider=self.name,
                ok=True,
                message=f"{profile.display_label} is not running, but the startup script is available.",
                details=_profile_details(profile, running=False, bootable=True),
            )

        return HealthCheck(
            provider=self.name,
            ok=False,
            message=f"{profile.display_label} is unavailable.",
            details=_profile_details(profile, running=False, bootable=False),
        )

    async def ensure_ready(self) -> HealthCheck:
        profile = self._resolve_profile()
        initial = await self.healthcheck()
        if initial.details.get("running") is not False:
            return initial
        if not initial.ok:
            return initial

        if profile.startup_script is None:
            return initial

        self._launcher(profile.startup_script)
        started_at = time.monotonic()
        timeout_seconds = SETTINGS.llama_cpp.startup_timeout_seconds
        poll_interval = SETTINGS.llama_cpp.poll_interval_seconds
        while time.monotonic() - started_at < timeout_seconds:
            if await self._is_healthy(profile):
                return HealthCheck(
                    provider=self.name,
                    ok=True,
                    message=f"{profile.display_label} started successfully.",
                    details=_profile_details(profile, running=True, bootable=True),
                )
            await self._sleep(poll_interval)

        return HealthCheck(
            provider=self.name,
            ok=False,
            message=f"Timed out waiting for {profile.display_label} to start.",
            details=_profile_details(profile, running=False, bootable=True),
        )

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        profile = self._resolve_profile()
        ready = await self.ensure_ready()
        if not ready.ok:
            raise ServiceUnavailableError(ready.message)

        model = request.model or profile.model
        reasoning_replay_enabled = _reasoning_replay_enabled(profile, model)
        if request.transcript_messages is not None:
            compacted_rows = prepare_transcript_for_model(request.transcript_messages)
            translated_messages = translate_for_openai(
                group_assistant_rows(compacted_rows),
                request.note_snapshot or "",
                request.user_prompt or "",
            )
            if reasoning_replay_enabled and request.assistant_message_patches:
                translated_messages = _apply_openai_assistant_patches(
                    translated_messages,
                    request.assistant_message_patches,
                    keep_recent=profile.reasoning_replay_turns,
                )
            messages = build_llama_structured_messages(
                translated_messages,
                request.system_prompt,
            )
        else:
            messages = build_llama_native_messages(request)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_output_tokens or SETTINGS.llama_cpp.max_output_tokens,
            "temperature": SETTINGS.llama_cpp.temperature,
        }
        if request.tools:
            payload["tools"] = _openai_tools(request.tools)
            payload["tool_choice"] = "auto"
        if reasoning_replay_enabled:
            payload["extra_body"] = {"reasoning_split": True}

        response = await self._post_chat_completion(profile, payload)
        message = _extract_message(response)
        provider_response = _provider_response_from_payload(
            payload=response,
            provider_name=self.name,
            base_url=profile.base_url,
        )
        return ProviderResponse(
            text=_extract_message_text(message),
            tool_calls=_extract_native_tool_calls(message),
            generated_rows=[],
            assistant_message_patch=_extract_assistant_message_patch(message),
            finish_reason=provider_response.finish_reason,
            usage=provider_response.usage,
            raw_items=provider_response.raw_items,
            provider_metadata={
                **provider_response.provider_metadata,
                "model": model,
                "tool_runtime": "provider_native",
                "history_seeded": request.transcript_messages is not None,
                "session_reused": False,
                "profile_id": profile.profile_id,
                "profile_label": profile.display_label,
                "reasoning_replay_enabled": reasoning_replay_enabled,
                "reasoning_replay_turns": (
                    profile.reasoning_replay_turns if reasoning_replay_enabled else 0
                ),
            },
        )

    def _resolve_profile(self) -> OpenAICompatibleProfile:
        resolved = resolve_openai_compatible_profile(self._project_root, profile_id=self._profile_id)
        if resolved is not None:
            return resolved
        if self._profile_id is not None and self._fallback_profile.profile_id == self._profile_id:
            return self._fallback_profile
        if self._profile_id is not None:
            raise ConfigurationError(
                f"OpenAI-compatible profile {self._profile_id!r} is not configured in "
                f"{self._project_root / '.aunic' / 'proto-settings.json'}."
            )
        return self._fallback_profile

    async def _is_healthy(self, profile: OpenAICompatibleProfile) -> bool:
        if profile.health_endpoint is None:
            return True
        url = f"{profile.base_url}{profile.health_endpoint}"
        try:
            async with self._client_factory(SETTINGS.llama_cpp.request_timeout_seconds) as client:
                response = await client.get(url, headers=_request_headers(profile))
            return response.status_code < 500
        except httpx.HTTPError:
            return False

    async def _post_chat_completion(
        self,
        profile: OpenAICompatibleProfile,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        url = f"{profile.base_url}{profile.chat_endpoint}"
        async with self._client_factory(SETTINGS.llama_cpp.request_timeout_seconds) as client:
            response = await client.post(
                url,
                json=payload,
                headers=_request_headers(profile),
            )
        if response.status_code >= 400:
            raise ServiceUnavailableError(
                f"OpenAI-compatible chat completion failed with status {response.status_code}: "
                f"{response.text}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise StructuredOutputError("OpenAI-compatible provider returned non-JSON payload.") from exc
        if not isinstance(data, dict):
            raise StructuredOutputError("OpenAI-compatible provider returned a non-object payload.")
        return data

    def _build_client(self, timeout_seconds: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout_seconds)

    def _launch_startup_script(self, script_path: Path) -> subprocess.Popen[Any]:
        return subprocess.Popen(  # noqa: S603
            [str(script_path)],
            cwd=str(script_path.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


class LlamaCppProvider(OpenAICompatibleProvider):
    """Backward-compatible wrapper for tests and old callers."""

    def __init__(
        self,
        settings: LlamaCppSettings | None = None,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        launcher: Callable[[Path], subprocess.Popen[Any]] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        legacy_profile = legacy_llama_profile(settings)

        wrapped_client_factory: Callable[[float], httpx.AsyncClient] | None = None
        if client_factory is not None:
            wrapped_client_factory = lambda _timeout: client_factory()

        super().__init__(
            profile_id=legacy_profile.profile_id,
            fallback_profile=legacy_profile,
            client_factory=wrapped_client_factory,
            launcher=launcher,
            sleep=sleep,
        )


def legacy_llama_profile(settings: LlamaCppSettings | None = None) -> OpenAICompatibleProfile:
    config = settings or SETTINGS.llama_cpp
    return OpenAICompatibleProfile(
        profile_id="llama_addie",
        provider_label="Llama",
        custom_model_name="Addie",
        model=config.default_model,
        base_url=config.base_url.rstrip("/"),
        chat_endpoint=config.chat_endpoint,
        health_endpoint=config.health_endpoint,
        startup_script=config.startup_script,
    )


def _profile_details(
    profile: OpenAICompatibleProfile,
    *,
    running: bool | None,
    bootable: bool | None,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "profile_id": profile.profile_id,
        "profile_label": profile.display_label,
        "base_url": profile.base_url,
    }
    if running is not None:
        details["running"] = running
    if bootable is not None:
        details["bootable"] = bootable
    if profile.startup_script is not None:
        details["startup_script"] = str(profile.startup_script)
    if profile.health_endpoint is not None:
        details["health_endpoint"] = profile.health_endpoint
    return details


def _reasoning_replay_enabled(profile: OpenAICompatibleProfile, model: str) -> bool:
    return profile.replay_reasoning_details or model.lower().startswith("minimax/")


def _request_headers(profile: OpenAICompatibleProfile) -> dict[str, str]:
    headers = dict(profile.headers)
    if profile.api_key:
        headers["Authorization"] = f"Bearer {profile.api_key}"
    return headers


def _extract_choice(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise StructuredOutputError("OpenAI-compatible payload is missing `choices[0]`.")
    return choices[0]


def _extract_message(payload: dict[str, Any]) -> dict[str, Any]:
    choice = _extract_choice(payload)
    message = choice.get("message")
    if not isinstance(message, dict):
        raise StructuredOutputError("OpenAI-compatible payload is missing `message`.")
    return message


def _extract_message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return _strip_think_blocks(content)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return _strip_think_blocks("".join(parts))
    raise StructuredOutputError("OpenAI-compatible provider returned an unsupported message content payload.")


def _strip_think_blocks(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _extract_assistant_message_patch(message: dict[str, Any]) -> dict[str, Any] | None:
    patch: dict[str, Any] = {}
    reasoning = message.get("reasoning")
    if isinstance(reasoning, str) and reasoning.strip():
        patch["reasoning"] = reasoning
    reasoning_details = message.get("reasoning_details")
    if isinstance(reasoning_details, list) and reasoning_details:
        patch["reasoning_details"] = deepcopy(reasoning_details)
    return patch or None


def _apply_openai_assistant_patches(
    translated_messages: list[dict[str, Any]],
    patches: list[dict[str, Any]],
    *,
    keep_recent: int,
) -> list[dict[str, Any]]:
    if not patches or keep_recent < 1:
        return translated_messages

    assistant_indexes = [
        index for index, message in enumerate(translated_messages) if message.get("role") == "assistant"
    ]
    if not assistant_indexes:
        return translated_messages

    patches_to_apply = [deepcopy(patch) for patch in patches[-keep_recent:] if patch]
    if not patches_to_apply:
        return translated_messages

    updated_messages = [deepcopy(message) for message in translated_messages]
    target_indexes = assistant_indexes[-len(patches_to_apply) :]
    for message_index, patch in zip(target_indexes, patches_to_apply):
        updated_messages[message_index].update(patch)
    return updated_messages


def _extract_native_tool_calls(message: dict[str, Any]) -> list[ToolCall]:
    payload = message.get("tool_calls")
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise StructuredOutputError("OpenAI-compatible provider returned a non-list `tool_calls` payload.")

    tool_calls: list[ToolCall] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise StructuredOutputError(f"Tool call at index {index} is not an object.")
        function = item.get("function")
        if not isinstance(function, dict):
            raise StructuredOutputError(
                f"Tool call at index {index} is missing a `function` payload."
            )
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise StructuredOutputError(
                f"Tool call at index {index} is missing a string function name."
            )
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments_payload = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError as exc:
                raise StructuredOutputError(
                    f"Tool call {name!r} returned invalid arguments JSON."
                ) from exc
        elif isinstance(arguments, dict):
            arguments_payload = arguments
        else:
            raise StructuredOutputError(
                f"Tool call {name!r} returned unsupported arguments payload."
            )
        if not isinstance(arguments_payload, dict):
            raise StructuredOutputError(f"Tool call {name!r} arguments must decode to an object.")
        tool_calls.append(
            ToolCall(
                name=name,
                arguments=arguments_payload,
                id=item.get("id") if isinstance(item.get("id"), str) else None,
            )
        )
    return tool_calls


def _openai_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
        for tool in tools
    ]


def _usage_from_payload(payload: dict[str, Any]) -> Usage | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    completion_details = usage.get("completion_tokens_details")
    reasoning_tokens = None
    if isinstance(completion_details, dict):
        reasoning_tokens = _coerce_int(completion_details.get("reasoning_tokens"))
    return Usage(
        total_tokens=_coerce_int(usage.get("total_tokens")),
        input_tokens=_coerce_int(usage.get("prompt_tokens")),
        output_tokens=_coerce_int(usage.get("completion_tokens")),
        reasoning_output_tokens=reasoning_tokens,
        model_context_window=(
            _coerce_int(usage.get("model_context_window"))
            or _coerce_int(usage.get("context_window"))
            or _coerce_int(usage.get("context_length"))
        ),
    )


def _coerce_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _provider_response_from_payload(
    *,
    payload: dict[str, Any],
    provider_name: str,
    base_url: str,
) -> ProviderResponse:
    choice = _extract_choice(payload)
    usage = _usage_from_payload(payload)
    finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
    return ProviderResponse(
        text="",
        tool_calls=[],
        finish_reason=finish_reason if isinstance(finish_reason, str) else None,
        usage=usage,
        raw_items=[choice] if isinstance(choice, dict) else [],
        provider_metadata={
            "provider": provider_name,
            "base_url": base_url,
            "transport": "openai_compatible",
        },
    )
