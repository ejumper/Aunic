from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aunic.config import ClaudeSettings
from aunic.errors import ClaudeSDKError
from aunic.providers.sdk_tools import (
    STRUCTURED_RESULT_KEY,
    AunicToolBridge,
    ToolBridgeConfig,
    canonical_sdk_tool_name,
    deserialize_tool_execution_result,
    provider_rows_from_tool_execution,
)


@dataclass(frozen=True)
class ClaudeTurnResult:
    text: str
    usage: dict[str, Any] | None
    finish_reason: str
    generated_rows: list[Any] = field(default_factory=list)
    raw_messages: list[dict[str, Any]] = field(default_factory=list)


class ClaudeSession:
    """Manages a persistent ClaudeSDKClient for a single run session."""

    def __init__(
        self,
        settings: ClaudeSettings,
        cwd: Path,
        model: str,
        *,
        system_prompt: str,
        bridge_config: ToolBridgeConfig | None = None,
        effort: str | None = None,
    ) -> None:
        self._settings = settings
        self._cwd = cwd
        self._model = model
        self._system_prompt = system_prompt
        self._bridge_config = bridge_config
        self._effort = effort
        self._client: Any = None
        self._bridge: AunicToolBridge | None = None
        self._generated_rows: list[Any] = []

    async def __aenter__(self) -> ClaudeSession:
        try:
            from claude_agent_sdk import (
                ClaudeAgentOptions,
                ClaudeSDKClient,
                HookMatcher,
                create_sdk_mcp_server,
            )
        except ImportError as exc:
            raise ClaudeSDKError(
                "claude-agent-sdk is not installed. Install it with: pip install claude-agent-sdk"
            ) from exc

        effort_value = _map_effort(self._effort) if self._effort else None
        mcp_servers: dict[str, Any] = {}
        allowed_tools: list[str] = []
        hooks: dict[str, list[Any]] | None = None

        if self._bridge_config is not None:
            self._bridge = AunicToolBridge(self._bridge_config)
            await self._bridge.start()
            allowed_tools = [
                f"mcp__{self._settings.mcp_server_name}__{definition.spec.name}"
                for definition in self._bridge.registry
            ]
            mcp_servers = {
                self._settings.mcp_server_name: create_sdk_mcp_server(
                    name=self._settings.mcp_server_name,
                    tools=self._bridge.build_claude_sdk_tools(),
                )
            }
            hooks = {
                "PostToolUse": [HookMatcher(hooks=[self._post_tool_use_hook])],
                "PostToolUseFailure": [HookMatcher(hooks=[self._post_tool_use_failure_hook])],
            }

        options = ClaudeAgentOptions(
            model=self._model,
            system_prompt=self._system_prompt,
            max_turns=None,
            tools={"type": "preset", "preset": "claude_code"},
            allowed_tools=allowed_tools,
            mcp_servers=mcp_servers,
            hooks=hooks,
            cwd=str(self._cwd),
            cli_path=self._settings.executable,
            permission_mode="bypassPermissions",
            continue_conversation=True,
            effort=effort_value,
        )
        self._client = ClaudeSDKClient(options=options)
        await self._client.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        if self._bridge is not None:
            await self._bridge.aclose()
            self._bridge = None

    async def query(
        self,
        *,
        prompt_text: str | None = None,
        seeded_messages: list[dict[str, Any]] | None = None,
        session_id: str = "default",
    ) -> ClaudeTurnResult:
        if self._client is None:
            raise ClaudeSDKError("ClaudeSession was used before connect.")

        self._generated_rows = []

        if seeded_messages is not None:
            await self._client.query(
                _iter_seeded_messages(seeded_messages),
                session_id=session_id,
            )
        else:
            await self._client.query(prompt_text or "", session_id=session_id)

        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

        last_assistant_text = ""
        fallback_result_text = ""
        usage: dict[str, Any] | None = None
        finish_reason = "stop"
        raw_messages: list[dict[str, Any]] = []

        try:
            async with asyncio.timeout(self._settings.turn_timeout_seconds):
                async for message in self._client.receive_response():
                    if isinstance(message, AssistantMessage):
                        raw_messages.append({"type": "assistant", "model": message.model})
                        if message.error:
                            finish_reason = message.error
                        if message.usage:
                            usage = message.usage
                        text_parts = [
                            block.text
                            for block in message.content
                            if isinstance(block, TextBlock)
                        ]
                        has_tool_use = any(
                            isinstance(block, ToolUseBlock) for block in message.content
                        )
                        if text_parts and not has_tool_use:
                            last_assistant_text = "".join(text_parts)

                    elif isinstance(message, ResultMessage):
                        raw_messages.append(
                            {
                                "type": "result",
                                "subtype": message.subtype,
                                "stop_reason": message.stop_reason,
                                "is_error": message.is_error,
                                "num_turns": message.num_turns,
                            }
                        )
                        if message.usage:
                            usage = message.usage
                        if message.stop_reason:
                            finish_reason = message.stop_reason
                        if isinstance(message.result, str) and message.result.strip():
                            fallback_result_text = message.result

        except TimeoutError as exc:
            raise ClaudeSDKError(
                f"Claude SDK timed out after {self._settings.turn_timeout_seconds}s."
            ) from exc

        assistant_text = last_assistant_text or fallback_result_text
        if not assistant_text and not self._generated_rows:
            raise ClaudeSDKError("Claude SDK returned no assistant text or tool activity.")

        return ClaudeTurnResult(
            text=assistant_text,
            usage=usage,
            finish_reason=finish_reason,
            generated_rows=list(self._generated_rows),
            raw_messages=raw_messages,
        )

    async def _post_tool_use_hook(self, hook_input: Any, *_: object) -> dict[str, Any]:
        hook_tool_name = _hook_string(hook_input, "tool_name", default="unknown_tool")
        tool_use_id = _hook_string(hook_input, "tool_use_id", default="unknown_tool_use")
        tool_input = _hook_dict(hook_input, "tool_input")
        payload = _hook_dict(hook_input, "tool_response")
        recorded_result = (
            self._bridge.consume_sdk_tool_execution(
                tool_name=hook_tool_name,
                arguments=tool_input,
            )
            if self._bridge is not None
            else None
        )
        serialized = payload.get(STRUCTURED_RESULT_KEY)
        if isinstance(serialized, dict):
            result = deserialize_tool_execution_result(serialized)
        elif recorded_result is not None:
            result = recorded_result
        else:
            tool_name = canonical_sdk_tool_name(hook_tool_name)
            serialized = {
                "tool_name": tool_name,
                "status": "tool_error",
                "in_memory_content": {
                    "category": "execution_error",
                    "reason": "missing_structured_result",
                    "message": "Claude SDK tool response did not include Aunic result metadata.",
                },
                "transcript_content": {
                    "category": "execution_error",
                    "reason": "missing_structured_result",
                    "message": "Claude SDK tool response did not include Aunic result metadata.",
                },
                "tool_failure": {
                    "category": "execution_error",
                    "reason": "missing_structured_result",
                    "tool_name": tool_name,
                    "message": "Claude SDK tool response did not include Aunic result metadata.",
                    "details": {},
                },
                "metadata": {},
            }
            result = deserialize_tool_execution_result(serialized)
        self._generated_rows.extend(
            provider_rows_from_tool_execution(
                tool_name=result.tool_name,
                tool_id=tool_use_id,
                arguments=tool_input,
                result=result,
            )
        )
        return {"continue_": True}

    async def _post_tool_use_failure_hook(
        self,
        hook_input: Any,
        *_: object,
    ) -> dict[str, Any]:
        tool_name = canonical_sdk_tool_name(
            _hook_string(hook_input, "tool_name", default="unknown_tool")
        )
        tool_use_id = _hook_string(hook_input, "tool_use_id", default="unknown_tool_use")
        tool_input = _hook_dict(hook_input, "tool_input")
        error_message = _hook_string(hook_input, "error", default="Tool execution failed.")
        result = deserialize_tool_execution_result(
            {
                "tool_name": tool_name,
                "status": "tool_error",
                "in_memory_content": {
                    "category": "execution_error",
                    "reason": "tool_execution_failed",
                    "message": error_message,
                },
                "transcript_content": {
                    "category": "execution_error",
                    "reason": "tool_execution_failed",
                    "message": error_message,
                },
                "tool_failure": {
                    "category": "execution_error",
                    "reason": "tool_execution_failed",
                    "tool_name": tool_name,
                    "message": error_message,
                    "details": {},
                },
                "metadata": {},
            }
        )
        self._generated_rows.extend(
            provider_rows_from_tool_execution(
                tool_name=tool_name,
                tool_id=tool_use_id,
                arguments=tool_input,
                result=result,
            )
        )
        return {"continue_": True}


async def _iter_seeded_messages(messages: list[dict[str, Any]]):
    for message in messages:
        yield dict(message)


def _map_effort(effort: str) -> str | None:
    mapping = {
        "none": "low",
        "minimal": "low",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "xhigh": "max",
    }
    return mapping.get(effort)


def _hook_value(hook_input: Any, key: str) -> Any:
    if isinstance(hook_input, dict):
        return hook_input.get(key)
    return getattr(hook_input, key, None)


def _hook_dict(hook_input: Any, key: str) -> dict[str, Any]:
    value = _hook_value(hook_input, key)
    return value if isinstance(value, dict) else {}


def _hook_string(hook_input: Any, key: str, *, default: str) -> str:
    value = _hook_value(hook_input, key)
    return value if isinstance(value, str) and value else default
