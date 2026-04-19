from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from aunic.config import SETTINGS
from aunic.domain import ToolSpec
from aunic.progress import ProgressEvent, emit_progress
from aunic.tools.base import ToolDefinition, ToolExecutionResult
from aunic.tools.runtime import RunToolContext


@dataclass(frozen=True)
class SleepArgs:
    duration_ms: int
    reason: str | None = None


def build_sleep_tool_registry() -> tuple[ToolDefinition[Any], ...]:
    return (
        ToolDefinition(
            spec=ToolSpec(
                name="sleep",
                description=(
                    "Wait for a short, intentional interval without running a shell process. "
                    "Use this when time passing is the actual next step, such as waiting for a "
                    "server to start, a retry cooldown, or an explicit user request to wait. "
                    "Do not sleep instead of answering when you already have enough information. "
                    "Prefer task-aware tools over sleep when a tool can directly observe the "
                    "condition you care about. The user can interrupt sleep at any time."
                ),
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["duration_ms"],
                    "properties": {
                        "duration_ms": {
                            "type": "integer",
                            "description": "Requested wait duration in milliseconds.",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Why waiting is useful. Required for long waits.",
                        },
                    },
                },
            ),
            parse_arguments=parse_sleep_args,
            execute=execute_sleep,
            persistence="ephemeral",
        ),
    )


def parse_sleep_args(payload: dict[str, Any]) -> SleepArgs:
    _ensure_no_extra_keys(payload, {"duration_ms", "reason"})
    duration_ms = payload.get("duration_ms")
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int):
        raise ValueError("`duration_ms` must be an integer.")
    if duration_ms <= 0:
        raise ValueError("`duration_ms` must be greater than zero.")

    reason_value = payload.get("reason")
    reason: str | None = None
    if reason_value is not None:
        if not isinstance(reason_value, str):
            raise ValueError("`reason` must be a string.")
        reason = reason_value.strip()
        if not reason:
            raise ValueError("`reason` must not be empty.")

    if duration_ms >= SETTINGS.sleep.sleep_require_reason_after_ms and reason is None:
        raise ValueError(
            "`reason` is required when `duration_ms` is "
            f"{SETTINGS.sleep.sleep_require_reason_after_ms}ms or longer."
        )
    return SleepArgs(duration_ms=duration_ms, reason=reason)


async def execute_sleep(runtime: RunToolContext, args: SleepArgs) -> ToolExecutionResult:
    settings = SETTINGS.sleep
    clamped_ms = max(settings.sleep_min_ms, min(args.duration_ms, settings.sleep_max_ms))
    clamped = clamped_ms != args.duration_ms
    clamped_down = args.duration_ms > settings.sleep_max_ms
    started_at = time.monotonic()
    deadline = started_at + (clamped_ms / 1000.0)

    await emit_progress(
        runtime.progress_sink,
        ProgressEvent(
            kind="sleep_started",
            message=args.reason or "Sleeping",
            path=runtime.active_file,
            details={
                "requested_ms": args.duration_ms,
                "duration_ms": clamped_ms,
                "started_monotonic": started_at,
                "deadline_monotonic": deadline,
                "reason": args.reason,
                "clamped": clamped,
            },
        ),
    )

    try:
        await asyncio.sleep(clamped_ms / 1000.0)
    except asyncio.CancelledError:
        slept_ms = _elapsed_ms(started_at)
        await _emit_sleep_ended(
            runtime,
            requested_ms=args.duration_ms,
            slept_ms=slept_ms,
            status="interrupted",
            woke_because="cancelled",
            reason=args.reason,
            duration_ms=clamped_ms,
            deadline_monotonic=deadline,
        )
        raise

    slept_ms = _elapsed_ms(started_at)
    status = "clamped" if clamped else "completed"
    woke_because = "max_duration" if clamped_down else "timer"
    payload = {
        "type": "sleep_result",
        "status": status,
        "requested_ms": args.duration_ms,
        "duration_ms": clamped_ms,
        "slept_ms": slept_ms,
        "woke_because": woke_because,
        "reason": args.reason,
    }
    await _emit_sleep_ended(
        runtime,
        requested_ms=args.duration_ms,
        slept_ms=slept_ms,
        status=status,
        woke_because=woke_because,
        reason=args.reason,
        duration_ms=clamped_ms,
        deadline_monotonic=deadline,
    )
    return ToolExecutionResult(
        tool_name="sleep",
        status="completed",
        in_memory_content=payload,
        transcript_content=None,
    )


async def _emit_sleep_ended(
    runtime: RunToolContext,
    *,
    requested_ms: int,
    slept_ms: int,
    status: str,
    woke_because: str,
    reason: str | None,
    duration_ms: int,
    deadline_monotonic: float,
) -> None:
    await emit_progress(
        runtime.progress_sink,
        ProgressEvent(
            kind="sleep_ended",
            message="Sleep ended.",
            path=runtime.active_file,
            details={
                "requested_ms": requested_ms,
                "duration_ms": duration_ms,
                "slept_ms": slept_ms,
                "status": status,
                "woke_because": woke_because,
                "reason": reason,
                "deadline_monotonic": deadline_monotonic,
            },
        ),
    )


def _elapsed_ms(started_at: float) -> int:
    return round((time.monotonic() - started_at) * 1000)


def _ensure_no_extra_keys(payload: dict[str, Any], expected: set[str]) -> None:
    extras = sorted(set(payload) - expected)
    if extras:
        raise ValueError(f"Unexpected fields: {', '.join(extras)}.")
