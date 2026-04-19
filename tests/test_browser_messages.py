from __future__ import annotations

import json
from pathlib import Path

import pytest

from aunic.browser.messages import (
    parse_client_message,
    serialize_file_change,
    serialize_file_snapshot,
    serialize_permission_request,
    serialize_progress_event,
    serialize_transcript_row,
)
from aunic.browser.errors import MessageProtocolError
from aunic.context.types import FileChange, FileSnapshot
from aunic.domain import TranscriptRow
from aunic.progress import ProgressEvent
from aunic.tools.runtime import PermissionRequest
from aunic.transcript.writer import append_transcript_row


def _snapshot(path: Path, text: str) -> FileSnapshot:
    return FileSnapshot(
        path=path.resolve(),
        raw_text=text,
        revision_id="rev-1",
        content_hash="hash",
        mtime_ns=1,
        size_bytes=len(text.encode("utf-8")),
    )


def test_parse_client_message_accepts_known_request() -> None:
    envelope = parse_client_message(
        json.dumps({"id": "1", "type": "hello", "payload": {}})
    )

    assert envelope.id == "1"
    assert envelope.type == "hello"
    assert envelope.payload == {}


@pytest.mark.parametrize(
    "message",
    [
        "not-json",
        json.dumps([]),
        json.dumps({"type": "hello", "payload": {}}),
        json.dumps({"id": 1, "type": "hello", "payload": {}}),
        json.dumps({"id": "1", "type": "hello", "payload": []}),
    ],
)
def test_parse_client_message_rejects_invalid_envelopes(message: str) -> None:
    with pytest.raises(MessageProtocolError):
        parse_client_message(message)


def test_parse_client_message_allows_unknown_request_for_dispatch_error() -> None:
    envelope = parse_client_message(
        json.dumps({"id": "1", "type": "future_request", "payload": {}})
    )

    assert envelope.type == "future_request"


def test_serialize_file_snapshot_sends_note_content_and_transcript_rows(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    text, _row_number = append_transcript_row(
        "# Note\n",
        "user",
        "message",
        None,
        None,
        "hello",
    )
    note.write_text(text, encoding="utf-8")

    payload = serialize_file_snapshot(_snapshot(note, text), workspace_root=tmp_path)

    assert payload["path"] == "note.md"
    assert payload["note_content"] == "# Note"
    assert "raw_text" not in payload
    assert payload["transcript_rows"][0]["content"] == "hello"


def test_serializers_return_json_ready_payloads(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body", encoding="utf-8")

    payloads = [
        serialize_progress_event(
            ProgressEvent(
                kind="status",
                message="Working",
                path=note,
                details={"path": note},
            ),
            workspace_root=tmp_path,
        ),
        serialize_transcript_row(
            TranscriptRow(
                row_number=1,
                role="assistant",
                type="message",
                content={"ok": True},
            )
        ),
        serialize_permission_request(
            PermissionRequest(
                tool_name="bash",
                action="run",
                target="pwd",
                message="Run command?",
                policy="ask",
                key="bash:pwd",
            )
        ),
        serialize_file_change(
            FileChange(path=note.resolve(), change="added", exists=True, revision_id="rev-2"),
            workspace_root=tmp_path,
        ),
    ]

    encoded = json.dumps(payloads)
    decoded = json.loads(encoded)
    assert decoded[0]["path"] == "note.md"
    assert decoded[3]["kind"] == "created"
