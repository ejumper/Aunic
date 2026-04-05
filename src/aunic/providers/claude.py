from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aunic.config import SETTINGS, ClaudeSettings
from aunic.domain import HealthCheck, Message, ProviderRequest, ProviderResponse, Usage
from aunic.errors import ClaudeSDKError
from aunic.providers.base import LLMProvider
from aunic.providers.claude_client import ClaudeSession, ClaudeTurnResult
from aunic.providers.sdk_tools import ToolBridgeConfig
from aunic.transcript.translation import (
    compose_final_user_message,
    group_assistant_rows,
    translate_for_anthropic,
)


CLAUDE_BASE_INSTRUCTIONS = (
    "You are Aunic's Claude transport. Aunic owns the transcript, tool definitions, and tool execution."
)

CLAUDE_DEVELOPER_INSTRUCTIONS = "\n".join(
    [
        "Use only the MCP tools exposed by Aunic when a tool is needed.",
        "Do not use Claude Code's built-in shell, file editing, search, or autonomous coding workflow.",
        "Do not assume any default agent workflow outside the supplied note/transcript context.",
        "Return a normal assistant message after any required MCP tool calls are complete.",
    ]
)


@dataclass
class _ClaudeRunTransport:
    session: ClaudeSession
    cwd: Path
    model: str
    system_prompt: str
    history_seeded: bool
    bridge_config: ToolBridgeConfig | None


class ClaudeProvider(LLMProvider):
    name = "claude"

    def __init__(
        self,
        settings: ClaudeSettings | None = None,
    ) -> None:
        self._settings = settings or SETTINGS.claude
        self._active_run_transports: dict[str, _ClaudeRunTransport] = {}

    async def healthcheck(self) -> HealthCheck:
        cwd = Path.cwd()
        try:
            async with ClaudeSession(
                self._settings,
                cwd,
                self._settings.default_model,
                system_prompt="Respond with only: OK",
                effort="low",
            ) as session:
                result = await session.query(prompt_text="Health check. Respond with only: OK")
            ok = bool(result.text.strip())
            return HealthCheck(
                provider=self.name,
                ok=ok,
                message=f"Claude SDK responded ({self._settings.default_model}).",
                details={"model": self._settings.default_model, "usage": result.usage},
            )
        except Exception as exc:
            return HealthCheck(
                provider=self.name,
                ok=False,
                message=f"Claude healthcheck failed: {exc}",
                details={"exception_type": type(exc).__name__},
            )

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        cwd = Path(request.metadata.get("cwd", os.getcwd())).expanduser().resolve()
        run_session_id = request.metadata.get("run_session_id")
        model = request.model or self._settings.default_model
        reasoning_effort = normalize_claude_reasoning_effort(
            model, request.reasoning_effort
        )
        bridge_config = _build_tool_bridge_config(request)
        system_prompt = _build_system_prompt(request.system_prompt)
        session_id = str(run_session_id or "default")

        if run_session_id:
            transport, session_reused = await self._get_or_create_run_transport(
                run_session_id=run_session_id,
                cwd=cwd,
                model=model,
                system_prompt=system_prompt,
                reasoning_effort=reasoning_effort,
                bridge_config=bridge_config,
            )
            if transport.history_seeded or request.transcript_messages is None:
                result = await transport.session.query(
                    prompt_text=_build_turn_input_text(request),
                    session_id=session_id,
                )
            else:
                result = await transport.session.query(
                    seeded_messages=build_claude_seed_messages(request, model=model),
                    session_id=session_id,
                )
                transport.history_seeded = True
            history_seeded = transport.history_seeded
        else:
            async with ClaudeSession(
                self._settings,
                cwd,
                model,
                system_prompt=system_prompt,
                bridge_config=bridge_config,
                effort=reasoning_effort,
            ) as session:
                if request.transcript_messages is not None:
                    result = await session.query(
                        seeded_messages=build_claude_seed_messages(request, model=model),
                        session_id=session_id,
                    )
                    history_seeded = True
                else:
                    result = await session.query(
                        prompt_text=_build_turn_input_text(request),
                        session_id=session_id,
                    )
                    history_seeded = False
            session_reused = False

        response = _response_from_turn_result(
            result,
            model=model,
            cwd=cwd,
            bridge_config=bridge_config,
        )
        response.provider_metadata["transport"] = "claude_code_sdk"
        response.provider_metadata["tool_runtime"] = "sdk_mcp"
        response.provider_metadata["history_seeded"] = history_seeded
        response.provider_metadata["session_reused"] = session_reused
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
        system_prompt: str,
        reasoning_effort: str,
        bridge_config: ToolBridgeConfig | None,
    ) -> tuple[_ClaudeRunTransport, bool]:
        existing = self._active_run_transports.get(run_session_id)
        if existing is not None:
            if (
                existing.cwd == cwd
                and existing.model == model
                and existing.system_prompt == system_prompt
                and existing.bridge_config == bridge_config
            ):
                return existing, True
            await existing.session.__aexit__(None, None, None)
            self._active_run_transports.pop(run_session_id, None)

        session = ClaudeSession(
            self._settings,
            cwd,
            model,
            system_prompt=system_prompt,
            bridge_config=bridge_config,
            effort=reasoning_effort,
        )
        await session.__aenter__()
        transport = _ClaudeRunTransport(
            session=session,
            cwd=cwd,
            model=model,
            system_prompt=system_prompt,
            history_seeded=False,
            bridge_config=bridge_config,
        )
        self._active_run_transports[run_session_id] = transport
        return transport, False


