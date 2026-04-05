from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Generic, Literal, TypeVar

from aunic.domain import ToolSpec

if TYPE_CHECKING:
    from aunic.loop.types import ToolFailure

ToolArgs = TypeVar("ToolArgs")
ToolPersistence = Literal["persistent", "ephemeral"]


@dataclass(frozen=True)
class ToolExecutionResult:
    tool_name: str
    status: str
    in_memory_content: Any
    transcript_content: Any | None = None
    tool_failure: ToolFailure | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolDefinition(Generic[ToolArgs]):
    spec: ToolSpec
    parse_arguments: Callable[[dict[str, Any]], ToolArgs]
    execute: Callable[[Any, ToolArgs], Awaitable[ToolExecutionResult]]
    persistence: ToolPersistence = "persistent"
