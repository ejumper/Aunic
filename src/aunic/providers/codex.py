from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aunic.config import SETTINGS, CodexSettings
from aunic.domain import (
    HealthCheck,
    Message,
    ProviderGeneratedRow,
    ProviderRequest,
    ProviderResponse,
    TranscriptRow,
    Usage,
)
from aunic.errors import CodexProtocolError, StructuredOutputError
from aunic.providers.base import LLMProvider
from aunic.providers.codex_client import (
    CodexAppServerSession,
    CodexTurnResult,
    build_stdio_mcp_config_overrides,
)
from aunic.providers.sdk_tools import (
    STRUCTURED_RESULT_KEY,
    AunicToolBridge,
    ToolBridgeConfig,
    deserialize_tool_execution_result,
    provider_rows_from_tool_execution,
)
from aunic.transcript.flattening import flatten_tool_result_for_provider
from aunic.transcript.translation import compose_final_user_message, group_assistant_rows


CODEX_BASE_INSTRUCTIONS = (
    "You are Aunic's Codex transport. Aunic owns the transcript, tool definitions, and tool execution."
)

CODEX_DEVELOPER_INSTRUCTIONS = "\n".join(
    [
        "Use only the MCP tools exposed by Aunic when a tool is needed.",
        "Do not use built-in shell, patch, file editing, or web search behavior.",
        "Do not assume any default coding workflow outside the supplied note/transcript context.",
        "Return a normal assistant message after any required MCP tool calls are complete.",
    ]
)


@dataclass
class _CodexRunTransport:
    session: CodexAppServerSession
    thread_id: str
    cwd: Path
    model: str
    reasoning_effort: str
    history_seeded: bool
    bridge_config: ToolBridgeConfig | None


