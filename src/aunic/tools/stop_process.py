from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aunic.domain import ToolSpec
from aunic.tools.base import ToolDefinition, ToolExecutionResult
from aunic.tools.runtime import (
    BackgroundProcessState,
    RunToolContext,
    failure_from_payload,
    failure_payload,
)

DEFAULT_GRACE_MS = 3_000
MAX_GRACE_MS = 30_000


@dataclass(frozen=True)
class StopProcessArgs:
    background_id: str
    force: bool = False
    grace_ms: int = DEFAULT_GRACE_MS
    reason: str | None = None


def build_stop_process_tool_registry() -> tuple[ToolDefinition[Any], ...]:
    return (build_stop_process_tool(),)


def build_stop_process_tool() -> ToolDefinition[StopProcessArgs]:
    return ToolDefinition(
        spec=ToolSpec(
            name="stop_process",
            description=(
                "Stop a background process started by Aunic, such as a bash command run "
                "with run_in_background=true. Provide the background_id returned by bash "
                '(for example, "bg-1"). By default this POSIX/Linux implementation sends '
                "SIGTERM to the process group, waits briefly, then escalates to SIGKILL. "
                "Use force=true to send SIGKILL immediately. Idempotent: stopping an "
                "already-exited process succeeds with status=\"already_exited\". This tool "
                "only stops processes Aunic started; it cannot stop arbitrary system processes."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["background_id"],
                "properties": {
                    "background_id": {
                        "type": "string",
                        "description": "The bg-N id returned by bash when run_in_background=true.",
                    },
                    "force": {
                        "type": "boolean",
                        "default": False,
                        "description": "If true, send SIGKILL without waiting for graceful exit.",
                    },
                    "grace_ms": {
                        "type": "integer",
                        "default": DEFAULT_GRACE_MS,
                        "minimum": 0,
                        "maximum": MAX_GRACE_MS,
                        "description": "Milliseconds to wait after SIGTERM before SIGKILL.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Optional human-readable reason for stopping the process.",
                    },
                },
            },
        ),
        parse_arguments=parse_stop_process_args,
        execute=execute_stop_process,
        persistence="persistent",
    )


def parse_stop_process_args(payload: dict[str, Any]) -> StopProcessArgs:
    _ensure_no_extra_keys(payload, {"background_id", "force", "grace_ms", "reason"})
    background_id_value = payload.get("background_id")
    if not isinstance(background_id_value, str):
        raise ValueError("`background_id` must be a string.")
    background_id = background_id_value.strip()
    if not background_id:
        raise ValueError("`background_id` must not be empty.")

    force_value = payload.get("force", False)
    if not isinstance(force_value, bool):
        raise ValueError("`force` must be a boolean.")

    grace_value = payload.get("grace_ms", DEFAULT_GRACE_MS)
    if isinstance(grace_value, bool) or not isinstance(grace_value, int):
        raise ValueError("`grace_ms` must be an integer.")

    reason_value = payload.get("reason")
    reason: str | None = None
    if reason_value is not None:
        if not isinstance(reason_value, str):
            raise ValueError("`reason` must be a string.")
        reason = reason_value.strip()
        if not reason:
            raise ValueError("`reason` must not be empty.")

    return StopProcessArgs(
        background_id=background_id,
        force=force_value,
        grace_ms=grace_value,
        reason=reason,
    )


