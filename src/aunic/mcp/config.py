from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from aunic.mcp.names import normalize_name_for_mcp

MCPTransport = Literal["stdio", "http", "sse"]
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class MCPConfigError:
    message: str
    server_name: str | None = None
    path: Path | None = None


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    normalized_name: str
    transport: MCPTransport
    source_path: Path
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    cwd: Path | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    disabled: bool = False


@dataclass(frozen=True)
class MCPConfigLoadResult:
    path: Path | None
    servers: tuple[MCPServerConfig, ...]
    errors: tuple[MCPConfigError, ...] = ()


def resolve_mcp_config_path(project_root: Path) -> Path | None:
    search_root = _normalized_search_root(project_root)
    for ancestor in (search_root, *search_root.parents):
        candidate = ancestor / ".aunic" / "mcp.json"
        if candidate.exists():
            return candidate

    home_candidate = Path.home().expanduser().resolve() / ".aunic" / "mcp.json"
    if home_candidate.exists():
        return home_candidate
    return None


def load_mcp_config(project_root: Path) -> MCPConfigLoadResult:
    path = resolve_mcp_config_path(project_root)
    if path is None:
        return MCPConfigLoadResult(path=None, servers=())

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return MCPConfigLoadResult(
            path=path,
            servers=(),
            errors=(MCPConfigError(f"MCP config is not valid JSON: {exc}", path=path),),
        )
    except OSError as exc:
        return MCPConfigLoadResult(
            path=path,
            servers=(),
            errors=(MCPConfigError(f"MCP config could not be read: {exc}", path=path),),
        )

    if not isinstance(payload, dict):
        return MCPConfigLoadResult(
            path=path,
            servers=(),
            errors=(MCPConfigError("MCP config must be a JSON object.", path=path),),
        )

    raw_servers = payload.get("mcpServers", {})
    if not isinstance(raw_servers, dict):
        return MCPConfigLoadResult(
            path=path,
            servers=(),
            errors=(MCPConfigError("MCP config field `mcpServers` must be an object.", path=path),),
        )

    servers: list[MCPServerConfig] = []
    errors: list[MCPConfigError] = []
    for name, raw_config in raw_servers.items():
        if not isinstance(name, str) or not name.strip():
            errors.append(MCPConfigError("MCP server names must be non-empty strings.", path=path))
            continue
        if not isinstance(raw_config, dict):
            errors.append(
                MCPConfigError("MCP server config must be an object.", server_name=name, path=path)
            )
            continue
        parsed, server_errors = _parse_server_config(name.strip(), raw_config, path)
        errors.extend(server_errors)
        if parsed is not None:
            servers.append(parsed)

    return MCPConfigLoadResult(path=path, servers=tuple(servers), errors=tuple(errors))


