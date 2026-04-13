from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import types

from aunic.mcp.client import MCPClientManager, MCPListedTool
from aunic.mcp.config import MCPConfigError, load_mcp_config
from aunic.mcp.names import build_mcp_server_policy_name
from aunic.proto_settings import get_tool_policy_override
from aunic.tools.base import ToolDefinition, ToolExecutionResult
from aunic.tools.runtime import (
    PermissionRequest,
    RunToolContext,
    failure_from_payload,
    failure_payload,
)


@dataclass
class MCPToolRegistry:
    tools: tuple[ToolDefinition[Any], ...]
    errors: tuple[MCPConfigError, ...]
    manager: MCPClientManager

    async def aclose(self) -> None:
        await self.manager.aclose()


async def build_mcp_tool_registry(cwd: Path) -> MCPToolRegistry:
    config = load_mcp_config(cwd)
    servers = tuple(
        server
        for server in config.servers
        if get_tool_policy_override(cwd, build_mcp_server_policy_name(server.name)) != "deny"
    )
    manager = MCPClientManager(servers, cwd=cwd)
    listed_tools, discovery_errors = await manager.discover_tools()
    errors = (*config.errors, *discovery_errors)
    definitions = tuple(
        _build_tool_definition(manager, listed_tool)
        for listed_tool in listed_tools
        if get_tool_policy_override(cwd, listed_tool.fully_qualified_name) != "deny"
    )
    return MCPToolRegistry(tools=definitions, errors=errors, manager=manager)


def merge_tool_registries(
    base_registry: tuple[ToolDefinition[Any], ...],
    extra_registry: tuple[ToolDefinition[Any], ...],
) -> tuple[ToolDefinition[Any], ...]:
    seen = {definition.spec.name for definition in base_registry}
    merged = list(base_registry)
    for definition in extra_registry:
        if definition.spec.name in seen:
            continue
        seen.add(definition.spec.name)
        merged.append(definition)
    return tuple(merged)


def _build_tool_definition(
    manager: MCPClientManager,
    listed_tool: MCPListedTool,
) -> ToolDefinition[dict[str, Any]]:
    async def execute(runtime: RunToolContext, args: dict[str, Any]) -> ToolExecutionResult:
        return await _execute_mcp_tool(manager, listed_tool, runtime, args)

    return ToolDefinition(
        spec=types_to_tool_spec(listed_tool),
        parse_arguments=_parse_mcp_arguments,
        execute=execute,
        persistence="persistent",
    )


def types_to_tool_spec(listed_tool: MCPListedTool):
    from aunic.domain import ToolSpec

    return ToolSpec(
        name=listed_tool.fully_qualified_name,
        description=listed_tool.description,
        input_schema=dict(listed_tool.input_schema),
    )


