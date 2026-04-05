from __future__ import annotations

import asyncio
import os
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aunic.config import SETTINGS
from aunic.domain import ToolSpec
from aunic.tools.base import ToolDefinition, ToolExecutionResult
from aunic.tools.runtime import PermissionRequest, RunToolContext, failure_from_payload, failure_payload, stable_signature

try:
    import bashlex  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional runtime dependency
    bashlex = None


@dataclass(frozen=True)
class BashArgs:
    command: str
    timeout: int | None = None
    description: str | None = None
    run_in_background: bool = False
    dangerouslyDisableSandbox: bool = False


def build_bash_tool_registry() -> tuple[ToolDefinition[Any], ...]:
    return (
        ToolDefinition(
            spec=ToolSpec(
                name="bash",
                description="Run a shell command in the project environment.",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["command"],
                    "properties": {
                        "command": {"type": "string"},
                        "timeout": {"type": "integer"},
                        "description": {"type": "string"},
                        "run_in_background": {"type": "boolean"},
                        "dangerouslyDisableSandbox": {"type": "boolean"},
                    },
                },
            ),
            parse_arguments=parse_bash_args,
            execute=execute_bash,
        ),
    )


def parse_bash_args(payload: dict[str, Any]) -> BashArgs:
    _ensure_no_extra_keys(
        payload,
        {
            "command",
            "timeout",
            "description",
            "run_in_background",
            "dangerouslyDisableSandbox",
        },
    )
    command = _require_string(payload, "command").strip()
    if not command:
        raise ValueError("`command` must not be empty.")
    timeout = payload.get("timeout")
    if timeout is not None and not isinstance(timeout, int):
        raise ValueError("`timeout` must be an integer.")
    description = payload.get("description")
    if description is not None and not isinstance(description, str):
        raise ValueError("`description` must be a string.")
    run_in_background = payload.get("run_in_background", False)
    if not isinstance(run_in_background, bool):
        raise ValueError("`run_in_background` must be a boolean.")
    disable_sandbox = payload.get("dangerouslyDisableSandbox", False)
    if not isinstance(disable_sandbox, bool):
        raise ValueError("`dangerouslyDisableSandbox` must be a boolean.")
    return BashArgs(
        command=command,
        timeout=timeout,
        description=description,
        run_in_background=run_in_background,
        dangerouslyDisableSandbox=disable_sandbox,
    )


