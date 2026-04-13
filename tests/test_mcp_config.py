from __future__ import annotations

import json
from pathlib import Path

from aunic.mcp.config import load_mcp_config, resolve_mcp_config_path
from aunic.mcp.names import build_mcp_tool_name, normalize_name_for_mcp, parse_mcp_tool_name


def test_mcp_config_resolves_nearest_ancestor(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    nested = repo / "notes" / "deep"
    config_dir = repo / ".aunic"
    nested.mkdir(parents=True)
    config_dir.mkdir()
    config_path = config_dir / "mcp.json"
    config_path.write_text('{"mcpServers": {}}', encoding="utf-8")

    assert resolve_mcp_config_path(nested) == config_path


def test_load_mcp_config_parses_stdio_http_and_sse(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_TOKEN", "secret")
    config_dir = tmp_path / ".aunic"
    config_dir.mkdir()
    (config_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "local files": {
                        "type": "stdio",
                        "command": "python",
                        "args": ["-m", "server"],
                        "env": {"TOKEN": "${MCP_TOKEN}"},
                        "cwd": "servers",
                    },
                    "remote-http": {
                        "type": "http",
                        "url": "https://example.com/mcp",
                        "headers": {"Authorization": "Bearer ${MCP_TOKEN}"},
                    },
                    "remote-sse": {
                        "type": "sse",
                        "url": "https://example.com/sse",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    result = load_mcp_config(tmp_path)

    assert result.errors == ()
    assert [server.transport for server in result.servers] == ["stdio", "http", "sse"]
    assert result.servers[0].normalized_name == "local_files"
    assert result.servers[0].env == {"TOKEN": "secret"}
    assert result.servers[0].cwd == (tmp_path / "servers").resolve()
    assert result.servers[1].headers == {"Authorization": "Bearer secret"}


def test_load_mcp_config_reports_missing_env_vars(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("MISSING_MCP_TOKEN", raising=False)
    config_dir = tmp_path / ".aunic"
    config_dir.mkdir()
    (config_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "remote": {
                        "type": "http",
                        "url": "https://example.com/${MISSING_MCP_TOKEN}",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = load_mcp_config(tmp_path)

    assert result.servers == ()
    assert len(result.errors) == 1
    assert "MISSING_MCP_TOKEN" in result.errors[0].message


def test_mcp_name_helpers_normalize_and_parse() -> None:
    assert normalize_name_for_mcp("github.com tools") == "github_com_tools"
    assert build_mcp_tool_name("github.com tools", "create issue") == "mcp__github_com_tools__create_issue"
    assert parse_mcp_tool_name("mcp__github_com_tools__create_issue") == (
        "github_com_tools",
        "create_issue",
    )
