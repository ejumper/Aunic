from __future__ import annotations

import asyncio
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
from aunic.errors import ServiceUnavailableError, StructuredOutputError
from aunic.providers.base import LLMProvider
from aunic.providers.envelope import (
    build_llama_structured_messages,
    build_llama_native_messages,
)
from aunic.transcript.translation import group_assistant_rows, translate_for_openai


class LlamaCppProvider(LLMProvider):
    name = "llama_cpp"

    def __init__(
        self,
        settings: LlamaCppSettings | None = None,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        launcher: Callable[[Path], subprocess.Popen[Any]] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._settings = settings or SETTINGS.llama_cpp
        self._client_factory = client_factory or self._build_client
        self._launcher = launcher or self._launch_startup_script
        self._sleep = sleep or asyncio.sleep

    async def healthcheck(self) -> HealthCheck:
        if await self._is_healthy():
            return HealthCheck(
                provider=self.name,
                ok=True,
                message="llama.cpp server is responding.",
                details={"base_url": self._settings.base_url, "running": True},
            )

        bootable = self._settings.startup_script.exists() and os.access(
            self._settings.startup_script,
            os.X_OK,
        )
        if bootable:
            return HealthCheck(
                provider=self.name,
                ok=True,
                message="llama.cpp server is not running, but the startup script is available.",
                details={
                    "base_url": self._settings.base_url,
                    "running": False,
                    "bootable": True,
                    "startup_script": str(self._settings.startup_script),
                },
            )

        return HealthCheck(
            provider=self.name,
            ok=False,
            message="llama.cpp is unavailable and the startup script is missing or not executable.",
            details={"startup_script": str(self._settings.startup_script)},
        )

    async def ensure_ready(self) -> HealthCheck:
        initial = await self.healthcheck()
        if initial.details.get("running"):
            return initial
        if not initial.ok:
            return initial

        self._launcher(self._settings.startup_script)
        started_at = time.monotonic()
        while time.monotonic() - started_at < self._settings.startup_timeout_seconds:
            if await self._is_healthy():
                return HealthCheck(
                    provider=self.name,
                    ok=True,
                    message="llama.cpp server started successfully.",
                    details={"base_url": self._settings.base_url, "running": True},
                )
            await self._sleep(self._settings.poll_interval_seconds)

        return HealthCheck(
            provider=self.name,
            ok=False,
            message="Timed out waiting for llama.cpp to start.",
            details={"startup_script": str(self._settings.startup_script)},
        )

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        ready = await self.ensure_ready()
        if not ready.ok:
            raise ServiceUnavailableError(ready.message)

        model = request.model or self._settings.default_model
        if request.transcript_messages is not None:
            translated_messages = translate_for_openai(
                group_assistant_rows(request.transcript_messages),
                request.note_snapshot or "",
                request.user_prompt or "",
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
            "max_tokens": request.max_output_tokens or self._settings.max_output_tokens,
            "temperature": self._settings.temperature,
        }
        if request.tools:
            payload["tools"] = _openai_tools(request.tools)
            payload["tool_choice"] = "auto"

        response = await self._post_chat_completion(payload)
        message = _extract_message(response)
        provider_response = _provider_response_from_payload(
            payload=response,
            provider_name=self.name,
            base_url=self._settings.base_url,
        )
        return ProviderResponse(
            text=_extract_message_text(message),
            tool_calls=_extract_native_tool_calls(message),
            generated_rows=[],
            finish_reason=provider_response.finish_reason,
            usage=provider_response.usage,
            raw_items=provider_response.raw_items,
            provider_metadata={
                **provider_response.provider_metadata,
                "model": model,
                "tool_runtime": "provider_native",
                "history_seeded": request.transcript_messages is not None,
                "session_reused": False,
            },
        )

    async def _is_healthy(self) -> bool:
        url = f"{self._settings.base_url}{self._settings.health_endpoint}"
        try:
            async with self._client_factory() as client:
                response = await client.get(url)
            return response.status_code < 500
        except httpx.HTTPError:
            return False

    async def _post_chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._settings.base_url}{self._settings.chat_endpoint}"
        async with self._client_factory() as client:
            response = await client.post(url, json=payload)
        if response.status_code >= 400:
            raise ServiceUnavailableError(
                f"llama.cpp chat completion failed with status {response.status_code}: "
                f"{response.text}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise StructuredOutputError("llama.cpp returned non-JSON payload.") from exc
        if not isinstance(data, dict):
            raise StructuredOutputError("llama.cpp returned a non-object payload.")
        return data

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._settings.request_timeout_seconds)

    def _launch_startup_script(self, script_path: Path) -> subprocess.Popen[Any]:
        return subprocess.Popen(  # noqa: S603
            [str(script_path)],
            cwd=str(script_path.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def _extract_choice(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise StructuredOutputError("llama.cpp payload is missing `choices[0]`.")
    return choices[0]


def _extract_message(payload: dict[str, Any]) -> dict[str, Any]:
    choice = _extract_choice(payload)
    message = choice.get("message")
    if not isinstance(message, dict):
        raise StructuredOutputError("llama.cpp payload is missing `message`.")
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
    raise StructuredOutputError("llama.cpp returned an unsupported message content payload.")


def _strip_think_blocks(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _extract_native_tool_calls(message: dict[str, Any]) -> list[ToolCall]:
    payload = message.get("tool_calls")
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise StructuredOutputError("llama.cpp returned a non-list `tool_calls` payload.")

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


def _usage_from_llama_payload(payload: dict[str, Any]) -> Usage | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    return Usage(
        total_tokens=_coerce_int(usage.get("total_tokens")),
        input_tokens=_coerce_int(usage.get("prompt_tokens")),
        output_tokens=_coerce_int(usage.get("completion_tokens")),
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
    usage = _usage_from_llama_payload(payload)
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
