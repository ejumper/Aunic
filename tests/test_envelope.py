from __future__ import annotations

from aunic.domain import Message, ProviderRequest
from aunic.providers.envelope import (
    build_llama_native_messages,
    build_llama_structured_messages,
    render_conversation,
)


def test_build_llama_structured_messages_preserves_translated_messages() -> None:
    translated = [{"role": "user", "content": "hello"}]

    messages = build_llama_structured_messages(translated, "extra guidance")

    assert messages[0]["role"] == "system"
    assert "Additional system guidance" in messages[0]["content"]
    assert messages[1:] == translated


def test_build_llama_native_messages_keeps_user_messages() -> None:
    request = ProviderRequest(
        messages=[Message(role="user", content="hello")],
        system_prompt="extra guidance",
    )

    messages = build_llama_native_messages(request)

    assert messages[0]["role"] == "system"
    assert messages[1] == {"role": "user", "content": "hello"}


def test_render_conversation_includes_roles_and_names() -> None:
    rendered = render_conversation(
        [
            Message(role="user", content="hello"),
            Message(role="tool", content="done", name="web_search"),
        ]
    )

    assert "[USER]" in rendered
    assert "[TOOL (web_search)]" in rendered
