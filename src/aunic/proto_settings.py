from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_VALID_POLICIES = {"allow", "ask", "deny"}
_VALID_EDITOR_SAVE_MODES = {"manual", "auto"}
_VALID_IMAGE_TRANSPORTS = {"claude_sdk_multimodal", "openai_chat_vision", "unsupported"}
_CACHE: dict[Path, tuple[int | None, dict[str, Any]]] = {}


@dataclass(frozen=True)
class OpenAICompatibleProfile:
    profile_id: str
    provider_label: str
    custom_model_name: str
    model: str
    base_url: str
    chat_endpoint: str
    api_key: str | None = None
    health_endpoint: str | None = None
    startup_script: Path | None = None
    headers: dict[str, str] = field(default_factory=dict)
    replay_reasoning_details: bool = False
    reasoning_replay_turns: int = 1
    context_window: int | None = None
    supports_images: bool = False
    image_transport: str = "unsupported"

    @property
    def display_label(self) -> str:
        return f"{self.provider_label} {self.custom_model_name}".strip()


def proto_settings_path(project_root: Path) -> Path:
    resolved = resolve_proto_settings_path(project_root)
    if resolved is not None:
        return resolved
    return _normalized_search_root(project_root) / ".aunic" / "proto-settings.json"


def resolve_proto_settings_path(project_root: Path) -> Path | None:
    search_root = _normalized_search_root(project_root)
    for ancestor in (search_root, *search_root.parents):
        candidate = ancestor / ".aunic" / "proto-settings.json"
        if candidate.exists():
            return candidate

    home_candidate = Path.home().expanduser().resolve() / ".aunic" / "proto-settings.json"
    if home_candidate.exists():
        return home_candidate
    return None


def get_tool_policy_override(project_root: Path, tool_name: str) -> str | None:
    overrides = _load_tool_policy_overrides(project_root)
    value = overrides.get(tool_name)
    if value in _VALID_POLICIES:
        return value
    if tool_name.startswith("mcp__"):
        prefix_parts = tool_name.split("__", 2)
        if len(prefix_parts) >= 2:
            server_value = overrides.get("__".join(prefix_parts[:2]))
            if server_value in _VALID_POLICIES:
                return server_value
    return None


def get_openai_compatible_profiles(project_root: Path) -> tuple[OpenAICompatibleProfile, ...]:
    payload = _load_proto_payload(project_root)
    raw_profiles = payload.get("openai_compatible_profiles", {})
    if not isinstance(raw_profiles, dict):
        return ()

    profiles: list[OpenAICompatibleProfile] = []
    for raw_profile_id, raw_profile in raw_profiles.items():
        if not isinstance(raw_profile_id, str) or not isinstance(raw_profile, dict):
            continue
        profile = _parse_openai_compatible_profile(raw_profile_id, raw_profile)
        if profile is not None:
            profiles.append(profile)
    return tuple(profiles)


def get_rag_config(project_root: Path) -> "RagConfig | None":
    """Return RagConfig from the proto-settings.json ``rag`` section, or None."""
    from aunic.rag.types import RagConfig, RagScope
    payload = _load_proto_payload(project_root)
    rag = payload.get("rag")
    if not isinstance(rag, dict):
        return None
    server = rag.get("server", "")
    if not isinstance(server, str) or not server.strip():
        return None
    raw_scopes = rag.get("scopes", [])
    if not isinstance(raw_scopes, list):
        raw_scopes = []
    scopes: list[RagScope] = []
    for entry in raw_scopes:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "")
        description = entry.get("description", "")
        if isinstance(name, str) and name.strip():
            scopes.append(RagScope(name=name.strip(), description=description or ""))

    raw_tui_scopes = rag.get("tui_scopes")
    tui_scopes: tuple[RagScope, ...] | None = None
    if isinstance(raw_tui_scopes, list):
        parsed: list[RagScope] = []
        for entry in raw_tui_scopes:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name", "")
            description = entry.get("description", "")
            if isinstance(name, str) and name.strip():
                parsed.append(RagScope(name=name.strip(), description=description or ""))
        tui_scopes = tuple(parsed)

    return RagConfig(server=server.strip(), scopes=tuple(scopes), tui_scopes=tui_scopes)


def get_selected_openai_compatible_profile_id(project_root: Path) -> str | None:
    payload = _load_proto_payload(project_root)
    selected = payload.get("selected_openai_compatible_profile")
    if isinstance(selected, str) and selected.strip():
        return selected.strip()
    return None


