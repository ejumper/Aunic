from __future__ import annotations

from typing import Any

from aunic.domain import Message, ProviderRequest


def build_llama_native_messages(request: ProviderRequest) -> list[dict[str, str]]:
    system_parts = [
        "You are the model execution layer for Aunic.",
        "Aunic owns orchestration, context assembly, and tool execution.",
        "If you need a tool, use the server's native tool calling interface.",
        "Do not serialize tool calls as fenced text blocks or custom JSON envelopes.",
    ]
    if request.system_prompt:
        system_parts.append(f"Additional system guidance:\n{request.system_prompt}")
    messages = [{"role": "system", "content": "\n\n".join(system_parts)}]
    for message in request.messages:
        messages.append({"role": message.role, "content": message.content})
    return messages


def build_plain_llama_messages(request: ProviderRequest) -> list[dict[str, str]]:
    system_parts = [
        "You are the model execution layer for Aunic.",
        "No tools are available for this request.",
        "Reply in plain text only.",
        "Do not return JSON or code fences.",
    ]
    if request.system_prompt:
        system_parts.append(f"Additional system guidance:\n{request.system_prompt}")
    messages = [{"role": "system", "content": "\n\n".join(system_parts)}]
    for message in request.messages:
        messages.append({"role": message.role, "content": message.content})
    return messages


def build_llama_structured_messages(
    translated_messages: list[dict[str, Any]],
    system_prompt: str | None,
) -> list[dict[str, Any]]:
    system_parts = [
        "You are the model execution layer for Aunic.",
        "Aunic owns orchestration, context assembly, and tool execution.",
        "If you need a tool, use the server's native tool calling interface.",
        "Do not serialize tool calls as fenced text blocks or custom JSON envelopes.",
    ]
    if system_prompt:
        system_parts.append(f"Additional system guidance:\n{system_prompt}")

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "\n\n".join(system_parts)}
    ]
    messages.extend(translated_messages)
    return messages


def render_conversation(messages: list[Message]) -> str:
    rendered: list[str] = []
    for message in messages:
        role = message.role.upper()
        name_suffix = f" ({message.name})" if message.name else ""
        rendered.append(f"[{role}{name_suffix}]\n{message.content}")
    return "\n\n".join(rendered)
