from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aunic.config import CodexSettings
from aunic.errors import CodexProtocolError


@dataclass(frozen=True)
class CodexTurnResult:
    thread_id: str
    turn_id: str
    status: str
    raw_items: list[dict[str, Any]]
    thread_items: list[dict[str, Any]]
    token_usage: dict[str, Any] | None
    error_message: str | None
    stderr_lines: list[str]


class CodexAppServerSession:
    def __init__(
        self,
        settings: CodexSettings,
        cwd: Path,
        *,
        config_overrides: tuple[str, ...] = (),
    ) -> None:
        self._settings = settings
        self._cwd = cwd
        self._config_overrides = config_overrides
        self._process: asyncio.subprocess.Process | None = None
        self._next_request_id = 1
        self._pending_messages: list[dict[str, Any]] = []
        self._stderr_lines: deque[str] = deque(maxlen=settings.stderr_log_limit)
        self._stderr_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> CodexAppServerSession:
        command = [
            self._settings.executable,
            "app-server",
            "--listen",
            "stdio://",
            "--session-source",
            "aunic",
        ]
        for override in self._config_overrides:
            command.extend(["-c", override])
        try:
            self._process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(self._cwd),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise CodexProtocolError(
                f"Codex executable {self._settings.executable!r} was not found."
            ) from exc

        self._stderr_task = asyncio.create_task(self._drain_stderr())
        await self._initialize()
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=2)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr_task

    @property
    def stderr_lines(self) -> list[str]:
        return list(self._stderr_lines)

    async def get_auth_status(self) -> dict[str, Any]:
        return await self._send_request(
            "getAuthStatus",
            {"includeToken": False, "refreshToken": False},
        )

    async def start_thread(
        self,
        *,
        model: str,
        reasoning_effort: str,
        base_instructions: str,
        developer_instructions: str,
    ) -> dict[str, Any]:
        return await self._send_request(
            "thread/start",
            {
                "model": model,
                "cwd": str(self._cwd),
                "approvalPolicy": self._settings.approval_policy,
                "sandbox": self._settings.sandbox_mode,
                "baseInstructions": base_instructions,
                "developerInstructions": developer_instructions,
                "ephemeral": True,
                "experimentalRawEvents": True,
                "persistExtendedHistory": False,
                "serviceName": "aunic",
                "config": {
                    "model_reasoning_effort": reasoning_effort,
                    "web_search": "disabled",
                    "tools": {
                        "view_image": False,
                    },
                },
            },
        )

    async def resume_thread_with_history(
        self,
        *,
        history: list[dict[str, Any]],
        model: str,
        reasoning_effort: str,
        base_instructions: str,
        developer_instructions: str,
    ) -> dict[str, Any]:
        return await self._send_request(
            "thread/resume",
            {
                "threadId": "aunic-history-seed",
                "history": history,
                "model": model,
                "cwd": str(self._cwd),
                "approvalPolicy": self._settings.approval_policy,
                "sandbox": self._settings.sandbox_mode,
                "baseInstructions": base_instructions,
                "developerInstructions": developer_instructions,
                "persistExtendedHistory": False,
                "config": {
                    "model_reasoning_effort": reasoning_effort,
                    "web_search": "disabled",
                    "tools": {
                        "view_image": False,
                    },
                },
            },
        )

    async def run_turn(
        self,
        *,
        thread_id: str,
        input_text: str,
        model: str,
        reasoning_effort: str,
        timeout_seconds: float,
    ) -> CodexTurnResult:
        response = await self._send_request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [
                    {
                        "type": "text",
                        "text": input_text,
                        "text_elements": [],
                    }
                ],
                "model": model,
                "effort": reasoning_effort,
            },
        )
        turn = response.get("turn")
        if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
            raise CodexProtocolError("Codex app-server returned an invalid turn payload.")
        turn_id = turn["id"]

        raw_items: list[dict[str, Any]] = []
        thread_items: list[dict[str, Any]] = []
        token_usage: dict[str, Any] | None = None
        error_message: str | None = None
        status = "unknown"

        while True:
            message = await self._next_message(timeout_seconds)

            if "id" in message and "result" in message:
                continue

            method = message.get("method")
            params = message.get("params", {})

            if method == "rawResponseItem/completed":
                if params.get("turnId") == turn_id and isinstance(params.get("item"), dict):
                    raw_items.append(params["item"])
                continue

            if method == "item/completed":
                if params.get("turnId") == turn_id and isinstance(params.get("item"), dict):
                    thread_items.append(params["item"])
                continue

            if method == "thread/tokenUsage/updated" and params.get("turnId") == turn_id:
                if isinstance(params.get("tokenUsage"), dict):
                    token_usage = params["tokenUsage"]
                continue

            if method == "error" and params.get("turnId") == turn_id:
                error_payload = params.get("error", {})
                if isinstance(error_payload, dict):
                    error_message = error_payload.get("message")
                continue

            if method == "turn/completed":
                completed_turn = params.get("turn", {})
                if params.get("threadId") == thread_id and completed_turn.get("id") == turn_id:
                    status = completed_turn.get("status", "unknown")
                    turn_error = completed_turn.get("error")
                    if error_message is None and isinstance(turn_error, dict):
                        error_message = turn_error.get("message")
                    break

        return CodexTurnResult(
            thread_id=thread_id,
            turn_id=turn_id,
            status=status,
            raw_items=raw_items,
            thread_items=thread_items,
            token_usage=token_usage,
            error_message=error_message,
            stderr_lines=self.stderr_lines,
        )

    async def _initialize(self) -> None:
        await self._send_request(
            "initialize",
            {
                "clientInfo": {"name": "aunic", "title": "Aunic", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )

    async def _send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_request_id
        self._next_request_id += 1

        process = self._require_process()
        assert process.stdin is not None
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        await process.stdin.drain()
        return await self._await_response(request_id)

    async def _await_response(self, request_id: int) -> dict[str, Any]:
        deferred_messages: list[dict[str, Any]] = []
        while True:
            if self._pending_messages:
                message = self._pending_messages.pop(0)
            else:
                message = await self._read_message(self._settings.startup_timeout_seconds)
            if message.get("id") != request_id:
                deferred_messages.append(message)
                continue
            if "error" in message:
                self._pending_messages = deferred_messages + self._pending_messages
                raise CodexProtocolError(json.dumps(message["error"]))
            result = message.get("result")
            if not isinstance(result, dict):
                self._pending_messages = deferred_messages + self._pending_messages
                raise CodexProtocolError(
                    f"Codex app-server returned a non-object result for request {request_id}."
                )
            self._pending_messages = deferred_messages + self._pending_messages
            return result

    async def _next_message(self, timeout_seconds: float) -> dict[str, Any]:
        if self._pending_messages:
            return self._pending_messages.pop(0)
        return await self._read_message(timeout_seconds)

    async def _read_message(self, timeout_seconds: float) -> dict[str, Any]:
        process = self._require_process()
        assert process.stdout is not None

        while True:
            try:
                line = await asyncio.wait_for(process.stdout.readline(), timeout_seconds)
            except asyncio.TimeoutError as exc:
                raise CodexProtocolError("Timed out waiting for Codex app-server output.") from exc

            if not line:
                stderr = "\n".join(self.stderr_lines)
                raise CodexProtocolError(
                    f"Codex app-server closed unexpectedly. STDERR:\n{stderr}"
                )

            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                self._stderr_lines.append(text)
                continue
            if isinstance(payload, dict):
                return payload

    async def _drain_stderr(self) -> None:
        process = self._require_process()
        assert process.stderr is not None
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                self._stderr_lines.append(text)

    def _require_process(self) -> asyncio.subprocess.Process:
        if self._process is None:
            raise CodexProtocolError("Codex app-server session was used before startup.")
        return self._process


def build_stdio_mcp_config_overrides(
    server_name: str,
    *,
    command: str | None = None,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[str, ...]:
    server_command = command or sys.executable
    server_args = args or ["-m", "aunic.providers.mcp_server"]
    overrides = [
        f"mcp_servers.{server_name}.command={json.dumps(server_command)}",
        f"mcp_servers.{server_name}.args={json.dumps(server_args)}",
    ]
    for key, value in sorted((env or {}).items()):
        overrides.append(
            f"mcp_servers.{server_name}.env.{key}={json.dumps(value)}"
        )
    return tuple(overrides)
