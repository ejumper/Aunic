from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]
WorkMode = Literal["off", "read", "work"]
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]
MessageType = Literal["message", "tool_call", "tool_result", "tool_error"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str
    name: str | None = None


@dataclass(frozen=True)
class TranscriptRow:
    """A parsed row from the markdown transcript table."""

    row_number: int
    role: Role
    type: MessageType
    tool_name: str | None = None
    tool_id: str | None = None
    content: Any = None

    def to_legacy_message(self) -> Message:
        if isinstance(self.content, str):
            text = self.content
        else:
            text = json.dumps(
                self.content,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        return Message(role=self.role, content=text, name=self.tool_name)


@dataclass(frozen=True)
class TextBlock:
    text: str


@dataclass(frozen=True)
class ToolUseBlock:
    tool_name: str
    tool_id: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolResultBlock:
    tool_id: str
    content: Any
    is_error: bool = False


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str | None = None


@dataclass(frozen=True)
class Usage:
    total_tokens: int | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    model_context_window: int | None = None


@dataclass(frozen=True)
class UsageLogEntry:
    index: int
    stage: str
    usage: Usage | None = None
    provider: str | None = None
    model: str | None = None
    finish_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UsageLog:
    entries: tuple[UsageLogEntry, ...] = ()
    total: Usage | None = None


@dataclass(frozen=True)
class ProviderRequest:
    messages: list[Message]
    transcript_messages: list[TranscriptRow] | None = None
    assistant_message_patches: list[dict[str, Any]] = field(default_factory=list)
    note_snapshot: str | None = None
    user_prompt: str | None = None
    tools: list[ToolSpec] = field(default_factory=list)
    system_prompt: str | None = None
    model: str | None = None
    reasoning_effort: ReasoningEffort | None = None
    max_output_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderGeneratedRow:
    row: TranscriptRow
    transcript_content: Any | None = None


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    generated_rows: list[ProviderGeneratedRow] = field(default_factory=list)
    assistant_message_patch: dict[str, Any] | None = None
    finish_reason: str | None = None
    usage: Usage | None = None
    raw_items: list[dict[str, Any]] = field(default_factory=list)
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HealthCheck:
    provider: str
    ok: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)