class CodexProvider(LLMProvider):
    name = "codex"

    def __init__(
        self,
        settings: CodexSettings | None = None,
    ) -> None:
        self._settings = settings or SETTINGS.codex
        self._active_run_transports: dict[str, _CodexRunTransport] = {}

    async def healthcheck(self) -> HealthCheck:
        cwd = Path.cwd()
        try:
            async with CodexAppServerSession(self._settings, cwd) as session:
                auth = await session.get_auth_status()
            auth_method = auth.get("authMethod")
            ok = bool(auth_method)
            message = (
                f"Codex auth detected via {auth_method}."
                if ok
                else "Codex app-server responded but no auth method is configured."
            )
            return HealthCheck(provider=self.name, ok=ok, message=message, details=auth)
        except Exception as exc:
            return HealthCheck(
                provider=self.name,
                ok=False,
                message=f"Codex healthcheck failed: {exc}",
                details={"exception_type": type(exc).__name__},
            )

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        cwd = Path(request.metadata.get("cwd", os.getcwd())).expanduser().resolve()
        run_session_id = request.metadata.get("run_session_id")
        model = request.model or self._settings.default_model
        requested_reasoning_effort = (
            request.reasoning_effort or self._settings.default_reasoning_effort
        )
        reasoning_effort = normalize_codex_reasoning_effort(requested_reasoning_effort)
        bridge_config = _build_tool_bridge_config(request)
        if run_session_id:
            transport, session_reused = await self._get_or_create_run_transport(
                run_session_id=run_session_id,
                cwd=cwd,
                model=model,
                reasoning_effort=reasoning_effort,
                bridge_config=bridge_config,
                transcript_messages=request.transcript_messages or [],
            )
            result = await transport.session.run_turn(
                thread_id=transport.thread_id,
                input_text=_build_turn_input_text(request),
                model=model,
                reasoning_effort=reasoning_effort,
                timeout_seconds=self._settings.turn_timeout_seconds,
            )
            history_seeded = transport.history_seeded
        else:
            config_overrides = _build_mcp_overrides(
                settings=self._settings,
                bridge_config=bridge_config,
            )
            async with CodexAppServerSession(
                self._settings,
                cwd,
                config_overrides=config_overrides,
            ) as session:
                thread_id, history_seeded = await _create_thread_for_request(
                    session=session,
                    request=request,
                    model=model,
                    reasoning_effort=reasoning_effort,
                )
                result = await session.run_turn(
                    thread_id=thread_id,
                    input_text=_build_turn_input_text(request),
                    model=model,
                    reasoning_effort=reasoning_effort,
                    timeout_seconds=self._settings.turn_timeout_seconds,
                )
            session_reused = False

        response = self._response_from_app_server_result(
            result,
            model=model,
            cwd=cwd,
            bridge_config=bridge_config,
        )
        response.provider_metadata["transport"] = "codex_sdk"
        response.provider_metadata["tool_runtime"] = "sdk_mcp"
        response.provider_metadata["history_seeded"] = history_seeded
        response.provider_metadata["session_reused"] = session_reused
        if reasoning_effort != requested_reasoning_effort:
            response.provider_metadata["requested_reasoning_effort"] = (
                requested_reasoning_effort
            )
            response.provider_metadata["effective_reasoning_effort"] = reasoning_effort
        return response

    async def close_run(self, run_session_id: str | None) -> None:
        if not run_session_id:
            return
        transport = self._active_run_transports.pop(run_session_id, None)
        if transport is None:
            return
        await transport.session.__aexit__(None, None, None)

    async def _get_or_create_run_transport(
        self,
        *,
        run_session_id: str,
        cwd: Path,
        model: str,
        reasoning_effort: str,
        bridge_config: ToolBridgeConfig | None,
        transcript_messages: list[TranscriptRow],
    ) -> tuple[_CodexRunTransport, bool]:
        existing = self._active_run_transports.get(run_session_id)
        if existing is not None:
            if (
                existing.cwd == cwd
                and existing.model == model
                and existing.reasoning_effort == reasoning_effort
                and existing.bridge_config == bridge_config
            ):
                return existing, True
            await existing.session.__aexit__(None, None, None)
            self._active_run_transports.pop(run_session_id, None)

        config_overrides = _build_mcp_overrides(
            settings=self._settings,
            bridge_config=bridge_config,
        )
        session = CodexAppServerSession(
            self._settings,
            cwd,
            config_overrides=config_overrides,
        )
        await session.__aenter__()
        history_seeded = bool(transcript_messages)
        thread_id, _ = await _create_thread_for_request(
            session=session,
            request=ProviderRequest(
                messages=[],
                transcript_messages=transcript_messages,
            ),
            model=model,
            reasoning_effort=reasoning_effort,
        )
        transport = _CodexRunTransport(
            session=session,
            thread_id=thread_id,
            cwd=cwd,
            model=model,
            reasoning_effort=reasoning_effort,
            history_seeded=history_seeded,
            bridge_config=bridge_config,
        )
        self._active_run_transports[run_session_id] = transport
        return transport, False

    def _response_from_app_server_result(
        self,
        result: CodexTurnResult,
        *,
        model: str,
        cwd: Path,
        bridge_config: ToolBridgeConfig | None,
    ) -> ProviderResponse:
        assistant_text = extract_assistant_text(result)
        if result.status != "completed" and not assistant_text:
            raise CodexProtocolError(
                result.error_message or f"Codex turn ended with status {result.status!r}."
            )

        usage = usage_from_codex_token_usage(result.token_usage)
        provider_metadata: dict[str, Any] = {
            "provider": self.name,
            "model": model,
            "cwd": str(cwd),
            "thread_id": result.thread_id,
            "turn_id": result.turn_id,
            "status": result.status,
            "stderr_lines": result.stderr_lines,
            "thread_item_count": len(result.thread_items),
        }
        if result.error_message:
            provider_metadata["error_message"] = result.error_message
        if bridge_config is not None:
            provider_metadata["tool_mode"] = bridge_config.mode
            provider_metadata["work_mode"] = bridge_config.work_mode

        return ProviderResponse(
            text=assistant_text,
            tool_calls=[],
            generated_rows=extract_generated_rows_from_thread_items(result.thread_items),
            finish_reason="stop" if result.status == "completed" else result.status,
            usage=usage,
            raw_items=result.raw_items,
            provider_metadata=provider_metadata,
        )


