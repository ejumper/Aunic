from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from aunic.loop.types import LoopEvent


ProgressKind = Literal[
    "run_started",
    "run_finished",
    "prompt_submitted",
    "status",
    "error",
    "file_written",
    "loop_event",
    "sleep_started",
    "sleep_ended",
]


@dataclass(frozen=True)
class ProgressEvent:
    kind: ProgressKind
    message: str
    path: Path | None = None
    details: dict[str, Any] = field(default_factory=dict)


class ProgressSink(Protocol):
    def __call__(self, event: ProgressEvent) -> object:
        ...


class AsyncProgressSink(Protocol):
    async def __call__(self, event: ProgressEvent) -> None:
        ...


async def emit_progress(
    sink: ProgressSink | None,
    event: ProgressEvent,
) -> None:
    if sink is None:
        return
    result = sink(event)
    if inspect.isawaitable(result):
        await result


def ensure_async_progress_sink(
    sink: ProgressSink | None,
) -> AsyncProgressSink | None:
    if sink is None:
        return None

    async def _wrapped(event: ProgressEvent) -> None:
        await emit_progress(sink, event)

    return _wrapped


def progress_from_loop_event(
    event: "LoopEvent",
    *,
    path: Path | None = None,
) -> ProgressEvent:
    return ProgressEvent(
        kind="loop_event",
        message=event.message,
        path=path,
        details={
            "loop_kind": event.kind,
            **event.details,
        },
    )
