from __future__ import annotations

import json
import os
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from mcp import types as mcp_types
from mcp.types import CallToolResult, TextContent

from aunic.context.file_manager import FileManager
from aunic.domain import ProviderGeneratedRow, TranscriptRow, WorkMode
from aunic.mcp.tools import MCPToolRegistry, build_mcp_tool_registry, merge_tool_registries
from aunic.research import FetchService, ResearchState, SearchService
from aunic.tools import (
    RunToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolSessionState,
    build_chat_tool_registry,
    build_note_tool_registry,
)
from aunic.tools.runtime import failure_from_payload, failure_payload
from aunic.transcript.flattening import flatten_tool_result_for_provider

if TYPE_CHECKING:
    from claude_agent_sdk import SdkMcpTool
    from aunic.loop.types import ToolFailure

ToolBridgeMode = Literal["note", "chat"]
STRUCTURED_RESULT_KEY = "_aunic_result"


@dataclass(frozen=True)
class ToolBridgeConfig:
    active_file: Path
    mode: ToolBridgeMode
    work_mode: WorkMode
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RecordedSDKToolExecution:
    tool_name: str
    arguments: dict[str, Any]
    result: ToolExecutionResult


class AunicToolBridge:
    def __init__(
        self,
        config: ToolBridgeConfig,
        *,
        file_manager: FileManager | None = None,
        search_service: SearchService | None = None,
        fetch_service: FetchService | None = None,
    ) -> None:
        self._config = ToolBridgeConfig(
            active_file=config.active_file.expanduser().resolve(),
            mode=config.mode,
            work_mode=config.work_mode,
            metadata=dict(config.metadata),
        )
        self._file_manager = file_manager or FileManager()
        self._search_service = search_service or SearchService()
        self._fetch_service = fetch_service or FetchService()
        self._session_state = ToolSessionState(cwd=self.cwd)
        self._runtime: RunToolContext | None = None
        self._registry: tuple[ToolDefinition[Any], ...] = ()
        self._mcp_registry: MCPToolRegistry | None = None
        self._recorded_sdk_results: deque[RecordedSDKToolExecution] = deque()

    @property
    def cwd(self) -> Path:
        raw = self._config.metadata.get("cwd")
        if isinstance(raw, str) and raw.strip():
            return Path(raw).expanduser().resolve()
        return self._config.active_file.parent

    @property
    def registry(self) -> tuple[ToolDefinition[Any], ...]:
        return self._registry

    async def start(self) -> None:
        if self._runtime is not None:
            return
        if self._config.mode == "note":
            base_registry = build_note_tool_registry(work_mode=self._config.work_mode, project_root=self.cwd)
        else:
            base_registry = build_chat_tool_registry(work_mode=self._config.work_mode, project_root=self.cwd)
        self._mcp_registry = await build_mcp_tool_registry(self.cwd)
        self._registry = merge_tool_registries(base_registry, self._mcp_registry.tools)
        self._runtime = await RunToolContext.create(
            file_manager=self._file_manager,
            context_result=None,
            prompt_run=None,
            active_file=self._config.active_file,
            session_state=self._session_state,
            search_service=self._search_service,
            fetch_service=self._fetch_service,
            research_state=ResearchState(),
            progress_sink=None,
            work_mode=self._config.work_mode,
            permission_handler=None,
            metadata=dict(self._config.metadata),
        )

    def tool_definitions_by_name(self) -> dict[str, ToolDefinition[Any]]:
        return {definition.spec.name: definition for definition in self._registry}

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
    ) -> ToolExecutionResult:
        await self.start()
        runtime = self._require_runtime()
        tool_map = self.tool_definitions_by_name()
        definition = tool_map.get(tool_name)
        if definition is None:
            payload = failure_payload(
                category="validation_error",
                reason="unknown_tool",
                message=f"Unknown tool {tool_name!r}.",
                tool_name=tool_name,
            )
            return _tool_error_result(tool_name, payload)

        payload = arguments or {}
        if not isinstance(payload, dict):
            error_payload = failure_payload(
                category="validation_error",
                reason="invalid_arguments",
                message=f"Arguments for {tool_name!r} must be an object.",
            )
            return _tool_error_result(tool_name, error_payload)

        try:
            parsed_args = definition.parse_arguments(payload)
        except ValueError as exc:
            error_payload = failure_payload(
                category="validation_error",
                reason="invalid_arguments",
                message=str(exc),
            )
            return _tool_error_result(tool_name, error_payload)

        try:
            return await definition.execute(runtime, parsed_args)
        except ValueError as exc:
            error_payload = failure_payload(
                category="validation_error",
                reason="invalid_execution_arguments",
                message=str(exc),
            )
            return _tool_error_result(tool_name, error_payload)
        except Exception as exc:
            error_payload = failure_payload(
                category="execution_error",
                reason="tool_execution_failed",
                message=str(exc),
            )
            return _tool_error_result(tool_name, error_payload)

    def build_codex_call_result(self, result: ToolExecutionResult) -> CallToolResult:
        payload = serialize_tool_execution_result(result)
        return CallToolResult(
            content=[TextContent(type="text", text=model_visible_tool_text(result))],
            structuredContent={STRUCTURED_RESULT_KEY: payload},
            isError=result.status != "completed",
        )

    def build_claude_sdk_tools(self) -> list["SdkMcpTool[Any]"]:
        from claude_agent_sdk import SdkMcpTool

        tools: list[SdkMcpTool[Any]] = []
        for definition in self._registry:
            tools.append(
                SdkMcpTool(
                    name=definition.spec.name,
                    description=definition.spec.description,
                    input_schema=definition.spec.input_schema,
                    handler=_build_claude_tool_handler(self, definition.spec.name),
                )
            )
        return tools

    def definition_for_tool_name(self, tool_name: str) -> ToolDefinition[Any] | None:
        return self.tool_definitions_by_name().get(tool_name)

    def record_sdk_tool_execution(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolExecutionResult,
    ) -> None:
        self._recorded_sdk_results.append(
            RecordedSDKToolExecution(
                tool_name=canonical_sdk_tool_name(tool_name),
                arguments=dict(arguments),
                result=result,
            )
        )

    def consume_sdk_tool_execution(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult | None:
        canonical_name = canonical_sdk_tool_name(tool_name)
        normalized_arguments = dict(arguments)
        for index, entry in enumerate(self._recorded_sdk_results):
            if entry.tool_name != canonical_name:
                continue
            if entry.arguments != normalized_arguments:
                continue
            del self._recorded_sdk_results[index]
            return entry.result
        for index, entry in enumerate(self._recorded_sdk_results):
            if entry.tool_name == canonical_name:
                del self._recorded_sdk_results[index]
                return entry.result
        return None

    def _require_runtime(self) -> RunToolContext:
        if self._runtime is None:
            raise RuntimeError("AunicToolBridge.start() must be called before tool execution.")
        return self._runtime

    async def aclose(self) -> None:
        if self._mcp_registry is None:
            return
        registry = self._mcp_registry
        self._mcp_registry = None
        await registry.aclose()


def provider_rows_from_tool_execution(
    *,
    tool_name: str,
    tool_id: str,
    arguments: dict[str, Any],
    result: ToolExecutionResult,
) -> list[ProviderGeneratedRow]:
    transcript_content = (
        result.in_memory_content
        if result.transcript_content is None
        else result.transcript_content
    )
    row_type = "tool_result" if result.status == "completed" else "tool_error"
    return [
        ProviderGeneratedRow(
            row=TranscriptRow(
                row_number=0,
                role="assistant",
                type="tool_call",
                tool_name=tool_name,
                tool_id=tool_id,
                content=arguments,
            )
        ),
        ProviderGeneratedRow(
            row=TranscriptRow(
                row_number=0,
                role="tool",
                type=row_type,
                tool_name=tool_name,
                tool_id=tool_id,
                content=result.in_memory_content,
            ),
            transcript_content=transcript_content,
        ),
    ]


def model_visible_tool_text(result: ToolExecutionResult) -> str:
    row = TranscriptRow(
        row_number=0,
        role="tool",
        type="tool_result" if result.status == "completed" else "tool_error",
        tool_name=result.tool_name,
        tool_id="sdk_tool",
        content=result.in_memory_content,
    )
    return flatten_tool_result_for_provider(row)


def build_mcp_tool_definition(definition: ToolDefinition[Any]) -> mcp_types.Tool:
    return mcp_types.Tool(
        name=definition.spec.name,
        description=definition.spec.description,
        inputSchema=dict(definition.spec.input_schema),
        outputSchema=None,
    )


def canonical_sdk_tool_name(tool_name: str) -> str:
    if tool_name.startswith("mcp__aunic__"):
        return tool_name[len("mcp__aunic__") :]
    return tool_name


def serialize_tool_execution_result(result: ToolExecutionResult) -> dict[str, Any]:
    return {
        "tool_name": result.tool_name,
        "status": result.status,
        "in_memory_content": result.in_memory_content,
        "transcript_content": result.transcript_content,
        "tool_failure": _serialize_tool_failure(result.tool_failure),
        "metadata": dict(result.metadata),
    }


def deserialize_tool_execution_result(payload: dict[str, Any]) -> ToolExecutionResult:
    tool_name = payload.get("tool_name")
    status = payload.get("status")
    if not isinstance(tool_name, str) or not isinstance(status, str):
        raise ValueError("Serialized tool execution result is missing tool_name or status.")
    failure = _deserialize_tool_failure(payload.get("tool_failure"), tool_name=tool_name)
    return ToolExecutionResult(
        tool_name=tool_name,
        status=status,
        in_memory_content=payload.get("in_memory_content"),
        transcript_content=payload.get("transcript_content"),
        tool_failure=failure,
        metadata=dict(payload.get("metadata") or {}),
    )


def _serialize_tool_failure(failure: "ToolFailure | None") -> dict[str, Any] | None:
    if failure is None:
        return None
    return {
        "category": failure.category,
        "reason": failure.reason,
        "tool_name": failure.tool_name,
        "message": failure.message,
        "target_identifier": failure.target_identifier,
        "details": dict(failure.details),
    }


def _deserialize_tool_failure(
    payload: Any,
    *,
    tool_name: str,
) -> "ToolFailure | None":
    if not isinstance(payload, dict):
        return None
    from aunic.loop.types import ToolFailure

    category = payload.get("category")
    reason = payload.get("reason")
    message = payload.get("message")
    if not all(isinstance(value, str) for value in (category, reason, message)):
        return None
    return ToolFailure(
        category=category,  # type: ignore[arg-type]
        reason=reason,
        tool_name=payload.get("tool_name") if isinstance(payload.get("tool_name"), str) else tool_name,
        message=message,
        target_identifier=(
            payload.get("target_identifier")
            if isinstance(payload.get("target_identifier"), str)
            else None
        ),
        details=dict(payload.get("details") or {}),
    )


def build_tool_bridge_config_from_env() -> ToolBridgeConfig:
    raw = os.environ.get("AUNIC_MCP_CONFIG_JSON")
    if not raw:
        raise RuntimeError("AUNIC_MCP_CONFIG_JSON is not set.")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError("AUNIC_MCP_CONFIG_JSON must decode to an object.")
    active_file = payload.get("active_file")
    mode = payload.get("mode")
    work_mode = payload.get("work_mode")
    metadata = payload.get("metadata") or {}
    if not isinstance(active_file, str) or not active_file.strip():
        raise RuntimeError("MCP config is missing active_file.")
    if mode not in {"note", "chat"}:
        raise RuntimeError("MCP config has an invalid mode.")
    if work_mode not in {"off", "read", "work"}:
        raise RuntimeError("MCP config has an invalid work_mode.")
    if not isinstance(metadata, dict):
        raise RuntimeError("MCP config metadata must be an object.")
    return ToolBridgeConfig(
        active_file=Path(active_file),
        mode=mode,
        work_mode=work_mode,
        metadata=dict(metadata),
    )


def _tool_error_result(tool_name: str, payload: dict[str, Any]) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=tool_name,
        status="tool_error",
        in_memory_content=payload,
        transcript_content=payload,
        tool_failure=failure_from_payload(payload, tool_name=tool_name),
    )


def _build_claude_tool_handler(
    bridge: AunicToolBridge,
    tool_name: str,
):
    async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        result = await bridge.execute_tool(tool_name, arguments)
        bridge.record_sdk_tool_execution(
            tool_name=tool_name,
            arguments=arguments,
            result=result,
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": model_visible_tool_text(result),
                }
            ],
            "is_error": result.status != "completed",
            STRUCTURED_RESULT_KEY: serialize_tool_execution_result(result),
        }

    return handler