async def _create_thread_for_request(
    *,
    session: CodexAppServerSession,
    request: ProviderRequest,
    model: str,
    reasoning_effort: str,
) -> tuple[str, bool]:
    transcript_messages = request.transcript_messages or []
    if transcript_messages:
        history = build_codex_history_items(transcript_messages)
        response = await session.resume_thread_with_history(
            history=history,
            model=model,
            reasoning_effort=reasoning_effort,
            base_instructions=CODEX_BASE_INSTRUCTIONS,
            developer_instructions=CODEX_DEVELOPER_INSTRUCTIONS,
        )
        return _extract_thread_id(response), True

    response = await session.start_thread(
        model=model,
        reasoning_effort=reasoning_effort,
        base_instructions=CODEX_BASE_INSTRUCTIONS,
        developer_instructions=CODEX_DEVELOPER_INSTRUCTIONS,
    )
    return _extract_thread_id(response), False


def _extract_thread_id(response: dict[str, Any]) -> str:
    thread = response.get("thread")
    if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
        raise CodexProtocolError("Codex app-server returned an invalid thread payload.")
    return thread["id"]


def _build_tool_bridge_config(request: ProviderRequest) -> ToolBridgeConfig | None:
    if not request.tools:
        return None
    active_file = request.metadata.get("active_file")
    mode = request.metadata.get("mode")
    work_mode = request.metadata.get("work_mode")
    if not isinstance(active_file, str) or not active_file.strip():
        return None
    if mode not in {"note", "chat"}:
        return None
    if work_mode not in {"off", "read", "work"}:
        return None
    return ToolBridgeConfig(
        active_file=Path(active_file),
        mode=mode,
        work_mode=work_mode,
        metadata=dict(request.metadata),
    )


