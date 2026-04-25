from __future__ import annotations

from pathlib import Path

from aunic.browser.messages import (
    CLIENT_MESSAGE_TYPES,
    serialize_file_change,
    serialize_file_snapshot,
    serialize_model_option,
    serialize_permission_request,
    serialize_progress_event,
    serialize_transcript_row,
)
from aunic.context.types import FileChange, FileSnapshot
from aunic.domain import TranscriptRow
from aunic.model_options import ModelOption
from aunic.progress import ProgressEvent
from aunic.tools.runtime import PermissionRequest


def test_browser_client_message_types_match_typescript_contract() -> None:
    assert CLIENT_MESSAGE_TYPES == {
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
        "rename_entry",
        "delete_entry",
        "get_project_state",
        "add_include",
        "create_plan",
        "delete_plan",
        "remove_include_entry",
        "set_active_plan",
        "set_include_entry_active",
        "set_project_child_active",
        "delete_transcript_row",
        "delete_search_result",
        "set_mode",
        "set_work_mode",
        "select_model",
    }


def test_browser_serializer_field_names_match_typescript_contract(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body", encoding="utf-8")
    snapshot = FileSnapshot(
        path=note.resolve(),
        raw_text="body",
        revision_id="rev-1",
        content_hash="hash",
        mtime_ns=1,
        size_bytes=4,
    )

    assert set(serialize_file_snapshot(snapshot, workspace_root=tmp_path)) == {
        "path",
        "revision_id",
        "content_hash",
        "mtime_ns",
        "size_bytes",
        "captured_at",
        "note_content",
        "transcript_rows",
        "has_transcript",
    }
    assert set(
        serialize_file_change(
            FileChange(
                path=note.resolve(),
                change="modified",
                exists=True,
                revision_id="rev-2",
            ),
            workspace_root=tmp_path,
        )
    ) == {"path", "revision_id", "kind", "exists", "captured_at"}
    assert set(
        serialize_progress_event(
            ProgressEvent(kind="status", message="Working", path=note),
            workspace_root=tmp_path,
        )
    ) == {"kind", "message", "path", "details"}
    assert set(
        serialize_transcript_row(
            TranscriptRow(
                row_number=1,
                role="user",
                type="message",
                content="hello",
            )
        )
    ) == {"row_number", "role", "type", "tool_name", "tool_id", "content"}
    assert set(
        serialize_permission_request(
            PermissionRequest(
                tool_name="bash",
                action="run",
                target="pwd",
                message="Run command?",
                policy="ask",
            )
        )
    ) == {"tool_name", "action", "target", "message", "policy", "key", "details"}
    assert set(
        serialize_model_option(
            ModelOption(
                label="Codex",
                provider_name="codex",
                model="gpt-5.4",
            )
        )
    ) == {
        "label",
        "provider_name",
        "model",
        "profile_id",
        "context_window",
        "supports_images",
        "image_transport",
    }
