from __future__ import annotations

import asyncio
import logging
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect

from aunic.browser.errors import BrowserError
from aunic.browser.messages import MessageEnvelope, make_envelope, parse_client_message
from aunic.browser.session import BrowserSession

logger = logging.getLogger(__name__)

_QUEUE_MAXSIZE = 256
_QUEUE_FULL_CLOSE_CODE = 1013
_PROTOCOL_CLOSE_CODE = 1003


class ConnectionHandler:
    def __init__(self, *, websocket: WebSocket, session: BrowserSession) -> None:
        self.websocket = websocket
        self.session = session
        self._outbound: asyncio.Queue[str | None] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._closed = False

    async def run(self) -> None:
        await self.websocket.accept()
        await self.session.attach(self)
        writer = asyncio.create_task(self._writer(), name="aunic-browser-ws-writer")
        try:
            await self._reader()
        finally:
            self._closed = True
            await self.session.detach(self)
            await self._outbound.put(None)
            writer.cancel()
            try:
                await writer
            except asyncio.CancelledError:
                pass

    async def send_event(
        self,
        message_type: str,
        payload: dict[str, Any],
        *,
        message_id: str | None = None,
    ) -> None:
        if self._closed:
            return
        message = make_envelope(message_type, payload, message_id=message_id)
        try:
            self._outbound.put_nowait(message)
        except asyncio.QueueFull:
            self._closed = True
            await self.websocket.close(code=_QUEUE_FULL_CLOSE_CODE)

    async def send_response(self, request_id: str, payload: dict[str, Any]) -> None:
        await self.send_event("response", payload, message_id=request_id)

    async def send_error(
        self,
        request_id: str,
        reason: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        await self.send_event(
            "error",
            {"reason": reason, "details": dict(details or {})},
            message_id=request_id,
        )

    async def _reader(self) -> None:
        while True:
            try:
                raw = await self.websocket.receive_text()
            except WebSocketDisconnect:
                return
            try:
                envelope = parse_client_message(raw)
            except BrowserError as exc:
                logger.info("Closing malformed browser WS message: %s", exc.reason)
                await self.websocket.close(code=_PROTOCOL_CLOSE_CODE)
                return

            try:
                await self._dispatch(envelope)
            except BrowserError as exc:
                await self.send_error(envelope.id, exc.reason, details=exc.details)
            except Exception as exc:
                logger.exception("Browser WS request failed")
                await self.send_error(
                    envelope.id,
                    "internal_error",
                    details={"message": str(exc)},
                )

    async def _writer(self) -> None:
        while True:
            message = await self._outbound.get()
            if message is None:
                return
            await self.websocket.send_text(message)

    async def _dispatch(self, envelope: MessageEnvelope) -> None:
        payload = envelope.payload
        if envelope.type == "hello":
            await self.send_event(
                "session_state",
                self.session.session_state(),
                message_id=envelope.id,
            )
            pending = self.session.pending_permission_payload()
            if pending is not None:
                await self.send_event("permission_request", pending)
            return

        if envelope.type == "list_files":
            subpath = payload.get("subpath")
            if subpath is not None and not isinstance(subpath, str):
                raise BrowserError("invalid_payload", "subpath must be a string.")
            await self.send_response(envelope.id, await self.session.list_files(subpath))
            return

        if envelope.type == "read_file":
            path = _required_string(payload, "path")
            await self.send_response(envelope.id, await self.session.read_file(path))
            return

        if envelope.type == "write_file":
            path = _required_string(payload, "path")
            text = _required_string(payload, "text")
            expected_revision = payload.get("expected_revision")
            if expected_revision is not None and not isinstance(expected_revision, str):
                raise BrowserError("invalid_payload", "expected_revision must be a string or null.")
            await self.send_response(
                envelope.id,
                await self.session.write_file(
                    path,
                    text=text,
                    expected_revision=expected_revision,
                ),
            )
            return

        if envelope.type == "create_file":
            path = _required_string(payload, "path")
            await self.send_response(envelope.id, await self.session.create_file(path))
            return

        if envelope.type == "create_directory":
            path = _required_string(payload, "path")
            await self.send_response(envelope.id, await self.session.create_directory(path))
            return

        if envelope.type == "delete_entry":
            path = _required_string(payload, "path")
            await self.send_response(envelope.id, await self.session.delete_entry(path))
            return

        if envelope.type == "delete_transcript_row":
            path = _required_string(payload, "path")
            row_number = _required_int(payload, "row_number")
            expected_revision = payload.get("expected_revision")
            if expected_revision is not None and not isinstance(expected_revision, str):
                raise BrowserError("invalid_payload", "expected_revision must be a string or null.")
            await self.send_response(
                envelope.id,
                await self.session.delete_transcript_row(
                    path,
                    row_number=row_number,
                    expected_revision=expected_revision,
                ),
            )
            return

        if envelope.type == "delete_search_result":
            path = _required_string(payload, "path")
            row_number = _required_int(payload, "row_number")
            result_index = _required_int(payload, "result_index")
            expected_revision = payload.get("expected_revision")
            if expected_revision is not None and not isinstance(expected_revision, str):
                raise BrowserError("invalid_payload", "expected_revision must be a string or null.")
            await self.send_response(
                envelope.id,
                await self.session.delete_search_result(
                    path,
                    row_number=row_number,
                    result_index=result_index,
                    expected_revision=expected_revision,
                ),
            )
            return

        if envelope.type == "set_mode":
            mode = _required_string(payload, "mode")
            await self.session.set_mode(mode)
            await self.send_response(envelope.id, {"mode": self.session.mode})
            return

        if envelope.type == "set_work_mode":
            work_mode = _required_string(payload, "work_mode")
            await self.session.set_work_mode(work_mode)
            await self.send_response(envelope.id, {"work_mode": self.session.work_mode})
            return

        if envelope.type == "select_model":
            index = _required_int(payload, "index")
            await self.session.select_model(index)
            await self.send_response(
                envelope.id,
                {
                    "selected_model_index": self.session.selected_model_index,
                    "selected_model": self.session.session_state()["selected_model"],
                },
            )
            return

        if envelope.type == "submit_prompt":
            active_file = _required_string(payload, "active_file")
            text = _required_string(payload, "text")
            included_files = payload.get("included_files", [])
            if not isinstance(included_files, list) or not all(
                isinstance(item, str) for item in included_files
            ):
                raise BrowserError("invalid_payload", "included_files must be a list of strings.")
            run_id = await self.session.submit_prompt(
                active_file=active_file,
                included_files=included_files,
                text=text,
            )
            await self.send_response(envelope.id, {"run_id": run_id})
            return

        if envelope.type == "run_prompt_command":
            active_file = _required_string(payload, "active_file")
            text = _required_string(payload, "text")
            await self.send_response(
                envelope.id,
                await self.session.run_prompt_command(
                    active_file=active_file,
                    text=text,
                ),
            )
            return

        if envelope.type == "research_fetch_result":
            active_file = _required_string(payload, "active_file")
            result_index = _required_int(payload, "result_index")
            await self.send_response(
                envelope.id,
                await self.session.research_fetch_result(
                    active_file=active_file,
                    result_index=result_index,
                ),
            )
            return

        if envelope.type == "research_insert_chunks":
            active_file = _required_string(payload, "active_file")
            mode = _required_string(payload, "mode")
            chunk_indices = payload.get("chunk_indices")
            if chunk_indices is not None and (
                not isinstance(chunk_indices, list)
                or not all(type(item) is int for item in chunk_indices)
            ):
                raise BrowserError("invalid_payload", "chunk_indices must be a list of integers.")
            await self.send_response(
                envelope.id,
                await self.session.research_insert_chunks(
                    active_file=active_file,
                    mode=mode,
                    chunk_indices=chunk_indices,
                ),
            )
            return

        if envelope.type == "research_back":
            await self.send_response(envelope.id, await self.session.research_back())
            return

        if envelope.type == "research_cancel":
            await self.send_response(envelope.id, await self.session.research_cancel())
            return

        if envelope.type == "cancel_run":
            run_id = payload.get("run_id")
            if run_id is not None and not isinstance(run_id, str):
                raise BrowserError("invalid_payload", "run_id must be a string or null.")
            await self.send_response(
                envelope.id,
                {"cancelled": await self.session.cancel_run(run_id or self.session.current_run_id)},
            )
            return

        if envelope.type == "resolve_permission":
            permission_id = _required_string(payload, "permission_id")
            resolution = _required_string(payload, "resolution")
            await self.send_response(
                envelope.id,
                await self.session.resolve_permission(permission_id, resolution),
            )
            return

        raise BrowserError("unknown_type", f"Unhandled message type: {envelope.type}")


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise BrowserError("invalid_payload", f"{key} must be a string.")
    return value


def _required_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int:
        raise BrowserError("invalid_payload", f"{key} must be an integer.")
    return value
