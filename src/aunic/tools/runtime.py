from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal

from aunic.domain import WorkMode
from aunic.progress import ProgressEvent, emit_progress
from aunic.proto_settings import get_tool_policy_override
from aunic.transcript.parser import split_note_and_transcript
from aunic.transcript.writer import append_transcript_row

if TYPE_CHECKING:
    from aunic.context.file_manager import FileManager
    from aunic.context.types import ContextBuildResult, FileSnapshot, PromptRun
    from aunic.loop.types import ToolFailure
    from aunic.research.fetch import FetchService
    from aunic.research.search import SearchService
    from aunic.research.types import ResearchState

PermissionPolicy = Literal["allow", "ask", "deny"]
PermissionResolution = Literal["once", "always", "reject"]
PermissionHandler = Callable[["PermissionRequest"], Awaitable[PermissionResolution]]


@dataclass(frozen=True)
class ActiveMarkdownNote:
    path: Path
    note_scope_paths: tuple[Path, ...]


@dataclass(frozen=True)
class PermissionRequest:
    tool_name: str
    action: str
    target: str
    message: str
    policy: PermissionPolicy = "allow"
    key: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    resolution: PermissionResolution | None
    reason: str


@dataclass(frozen=True)
class ReadStateEntry:
    path: Path
    revision_id: str
    mtime_ns: int
    is_full_read: bool
    offset: int | None = None
    limit: int | None = None
    pages: str | None = None
    content: Any = None


BackgroundProcessStatus = Literal["running", "stopped", "exited", "failed"]


@dataclass
class BackgroundProcessState:
    background_id: str
    process: asyncio.subprocess.Process
    command: str
    description: str | None
    cwd: Path
    pid: int
    pgid: int
    started_at: datetime
    started_monotonic: float
    status: BackgroundProcessStatus = "running"
    returncode: int | None = None
    ended_at: datetime | None = None
    signals_sent: tuple[str, ...] = ()
    stop_reason: str | None = None


@dataclass
class ShellSessionState:
    cwd: Path
    base_env: dict[str, str] | None = None
    env_overlays: dict[str, str] = field(default_factory=dict)
    background_processes: dict[str, BackgroundProcessState] = field(default_factory=dict)
    next_background_id: int = 1

    def register_background_process(self, state: BackgroundProcessState) -> None:
        self.background_processes[state.background_id] = state

    def get_background_process(self, background_id: str) -> BackgroundProcessState | None:
        return self.background_processes.get(background_id)

    def next_bg_id(self) -> str:
        background_id = f"bg-{self.next_background_id}"
        self.next_background_id += 1
        return background_id


class ToolSessionState:
    def __init__(self, *, cwd: Path | None = None) -> None:
        resolved_cwd = (cwd or Path.cwd()).expanduser().resolve()
        self.cwd = resolved_cwd
        self.read_state: dict[Path, ReadStateEntry] = {}
        self.always_allow_keys: set[str] = set()
        self._doom_loop_counts: dict[tuple[str, str], int] = {}
        self._doom_loop_last: dict[str, str] = {}
        self.shell = ShellSessionState(cwd=resolved_cwd)
        self.active_plan_id: str | None = None
        self.active_plan_path: Path | None = None
        self.planning_status: str = "none"
        self.pre_plan_work_mode: WorkMode | None = None

    def record_read(self, entry: ReadStateEntry) -> None:
        self.read_state[entry.path] = entry

    def mark_written(self, snapshot: "FileSnapshot", *, content: Any) -> None:
        self.read_state[snapshot.path] = ReadStateEntry(
            path=snapshot.path,
            revision_id=snapshot.revision_id,
            mtime_ns=snapshot.mtime_ns,
            is_full_read=True,
            content=content,
        )

    def read_entry(self, path: Path) -> ReadStateEntry | None:
        return self.read_state.get(path)

    def record_signature(self, tool_name: str, signature: str) -> int:
        last = self._doom_loop_last.get(tool_name)
        if last == signature:
            count = self._doom_loop_counts.get((tool_name, signature), 0) + 1
        else:
            if last is not None:
                self._doom_loop_counts.pop((tool_name, last), None)
            count = 1
        self._doom_loop_last[tool_name] = signature
        self._doom_loop_counts[(tool_name, signature)] = count
        return count


