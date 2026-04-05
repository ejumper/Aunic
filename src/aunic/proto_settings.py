from __future__ import annotations

import json
from pathlib import Path

_VALID_POLICIES = {"allow", "ask", "deny"}
_CACHE: dict[Path, tuple[int | None, dict[str, str]]] = {}


def proto_settings_path(project_root: Path) -> Path:
    return project_root.expanduser().resolve() / ".aunic" / "proto-settings.json"


def get_tool_policy_override(project_root: Path, tool_name: str) -> str | None:
    overrides = _load_tool_policy_overrides(project_root)
    value = overrides.get(tool_name)
    if value in _VALID_POLICIES:
        return value
    return None


def _load_tool_policy_overrides(project_root: Path) -> dict[str, str]:
    path = proto_settings_path(project_root)
    try:
        stat = path.stat()
    except FileNotFoundError:
        cached = _CACHE.get(path)
        if cached is not None and cached[0] is None:
            return cached[1]
        _CACHE[path] = (None, {})
        return {}
    except OSError:
        return {}

    cached = _CACHE.get(path)
    if cached is not None and cached[0] == stat.st_mtime_ns:
        return cached[1]

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _CACHE[path] = (stat.st_mtime_ns, {})
        return {}

    raw_overrides = payload.get("tool_policy_overrides", {})
    if not isinstance(raw_overrides, dict):
        _CACHE[path] = (stat.st_mtime_ns, {})
        return {}

    normalized: dict[str, str] = {}
    for key, value in raw_overrides.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        policy = value.strip().lower()
        if policy in _VALID_POLICIES:
            normalized[key.strip()] = policy

    _CACHE[path] = (stat.st_mtime_ns, normalized)
    return normalized