def get_editor_save_mode(project_root: Path) -> str:
    payload = _load_proto_payload(project_root)
    editor = payload.get("editor")
    if not isinstance(editor, dict):
        return "manual"
    raw_mode = editor.get("save_mode")
    if not isinstance(raw_mode, str):
        return "manual"
    mode = raw_mode.strip().lower()
    if mode in _VALID_EDITOR_SAVE_MODES:
        return mode
    return "manual"


def resolve_openai_compatible_profile(
    project_root: Path,
    *,
    profile_id: str | None = None,
) -> OpenAICompatibleProfile | None:
    profiles = get_openai_compatible_profiles(project_root)
    if not profiles:
        return None

    by_id = {profile.profile_id: profile for profile in profiles}
    if profile_id is not None:
        return by_id.get(profile_id)

    selected_id = get_selected_openai_compatible_profile_id(project_root)
    if selected_id is not None and selected_id in by_id:
        return by_id[selected_id]
    return profiles[0]


def _load_tool_policy_overrides(project_root: Path) -> dict[str, str]:
    payload = _load_proto_payload(project_root)
    raw_overrides = payload.get("tool_policy_overrides", {})
    if not isinstance(raw_overrides, dict):
        return {}

    normalized: dict[str, str] = {}
    for key, value in raw_overrides.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        policy = value.strip().lower()
        if policy in _VALID_POLICIES:
            normalized[key.strip()] = policy
    return normalized


def _load_proto_payload(project_root: Path) -> dict[str, Any]:
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

    if not isinstance(payload, dict):
        payload = {}

    _CACHE[path] = (stat.st_mtime_ns, payload)
    return payload


def _normalized_search_root(project_root: Path) -> Path:
    resolved = project_root.expanduser().resolve()
    if resolved.exists() and resolved.is_file():
        return resolved.parent
    if not resolved.exists() and resolved.suffix:
        return resolved.parent
    return resolved


def _parse_openai_compatible_profile(
    profile_id: str,
    payload: dict[str, Any],
) -> OpenAICompatibleProfile | None:
    provider_label = _get_non_empty_string(payload, "provider_label")
    custom_model_name = _get_non_empty_string(payload, "custom_model_name")
    model = _get_non_empty_string(payload, "model")
    base_url = _get_non_empty_string(payload, "base_url")
    chat_endpoint = _get_non_empty_string(payload, "chat_endpoint")
    if (
        provider_label is None
        or custom_model_name is None
        or model is None
        or base_url is None
        or chat_endpoint is None
    ):
        return None

    api_key = _get_non_empty_string(payload, "api_key")
    health_endpoint = _get_non_empty_string(payload, "health_endpoint")

    startup_script: Path | None = None
    startup_script_text = _get_non_empty_string(payload, "startup_script")
    if startup_script_text is not None:
        startup_script = Path(startup_script_text).expanduser()

    headers: dict[str, str] = {}
    raw_headers = payload.get("headers", {})
    if isinstance(raw_headers, dict):
        for key, value in raw_headers.items():
            if isinstance(key, str) and isinstance(value, str) and key.strip():
                headers[key.strip()] = value

    replay_reasoning_details = bool(payload.get("replay_reasoning_details", False))
    reasoning_replay_turns = payload.get("reasoning_replay_turns", 1)
    if not isinstance(reasoning_replay_turns, int) or reasoning_replay_turns < 1:
        reasoning_replay_turns = 1

    context_window = payload.get("context_window")
    if not isinstance(context_window, int) or context_window <= 0:
        context_window = None

    supports_images = bool(payload.get("supports_images", False))
    raw_image_transport = payload.get("image_transport")
    if isinstance(raw_image_transport, str):
        image_transport = raw_image_transport.strip()
    else:
        image_transport = "unsupported"
    if image_transport not in _VALID_IMAGE_TRANSPORTS:
        image_transport = "unsupported"

    return OpenAICompatibleProfile(
        profile_id=profile_id,
        provider_label=provider_label,
        custom_model_name=custom_model_name,
        model=model,
        base_url=base_url.rstrip("/"),
        chat_endpoint=_normalize_endpoint(chat_endpoint),
        api_key=api_key,
        health_endpoint=_normalize_endpoint(health_endpoint) if health_endpoint else None,
        startup_script=startup_script,
        headers=headers,
        replay_reasoning_details=replay_reasoning_details,
        reasoning_replay_turns=reasoning_replay_turns,
        context_window=context_window,
        supports_images=supports_images,
        image_transport=image_transport,
    )


def _get_non_empty_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_endpoint(value: str) -> str:
    if value.startswith("/"):
        return value
    return f"/{value}"