@dataclass
class RunToolContext:
    file_manager: "FileManager"
    context_result: "ContextBuildResult | None"
    prompt_run: "PromptRun | None"
    active_file: Path
    session_state: ToolSessionState
    search_service: "SearchService"
    fetch_service: "FetchService"
    research_state: "ResearchState"
    progress_sink: Any
    work_mode: WorkMode
    permission_handler: PermissionHandler | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    active_markdown_note: ActiveMarkdownNote | None = None
    working_note_content: str = ""
    note_baseline_content: str = ""
    working_parsed_content: str = ""
    active_plan_id: str | None = None
    active_plan_path: Path | None = None
    planning_status: str = "none"
    working_plan_content: str = ""
    plan_baseline_content: str = ""

    @classmethod
    async def create(
        cls,
        *,
        file_manager: "FileManager",
        context_result: "ContextBuildResult | None",
        prompt_run: "PromptRun | None",
        active_file: Path,
        session_state: ToolSessionState,
        search_service: "SearchService",
        fetch_service: "FetchService",
        research_state: "ResearchState",
        progress_sink: Any,
        work_mode: WorkMode,
        permission_handler: PermissionHandler | None,
        metadata: dict[str, Any],
        active_plan_id: str | None = None,
        active_plan_path: Path | None = None,
        planning_status: str = "none",
    ) -> "RunToolContext":
        snapshot = await file_manager.read_snapshot(active_file)
        note_text, _ = split_note_and_transcript(snapshot.raw_text)
        note_scope_paths = [active_file.expanduser().resolve()]
        parsed_content = note_text
        if context_result and context_result.parsed_files:
            pf = context_result.parsed_files[0]
            parsed_content = pf.hinted_parsed_text or pf.parsed_text or note_text
        resolved_plan_path = (
            active_plan_path.expanduser().resolve()
            if active_plan_path is not None
            else session_state.active_plan_path
        )
        resolved_plan_id = active_plan_id or session_state.active_plan_id
        resolved_planning_status = (
            planning_status
            if planning_status != "none"
            else session_state.planning_status
        )
        plan_content = ""
        if resolved_plan_path is not None:
            try:
                plan_content = resolved_plan_path.read_text(encoding="utf-8")
            except OSError:
                plan_content = ""
        session_state.active_plan_id = resolved_plan_id
        session_state.active_plan_path = resolved_plan_path
        session_state.planning_status = resolved_planning_status
        return cls(
            file_manager=file_manager,
            context_result=context_result,
            prompt_run=prompt_run,
            active_file=active_file.expanduser().resolve(),
            session_state=session_state,
            search_service=search_service,
            fetch_service=fetch_service,
            research_state=research_state,
            progress_sink=progress_sink,
            work_mode=work_mode,
            permission_handler=permission_handler,
            metadata=dict(metadata),
            active_markdown_note=ActiveMarkdownNote(
                path=active_file.expanduser().resolve(),
                note_scope_paths=tuple(dict.fromkeys(note_scope_paths)),
            ),
            working_note_content=note_text,
            note_baseline_content=note_text,
            working_parsed_content=parsed_content,
            active_plan_id=resolved_plan_id,
            active_plan_path=resolved_plan_path,
            planning_status=resolved_planning_status,
            working_plan_content=plan_content,
            plan_baseline_content=plan_content,
        )

    @property
    def cwd(self) -> Path:
        raw = self.metadata.get("cwd")
        if isinstance(raw, str) and raw.strip():
            return Path(raw).expanduser().resolve()
        return self.session_state.cwd

    async def emit_status(self, message: str, *, kind: str = "status") -> None:
        await emit_progress(
            self.progress_sink,
            ProgressEvent(
                kind=kind,  # type: ignore[arg-type]
                message=message,
                path=self.active_file,
            ),
        )

    def normalize_path(self, path: str | Path) -> Path:
        raw = Path(path).expanduser()
        if raw.is_absolute():
            return raw.resolve()
        return (self.cwd / raw).resolve()

    def note_scope_paths(self) -> tuple[Path, ...]:
        if self.active_markdown_note is None:
            return ()
        return self.active_markdown_note.note_scope_paths

    def is_note_scope_path(self, path: Path) -> bool:
        resolved = path.expanduser().resolve()
        return resolved in self.note_scope_paths()

    def note_snapshot_text(self) -> str:
        parts = [
            (
                "NOTE SNAPSHOT\n"
                f"ACTIVE MARKDOWN NOTE: {self.active_file}\n"
                "EDITABLE BUFFER: note-content only\n\n"
                f"{self.working_parsed_content}"
            )
        ]
        if self.prompt_run is not None and self.prompt_run.target_map_text:
            parts.append(f"TARGET MAP\n{self.prompt_run.target_map_text}")
        if self.prompt_run is not None and self.prompt_run.read_only_map_text:
            parts.append(f"READ-ONLY MAP\n{self.prompt_run.read_only_map_text}")
        if self.planning_status in {"drafting", "awaiting_approval"} and self.active_plan_path:
            plan_text = self.working_plan_content
            if not plan_text:
                try:
                    plan_text = self.active_plan_path.read_text(encoding="utf-8")
                except OSError:
                    plan_text = ""
            if plan_text:
                parts.append(
                    "PLAN DRAFT\n"
                    f"PLAN FILE: {self.active_plan_path}\n"
                    f"STATUS: {self.planning_status}\n\n"
                    f"{plan_text}"
                )
        return "\n\n".join(part for part in parts if part.strip())

    async def resolve_permission(self, request: PermissionRequest) -> PermissionDecision:
        override_policy = get_tool_policy_override(self.cwd, request.tool_name)
        effective_policy = override_policy or request.policy
        if request.key and request.key in self.session_state.always_allow_keys:
            return PermissionDecision(True, "always", "rule")
        if effective_policy == "allow":
            return PermissionDecision(True, None, "policy")
        if effective_policy == "deny":
            return PermissionDecision(False, None, "policy")
        if self.permission_handler is None:
            return PermissionDecision(False, None, "no_handler")
        resolution = await self.permission_handler(request)
        if resolution == "always" and request.key:
            self.session_state.always_allow_keys.add(request.key)
            return PermissionDecision(True, resolution, "user_allow")
        if resolution == "once":
            return PermissionDecision(True, resolution, "user_allow")
        return PermissionDecision(False, resolution, "user_reject")

    def record_signature(self, tool_name: str, signature: str) -> int:
        return self.session_state.record_signature(tool_name, signature)

    async def read_snapshot(self, path: str | Path) -> "FileSnapshot":
        return await self.file_manager.read_snapshot(self.normalize_path(path))

    async def read_active_plan(self) -> tuple[Path, str]:
        if self.active_plan_path is None:
            raise FileNotFoundError("No active plan is selected.")
        snapshot = await self.file_manager.read_snapshot(self.active_plan_path)
        return snapshot.path, snapshot.raw_text

    async def write_active_plan(
        self,
        new_text: str,
        *,
        expected_revision: str | None = None,
    ) -> "FileSnapshot":
        if self.active_plan_path is None:
            raise FileNotFoundError("No active plan is selected.")
        written = await self.file_manager.write_text(
            self.active_plan_path,
            new_text,
            expected_revision=expected_revision,
        )
        self.working_plan_content = new_text
        self.plan_baseline_content = new_text
        return written

    def is_plan_scope_path(self, path: Path) -> bool:
        return self.active_plan_path is not None and path.expanduser().resolve() == self.active_plan_path

    def set_active_plan(
        self,
        *,
        plan_id: str | None,
        path: Path | None,
        planning_status: str,
        content: str = "",
    ) -> None:
        resolved_path = path.expanduser().resolve() if path is not None else None
        self.active_plan_id = plan_id
        self.active_plan_path = resolved_path
        self.planning_status = planning_status
        self.working_plan_content = content
        self.plan_baseline_content = content
        self.session_state.active_plan_id = plan_id
        self.session_state.active_plan_path = resolved_path
        self.session_state.planning_status = planning_status

    def set_planning_status(self, planning_status: str) -> None:
        self.planning_status = planning_status
        self.session_state.planning_status = planning_status

    async def write_transcript_row(
        self,
        role: str,
        row_type: str,
        tool_name: str | None,
        tool_id: str | None,
        content: Any,
    ) -> int:
        snapshot = await self.file_manager.read_snapshot(self.active_file)
        updated_text, row_number = append_transcript_row(
            snapshot.raw_text,
            role,  # type: ignore[arg-type]
            row_type,  # type: ignore[arg-type]
            tool_name,
            tool_id,
            content,
        )
        written = await self.file_manager.write_text(
            self.active_file,
            updated_text,
            expected_revision=snapshot.revision_id,
        )
        await emit_progress(
            self.progress_sink,
            ProgressEvent(
                kind="file_written",
                message="Updated the active file transcript.",
                path=self.active_file,
                details={
                    "reason": "transcript_row_append",
                    "revision_id": written.revision_id,
                    "row_number": row_number,
                    "role": role,
                    "type": row_type,
                },
            ),
        )
        return row_number

    async def write_text(
        self,
        path: str | Path,
        new_text: str,
        *,
        expected_revision: str | None = None,
    ) -> "FileSnapshot":
        snapshot = await self.file_manager.write_text(
            self.normalize_path(path),
            new_text,
            expected_revision=expected_revision,
        )
        return snapshot

    async def read_live_note(self) -> tuple["FileSnapshot", str, str | None]:
        snapshot = await self.file_manager.read_snapshot(self.active_file)
        note_text, transcript_text = split_note_and_transcript(snapshot.raw_text)
        return snapshot, note_text, transcript_text

    async def write_live_note_content(
        self,
        new_note_content: str,
        *,
        expected_revision: str | None = None,
    ) -> "FileSnapshot":
        from aunic.context.markers import reparse_hinted_text

        snapshot, _, transcript_text = await self.read_live_note()
        full_text = join_note_and_transcript(new_note_content, transcript_text)
        written = await self.file_manager.write_text(
            self.active_file,
            full_text,
            expected_revision=expected_revision or snapshot.revision_id,
        )
        self.working_note_content = new_note_content
        self.note_baseline_content = new_note_content
        self.working_parsed_content = reparse_hinted_text(new_note_content, self.active_file)
        return written


