from __future__ import annotations

import json
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from aunic.browser.errors import MessageProtocolError
from aunic.browser.paths import workspace_relative_path
from aunic.context.types import FileChange, FileSnapshot
from aunic.domain import TranscriptRow
from aunic.model_options import ModelOption
from aunic.progress import ProgressEvent
from aunic.tools.runtime import PermissionRequest
from aunic.transcript.parser import parse_transcript_rows, split_note_and_transcript

CLIENT_MESSAGE_TYPES: frozenset[str] = frozenset(
    {
        "hello",
        "list_files",
        "read_file",
        "write_file",
        "submit_prompt",
        "run_prompt_command",
        "research_fetch_result",
        "research_insert_chunks",
        "research_back",
        "research_cancel",
        "cancel_run",
        "resolve_permission",
        "create_file",
        "create_directory",
        "delete_entry",
        "delete_transcript_row",
        "delete_search_result",
        "set_mode",
        "set_work_mode",
        "select_model",
    }
)

SERVER_MESSAGE_TYPES: frozenset[str] = frozenset(
    {
        "session_state",
        "progress_event",
        "transcript_row",
        "file_changed",
        "permission_request",
        "response",
        "error",
    }
)

PERMISSION_RESOLUTIONS: frozenset[str] = frozenset({"once", "always", "reject"})


@dataclass(frozen=True)
class MessageEnvelope:
    id: str
    type: str
    payload: dict[str, Any]


def parse_client_message(raw: str | bytes) -> MessageEnvelope:
    try:
        decoded = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        data = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MessageProtocolError("malformed_json", "Message must be valid JSON.") from exc

    if not isinstance(data, dict):
        raise MessageProtocolError("invalid_envelope", "Message envelope must be an object.")

    message_id = data.get("id")
    message_type = data.get("type")
    payload = data.get("payload")

    if not isinstance(message_id, str) or not message_id:
        raise MessageProtocolError("invalid_envelope", "Message id must be a non-empty string.")
    if not isinstance(message_type, str) or not message_type:
        raise MessageProtocolError("invalid_envelope", "Message type must be a non-empty string.")
    if not isinstance(payload, dict):
        raise MessageProtocolError("invalid_envelope", "Message payload must be an object.")

    return MessageEnvelope(id=message_id, type=message_type, payload=payload)


def make_envelope(
    message_type: str,
    payload: dict[str, Any],
    *,
    message_id: str | None = None,
) -> str:
    if message_type not in SERVER_MESSAGE_TYPES:
        raise ValueError(f"Unknown server message type: {message_type}")
    envelope = {
        "id": message_id or uuid4().hex,
        "type": message_type,
        "payload": payload,
    }
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))


def serialize_file_snapshot(
    snapshot: FileSnapshot,
    *,
    workspace_root: Path,
) -> dict[str, Any]:
    note_content, transcript_text = split_note_and_transcript(snapshot.raw_text)
    transcript_rows = (
        [serialize_transcript_row(row) for row in parse_transcript_rows(transcript_text)]
        if transcript_text
        else []
    )
    return {
        "path": workspace_relative_path(snapshot.path, workspace_root=workspace_root),
        "revision_id": snapshot.revision_id,
        "content_hash": snapshot.content_hash,
        "mtime_ns": snapshot.mtime_ns,
        "size_bytes": snapshot.size_bytes,
        "captured_at": _serialize_datetime(snapshot.captured_at),
        "note_content": note_content,
        "transcript_rows": transcript_rows,
        "has_transcript": bool(transcript_text),
    }


def serialize_file_change(
    change: FileChange,
    *,
    workspace_root: Path,
) -> dict[str, Any]:
    kind = "created" if change.change == "added" else change.change
    return {
        "path": workspace_relative_path(change.path, workspace_root=workspace_root),
        "revision_id": change.revision_id,
        "kind": kind,
        "exists": change.exists,
        "captured_at": _serialize_datetime(change.captured_at),
    }


def serialize_progress_event(
    event: ProgressEvent,
    *,
    workspace_root: Path,
) -> dict[str, Any]:
    return {
        "kind": event.kind,
        "message": event.message,
        "path": (
            workspace_relative_path(event.path, workspace_root=workspace_root)
            if event.path is not None
            else None
        ),
        "details": _jsonable(event.details, workspace_root=workspace_root),
    }


def serialize_transcript_row(row: TranscriptRow) -> dict[str, Any]:
    return {
        "row_number": row.row_number,
        "role": row.role,
        "type": row.type,
        "tool_name": row.tool_name,
        "tool_id": row.tool_id,
        "content": _jsonable(row.content),
    }


def serialize_permission_request(request: PermissionRequest) -> dict[str, Any]:
    return {
        "tool_name": request.tool_name,
        "action": request.action,
        "target": request.target,
        "message": request.message,
        "policy": request.policy,
        "key": request.key,
        "details": _jsonable(request.details),
    }


def serialize_model_option(option: ModelOption) -> dict[str, Any]:
    return {
        "label": option.label,
        "provider_name": option.provider_name,
        "model": option.model,
        "profile_id": option.profile_id,
        "context_window": option.context_window,
    }


def _serialize_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _jsonable(value: Any, *, workspace_root: Path | None = None) -> Any:
    if isinstance(value, Path):
        if workspace_root is not None:
            return workspace_relative_path(value, workspace_root=workspace_root)
        return str(value)
    if isinstance(value, datetime):
        return _serialize_datetime(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _jsonable(getattr(value, field.name), workspace_root=workspace_root)
            for field in fields(value)
        }
    if isinstance(value, dict):
        return {
            str(key): _jsonable(item, workspace_root=workspace_root)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item, workspace_root=workspace_root) for item in value]
    return value