def _parse_server_config(
    name: str,
    payload: dict[str, Any],
    source_path: Path,
) -> tuple[MCPServerConfig | None, list[MCPConfigError]]:
    errors: list[MCPConfigError] = []
    disabled = payload.get("disabled") is True or payload.get("enabled") is False
    transport = payload.get("type")
    if transport is None:
        transport = "stdio" if "command" in payload else "http" if "url" in payload else None
    if transport not in {"stdio", "http", "sse"}:
        return None, [
            MCPConfigError(
                "MCP server `type` must be one of: stdio, http, sse.",
                server_name=name,
                path=source_path,
            )
        ]

    base_dir = _config_base_dir(source_path)
    normalized_name = normalize_name_for_mcp(name)

    if transport == "stdio":
        command, command_missing = _expanded_string(payload.get("command"))
        args, args_missing, args_error = _expanded_string_list(payload.get("args", []))
        env, env_missing, env_error = _expanded_string_map(payload.get("env", {}), field_name="env")
        cwd, cwd_missing, cwd_error = _expanded_optional_path(payload.get("cwd"), base_dir=base_dir)
        missing = sorted(set(command_missing + args_missing + env_missing + cwd_missing))
        if command is None:
            errors.append(MCPConfigError("stdio MCP server requires `command`.", server_name=name, path=source_path))
        if args_error:
            errors.append(MCPConfigError(args_error, server_name=name, path=source_path))
        if env_error:
            errors.append(MCPConfigError(env_error, server_name=name, path=source_path))
        if cwd_error:
            errors.append(MCPConfigError(cwd_error, server_name=name, path=source_path))
        if missing:
            errors.append(
                MCPConfigError(
                    f"MCP server references unset environment variables: {', '.join(missing)}.",
                    server_name=name,
                    path=source_path,
                )
            )
        if errors:
            return None, errors
        return MCPServerConfig(
            name=name,
            normalized_name=normalized_name,
            transport="stdio",
            source_path=source_path,
            command=command,
            args=tuple(args),
            env=env,
            cwd=cwd,
            disabled=disabled,
        ), []

    url, url_missing = _expanded_string(payload.get("url"))
    headers, headers_missing, headers_error = _expanded_string_map(payload.get("headers", {}), field_name="headers")
    missing = sorted(set(url_missing + headers_missing))
    if url is None:
        errors.append(MCPConfigError(f"{transport} MCP server requires `url`.", server_name=name, path=source_path))
    if headers_error:
        errors.append(MCPConfigError(headers_error, server_name=name, path=source_path))
    if missing:
        errors.append(
            MCPConfigError(
                f"MCP server references unset environment variables: {', '.join(missing)}.",
                server_name=name,
                path=source_path,
            )
        )
    if errors:
        return None, errors
    return MCPServerConfig(
        name=name,
        normalized_name=normalized_name,
        transport=transport,  # type: ignore[arg-type]
        source_path=source_path,
        url=url,
        headers=headers,
        disabled=disabled,
    ), []


def _expanded_string(value: Any) -> tuple[str | None, list[str]]:
    if not isinstance(value, str) or not value.strip():
        return None, []
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in os.environ:
            missing.append(name)
            return match.group(0)
        return os.environ[name]

    return _ENV_PATTERN.sub(replace, value), missing


def _expanded_string_list(value: Any) -> tuple[list[str], list[str], str | None]:
    if value is None:
        return [], [], None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return [], [], "`args` must be an array of strings."
    expanded: list[str] = []
    missing: list[str] = []
    for item in value:
        text, item_missing = _expanded_string(item)
        expanded.append(text or "")
        missing.extend(item_missing)
    return expanded, missing, None


def _expanded_string_map(value: Any, *, field_name: str) -> tuple[dict[str, str], list[str], str | None]:
    if value is None:
        return {}, [], None
    if not isinstance(value, dict):
        return {}, [], f"`{field_name}` must be an object of string values."
    expanded: dict[str, str] = {}
    missing: list[str] = []
    for key, raw_value in value.items():
        if not isinstance(key, str) or not isinstance(raw_value, str):
            return {}, [], f"`{field_name}` must be an object of string values."
        text, value_missing = _expanded_string(raw_value)
        expanded[key] = text or ""
        missing.extend(value_missing)
    return expanded, missing, None


def _expanded_optional_path(value: Any, *, base_dir: Path) -> tuple[Path | None, list[str], str | None]:
    if value is None:
        return None, [], None
    text, missing = _expanded_string(value)
    if text is None:
        return None, missing, "`cwd` must be a non-empty string when provided."
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve(), missing, None


def _config_base_dir(path: Path) -> Path:
    if path.parent.name == ".aunic":
        return path.parent.parent
    return path.parent


def _normalized_search_root(project_root: Path) -> Path:
    resolved = project_root.expanduser().resolve()
    if resolved.exists() and resolved.is_file():
        return resolved.parent
    if not resolved.exists() and resolved.suffix:
        return resolved.parent
    return resolved
