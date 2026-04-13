from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from difflib import SequenceMatcher
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from aunic.config import SETTINGS
from aunic.errors import FileReadError
from aunic.domain import ToolSpec
from aunic.tools.base import ToolDefinition, ToolExecutionResult
from aunic.tools.runtime import (
    PermissionRequest,
    ReadStateEntry,
    RunToolContext,
    failure_from_payload,
    failure_payload,
    stable_signature,
)

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - optional at runtime
    PdfReader = None  # type: ignore[assignment]

try:
    from PIL import Image
except Exception:  # pragma: no cover - optional at runtime
    Image = None  # type: ignore[assignment]


@dataclass(frozen=True)
class ReadArgs:
    file_path: str
    offset: int | None = None
    limit: int | None = None
    pages: str | None = None


@dataclass(frozen=True)
class EditArgs:
    file_path: str
    old_string: str
    new_string: str
    replace_all: bool = False


@dataclass(frozen=True)
class WriteArgs:
    file_path: str
    content: str


@dataclass(frozen=True)
class GrepArgs:
    pattern: str
    path: str | None = None
    include: str | None = None
    literal_text: bool = False


@dataclass(frozen=True)
class GlobArgs:
    pattern: str
    path: str | None = None


@dataclass(frozen=True)
class ListArgs:
    path: str | None = None
    ignore: tuple[str, ...] = ()


def build_read_tool_registry() -> tuple[ToolDefinition[Any], ...]:
    return (
        ToolDefinition(
            spec=ToolSpec(
                name="read",
                description="Read a file from the filesystem. Supports text, images, notebooks, and PDFs.",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["file_path"],
                    "properties": {
                        "file_path": {"type": "string"},
                        "offset": {"type": "integer"},
                        "limit": {"type": "integer"},
                        "pages": {"type": "string"},
                    },
                },
            ),
            parse_arguments=parse_read_args,
            execute=execute_read,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="grep",
                description="Search file contents with ripgrep or a filesystem fallback.",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["pattern"],
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string"},
                        "include": {"type": "string"},
                        "literal_text": {"type": "boolean"},
                    },
                },
            ),
            parse_arguments=parse_grep_args,
            execute=execute_grep,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="glob",
                description="Find files by glob pattern.",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["pattern"],
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string"},
                    },
                },
            ),
            parse_arguments=parse_glob_args,
            execute=execute_glob,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="list",
                description="List a directory tree.",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "path": {"type": "string"},
                        "ignore": {"type": "array", "items": {"type": "string"}},
                    },
                },
            ),
            parse_arguments=parse_list_args,
            execute=execute_list,
        ),
    )


def build_mutating_file_tool_registry() -> tuple[ToolDefinition[Any], ...]:
    return (
        ToolDefinition(
            spec=ToolSpec(
                name="edit",
                description="Edit a file with exact old_string/new_string replacement semantics.",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["file_path", "old_string", "new_string"],
                    "properties": {
                        "file_path": {"type": "string"},
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                        "replace_all": {"type": "boolean"},
                    },
                },
            ),
            parse_arguments=parse_edit_args,
            execute=execute_edit,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="write",
                description="Create or replace a whole file.",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["file_path", "content"],
                    "properties": {
                        "file_path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                },
            ),
            parse_arguments=parse_write_args,
            execute=execute_write,
        ),
    )


def parse_read_args(payload: dict[str, Any]) -> ReadArgs:
    _ensure_no_extra_keys(payload, {"file_path", "offset", "limit", "pages"})
    file_path = _require_string(payload, "file_path")
    offset = _optional_int(payload, "offset")
    limit = _optional_int(payload, "limit")
    pages = payload.get("pages")
    if pages is not None and not isinstance(pages, str):
        raise ValueError("`pages` must be a string.")
    return ReadArgs(file_path=file_path, offset=offset, limit=limit, pages=pages)


def parse_edit_args(payload: dict[str, Any]) -> EditArgs:
    _ensure_no_extra_keys(payload, {"file_path", "old_string", "new_string", "replace_all"})
    return EditArgs(
        file_path=_require_string(payload, "file_path"),
        old_string=_require_string(payload, "old_string"),
        new_string=_require_string(payload, "new_string"),
        replace_all=_optional_bool(payload, "replace_all", default=False),
    )


def parse_write_args(payload: dict[str, Any]) -> WriteArgs:
    _ensure_no_extra_keys(payload, {"file_path", "content"})
    return WriteArgs(
        file_path=_require_string(payload, "file_path"),
        content=_require_string(payload, "content"),
    )