def build_claude_seed_messages(
    request: ProviderRequest,
    *,
    model: str,
) -> list[dict[str, Any]]:
    translated = translate_for_anthropic(
        group_assistant_rows(request.transcript_messages or []),
        request.note_snapshot or "",
        request.user_prompt or "",
    )
    seeded: list[dict[str, Any]] = []
    for message in translated:
        role = message.get("role")
        content = message.get("content")
        if role == "assistant":
            assistant_content = content
            if isinstance(assistant_content, str):
                assistant_content = [{"type": "text", "text": assistant_content}]
            seeded.append(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "model": model,
                        "content": assistant_content,
                    },
                    "parent_tool_use_id": None,
                }
            )
            continue
        seeded.append(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": content,
                },
                "parent_tool_use_id": None,
            }
        )
    return seeded


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


def _build_system_prompt(system_prompt: str | None) -> str:
    parts = [CLAUDE_BASE_INSTRUCTIONS, CLAUDE_DEVELOPER_INSTRUCTIONS]
    if system_prompt:
        parts.append(system_prompt)
    return "\n\n".join(part for part in parts if part.strip())


def _response_from_turn_result(
    result: ClaudeTurnResult,
    *,
    model: str,
    cwd: Path,
    bridge_config: ToolBridgeConfig | None,
) -> ProviderResponse:
    usage = _usage_from_sdk(result.usage)
    response = ProviderResponse(
        text=result.text,
        tool_calls=[],
        generated_rows=result.generated_rows,
        finish_reason=result.finish_reason,
        usage=usage,
        raw_items=result.raw_messages,
        provider_metadata={
            "provider": "claude",
            "model": model,
            "cwd": str(cwd),
            "raw_message_count": len(result.raw_messages),
        },
    )
    if bridge_config is not None:
        response.provider_metadata["tool_mode"] = bridge_config.mode
        response.provider_metadata["work_mode"] = bridge_config.work_mode
    return response


def _usage_from_sdk(usage: dict[str, Any] | None) -> Usage | None:
    if not isinstance(usage, dict):
        return None
    return Usage(
        total_tokens=_coerce_int(usage.get("total_tokens")),
        input_tokens=_coerce_int(usage.get("input_tokens")),
        cached_input_tokens=_coerce_int(usage.get("cache_creation_input_tokens")),
        output_tokens=_coerce_int(usage.get("output_tokens")),
    )


def _coerce_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def normalize_claude_reasoning_effort(
    model: str,
    reasoning_effort: str | None,
) -> str:
    if not reasoning_effort:
        return "medium"
    if "haiku" in model.lower() and reasoning_effort == "xhigh":
        return "high"
    if reasoning_effort == "minimal":
        return "low"
    return reasoning_effort