async def execute_stop_process(
    runtime: RunToolContext,
    args: StopProcessArgs,
) -> ToolExecutionResult:
    state = runtime.session_state.shell.get_background_process(args.background_id)
    if state is None:
        return _tool_error(
            failure_payload(
                category="validation_error",
                reason="not_found",
                message=f"No Aunic background process with id {args.background_id}.",
                type="process_stop",
                background_id=args.background_id,
            )
        )

    await _refresh_terminal_state(state)
    if state.process.returncode is not None or state.status != "running":
        return _result(
            status="already_exited",
            state=state,
            signals_sent=(),
            forced=False,
            requested_grace_ms=args.grace_ms,
            grace_ms=_clamp_grace_ms(args.grace_ms),
            reason=args.reason,
            elapsed_ms=0,
        )

    requested_grace_ms = args.grace_ms
    grace_ms = _clamp_grace_ms(requested_grace_ms)
    force = bool(args.force or grace_ms == 0)
    signals_sent: list[str] = []
    started_at = time.monotonic()

    try:
        if force:
            if _signal_pgid(state.pgid, signal.SIGKILL):
                signals_sent.append("SIGKILL")
        else:
            if _signal_pgid(state.pgid, signal.SIGTERM):
                signals_sent.append("SIGTERM")
            try:
                await asyncio.wait_for(state.process.wait(), timeout=grace_ms / 1000.0)
            except asyncio.TimeoutError:
                if _signal_pgid(state.pgid, signal.SIGKILL):
                    signals_sent.append("SIGKILL")
                force = True
                await state.process.wait()
            else:
                # The shell process can exit on SIGTERM while a child that ignored
                # SIGTERM remains alive in the same process group. Treat a still-live
                # group as an escalation case so stop_process stops the whole tree.
                if await _process_group_exists_after_settle(state.pgid):
                    if _signal_pgid(state.pgid, signal.SIGKILL):
                        signals_sent.append("SIGKILL")
                    force = True

        if state.process.returncode is None:
            await state.process.wait()
    except OSError as exc:
        state.status = "failed"
        state.ended_at = datetime.now()
        return _tool_error(
            failure_payload(
                category="execution_error",
                reason="signal_failed",
                message=f"Failed to stop background process {state.background_id}: {exc}",
                type="process_stop",
                background_id=state.background_id,
                pid=state.pid,
                pgid=state.pgid,
                command=state.command,
                description=state.description,
            )
        )

    state.returncode = state.process.returncode
    state.status = "stopped"
    state.ended_at = datetime.now()
    state.signals_sent = state.signals_sent + tuple(signals_sent)
    state.stop_reason = args.reason
    elapsed_ms = round((time.monotonic() - started_at) * 1000)
    return _result(
        status="stopped",
        state=state,
        signals_sent=tuple(signals_sent),
        forced=force,
        requested_grace_ms=requested_grace_ms,
        grace_ms=grace_ms,
        reason=args.reason,
        elapsed_ms=elapsed_ms,
    )


def _clamp_grace_ms(grace_ms: int) -> int:
    return max(0, min(grace_ms, MAX_GRACE_MS))


def _signal_pgid(pgid: int, sig: signal.Signals) -> bool:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return False
    return True


def _process_group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


async def _process_group_exists_after_settle(pgid: int) -> bool:
    for _ in range(3):
        if not _process_group_exists(pgid):
            return False
        await asyncio.sleep(0.02)
    return _process_group_exists(pgid)


async def _refresh_terminal_state(state: BackgroundProcessState) -> None:
    if state.process.returncode is None:
        return
    state.returncode = state.process.returncode
    if state.status == "running":
        state.status = "exited"
    if state.ended_at is None:
        state.ended_at = datetime.now()


def _result(
    *,
    status: str,
    state: BackgroundProcessState,
    signals_sent: tuple[str, ...],
    forced: bool,
    requested_grace_ms: int,
    grace_ms: int,
    reason: str | None,
    elapsed_ms: int,
) -> ToolExecutionResult:
    payload = {
        "type": "process_stop",
        "status": status,
        "background_id": state.background_id,
        "pid": state.pid,
        "pgid": state.pgid,
        "command": state.command,
        "description": state.description,
        "exit_code": state.returncode,
        "signals_sent": list(signals_sent),
        "forced": forced,
        "elapsed_ms": elapsed_ms,
        "reason": reason,
        "requested_grace_ms": requested_grace_ms,
        "grace_ms": grace_ms,
        "clamped_grace_ms": grace_ms if grace_ms != requested_grace_ms else None,
    }
    return ToolExecutionResult("stop_process", "completed", payload)


def _tool_error(payload: dict[str, Any]) -> ToolExecutionResult:
    return ToolExecutionResult(
        "stop_process",
        "tool_error",
        payload,
        tool_failure=failure_from_payload(payload, tool_name="stop_process"),
    )


def _ensure_no_extra_keys(payload: dict[str, Any], expected: set[str]) -> None:
    extras = sorted(set(payload) - expected)
    if extras:
        raise ValueError(f"Unexpected fields: {', '.join(extras)}.")