def parse_grep_args(payload: dict[str, Any]) -> GrepArgs:
    _ensure_no_extra_keys(payload, {"pattern", "path", "include", "literal_text"})
    pattern = _require_string(payload, "pattern")
    if not pattern.strip():
        raise ValueError("`pattern` must not be empty.")
    return GrepArgs(
        pattern=pattern,
        path=_optional_string(payload, "path"),
        include=_optional_string(payload, "include"),
        literal_text=_optional_bool(payload, "literal_text", default=False),
    )


def parse_glob_args(payload: dict[str, Any]) -> GlobArgs:
    _ensure_no_extra_keys(payload, {"pattern", "path"})
    pattern = _require_string(payload, "pattern")
    if not pattern.strip():
        raise ValueError("`pattern` must not be empty.")
    return GlobArgs(pattern=pattern, path=_optional_string(payload, "path"))


def parse_list_args(payload: dict[str, Any]) -> ListArgs:
    _ensure_no_extra_keys(payload, {"path", "ignore"})
    raw_ignore = payload.get("ignore", [])
    if not isinstance(raw_ignore, list) or not all(isinstance(item, str) for item in raw_ignore):
        raise ValueError("`ignore` must be an array of strings.")
    return ListArgs(
        path=_optional_string(payload, "path"),
        ignore=tuple(raw_ignore),
    )


async def execute_read(runtime: RunToolContext, args: ReadArgs) -> ToolExecutionResult:
    signature_count = runtime.record_signature(
        "read",
        stable_signature("read", args.__dict__),
    )
    if signature_count >= 3:
        return _tool_error_result(
            "read",
            failure_payload(
                category="permission_denied",
                reason="doom_loop",
                message="Repeated identical read requests were blocked.",
                file_path=args.file_path,
            ),
        )
    path = runtime.normalize_path(args.file_path)
    permission = await _resolve_path_permission(
        runtime,
        tool_name="read",
        action="read",
        path=path,
        default_policy=SETTINGS.tools.permissions.default_read_policy,
    )
    if permission is not None:
        return permission
    try:
        metadata = await runtime.file_manager.read_metadata(path)
    except FileNotFoundError:
        return _tool_error_result(
            "read",
            failure_payload(
                category="validation_error",
                reason="not_found",
                message=f"File not found: {path}. Current working directory: {runtime.cwd}",
                file_path=str(path),
            ),
        )
    except Exception as exc:
        if isinstance(exc, OSError):
            return _tool_error_result(
                "read",
                failure_payload(
                    category="validation_error",
                    reason="read_failed",
                    message=f"Could not access file: {path}.",
                    file_path=str(path),
                ),
            )
        if isinstance(exc, FileReadError) and str(exc).startswith("File does not exist:"):
            return _tool_error_result(
                "read",
                failure_payload(
                    category="validation_error",
                    reason="not_found",
                    message=f"File not found: {path}. Current working directory: {runtime.cwd}",
                    file_path=str(path),
                ),
            )
        return _tool_error_result(
            "read",
            failure_payload(
                category="validation_error",
                reason="read_failed",
                message=str(exc),
                file_path=str(path),
            ),
        )
    if await _reject_special_read_path(path):
        return _tool_error_result(
            "read",
            failure_payload(
                category="validation_error",
                reason="unsupported_path",
                message=f"Cannot read special file path {path}.",
                file_path=str(path),
            ),
        )
    if path.is_dir():
        return _tool_error_result(
            "read",
            failure_payload(
                category="validation_error",
                reason="is_directory",
                message=f"{path} is a directory. Use list instead." if runtime.work_mode != "work" else f"{path} is a directory. Use list or bash instead.",
                file_path=str(path),
            ),
        )

    previous = runtime.session_state.read_entry(path)
    if previous is not None and previous.revision_id == metadata.revision_id and previous.offset == (args.offset or 1) and previous.limit == args.limit and previous.pages == args.pages:
        payload = {
            "type": "file_unchanged",
            "file_path": str(path),
            "message": "The earlier read result is still current.",
        }
        return ToolExecutionResult(
            tool_name="read",
            status="completed",
            in_memory_content=payload,
            transcript_content=payload,
        )

    suffix = path.suffix.lower()
    if suffix == ".ipynb":
        snapshot = await _read_text_snapshot(runtime, path)
        if isinstance(snapshot, ToolExecutionResult):
            return snapshot
        payload = _read_notebook(snapshot)
        runtime.session_state.record_read(
            ReadStateEntry(
                path=path,
                revision_id=snapshot.revision_id,
                mtime_ns=snapshot.mtime_ns,
                is_full_read=True,
                content=payload,
            )
        )
        return ToolExecutionResult("read", "completed", payload)
    if suffix == ".pdf":
        payload = _read_pdf(path, pages=args.pages)
        if isinstance(payload, dict) and payload.get("category"):
            return _tool_error_result("read", payload)
        runtime.session_state.record_read(
            ReadStateEntry(
                path=path,
                revision_id=metadata.revision_id,
                mtime_ns=metadata.mtime_ns,
                is_full_read=args.pages is None,
                pages=args.pages,
                content=payload,
            )
        )
        return ToolExecutionResult("read", "completed", payload)
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        payload = _read_image(path, metadata)
        if isinstance(payload, dict) and payload.get("category"):
            return _tool_error_result("read", payload)
        runtime.session_state.record_read(
            ReadStateEntry(
                path=path,
                revision_id=metadata.revision_id,
                mtime_ns=metadata.mtime_ns,
                is_full_read=True,
                content=payload,
            )
        )
        return ToolExecutionResult("read", "completed", payload)

    snapshot = await _read_text_snapshot(runtime, path)
    if isinstance(snapshot, ToolExecutionResult):
        return snapshot
    payload = _read_text(snapshot, offset=args.offset, limit=args.limit)
    if isinstance(payload, dict) and payload.get("category"):
        return _tool_error_result("read", payload)
    runtime.session_state.record_read(
        ReadStateEntry(
            path=path,
            revision_id=snapshot.revision_id,
            mtime_ns=snapshot.mtime_ns,
            is_full_read=bool(payload.get("is_full_read")),
            offset=int(payload.get("start_line", 1)),
            limit=args.limit,
            content=payload,
        )
    )
    return ToolExecutionResult("read", "completed", payload)


