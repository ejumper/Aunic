from __future__ import annotations

from pathlib import Path
from typing import Any

from aunic.domain import Message, ProviderRequest
from aunic.providers.sdk_tools import ToolBridgeConfig
from aunic.transcript.translation import compose_final_user_message


def build_tool_bridge_config(request: ProviderRequest) -> ToolBridgeConfig | None:
    if not request.tools:
        return None
    active_file = request.metadata.get("active_file")
    mode = request.metadata.get("mode")
    work_mode = request.metadata.get("work_mode")
    if not isinstance(active_file, str) or not active_file.strip():
        return None
    if mode not in {"note", "chat"}:
        return None
    if work_mode not in {"off", "read", "work"}:
        return None
    return ToolBridgeConfig(
        active_file=Path(active_file),
        mode=mode,
        work_mode=work_mode,
        metadata=dict(request.metadata),
    )


def build_turn_input_text(request: ProviderRequest) -> str:
    if request.transcript_messages is not None:
        return compose_final_user_message(request.note_snapshot or "", request.user_prompt or "")
    return render_messages_for_sdk(request.messages)


def render_messages_for_sdk(messages: list[Message]) -> str:
    rendered: list[str] = []
    for message in messages:
        name_suffix = f" ({message.name})" if message.name else ""
        rendered.append(f"[{message.role.upper()}{name_suffix}]")
        rendered.append(message.content)
    return "\n\n".join(rendered)


def coerce_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def normalize_reasoning_effort(
    reasoning_effort: str | None,
    *,
    model: str = "",
    cap_xhigh_for_haiku: bool = False,
) -> str:
    """Normalize a reasoning effort string to a provider-accepted value.

    - None / empty → "medium"
    - "minimal" → "low"
    - "xhigh" for haiku models (when cap_xhigh_for_haiku=True) → "high"
    """
    if not reasoning_effort:
        return "medium"
    if reasoning_effort == "minimal":
        return "low"
    if cap_xhigh_for_haiku and "haiku" in model.lower() and reasoning_effort == "xhigh":
        return "high"
    return reasoning_effort