async def execute_bash(runtime: RunToolContext, args: BashArgs) -> ToolExecutionResult:
    signature_count = runtime.record_signature("bash", stable_signature("bash", args.__dict__))
    if signature_count >= 3:
        return _tool_error(
            "bash",
            failure_payload(
                category="permission_denied",
                reason="doom_loop",
                message="Repeated identical bash requests were blocked.",
                command=args.command,
            ),
            status="protected_rejection",
        )
    if args.dangerouslyDisableSandbox:
        return _tool_error(
            "bash",
            failure_payload(
                category="validation_error",
                reason="sandbox_override_unsupported",
                message="dangerouslyDisableSandbox is not supported in this Aunic runtime.",
                command=args.command,
            ),
        )
    if _obviously_mutates_note_scope(runtime, args.command):
        return _tool_error(
            "bash",
            failure_payload(
                category="protected_rejection",
                reason="note_scope",
                message="Work-mode bash cannot mutate note-content.",
                command=args.command,
            ),
            status="protected_rejection",
        )

    command_class = _classify_command(args.command)
    policy = SETTINGS.tools.permissions.default_bash_policy
    if command_class in {"too_complex", "parse_unavailable"}:
        policy = "ask"
    decision = await runtime.resolve_permission(
        PermissionRequest(
            tool_name="bash",
            action="execute",
            target=args.command,
            message=args.description or f"bash wants to run: {args.command}",
            policy=policy,  # type: ignore[arg-type]
            key=f"bash:{args.command}",
            details={"classification": command_class},
        )
    )
    if not decision.allowed:
        return _tool_error(
            "bash",
            failure_payload(
                category="permission_denied",
                reason=decision.reason,
                message="bash execution was rejected.",
                command=args.command,
            ),
            status="protected_rejection",
        )

    await _ensure_shell_snapshot(runtime)
    timeout_ms = args.timeout or SETTINGS.tools.bash_timeout_ms
    timeout_ms = max(1, min(timeout_ms, SETTINGS.tools.bash_timeout_max_ms))
    if args.run_in_background:
        process = await asyncio.create_subprocess_exec(
            _shell_executable(),
            "-lc",
            args.command,
            cwd=runtime.session_state.shell.cwd,
            env=_shell_env(runtime),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        task_id = f"bg-{runtime.session_state.shell.next_background_id}"
        runtime.session_state.shell.next_background_id += 1
        runtime.session_state.shell.background_tasks[task_id] = process
        payload = {
            "type": "bash_background",
            "command": args.command,
            "description": args.description,
            "task_id": task_id,
            "pid": process.pid,
        }
        return ToolExecutionResult("bash", "completed", payload)

    marker = "__AUNIC_PWD__="
    wrapped_command = f"{args.command}\nprintf '{marker}%s\\n' \"$(pwd -P)\""
    try:
        process = await asyncio.create_subprocess_exec(
            _shell_executable(),
            "-lc",
            wrapped_command,
            cwd=runtime.session_state.shell.cwd,
            env=_shell_env(runtime),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_ms / 1000.0,
        )
    except asyncio.TimeoutError:
        if process.returncode is None:  # type: ignore[union-attr]
            process.kill()  # type: ignore[union-attr]
        return _tool_error(
            "bash",
            failure_payload(
                category="timeout",
                reason="timeout",
                message=f"bash timed out after {timeout_ms}ms.",
                command=args.command,
            ),
        )

    stdout = stdout_bytes.decode(errors="replace")
    stderr = stderr_bytes.decode(errors="replace")
    cwd = runtime.session_state.shell.cwd
    if marker in stdout:
        stdout, _, pwd_tail = stdout.rpartition(marker)
        pwd_value = pwd_tail.strip().splitlines()[0] if pwd_tail.strip() else ""
        if pwd_value:
            cwd = Path(pwd_value).expanduser().resolve()
            runtime.session_state.shell.cwd = cwd
    payload = {
        "type": "bash_result",
        "command": args.command,
        "description": args.description,
        "exit_code": process.returncode,
        "cwd": str(cwd),
        "stdout": _truncate_output(stdout),
        "stderr": _truncate_output(stderr),
    }
    status = "completed" if process.returncode == 0 else "tool_error"
    if status == "completed":
        return ToolExecutionResult("bash", status, payload)
    return ToolExecutionResult(
        "bash",
        status,
        payload,
        tool_failure=failure_from_payload(
            failure_payload(
                category="execution_error",
                reason="non_zero_exit",
                message=f"bash exited with code {process.returncode}.",
                command=args.command,
                exit_code=process.returncode,
            ),
            tool_name="bash",
        ),
    )


async def _ensure_shell_snapshot(runtime: RunToolContext) -> None:
    if runtime.session_state.shell.base_env is not None:
        return
    shell = _shell_executable()
    try:
        process = await asyncio.create_subprocess_exec(
            shell,
            "-lc",
            "env -0",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await process.communicate()
        values = stdout.decode(errors="replace").split("\0")
        env = {}
        for item in values:
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            env[key] = value
        runtime.session_state.shell.base_env = env or dict(os.environ)
    except Exception:
        runtime.session_state.shell.base_env = dict(os.environ)


def _shell_env(runtime: RunToolContext) -> dict[str, str]:
    env = dict(runtime.session_state.shell.base_env or os.environ)
    env.update(runtime.session_state.shell.env_overlays)
    return env


def _shell_executable() -> str:
    shell = os.environ.get("SHELL")
    if shell and Path(shell).exists():
        return shell
    return "bash"


def _classify_command(command: str) -> str:
    if bashlex is not None:
        try:
            parts = bashlex.parse(command)
        except Exception:
            return "too_complex"
        return "read_only" if _bashlex_read_only(parts) else "simple"
    try:
        shlex.split(command)
    except ValueError:
        return "parse_unavailable"
    return "read_only" if _shlex_read_only(command) else "simple"


def _bashlex_read_only(parts: list[Any]) -> bool:
    words: list[str] = []
    for part in parts:
        for node in getattr(part, "parts", []):
            if getattr(node, "kind", "") == "word":
                words.append(getattr(node, "word", ""))
    if not words:
        return False
    return _first_word_read_only(words[0]) and not _contains_write_operators(" ".join(words))


def _shlex_read_only(command: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens:
        return False
    return _first_word_read_only(tokens[0]) and not _contains_write_operators(command)


def _first_word_read_only(word: str) -> bool:
    return word in {
        "ls",
        "pwd",
        "cat",
        "head",
        "tail",
        "rg",
        "grep",
        "find",
        "git",
        "which",
        "env",
        "echo",
    }


def _contains_write_operators(command: str) -> bool:
    indicators = [">", ">>", " tee ", " sed -i", " perl -i", " mv ", " cp ", " rm ", " touch "]
    lowered = f" {command.lower()} "
    return any(token in lowered for token in indicators)


def _obviously_mutates_note_scope(runtime: RunToolContext, command: str) -> bool:
    lowered = command.lower()
    if not _contains_write_operators(command) and all(token not in lowered for token in {"python", "node", "ruby"}):
        return False
    return any(str(path).lower() in lowered for path in runtime.note_scope_paths())


def _truncate_output(text: str) -> str:
    if len(text) <= SETTINGS.tools.bash_max_output_chars:
        return text
    return text[: SETTINGS.tools.bash_max_output_chars] + "\n\n[Truncated by Aunic bash limit.]"


def _tool_error(tool_name: str, payload: dict[str, Any], *, status: str = "tool_error") -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=tool_name,
        status=status,
        in_memory_content=payload,
        transcript_content=payload,
        tool_failure=failure_from_payload(payload, tool_name=tool_name),
    )


def _require_string(payload: dict[str, Any], key: str) -> str:
    if key not in payload:
        raise ValueError(f"Missing required field `{key}`.")
    value = payload[key]
    if not isinstance(value, str):
        raise ValueError(f"`{key}` must be a string.")
    return value


def _ensure_no_extra_keys(payload: dict[str, Any], expected: set[str]) -> None:
    extras = sorted(set(payload) - expected)
    if extras:
        raise ValueError(f"Unexpected fields: {', '.join(extras)}.")