async def execute_edit(runtime: RunToolContext, args: EditArgs) -> ToolExecutionResult:
    signature_count = runtime.record_signature(
        "edit",
        stable_signature("edit", args.__dict__),
    )
    if signature_count >= 3:
        return _tool_error_result(
            "edit",
            failure_payload(
                category="permission_denied",
                reason="doom_loop",
                message="Repeated identical edit requests were blocked.",
                file_path=args.file_path,
            ),
        )
    path = runtime.normalize_path(args.file_path)
    if runtime.is_note_scope_path(path):
        return _tool_error_result(
            "edit",
            failure_payload(
                category="protected_rejection",
                reason="note_scope",
                message="Work-mode edit cannot modify note-content. Use note-edit instead.",
                file_path=str(path),
                target_identifier=str(path),
            ),
        )
    if path.suffix.lower() == ".ipynb":
        return _tool_error_result(
            "edit",
            failure_payload(
                category="validation_error",
                reason="notebook_unsupported",
                message="Notebook files are not editable with edit.",
                file_path=str(path),
            ),
        )
    permission = await _resolve_path_permission(
        runtime,
        tool_name="edit",
        action="write",
        path=path,
        default_policy=SETTINGS.tools.permissions.default_write_policy,
    )
    if permission is not None:
        return permission
    if args.old_string == args.new_string:
        return _tool_error_result(
            "edit",
            failure_payload(
                category="validation_error",
                reason="no_op",
                message="old_string and new_string must differ.",
                file_path=str(path),
            ),
        )
    exists = path.exists()
    if exists:
        snapshot = await runtime.file_manager.read_snapshot(path)
        stale = _stale_read_failure(runtime, path, snapshot)
        if stale is not None:
            return _tool_error_result("edit", stale)
        if args.old_string == "" and snapshot.raw_text:
            return _tool_error_result(
                "edit",
                failure_payload(
                    category="validation_error",
                    reason="empty_old_string_existing_file",
                    message="old_string may only be empty when creating a new file or replacing an empty file.",
                    file_path=str(path),
                ),
            )
        new_text, actual_old, error = _apply_exact_edit(
            snapshot.raw_text,
            old_string=args.old_string,
            new_string=args.new_string,
            replace_all=args.replace_all,
        )
        if error is not None:
            return _tool_error_result("edit", {**error, "file_path": str(path)})
        if new_text == snapshot.raw_text:
            return _tool_error_result(
                "edit",
                failure_payload(
                    category="validation_error",
                    reason="no_op",
                    message="Edit would leave the file unchanged.",
                    file_path=str(path),
                ),
            )
        written = await runtime.file_manager.write_text(
            path,
            new_text,
            expected_revision=snapshot.revision_id,
        )
        runtime.session_state.mark_written(written, content=new_text)
        payload = {
            "type": "file_edit",
            "file_path": str(path),
            "old_string": args.old_string,
            "new_string": args.new_string,
            "actual_old_string": actual_old,
            "replace_all": args.replace_all,
            "structured_patch": _build_structured_patch(snapshot.raw_text, new_text),
        }
        return ToolExecutionResult("edit", "completed", payload)

    if args.old_string != "":
        return _tool_error_result(
            "edit",
            failure_payload(
                category="validation_error",
                reason="not_found",
                message=f"File does not exist: {path}.",
                file_path=str(path),
            ),
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    written = await runtime.file_manager.write_text(path, args.new_string)
    runtime.session_state.mark_written(written, content=args.new_string)
    payload = {
        "type": "file_edit",
        "file_path": str(path),
        "old_string": "",
        "new_string": args.new_string,
        "actual_old_string": "",
        "replace_all": False,
        "structured_patch": _build_structured_patch("", args.new_string),
    }
    return ToolExecutionResult("edit", "completed", payload)


async def execute_write(runtime: RunToolContext, args: WriteArgs) -> ToolExecutionResult:
    signature_count = runtime.record_signature(
        "write",
        stable_signature("write", args.__dict__),
    )
    if signature_count >= 3:
        return _tool_error_result(
            "write",
            failure_payload(
                category="permission_denied",
                reason="doom_loop",
                message="Repeated identical write requests were blocked.",
                file_path=args.file_path,
            ),
        )
    path = runtime.normalize_path(args.file_path)
    if runtime.is_note_scope_path(path):
        return _tool_error_result(
            "write",
            failure_payload(
                category="protected_rejection",
                reason="note_scope",
                message="Work-mode write cannot modify note-content. Use note-write instead.",
                file_path=str(path),
                target_identifier=str(path),
            ),
        )
    permission = await _resolve_path_permission(
        runtime,
        tool_name="write",
        action="write",
        path=path,
        default_policy=SETTINGS.tools.permissions.default_write_policy,
    )
    if permission is not None:
        return permission
    old_text = ""
    expected_revision = None
    if path.exists():
        snapshot = await runtime.file_manager.read_snapshot(path)
        read_entry = runtime.session_state.read_entry(path)
        if read_entry is None or not read_entry.is_full_read:
            return _tool_error_result(
                "write",
                failure_payload(
                    category="validation_error",
                    reason="file_not_read",
                    message="File has not been fully read yet. Read it before overwriting it.",
                    file_path=str(path),
                ),
            )
        stale = _stale_read_failure(runtime, path, snapshot)
        if stale is not None:
            return _tool_error_result("write", stale)
        old_text = snapshot.raw_text
        expected_revision = snapshot.revision_id
    path.parent.mkdir(parents=True, exist_ok=True)
    written = await runtime.file_manager.write_text(path, args.content, expected_revision=expected_revision)
    runtime.session_state.mark_written(written, content=args.content)
    payload = {
        "type": "file_write",
        "file_path": str(path),
        "content": args.content,
        "original_content": old_text,
        "structured_patch": _build_structured_patch(old_text, args.content),
    }
    return ToolExecutionResult("write", "completed", payload)


async def execute_grep(runtime: RunToolContext, args: GrepArgs) -> ToolExecutionResult:
    signature_count = runtime.record_signature("grep", stable_signature("grep", args.__dict__))
    if signature_count >= 3:
        return _tool_error_result(
            "grep",
            failure_payload(
                category="permission_denied",
                reason="doom_loop",
                message="Repeated identical grep requests were blocked.",
                pattern=args.pattern,
            ),
        )
    root = runtime.normalize_path(args.path or runtime.cwd)
    permission = await _resolve_path_permission(
        runtime,
        tool_name="grep",
        action="read",
        path=root,
        default_policy=SETTINGS.tools.permissions.default_read_policy,
        key_override=f"grep:{args.pattern}:{root}",
        target_override=args.pattern,
    )
    if permission is not None:
        return permission
    text = _run_grep(root=root, args=args)
    return ToolExecutionResult("grep", "completed", text)


async def execute_glob(runtime: RunToolContext, args: GlobArgs) -> ToolExecutionResult:
    signature_count = runtime.record_signature("glob", stable_signature("glob", args.__dict__))
    if signature_count >= 3:
        return _tool_error_result(
            "glob",
            failure_payload(
                category="permission_denied",
                reason="doom_loop",
                message="Repeated identical glob requests were blocked.",
                pattern=args.pattern,
            ),
        )
    root = runtime.normalize_path(args.path or runtime.cwd)
    permission = await _resolve_path_permission(
        runtime,
        tool_name="glob",
        action="read",
        path=root,
        default_policy=SETTINGS.tools.permissions.default_read_policy,
        key_override=f"glob:{args.pattern}:{root}",
        target_override=args.pattern,
    )
    if permission is not None:
        return permission
    text = _run_glob(root=root, pattern=args.pattern)
    return ToolExecutionResult("glob", "completed", text)


async def execute_list(runtime: RunToolContext, args: ListArgs) -> ToolExecutionResult:
    signature_count = runtime.record_signature("list", stable_signature("list", args.__dict__))
    if signature_count >= 3:
        return _tool_error_result(
            "list",
            failure_payload(
                category="permission_denied",
                reason="doom_loop",
                message="Repeated identical list requests were blocked.",
                path=args.path or str(runtime.cwd),
            ),
        )
    root = runtime.normalize_path(args.path or runtime.cwd)
    permission = await _resolve_path_permission(
        runtime,
        tool_name="list",
        action="read",
        path=root,
        default_policy=SETTINGS.tools.permissions.default_read_policy,
    )
    if permission is not None:
        return permission
    text = _build_tree_text(root, ignore=args.ignore)
    return ToolExecutionResult("list", "completed", text)


async def _resolve_path_permission(
    runtime: RunToolContext,
    *,
    tool_name: str,
    action: str,
    path: Path,
    default_policy: str,
    key_override: str | None = None,
    target_override: str | None = None,
) -> ToolExecutionResult | None:
    project_root = runtime.cwd
    policy = "allow"
    message = f"{tool_name} is requesting access to {path}."
    if not path.is_relative_to(project_root):
        policy = SETTINGS.tools.permissions.external_directory_policy
        message = f"{tool_name} is requesting access outside the working directory: {path}"
    elif default_policy in {"allow", "ask", "deny"}:
        policy = default_policy
    decision = await runtime.resolve_permission(
        PermissionRequest(
            tool_name=tool_name,
            action=action,
            target=target_override or str(path),
            message=message,
            policy=policy,  # type: ignore[arg-type]
            key=key_override or f"{tool_name}:{action}:{path}",
            details={"path": str(path)},
        )
    )
    if decision.allowed:
        return None
    return _tool_error_result(
        tool_name,
        failure_payload(
            category="permission_denied",
            reason=decision.reason if decision.reason != "policy" else "deny_rule",
            message=f"{tool_name} was not allowed to access {path}.",
            path=str(path),
        ),
        status="protected_rejection",
    )


async def _reject_special_read_path(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode)


def _read_text(snapshot, *, offset: int | None, limit: int | None) -> dict[str, Any]:
    if snapshot.size_bytes > SETTINGS.tools.read_max_bytes and limit is None:
        return failure_payload(
            category="validation_error",
            reason="file_too_large",
            message="File is too large to read at once. Use offset/limit for a smaller range.",
            file_path=str(snapshot.path),
        )
    text = snapshot.raw_text.replace("\r\n", "\n").lstrip("\ufeff")
    lines = text.split("\n")
    start_line = max(1, offset or 1)
    start_index = start_line - 1
    end_index = len(lines) if limit is None else min(len(lines), start_index + limit)
    selected = lines[start_index:end_index]
    numbered = "\n".join(f"{start_line + index}: {line}" for index, line in enumerate(selected))
    if _estimate_tokens(numbered) > SETTINGS.tools.read_max_output_tokens:
        return failure_payload(
            category="validation_error",
            reason="token_limit",
            message="Read result would exceed the output token limit.",
            file_path=str(snapshot.path),
        )
    return {
        "type": "text_file",
        "file_path": str(snapshot.path),
        "content": numbered,
        "start_line": start_line,
        "num_lines": len(selected),
        "total_lines": len(lines),
        "is_full_read": start_line == 1 and end_index == len(lines),
    }


async def _read_text_snapshot(runtime: RunToolContext, path: Path):
    try:
        return await runtime.file_manager.read_snapshot(path)
    except FileReadError as exc:
        message = str(exc)
        if "valid UTF-8" in message:
            return _tool_error_result(
                "read",
                failure_payload(
                    category="validation_error",
                    reason="invalid_utf8",
                    message=message,
                    file_path=str(path),
                ),
            )
        return _tool_error_result(
            "read",
            failure_payload(
                category="validation_error",
                reason="read_failed",
                message=message,
                file_path=str(path),
            ),
        )


def _read_notebook(snapshot) -> dict[str, Any]:
    try:
        notebook = json.loads(snapshot.raw_text)
    except json.JSONDecodeError:
        return failure_payload(
            category="validation_error",
            reason="invalid_notebook",
            message="Notebook is not valid JSON.",
            file_path=str(snapshot.path),
        )
    if _estimate_tokens(snapshot.raw_text) > SETTINGS.tools.read_max_output_tokens:
        return failure_payload(
            category="validation_error",
            reason="token_limit",
            message="Notebook content would exceed the output token limit.",
            file_path=str(snapshot.path),
        )
    return {
        "type": "notebook",
        "file_path": str(snapshot.path),
        "content": notebook,
    }


def _read_pdf(path: Path, *, pages: str | None) -> dict[str, Any]:
    if PdfReader is None:
        return failure_payload(
            category="validation_error",
            reason="pdf_unavailable",
            message="PDF reading is unavailable because pypdf is not installed.",
            file_path=str(path),
        )
    try:
        reader = PdfReader(str(path))
    except Exception:
        return failure_payload(
            category="validation_error",
            reason="invalid_pdf",
            message=f"PDF file is corrupted or unreadable: {path}",
            file_path=str(path),
        )
    page_indexes = _parse_pdf_pages(pages, len(reader.pages))
    if isinstance(page_indexes, dict):
        page_indexes.setdefault("file_path", str(path))
        return page_indexes
    if len(page_indexes) > SETTINGS.tools.read_max_pdf_pages:
        return failure_payload(
            category="validation_error",
            reason="page_limit",
            message="Requested PDF page range exceeds the configured limit.",
            file_path=str(path),
        )
    extracted: list[str] = []
    for index in page_indexes:
        try:
            text = reader.pages[index].extract_text() or ""
        except Exception:
            return failure_payload(
                category="validation_error",
                reason="invalid_pdf",
                message=f"PDF file is corrupted or unreadable: {path}",
                file_path=str(path),
            )
        extracted.append(f"Page {index + 1}:\n{text.strip()}")
    content = "\n\n".join(item for item in extracted if item.strip())
    if _estimate_tokens(content) > SETTINGS.tools.read_max_output_tokens:
        return failure_payload(
            category="validation_error",
            reason="token_limit",
            message="PDF content would exceed the output token limit.",
            file_path=str(path),
        )
    return {
        "type": "pdf",
        "file_path": str(path),
        "pages": pages or f"1-{len(reader.pages)}",
        "content": content,
    }


def _read_image(path: Path, metadata) -> dict[str, Any]:
    if metadata.size_bytes == 0:
        return failure_payload(
            category="validation_error",
            reason="empty_image",
            message="Image file is empty.",
            file_path=str(path),
        )
    width = height = None
    format_name = path.suffix.lower().lstrip(".")
    if Image is not None:
        try:
            with Image.open(path) as img:
                width, height = img.size
                format_name = (img.format or format_name).lower()
        except Exception:
            return failure_payload(
                category="validation_error",
                reason="invalid_image",
                message=f"Image file is corrupted or unreadable: {path}",
                file_path=str(path),
            )
    return {
        "type": "image",
        "file_path": str(path),
        "format": format_name,
        "size_bytes": metadata.size_bytes,
        "width": width,
        "height": height,
    }


def _parse_pdf_pages(pages: str | None, total_pages: int) -> list[int] | dict[str, Any]:
    if pages is None or not pages.strip():
        return list(range(total_pages))
    text = pages.strip()
    if "-" in text:
        start_text, end_text = text.split("-", 1)
        if not start_text.isdigit() or not end_text.isdigit():
            return failure_payload(
                category="validation_error",
                reason="invalid_pages",
                message=f"Invalid PDF page range {pages!r}.",
            )
        start = int(start_text)
        end = int(end_text)
        if start <= 0 or end < start or end > total_pages:
            return failure_payload(
                category="validation_error",
                reason="invalid_pages",
                message=f"Invalid PDF page range {pages!r}.",
            )
        return list(range(start - 1, end))
    if not text.isdigit():
        return failure_payload(
            category="validation_error",
            reason="invalid_pages",
            message=f"Invalid PDF page range {pages!r}.",
        )
    page = int(text)
    if page <= 0 or page > total_pages:
        return failure_payload(
            category="validation_error",
            reason="invalid_pages",
            message=f"Invalid PDF page range {pages!r}.",
        )
    return [page - 1]


def _run_grep(*, root: Path, args: GrepArgs) -> str:
    if shutil.which("rg"):
        command = ["rg", "-n", "--no-heading", "--color", "never"]
        if args.literal_text:
            command.append("-F")
        if args.include:
            command.extend(["-g", args.include])
        command.extend([args.pattern, str(root)])
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode not in {0, 1}:
            raise ValueError(result.stderr.strip() or "ripgrep failed")
        matches = _parse_grep_output(result.stdout, root)
    else:
        matches = _grep_fallback(root, args)
    if not matches:
        return "No files found"
    matches.sort(key=lambda item: (-item["mtime_ns"], item["file"], item["line"]))
    visible = matches[: SETTINGS.tools.grep_max_matches]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in visible:
        grouped.setdefault(item["file"], []).append(item)
    lines = [f"Found {len(visible)} matches"]
    for file_path, file_matches in grouped.items():
        lines.append(file_path + ":")
        for item in file_matches:
            lines.append(f"  Line {item['line']}: {item['text']}")
        lines.append("")
    if len(matches) > SETTINGS.tools.grep_max_matches:
        lines.append("(Results are truncated. Consider using a more specific path or pattern.)")
    return "\n".join(lines).rstrip()


def _parse_grep_output(stdout: str, root: Path) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        path_text, sep, remainder = line.partition(":")
        if not sep:
            continue
        line_text, sep, content = remainder.partition(":")
        if not sep or not line_text.isdigit():
            continue
        path = Path(path_text)
        if not path.is_absolute():
            path = (root / path).resolve()
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        matches.append(
            {"file": str(path), "line": int(line_text), "text": content, "mtime_ns": mtime_ns}
        )
    return matches


def _grep_fallback(root: Path, args: GrepArgs) -> list[dict[str, Any]]:
    matcher = re.compile(re.escape(args.pattern) if args.literal_text else args.pattern)
    matches: list[dict[str, Any]] = []
    for path in root.rglob("*"):
        if not path.is_file() or _skip_hidden_or_junk(path):
            continue
        if args.include and not fnmatch(path.name, args.include):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for index, line in enumerate(text.splitlines(), start=1):
            if matcher.search(line):
                matches.append(
                    {"file": str(path.resolve()), "line": index, "text": line, "mtime_ns": path.stat().st_mtime_ns}
                )
    return matches


def _run_glob(*, root: Path, pattern: str) -> str:
    if shutil.which("rg"):
        rg_pattern = pattern if pattern.startswith("/") else f"/{pattern}"
        result = subprocess.run(
            ["rg", "--files", "-g", rg_pattern],
            capture_output=True,
            text=True,
            cwd=root,
            check=False,
        )
        if result.returncode not in {0, 1}:
            raise ValueError(result.stderr.strip() or "ripgrep failed")
        matches = [str((root / line.strip()).resolve()) for line in result.stdout.splitlines() if line.strip()]
        matches = [item for item in matches if Path(item).is_file()]
        matches.sort(key=lambda item: (len(item), item.casefold()))
    else:
        matches = [
            str(path.resolve())
            for path in root.glob(pattern)
            if path.is_file() and not _skip_hidden_or_junk(path)
        ]
        matches.sort(key=lambda item: (-Path(item).stat().st_mtime_ns, item.casefold()))
    if not matches:
        return "No files found"
    visible = matches[: SETTINGS.tools.glob_max_matches]
    lines = list(visible)
    if len(matches) >= SETTINGS.tools.glob_max_matches:
        lines.append("(Results are truncated. Consider using a more specific path or pattern.)")
    return "\n".join(lines)


def _build_tree_text(root: Path, *, ignore: tuple[str, ...]) -> str:
    if not root.exists():
        raise ValueError(f"Path does not exist: {root}")
    lines = [f"- {root}/"]
    count = 0
    truncated = False

    def walk(path: Path, prefix: str) -> None:
        nonlocal count, truncated
        if truncated:
            return
        try:
            entries = list(path.iterdir())
        except OSError:
            return
        for entry in entries:
            if _skip_hidden_or_junk(entry):
                continue
            if any(fnmatch(entry.name, pattern) for pattern in ignore):
                continue
            count += 1
            if count > SETTINGS.tools.list_max_entries:
                truncated = True
                return
            label = entry.name + ("/" if entry.is_dir() else "")
            lines.append(f"{prefix}- {label}")
            if entry.is_dir():
                walk(entry, prefix + "  ")

    walk(root, "  ")
    if truncated:
        lines.insert(
            0,
            "There are more than 1000 files in the directory. Use a more specific path or use the Glob tool to find specific files. The first 1000 files and directories are included below:",
        )
    return "\n".join(lines)


def _skip_hidden_or_junk(path: Path) -> bool:
    parts = path.parts
    builtins = {
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        "target",
        "vendor",
        "bin",
        "obj",
        ".git",
        ".idea",
        ".vscode",
        ".DS_Store",
    }
    if any(part.startswith(".") for part in parts if part not in {".", ".."}):
        return True
    return any(part in builtins for part in parts) or "__pycache__" in path.as_posix()


def _apply_exact_edit(
    original: str,
    *,
    old_string: str,
    new_string: str,
    replace_all: bool,
) -> tuple[str, str, dict[str, Any] | None]:
    candidate_old = old_string
    actual_old = candidate_old
    occurrences = original.count(candidate_old)
    if occurrences == 0 and "'" in old_string:
        smart_old = old_string.replace("'", "’")
        occurrences = original.count(smart_old)
        if occurrences > 0:
            candidate_old = smart_old
            actual_old = smart_old
            new_string = new_string.replace("'", "’")
    if occurrences == 0:
        return original, actual_old, failure_payload(
            category="validation_error",
            reason="old_string_not_found",
            message="old_string was not found in the file.",
        )
    if not replace_all and occurrences != 1:
        return original, actual_old, failure_payload(
            category="validation_error",
            reason="multiple_matches",
            message=f"old_string matched {occurrences} times. Add more context or set replace_all to true.",
            match_count=occurrences,
        )
    updated = (
        original.replace(candidate_old, new_string)
        if replace_all
        else original.replace(candidate_old, new_string, 1)
    )
    return updated, actual_old, None


def _build_structured_patch(old_text: str, new_text: str) -> list[dict[str, Any]]:
    matcher = SequenceMatcher(a=old_text.splitlines(), b=new_text.splitlines())
    patch: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        patch.append(
            {
                "op": tag,
                "old_start_line": i1 + 1,
                "old_end_line": i2,
                "new_start_line": j1 + 1,
                "new_end_line": j2,
                "old_lines": old_text.splitlines()[i1:i2],
                "new_lines": new_text.splitlines()[j1:j2],
            }
        )
    return patch


def _stale_read_failure(runtime: RunToolContext, path: Path, snapshot) -> dict[str, Any] | None:
    read_entry = runtime.session_state.read_entry(path)
    if read_entry is None or not read_entry.is_full_read:
        return failure_payload(
            category="validation_error",
            reason="file_not_read",
            message="File has not been fully read yet. Read it before editing or writing.",
            file_path=str(path),
        )
    if read_entry.revision_id != snapshot.revision_id:
        return failure_payload(
            category="validation_error",
            reason="stale_read",
            message="The file changed after it was read. Read it again before modifying it.",
            file_path=str(path),
        )
    return None


def _tool_error_result(
    tool_name: str,
    payload: dict[str, Any],
    *,
    status: str = "tool_error",
) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=tool_name,
        status=status,
        in_memory_content=payload,
        transcript_content=payload,
        tool_failure=failure_from_payload(payload, tool_name=tool_name),
    )


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _require_string(payload: dict[str, Any], key: str) -> str:
    if key not in payload:
        raise ValueError(f"Missing required field `{key}`.")
    value = payload[key]
    if not isinstance(value, str):
        raise ValueError(f"`{key}` must be a string.")
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"`{key}` must be a string.")
    return value


def _optional_bool(payload: dict[str, Any], key: str, *, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"`{key}` must be a boolean.")
    return value


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError(f"`{key}` must be an integer.")
    return value


def _ensure_no_extra_keys(payload: dict[str, Any], expected: set[str]) -> None:
    extras = sorted(set(payload) - expected)
    if extras:
        raise ValueError(f"Unexpected fields: {', '.join(extras)}.")