def _parse_mcp_arguments(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("MCP tool arguments must be an object.")
    return dict(payload)


async def _execute_mcp_tool(
    manager: MCPClientManager,
    listed_tool: MCPListedTool,
    runtime: RunToolContext,
    args: dict[str, Any],
) -> ToolExecutionResult:
    decision = await runtime.resolve_permission(
        PermissionRequest(
            tool_name=listed_tool.fully_qualified_name,
            action="execute",
            target=f"{listed_tool.server_name}.{listed_tool.original_tool_name}",
            message=(
                f"MCP server {listed_tool.server_name!r} wants to run tool "
                f"{listed_tool.original_tool_name!r}."
            ),
            policy="ask",
            key=f"{listed_tool.fully_qualified_name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}",
            details={
                "server_name": listed_tool.server_name,
                "normalized_server_name": listed_tool.normalized_server_name,
                "tool_name": listed_tool.original_tool_name,
                "normalized_tool_name": listed_tool.normalized_tool_name,
                "fully_qualified_name": listed_tool.fully_qualified_name,
                "arguments": dict(args),
                "transport": listed_tool.transport,
            },
        )
    )
    if not decision.allowed:
        payload = failure_payload(
            category="permission_denied",
            reason=decision.reason if decision.reason != "policy" else "deny_rule",
            message=f"MCP tool {listed_tool.fully_qualified_name} was not allowed to run.",
            server_name=listed_tool.server_name,
            tool_name=listed_tool.original_tool_name,
            arguments=dict(args),
        )
        return _tool_error_result(listed_tool.fully_qualified_name, payload)

    started_at = time.monotonic()
    try:
        result = await manager.call_tool(listed_tool, args)
    except TimeoutError as exc:
        payload = failure_payload(
            category="timeout",
            reason="mcp_tool_timeout",
            message=str(exc) or f"MCP tool {listed_tool.fully_qualified_name} timed out.",
            server_name=listed_tool.server_name,
            tool_name=listed_tool.original_tool_name,
            arguments=dict(args),
        )
        return _tool_error_result(listed_tool.fully_qualified_name, payload)
    except Exception as exc:
        payload = failure_payload(
            category="execution_error",
            reason="mcp_tool_call_failed",
            message=str(exc) or f"MCP tool {listed_tool.fully_qualified_name} failed.",
            server_name=listed_tool.server_name,
            tool_name=listed_tool.original_tool_name,
            arguments=dict(args),
        )
        return _tool_error_result(listed_tool.fully_qualified_name, payload)

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    payload = _mcp_result_payload(listed_tool, result, elapsed_ms=elapsed_ms)
    metadata = {
        "server_name": listed_tool.server_name,
        "normalized_server_name": listed_tool.normalized_server_name,
        "tool_name": listed_tool.original_tool_name,
        "normalized_tool_name": listed_tool.normalized_tool_name,
        "transport": listed_tool.transport,
        "elapsed_ms": elapsed_ms,
    }
    if result.isError:
        error_payload = {
            **payload,
            **failure_payload(
                category="execution_error",
                reason="mcp_tool_error",
                message=payload.get("content") or "MCP tool returned an error.",
                server_name=listed_tool.server_name,
                tool_name=listed_tool.original_tool_name,
            ),
        }
        return ToolExecutionResult(
            tool_name=listed_tool.fully_qualified_name,
            status="tool_error",
            in_memory_content=error_payload,
            transcript_content=error_payload,
            tool_failure=failure_from_payload(error_payload, tool_name=listed_tool.fully_qualified_name),
            metadata=metadata,
        )

    return ToolExecutionResult(
        tool_name=listed_tool.fully_qualified_name,
        status="completed",
        in_memory_content=payload,
        transcript_content=payload,
        metadata=metadata,
    )


def _mcp_result_payload(
    listed_tool: MCPListedTool,
    result: types.CallToolResult,
    *,
    elapsed_ms: int,
) -> dict[str, Any]:
    content_text = _content_blocks_to_text(result.content)
    payload: dict[str, Any] = {
        "type": "mcp_tool_result",
        "server": listed_tool.server_name,
        "tool": listed_tool.original_tool_name,
        "name": listed_tool.fully_qualified_name,
        "content": content_text,
        "elapsed_ms": elapsed_ms,
    }
    if result.structuredContent is not None:
        payload["structured_content"] = result.structuredContent
    if result.meta:
        payload["_meta"] = result.meta
    return payload


def _content_blocks_to_text(content: list[Any]) -> str:
    parts = [_content_block_to_text(item) for item in content]
    rendered = "\n\n".join(part for part in parts if part.strip())
    return rendered or "(empty MCP result)"


def _content_block_to_text(item: Any) -> str:
    item_type = getattr(item, "type", None)
    if item_type == "text":
        return str(getattr(item, "text", ""))
    if item_type == "image":
        mime_type = getattr(item, "mimeType", "unknown")
        data = str(getattr(item, "data", ""))
        return f"[Image content: {mime_type}, {len(data)} base64 characters]"
    if item_type == "audio":
        mime_type = getattr(item, "mimeType", "unknown")
        data = str(getattr(item, "data", ""))
        return f"[Audio content: {mime_type}, {len(data)} base64 characters]"
    if item_type == "resource":
        resource = getattr(item, "resource", None)
        uri = getattr(resource, "uri", None)
        if hasattr(resource, "text"):
            prefix = f"[Resource: {uri}]\n" if uri else "[Resource]\n"
            return prefix + str(getattr(resource, "text", ""))
        blob = str(getattr(resource, "blob", ""))
        mime_type = getattr(resource, "mimeType", "unknown")
        target = f" at {uri}" if uri else ""
        return f"[Binary resource{target}: {mime_type}, {len(blob)} base64 characters]"
    if item_type == "resource_link":
        uri = getattr(item, "uri", "")
        name = getattr(item, "name", "")
        description = getattr(item, "description", None)
        suffix = f" ({description})" if description else ""
        return f"[Resource link: {name}] {uri}{suffix}".strip()
    if hasattr(item, "model_dump"):
        return json.dumps(item.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
    return str(item)


def _tool_error_result(tool_name: str, payload: dict[str, Any]) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=tool_name,
        status="tool_error",
        in_memory_content=payload,
        transcript_content=payload,
        tool_failure=failure_from_payload(payload, tool_name=tool_name),
    )