def _build_mcp_overrides(
    *,
    settings: CodexSettings,
    bridge_config: ToolBridgeConfig | None,
) -> tuple[str, ...]:
    if bridge_config is None:
        return ()
    env = {
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "AUNIC_MCP_CONFIG_JSON": json.dumps(
            {
                "active_file": str(bridge_config.active_file),
                "mode": bridge_config.mode,
                "work_mode": bridge_config.work_mode,
                "metadata": dict(bridge_config.metadata),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    return build_stdio_mcp_config_overrides(
        settings.mcp_server_name,
        env=env,
    )


def _build_turn_input_text(request: ProviderRequest) -> str:
    if request.transcript_messages is not None:
        return compose_final_user_message(request.note_snapshot or "", request.user_prompt or "")
    return render_messages_for_sdk(request.messages)


def render_messages_for_sdk(messages: list[Message]) -> str:
    rendered: list[str] = []
    for message in messages:
        name_suffix = f" ({message.name})" if message.name else ""
        rendered.append(f"[{message.role.upper()}{name_suffix}]")
        rendered.append(message.content)
    return "\n\n".join(rendered)


def build_codex_history_items(rows: list[TranscriptRow]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for group in group_assistant_rows(rows):
        if isinstance(group, list):
            text_parts: list[str] = []
            tool_items: list[dict[str, Any]] = []
            for row in group:
                if row.type == "message":
                    text_parts.append(_row_content_as_text(row))
                    continue
                tool_items.append(
                    {
                        "type": "function_call",
                        "name": row.tool_name,
                        "arguments": json.dumps(row.content, ensure_ascii=False, separators=(",", ":")),
                        "call_id": row.tool_id,
                    }
                )
            if text_parts:
                items.append(_message_item("assistant", "\n".join(text_parts)))
            items.extend(tool_items)
            continue

        if group.role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": group.tool_id,
                    "output": flatten_tool_result_for_provider(group),
                }
            )
            continue

        items.append(_message_item(group.role, _row_content_as_text(group)))
    return items


def _message_item(role: str, text: str) -> dict[str, Any]:
    content_type = "input_text" if role == "user" else "output_text"
    return {
        "type": "message",
        "role": role,
        "content": [{"type": content_type, "text": text}],
    }


def _row_content_as_text(row: TranscriptRow) -> str:
    if isinstance(row.content, str):
        return row.content
    return json.dumps(row.content, ensure_ascii=False, separators=(",", ":"))


def extract_generated_rows_from_thread_items(
    thread_items: list[dict[str, Any]],
) -> list[ProviderGeneratedRow]:
    generated_rows: list[ProviderGeneratedRow] = []
    for item in thread_items:
        if item.get("type") != "mcpToolCall":
            continue
        tool_id = item.get("id")
        tool_name = item.get("tool")
        arguments = item.get("arguments")
        if not isinstance(tool_id, str) or not isinstance(tool_name, str) or not isinstance(arguments, dict):
            continue

        structured = ((item.get("result") or {}).get("structuredContent"))
        if isinstance(structured, dict) and isinstance(structured.get(STRUCTURED_RESULT_KEY), dict):
            result = deserialize_tool_execution_result(structured[STRUCTURED_RESULT_KEY])
        else:
            error_message = _extract_thread_item_error_message(item) or (
                "Codex MCP tool call did not return structured result metadata."
            )
            result = _missing_structured_result(tool_name, error_message)

        generated_rows.extend(
            provider_rows_from_tool_execution(
                tool_name=tool_name,
                tool_id=tool_id,
                arguments=arguments,
                result=result,
            )
        )
    return generated_rows


def _extract_thread_item_error_message(item: dict[str, Any]) -> str | None:
    error = item.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    return None


def _missing_structured_result(tool_name: str, message: str):
    return deserialize_tool_execution_result(
        {
            "tool_name": tool_name,
            "status": "tool_error",
            "in_memory_content": {
                "category": "execution_error",
                "reason": "missing_structured_result",
                "message": message,
            },
            "transcript_content": {
                "category": "execution_error",
                "reason": "missing_structured_result",
                "message": message,
            },
            "tool_failure": {
                "category": "execution_error",
                "reason": "missing_structured_result",
                "tool_name": tool_name,
                "message": message,
                "details": {},
            },
            "metadata": {},
        }
    )


def extract_assistant_text(result: CodexTurnResult) -> str:
    for item in reversed(result.thread_items):
        if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
            return item["text"]
    for item in reversed(result.raw_items):
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue
        content = item.get("content", [])
        if not isinstance(content, list):
            continue
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str):
                parts.append(text)
        if parts:
            return "".join(parts)
    if result.status == "completed":
        raise StructuredOutputError("Codex did not return a final assistant message.")
    return ""


def usage_from_codex_token_usage(token_usage: dict[str, Any] | None) -> Usage | None:
    if not token_usage:
        return None
    payload = token_usage.get("last") or token_usage.get("total")
    if not isinstance(payload, dict):
        return None
    return Usage(
        total_tokens=_coerce_int(payload.get("totalTokens")),
        input_tokens=_coerce_int(payload.get("inputTokens")),
        cached_input_tokens=_coerce_int(payload.get("cachedInputTokens")),
        output_tokens=_coerce_int(payload.get("outputTokens")),
        reasoning_output_tokens=_coerce_int(payload.get("reasoningOutputTokens")),
        model_context_window=_coerce_int(token_usage.get("modelContextWindow")),
    )


def _coerce_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def normalize_codex_reasoning_effort(reasoning_effort: str) -> str:
    if reasoning_effort == "minimal":
        return "low"
    return reasoning_effort
