from __future__ import annotations

import re

_MCP_PREFIX = "mcp"
_INVALID_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def normalize_name_for_mcp(name: str) -> str:
    """Normalize MCP server/tool names to provider-safe identifier fragments."""
    normalized = _INVALID_NAME_CHARS.sub("_", name.strip())
    return normalized or "unnamed"


def build_mcp_tool_name(server_name: str, tool_name: str) -> str:
    return f"{_MCP_PREFIX}__{normalize_name_for_mcp(server_name)}__{normalize_name_for_mcp(tool_name)}"


def build_mcp_server_policy_name(server_name: str) -> str:
    return f"{_MCP_PREFIX}__{normalize_name_for_mcp(server_name)}"


def parse_mcp_tool_name(tool_name: str) -> tuple[str, str | None] | None:
    parts = tool_name.split("__")
    if len(parts) < 2 or parts[0] != _MCP_PREFIX:
        return None
    server_name = parts[1]
    if not server_name:
        return None
    if len(parts) == 2:
        return server_name, None
    return server_name, "__".join(parts[2:])
