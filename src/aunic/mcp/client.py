from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp import ClientSession, types
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client

from aunic.config import SETTINGS
from aunic.mcp.config import MCPConfigError, MCPServerConfig
from aunic.mcp.names import build_mcp_tool_name, normalize_name_for_mcp


@dataclass(frozen=True)
class MCPListedTool:
    server_name: str
    normalized_server_name: str
    original_tool_name: str
    normalized_tool_name: str
    fully_qualified_name: str
    description: str
    input_schema: dict[str, Any]
    transport: str


@dataclass
class MCPServerConnection:
    config: MCPServerConfig
    session: ClientSession
    stack: AsyncExitStack
    capabilities: types.ServerCapabilities | None = None
    server_info: types.Implementation | None = None


class MCPClientManager:
    def __init__(
        self,
        servers: tuple[MCPServerConfig, ...],
        *,
        cwd: Path,
    ) -> None:
        self._servers = servers
        self._cwd = cwd.expanduser().resolve()
        self._connections: dict[str, MCPServerConnection] = {}

    async def discover_tools(self) -> tuple[tuple[MCPListedTool, ...], tuple[MCPConfigError, ...]]:
        tools: list[MCPListedTool] = []
        errors: list[MCPConfigError] = []
        seen_names: set[str] = set()

        for config in self._servers:
            if config.disabled:
                continue
            if config.normalized_name in self._connections:
                errors.append(
                    MCPConfigError(
                        f"Duplicate normalized MCP server name {config.normalized_name!r}.",
                        server_name=config.name,
                        path=config.source_path,
                    )
                )
                continue
            try:
                connection = await self._connect(config)
            except Exception as exc:
                errors.append(
                    MCPConfigError(
                        f"MCP server failed to connect: {exc}",
                        server_name=config.name,
                        path=config.source_path,
                    )
                )
                continue
            self._connections[config.normalized_name] = connection

            try:
                listed_tools = await self._list_tools(connection)
            except Exception as exc:
                errors.append(
                    MCPConfigError(
                        f"MCP server failed to list tools: {exc}",
                        server_name=config.name,
                        path=config.source_path,
                    )
                )
                continue

            for listed_tool in listed_tools:
                if listed_tool.fully_qualified_name in seen_names:
                    errors.append(
                        MCPConfigError(
                            f"Duplicate MCP tool name {listed_tool.fully_qualified_name!r}.",
                            server_name=config.name,
                            path=config.source_path,
                        )
                    )
                    continue
                seen_names.add(listed_tool.fully_qualified_name)
                tools.append(listed_tool)

        return tuple(tools), tuple(errors)

    async def call_tool(
        self,
        listed_tool: MCPListedTool,
        arguments: dict[str, Any],
    ) -> types.CallToolResult:
        connection = self._connections.get(listed_tool.normalized_server_name)
        if connection is None:
            raise RuntimeError(f"MCP server {listed_tool.server_name!r} is not connected.")
        return await connection.session.call_tool(
            listed_tool.original_tool_name,
            arguments,
            read_timeout_seconds=timedelta(seconds=SETTINGS.mcp.tool_timeout_seconds),
        )

    async def aclose(self) -> None:
        connections = list(self._connections.values())
        self._connections.clear()
        for connection in reversed(connections):
            try:
                await connection.stack.aclose()
            except Exception:
                pass

    async def _connect(self, config: MCPServerConfig) -> MCPServerConnection:
        stack = AsyncExitStack()
        try:
            if config.transport == "stdio":
                errlog = stack.enter_context(open(os.devnull, "w", encoding="utf-8"))
                read_stream, write_stream = await stack.enter_async_context(
                    stdio_client(
                        StdioServerParameters(
                            command=config.command or "",
                            args=list(config.args),
                            env=dict(config.env) if config.env else None,
                            cwd=config.cwd,
                        ),
                        errlog=errlog,
                    )
                )
            elif config.transport == "http":
                read_stream, write_stream, _session_id = await stack.enter_async_context(
                    streamablehttp_client(
                        config.url or "",
                        headers=dict(config.headers) or None,
                        timeout=SETTINGS.mcp.connect_timeout_seconds,
                        sse_read_timeout=SETTINGS.mcp.sse_read_timeout_seconds,
                    )
                )
            else:
                read_stream, write_stream = await stack.enter_async_context(
                    sse_client(
                        config.url or "",
                        headers=dict(config.headers) or None,
                        timeout=SETTINGS.mcp.connect_timeout_seconds,
                        sse_read_timeout=SETTINGS.mcp.sse_read_timeout_seconds,
                    )
                )

            session = ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=SETTINGS.mcp.request_timeout_seconds),
                list_roots_callback=self._list_roots,
                client_info=types.Implementation(
                    name="aunic",
                    title="Aunic",
                    version="0.1.0",
                ),
            )
            await stack.enter_async_context(session)
            initialize_result = await asyncio.wait_for(
                session.initialize(),
                timeout=SETTINGS.mcp.connect_timeout_seconds,
            )
            return MCPServerConnection(
                config=config,
                session=session,
                stack=stack,
                capabilities=initialize_result.capabilities,
                server_info=initialize_result.serverInfo,
            )
        except Exception:
            await stack.aclose()
            raise

    async def _list_tools(self, connection: MCPServerConnection) -> tuple[MCPListedTool, ...]:
        if connection.capabilities is not None and connection.capabilities.tools is None:
            return ()

        listed: list[MCPListedTool] = []
        cursor: str | None = None
        while True:
            result = await connection.session.list_tools(cursor)
            for tool in result.tools:
                original_name = tool.name
                normalized_tool_name = normalize_name_for_mcp(original_name)
                fully_qualified_name = build_mcp_tool_name(connection.config.name, original_name)
                listed.append(
                    MCPListedTool(
                        server_name=connection.config.name,
                        normalized_server_name=connection.config.normalized_name,
                        original_tool_name=original_name,
                        normalized_tool_name=normalized_tool_name,
                        fully_qualified_name=fully_qualified_name,
                        description=_truncate_description(tool.description or ""),
                        input_schema=_coerce_input_schema(tool.inputSchema),
                        transport=connection.config.transport,
                    )
                )
            cursor = result.nextCursor
            if not cursor:
                return tuple(listed)

    async def _list_roots(self, _context: Any) -> types.ListRootsResult:
        return types.ListRootsResult(
            roots=[
                types.Root(
                    uri=f"file://{self._cwd}",
                    name="workspace",
                )
            ]
        )


def _truncate_description(description: str) -> str:
    limit = SETTINGS.mcp.max_description_chars
    if len(description) <= limit:
        return description
    return description[:limit] + "... [truncated]"


def _coerce_input_schema(schema: Any) -> dict[str, Any]:
    if isinstance(schema, dict) and schema.get("type") == "object":
        return dict(schema)
    if isinstance(schema, dict) and "properties" in schema:
        coerced = dict(schema)
        coerced.setdefault("type", "object")
        return coerced
    return {"type": "object", "additionalProperties": True, "properties": {}}
