from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import types

from aunic.mcp.client import MCPListedTool
from aunic.mcp.tools import build_mcp_tool_registry, merge_tool_registries
from aunic.tools.base import ToolDefinition
from aunic.tools.note_edit import build_note_only_registry
from aunic.tools.runtime import PermissionDecision


class _AllowRuntime:
    cwd: Path

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self.permission_requests = []

    async def resolve_permission(self, request):
        self.permission_requests.append(request)
        return PermissionDecision(True, "once", "user_allow")


class _DenyRuntime:
    cwd: Path

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd

    async def resolve_permission(self, request):
        return PermissionDecision(False, "reject", "user_reject")


def _fake_server_script(path: Path) -> None:
    path.write_text(
        """
from __future__ import annotations

import asyncio
from typing import Any

from mcp import types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.stdio import stdio_server


async def main() -> None:
    server: Server[Any, Any] = Server("fake")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="echo text",
                description="Echo text back.",
                inputSchema={
                    "type": "object",
                    "required": ["text"],
                    "properties": {"text": {"type": "string"}},
                },
            )
        ]

    @server.call_tool(validate_input=True)
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        text = str((arguments or {}).get("text", ""))
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(
                notification_options=NotificationOptions(),
                experimental_capabilities={},
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
""".lstrip(),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_build_mcp_tool_registry_discovers_and_executes_stdio_tool(tmp_path: Path) -> None:
    script = tmp_path / "fake_mcp_server.py"
    _fake_server_script(script)
    config_dir = tmp_path / ".aunic"
    config_dir.mkdir()
    (config_dir / "mcp.json").write_text(
        (
            "{\n"
            '  "mcpServers": {\n'
            '    "fake server": {\n'
            '      "type": "stdio",\n'
            f'      "command": {sys.executable!r},\n'
            f'      "args": [{str(script)!r}]\n'
            "    }\n"
            "  }\n"
            "}\n"
        ).replace("'", '"'),
        encoding="utf-8",
    )

    registry = await build_mcp_tool_registry(tmp_path)
    try:
        assert registry.errors == ()
        assert [tool.spec.name for tool in registry.tools] == ["mcp__fake_server__echo_text"]
        definition = registry.tools[0]
        result = await definition.execute(_AllowRuntime(tmp_path), {"text": "hello"})

        assert result.status == "completed"
        assert result.in_memory_content["content"] == "hello"
        assert result.in_memory_content["server"] == "fake server"
        assert result.in_memory_content["tool"] == "echo text"
    finally:
        await registry.aclose()


@pytest.mark.asyncio
async def test_server_wide_deny_policy_skips_mcp_server_startup(tmp_path: Path) -> None:
    config_dir = tmp_path / ".aunic"
    config_dir.mkdir()
    (config_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "blocked server": {
                        "type": "stdio",
                        "command": "definitely-not-a-real-mcp-server",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (config_dir / "proto-settings.json").write_text(
        json.dumps({"tool_policy_overrides": {"mcp__blocked_server": "deny"}}),
        encoding="utf-8",
    )

    registry = await build_mcp_tool_registry(tmp_path)
    try:
        assert registry.tools == ()
        assert registry.errors == ()
    finally:
        await registry.aclose()


@pytest.mark.asyncio
async def test_mcp_tool_permission_denial_returns_tool_error(tmp_path: Path) -> None:
    class FakeManager:
        async def call_tool(self, listed_tool: MCPListedTool, arguments: dict[str, Any]):
            raise AssertionError("permission denial should happen before call_tool")

    from aunic.mcp.tools import _build_tool_definition

    listed = MCPListedTool(
        server_name="server",
        normalized_server_name="server",
        original_tool_name="tool",
        normalized_tool_name="tool",
        fully_qualified_name="mcp__server__tool",
        description="Tool.",
        input_schema={"type": "object"},
        transport="stdio",
    )
    definition = _build_tool_definition(FakeManager(), listed)  # type: ignore[arg-type]

    result = await definition.execute(_DenyRuntime(tmp_path), {})

    assert result.status == "tool_error"
    assert result.tool_failure is not None
    assert result.tool_failure.category == "permission_denied"


def test_merge_tool_registries_keeps_builtin_on_name_conflict() -> None:
    base = build_note_only_registry()
    duplicate = ToolDefinition(
        spec=base[0].spec,
        parse_arguments=lambda payload: payload,
        execute=lambda runtime, args: None,  # type: ignore[arg-type]
    )

    merged = merge_tool_registries(base, (duplicate,))

    assert merged == base