def failure_payload(
    *,
    category: str,
    reason: str,
    message: str,
    **extra: Any,
) -> dict[str, Any]:
    payload = {
        "category": category,
        "reason": reason,
        "message": message,
    }
    payload.update(extra)
    return payload


def failure_from_payload(
    payload: dict[str, Any],
    *,
    tool_name: str | None,
    default_category: str = "validation_error",
) -> "ToolFailure":
    from aunic.loop.types import ToolFailure

    category = payload.get("category")
    mapped = str(category) if isinstance(category, str) else default_category
    return ToolFailure(
        category=_tool_failure_category(mapped),
        reason=str(payload.get("reason", "unknown")),
        tool_name=tool_name,
        message=str(payload.get("message", "Tool failed.")),
        target_identifier=str(payload.get("target_identifier")) if payload.get("target_identifier") is not None else None,
        details={k: v for k, v in payload.items() if k not in {"category", "reason", "message"}},
    )


def _tool_failure_category(category: str) -> str:
    if category in {
        "malformed_turn",
        "validation_error",
        "protected_rejection",
        "conflict",
        "provider_error",
        "permission_denied",
        "user_cancel",
        "execution_error",
        "timeout",
    }:
        return category
    return "validation_error"


def join_note_and_transcript(note_text: str, transcript_text: str | None) -> str:
    stripped_note = note_text.rstrip("\n")
    if not transcript_text:
        return stripped_note
    if not stripped_note:
        return transcript_text.lstrip("\n")
    return f"{stripped_note}\n\n{transcript_text.lstrip('\n')}"


def stable_signature(tool_name: str, payload: dict[str, Any]) -> str:
    return f"{tool_name}:{json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
