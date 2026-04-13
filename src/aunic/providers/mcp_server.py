from __future__ import annotations

import asyncio
from typing import Any

from mcp import types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.stdio import stdio_server

from aunic.providers.sdk_tools import (
    AunicToolBridge,
    build_mcp_tool_definition,
    build_tool_bridge_config_from_env,
)


async def _build_server() -> tuple[Server[Any, Any], AunicToolBridge]:
    config = build_tool_bridge_config_from_env()
    bridge = AunicToolBridge(config)
    await bridge.start()

    server: Server[Any, Any] = Server("aunic")

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [build_mcp_tool_definition(definition) for definition in bridge.registry]

    @server.call_tool(validate_input=True)
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        result = await bridge.execute_tool(name, arguments)
        return bridge.build_codex_call_result(result)

    return server, bridge


async def _main_async() -> None:
    server, bridge = await _build_server()
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            )
    finally:
        await bridge.aclose()


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
