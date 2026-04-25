from __future__ import annotations

import asyncio
import logging
import re
import shutil
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from aunic.browser.errors import (
    BrowserError,
    PermissionRequestNotFound,
    RevisionConflict,
    RunInProgress,
)
from aunic.browser.messages import (
    PERMISSION_RESOLUTIONS,
    serialize_file_change,
    serialize_file_snapshot,
    serialize_model_option,
    serialize_permission_request,
    serialize_progress_event,
    serialize_transcript_row,
)
from aunic.browser.paths import (
    WorkspacePathError,
    resolve_workspace_directory,
    resolve_workspace_path,
    workspace_relative_path,
)
from aunic.browser.watch_hub import FileWatchHub
from aunic.context import ContextEngine, FileChange, FileManager
from aunic.domain import ProviderImageInput, ReasoningEffort, WorkMode
from aunic.errors import ChatModeError, FileReadError, NoteModeError, OptimisticWriteError
from aunic.file_ui_state import (
    IncludeEntry,
    ProjectIncludeState,
    load_project_include_state,
    normalize_project_path_for_storage,
    resolve_include_entry_path,
    resolve_project_context_paths,
    resolve_project_relative_path,
    save_project_include_state,
)
from aunic.image_inputs import is_supported_image_path, prepare_image_input_from_base64
from aunic.loop import ToolLoop
from aunic.model_options import ModelOption, build_model_options
from aunic.modes import (
    ChatModeRunRequest,
    ChatModeRunner,
    NoteModeRunRequest,
    NoteModeRunResult,
    NoteModeRunner,
)
from aunic.plans import PlanService
from aunic.progress import ProgressEvent
from aunic.providers import ClaudeProvider, CodexProvider, OpenAICompatibleProvider
from aunic.proto_settings import get_editor_save_mode
from aunic.research import FetchPacket, FetchService, FetchedChunk, ResearchState, SearchResult, SearchService
from aunic.tools.runtime import PermissionRequest, PermissionResolution, join_note_and_transcript
from aunic.transcript.parser import parse_transcript_rows, split_note_and_transcript
from aunic.transcript.writer import (
    append_synthetic_tool_pair,
    delete_row_by_number,
    delete_search_result_item,
)

if TYPE_CHECKING:
    from aunic.browser.connection import ConnectionHandler

logger = logging.getLogger(__name__)

BrowserMode = Literal["note", "chat"]
BrowserAgentMode = Literal["off", "read", "work", "plan"]
ProviderFactory = Callable[[ModelOption, Path], Any]
_MAX_ENTRY_NAME_BYTES = 255
_PROMPT_COMMANDS = (
    "/context",
    "/note",
    "/chat",
    "/work",
    "/read",
    "/off",
    "/plan",
    "/model",
    "/find",
    "/replace",
    "/include",
    "/exclude",
    "/isolate",
    "/map",
    "/clear-history",
    "@web",
    "@rag",
)
_PROMPT_COMMAND_RE = re.compile(
    "("
    + "|".join(re.escape(cmd) + r"\b" for cmd in sorted(_PROMPT_COMMANDS, key=len, reverse=True))
    + ")"
)

@dataclass(frozen=True)
class BrowserResearchResult:
    title: str
    url: str | None
    snippet: str
    source: str | None = None
    result_id: str | None = None
    local_path: str | None = None
    score: float | None = None
    heading_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class BrowserResearchPacket:
    title: str
    url: str | None
    full_text: str
    chunks: tuple[FetchedChunk, ...]
    source: str | None = None
    result_id: str | None = None
    total_chunks: int | None = None
    truncated: bool = False


class BrowserSession:
    def __init__(
        self,
        *,
        instance_id: str = "browser",
        workspace_root: Path,
        file_manager: FileManager | None = None,
        note_runner: NoteModeRunner | None = None,
        chat_runner: ChatModeRunner | None = None,
        provider_factory: ProviderFactory | None = None,
        search_service: SearchService | None = None,
        fetch_service: FetchService | None = None,
        cwd: Path | None = None,
        mode: BrowserMode = "note",
        work_mode: WorkMode = "off",
        reasoning_effort: ReasoningEffort | None = None,
        total_turn_budget: int = 100_000,
        model_options: tuple[ModelOption, ...] | None = None,
        selected_model_index: int = 0,
        acquire_run_file: Callable[[Path], Awaitable[None]] | None = None,
        release_run_file: Callable[[Path], Awaitable[None]] | None = None,
    ) -> None:
        self.instance_id = instance_id
        self.workspace_root = workspace_root.expanduser().resolve()
        self.cwd = (cwd or self.workspace_root).expanduser().resolve()
        self.file_manager = file_manager or FileManager()
        self.note_runner = note_runner or NoteModeRunner(
            context_engine=ContextEngine(self.file_manager),
            tool_loop=ToolLoop(
                file_manager=self.file_manager,
                search_service=SearchService(),
                fetch_service=FetchService(),
            ),
            file_manager=self.file_manager,
        )
        self.chat_runner = chat_runner or ChatModeRunner(file_manager=self.file_manager)
        self.provider_factory = provider_factory or _build_provider
        self.search_service = search_service or SearchService()
        self.fetch_service = fetch_service or FetchService()
        self.mode: BrowserMode = mode
        self.work_mode = work_mode
        self.agent_mode: BrowserAgentMode = work_mode
        self.reasoning_effort = reasoning_effort
        self.total_turn_budget = total_turn_budget
        self._model_options_fixed = model_options is not None
        self.model_options = model_options or build_model_options(self.cwd, "codex", None)
        self.selected_model_index = min(max(selected_model_index, 0), len(self.model_options) - 1)

        self._connections: set["ConnectionHandler"] = set()
        self._run_lock = asyncio.Lock()
        self._run_task: asyncio.Task[None] | None = None
        self._run_id: str | None = None
        self._force_stopped = False
        self._pending_permission: PermissionRequest | None = None
        self._permission_future: asyncio.Future[PermissionResolution] | None = None
        self._permission_id: str | None = None
        self._watch_hub = FileWatchHub(self.file_manager)
        self._watch_unsubscribe: Callable[[], None] | None = None
        self._research_mode: Literal["idle", "results", "chunks"] = "idle"
        self._research_source: Literal["web", "rag"] | None = None
        self._research_query = ""
        self._research_scope: str | None = None
        self._research_results: tuple[BrowserResearchResult, ...] = ()
        self._research_packet: BrowserResearchPacket | None = None
        self._research_busy: Literal["searching", "fetching", "inserting"] | None = None
        self._ctx_tokens_used: int | None = None
        self._ctx_window_size: int | None = _known_context_window(self.selected_model)
        self._ctx_last_note_chars: int | None = None
        self._acquire_run_file = acquire_run_file
        self._release_run_file = release_run_file
        self._leased_run_file: Path | None = None

    @property
    def run_active(self) -> bool:
        return self._run_task is not None and not self._run_task.done()

    @property
    def current_run_id(self) -> str | None:
        return self._run_id

    @property
    def selected_model(self) -> ModelOption:
        return self.model_options[self.selected_model_index]

    async def broadcast_session_state(self) -> None:
        await self.broadcast("session_state", self.session_state())

    async def attach(self, conn: "ConnectionHandler") -> None:
        self._connections.add(conn)
        if not self._model_options_fixed:
            current = self.selected_model
            self.model_options = build_model_options(self.cwd, "codex", None)
            new_index = next(
                (
                    i
                    for i, opt in enumerate(self.model_options)
                    if opt.provider_name == current.provider_name
                    and opt.model == current.model
                    and opt.profile_id == current.profile_id
                ),
                0,
            )
            self.selected_model_index = new_index
        if self._watch_unsubscribe is None:
            self._watch_unsubscribe = self._watch_hub.subscribe(self._handle_file_change)
        await self._watch_hub.start((self.workspace_root,))
        if len(self._connections) == 1:
            try:
                from aunic.map.builder import ensure_map_ready_shared

                await ensure_map_ready_shared(
                    self.workspace_root,
                    fallback_root=self.workspace_root,
                )
            except Exception as exc:
                logger.warning("map ensure on browser attach failed: %s", exc)

    async def detach(self, conn: "ConnectionHandler") -> None:
        self._connections.discard(conn)
        if self._connections:
            return
        if self._watch_unsubscribe is not None:
            self._watch_unsubscribe()
            self._watch_unsubscribe = None
        await self._watch_hub.stop()

    async def shutdown(self) -> None:
        if self._run_id is not None:
            await self.cancel_run(self._run_id)
        await self._watch_hub.stop()
        await self._release_run_file_lease()

    def session_state(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "run_active": self.run_active,
            "run_id": self._run_id,
            "workspace_root": str(self.workspace_root),
            "default_mode": self.mode,
            "mode": self.mode,
            "work_mode": self.agent_mode,
            "models": [serialize_model_option(option) for option in self.model_options],
            "selected_model_index": self.selected_model_index,
            "selected_model": serialize_model_option(self.selected_model),
            "pending_permission": self.pending_permission_payload(),
            "research_state": self.research_state_payload(),
            "context_usage": self.context_usage_payload(),
            "editor_settings": {
                "save_mode": get_editor_save_mode(self.cwd),
            },
            "capabilities": {
                "prompt_commands": True,
                "research_flow": True,
                "plan_flow": True,
            },
        }

    def pending_permission_payload(self) -> dict[str, Any] | None:
        if self._pending_permission is None or self._permission_id is None:
            return None
        return {
            "permission_id": self._permission_id,
            "request": serialize_permission_request(self._pending_permission),
        }

    def research_state_payload(self) -> dict[str, Any]:
        return {
            "mode": self._research_mode,
            "source": self._research_source,
            "query": self._research_query,
            "scope": self._research_scope,
            "busy": self._research_busy,
            "results": [_serialize_research_result(result) for result in self._research_results],
            "packet": (
                _serialize_research_packet(self._research_packet)
                if self._research_packet is not None
                else None
            ),
        }

    def context_usage_payload(self) -> dict[str, Any]:
        window = self._ctx_window_size or _known_context_window(self.selected_model)
        fraction = None
        if self._ctx_tokens_used is not None and window:
            fraction = min(1.0, max(0.0, self._ctx_tokens_used / window))
        return {
            "tokens_used": self._ctx_tokens_used,
            "context_window": window,
            "fraction": fraction,
            "last_note_chars": self._ctx_last_note_chars,
        }

    async def set_mode(self, mode: str) -> None:
        if self.run_active:
            raise BrowserError(
                "run_active",
                "Wait for the current run to finish before switching modes.",
            )
        if mode not in {"note", "chat"}:
            raise BrowserError("invalid_mode", "Mode must be note or chat.")
        self.mode = mode  # type: ignore[assignment]
        await self.broadcast_session_state()

    async def set_work_mode(self, work_mode: str) -> None:
        if self.run_active:
            raise BrowserError(
                "run_active",
                "Wait for the current run to finish before switching work mode.",
            )
        if work_mode not in {"off", "read", "work", "plan"}:
            raise BrowserError("invalid_work_mode", "Work mode must be off, read, work, or plan.")
        self.agent_mode = work_mode  # type: ignore[assignment]
        if work_mode in {"off", "read", "work"}:
            self.work_mode = work_mode  # type: ignore[assignment]
        await self.broadcast_session_state()

    async def select_model(self, index: int) -> None:
        if self.run_active:
            raise BrowserError(
                "run_active",
                "Wait for the current run to finish before switching models.",
            )
        if type(index) is not int:
            raise BrowserError("invalid_model_index", "Model index must be an integer.")
        if index < 0 or index >= len(self.model_options):
            raise BrowserError("invalid_model_index", "Model index is out of range.")
        self.selected_model_index = index
        self._ctx_window_size = _known_context_window(self.selected_model)
        await self.broadcast_session_state()

    async def list_files(self, subpath: str | None = None) -> dict[str, Any]:
        directory = resolve_workspace_directory(subpath, workspace_root=self.workspace_root)
        if not directory.exists():
            raise WorkspacePathError("not_found", "Directory does not exist.")
        if not directory.is_dir():
            raise WorkspacePathError("not_directory", "Path is not a directory.")

        entries = await asyncio.to_thread(self._list_directory, directory)
        return {"path": workspace_relative_path(directory, workspace_root=self.workspace_root), "entries": entries}

    async def read_file(self, subpath: str) -> dict[str, Any]:
        path = resolve_workspace_path(subpath, workspace_root=self.workspace_root)
        snapshot = await self.file_manager.read_snapshot(path)
        try:
            from aunic.map.builder import refresh_map_entry_if_stale

            refresh_map_entry_if_stale(path, fallback_root=self.workspace_root)
        except Exception as exc:
            logger.warning("map refresh on browser file read failed: %s", exc)
        return serialize_file_snapshot(snapshot, workspace_root=self.workspace_root)

    async def write_file(
        self,
        subpath: str,
        *,
        text: str,
        expected_revision: str | None,
    ) -> dict[str, Any]:
        path = resolve_workspace_path(subpath, workspace_root=self.workspace_root)
        try:
            current = await self.file_manager.read_snapshot(path)
            _, transcript_text = split_note_and_transcript(current.raw_text)
            full_text = join_note_and_transcript(text, transcript_text)
            written = await self.file_manager.write_text(
                path,
                full_text,
                expected_revision=expected_revision,
            )
        except OptimisticWriteError as exc:
            raise RevisionConflict(
                "revision_conflict",
                "File changed before the browser save completed.",
            ) from exc
        try:
            from aunic.map.builder import mark_map_entry_stale

            mark_map_entry_stale(path)
        except Exception as exc:
            logger.warning("map stale-mark on browser save failed: %s", exc)
        return serialize_file_snapshot(written, workspace_root=self.workspace_root)

    async def delete_transcript_row(
        self,
        subpath: str,
        *,
        row_number: int,
        expected_revision: str | None,
    ) -> dict[str, Any]:
        return await self._write_transcript_mutation(
            subpath,
            expected_revision=expected_revision,
            mutate=lambda text: delete_row_by_number(text, row_number),
        )

    async def delete_search_result(
        self,
        subpath: str,
        *,
        row_number: int,
        result_index: int,
        expected_revision: str | None,
    ) -> dict[str, Any]:
        return await self._write_transcript_mutation(
            subpath,
            expected_revision=expected_revision,
            mutate=lambda text: delete_search_result_item(text, row_number, result_index),
        )

    async def create_file(self, subpath: str) -> dict[str, Any]:
        path = resolve_workspace_path(subpath, workspace_root=self.workspace_root)
        _validate_new_entry_name(path.name)
        if path.suffix.lower() != ".md":
            raise BrowserError(
                "invalid_extension",
                "Only .md files can be created from the browser.",
            )
        if await asyncio.to_thread(path.exists):
            raise BrowserError("already_exists", "A file or directory already exists at that path.")
        if not await asyncio.to_thread(path.parent.is_dir):
            raise BrowserError("parent_not_found", "Parent directory does not exist.")

        await asyncio.to_thread(path.write_text, "", encoding="utf-8")
        snapshot = await self.file_manager.read_snapshot(path)
        return serialize_file_snapshot(snapshot, workspace_root=self.workspace_root)

    async def create_directory(self, subpath: str) -> dict[str, Any]:
        path = resolve_workspace_path(subpath, workspace_root=self.workspace_root)
        _validate_new_entry_name(path.name)
        if await asyncio.to_thread(path.exists):
            raise BrowserError("already_exists", "A file or directory already exists at that path.")
        if not await asyncio.to_thread(path.parent.is_dir):
            raise BrowserError("parent_not_found", "Parent directory does not exist.")

        await asyncio.to_thread(path.mkdir)
        return {
            "path": workspace_relative_path(path, workspace_root=self.workspace_root),
            "kind": "dir",
        }

    async def rename_entry(self, subpath: str, new_name: str) -> dict[str, Any]:
        if subpath in {"", "."}:
            raise BrowserError("refused", "Refusing to rename workspace root.")
        path = resolve_workspace_path(subpath, workspace_root=self.workspace_root)
        if path == self.workspace_root:
            raise BrowserError("refused", "Refusing to rename workspace root.")
        if not await asyncio.to_thread(path.exists):
            raise BrowserError("not_found", "File or directory does not exist.")

        name = new_name.strip()
        if not name:
            raise BrowserError("invalid_name", "Enter a name.")
        if "/" in name or "\\" in name:
            raise BrowserError("invalid_name", "Name cannot contain path separators.")
        _validate_new_entry_name(name)

        new_path = path.parent / name
        if await asyncio.to_thread(new_path.exists):
            raise BrowserError("already_exists", "A file or directory already exists with that name.")

        await asyncio.to_thread(path.rename, new_path)
        kind = "dir" if await asyncio.to_thread(new_path.is_dir) else "file"
        return {
            "path": workspace_relative_path(new_path, workspace_root=self.workspace_root),
            "old_path": workspace_relative_path(path, workspace_root=self.workspace_root),
            "kind": kind,
        }

    async def delete_entry(self, subpath: str) -> dict[str, Any]:
        if subpath in {"", "."}:
            raise BrowserError("refused", "Refusing to delete workspace root.")
        path = resolve_workspace_path(subpath, workspace_root=self.workspace_root)
        if path == self.workspace_root:
            raise BrowserError("refused", "Refusing to delete workspace root.")
        if not await asyncio.to_thread(path.exists):
            raise BrowserError("not_found", "File or directory does not exist.")

        relative = workspace_relative_path(path, workspace_root=self.workspace_root)
        if await asyncio.to_thread(path.is_dir):
            await asyncio.to_thread(shutil.rmtree, path)
            return {"path": relative, "kind": "dir"}

        await asyncio.to_thread(path.unlink)
        return {"path": relative, "kind": "file"}

    async def _write_transcript_mutation(
        self,
        subpath: str,
        *,
        expected_revision: str | None,
        mutate: Callable[[str], str],
    ) -> dict[str, Any]:
        path = resolve_workspace_path(subpath, workspace_root=self.workspace_root)
        try:
            current = await self.file_manager.read_snapshot(path)
            updated_text = mutate(current.raw_text)
            if updated_text == current.raw_text:
                return serialize_file_snapshot(current, workspace_root=self.workspace_root)
            written = await self.file_manager.write_text(
                path,
                updated_text,
                expected_revision=expected_revision,
            )
        except OptimisticWriteError as exc:
            raise RevisionConflict(
                "revision_conflict",
                "File changed before the transcript edit completed.",
            ) from exc
        return serialize_file_snapshot(written, workspace_root=self.workspace_root)

    async def submit_prompt(
        self,
        *,
        active_file: str,
        included_files: Iterable[str],
        text: str,
        image_attachments: Iterable[dict[str, Any]] = (),
    ) -> str:
        attachments = tuple(image_attachments)
        if not text.strip() and not attachments:
            raise BrowserError("empty_prompt", "Prompt text cannot be empty.")

        active_path = resolve_workspace_path(active_file, workspace_root=self.workspace_root)
        included_paths, included_image_paths = self._merge_included_context(
            active_path,
            tuple(resolve_workspace_path(item, workspace_root=self.workspace_root) for item in included_files),
        )
        prompt_images = await self._prepare_browser_prompt_images(attachments)
        self._ensure_selected_model_supports_images(
            persistent_image_paths=included_image_paths,
            prompt_images=prompt_images,
        )
        request_work_mode, active_plan_id, active_plan_path, planning_status = self._resolve_run_plan_context(
            active_path
        )
        run_id = uuid4().hex
        async with self._run_lock:
            if self.run_active:
                raise RunInProgress()
            lease_acquired = False
            try:
                if self._acquire_run_file is not None:
                    await self._acquire_run_file(active_path)
                    self._leased_run_file = active_path
                    lease_acquired = True
                self._run_id = run_id
                self._force_stopped = False
                self._run_task = asyncio.create_task(
                    self._run_prompt(
                        run_id=run_id,
                        active_file=active_path,
                        included_files=included_paths,
                        included_image_files=included_image_paths,
                        prompt_images=prompt_images,
                        text=text,
                        request_work_mode=request_work_mode,
                        active_plan_id=active_plan_id,
                        active_plan_path=active_plan_path,
                        planning_status=planning_status,
                    ),
                    name=f"aunic-browser-run-{run_id}",
                )
            except Exception:
                if lease_acquired:
                    await self._release_run_file_lease()
                raise
        await self.broadcast_session_state()
        return run_id

    async def run_prompt_command(self, *, active_file: str, text: str) -> dict[str, Any]:
        match = _parse_prompt_command(text)
        if match is None:
            raise BrowserError("no_prompt_command", "No recognized prompt command was found.")

        command, remaining = match
        active_path = resolve_workspace_path(active_file, workspace_root=self.workspace_root)

        if command in {"/note", "/chat"}:
            mode = command[1:]
            await self.set_mode(mode)
            return _command_response(draft=remaining, message=f"Switched to {mode} mode.")

        if command in {"/work", "/read", "/off"}:
            work_mode = command[1:]
            await self.set_work_mode(work_mode)
            return _command_response(draft=remaining, message=f"Agent mode set to {work_mode}.")

        if command == "/plan":
            await self.set_work_mode("plan")
            return _command_response(draft=remaining, message="Agent mode set to plan.")

        if command == "/map":
            message = self._handle_map_command(active_path, remaining)
            return _command_response(draft="", message=message)

        if command == "/include":
            message = self._handle_include_command(active_path, remaining)
            return _command_response(draft="", message=message)

        if command == "/exclude":
            message = self._handle_exclude_command(active_path, remaining)
            return _command_response(draft="", message=message)

        if command == "/clear-history":
            snapshot = await self._clear_transcript(active_path)
            return _command_response(
                draft=remaining,
                message="Transcript cleared.",
                snapshot=snapshot,
            )

        if command == "@web":
            if not remaining:
                raise BrowserError("invalid_prompt_command", "Usage: @web <search query>")
            snapshot = await self._run_web_search_command(active_path, remaining)
            count = len(self._research_results)
            return _command_response(
                draft="",
                message=f"Found {count} web result{'s' if count != 1 else ''}.",
                snapshot=snapshot,
            )

        if command == "@rag":
            if not remaining:
                raise BrowserError("invalid_prompt_command", "Usage: @rag <search query>")
            snapshot = await self._run_rag_search_command(active_path, remaining, scope=None)
            count = len(self._research_results)
            return _command_response(
                draft="",
                message=f"Found {count} RAG result{'s' if count != 1 else ''}.",
                snapshot=snapshot,
            )

        raise BrowserError(
            "unsupported_prompt_command",
            f"{command} is recognized but is not implemented in the browser UI yet.",
        )

    async def cancel_run(self, run_id: str | None) -> bool:
        if run_id is None or self._run_id != run_id or self._run_task is None:
            return False
        self._force_stopped = True
        if self._permission_future is not None and not self._permission_future.done():
            self._permission_future.set_result("reject")
        self._run_task.cancel()
        return True

    async def request_permission(self, request: PermissionRequest) -> PermissionResolution:
        if self._permission_future is not None:
            return "reject"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[PermissionResolution] = loop.create_future()
        self._permission_future = future
        self._permission_id = uuid4().hex
        self._pending_permission = request
        await self.broadcast("permission_request", self.pending_permission_payload() or {})
        try:
            return await future
        finally:
            self._pending_permission = None
            self._permission_future = None
            self._permission_id = None
            await self.broadcast_session_state()

    async def resolve_permission(self, permission_id: str, resolution: str) -> dict[str, bool]:
        if resolution not in PERMISSION_RESOLUTIONS:
            raise BrowserError(
                "invalid_permission_resolution",
                "Permission resolution must be once, always, or reject.",
            )
        if self._permission_id != permission_id or self._permission_future is None:
            raise PermissionRequestNotFound(
                "permission_not_found",
                "No matching permission request is pending.",
            )
        if not self._permission_future.done():
            self._permission_future.set_result(resolution)  # type: ignore[arg-type]
        return {"ok": True}

    async def get_project_state(self, *, source_file: str) -> dict[str, Any]:
        source_path = resolve_workspace_path(source_file, workspace_root=self.workspace_root)
        return self._project_state_payload(source_path)

    async def create_plan(self, *, source_file: str, title: str) -> dict[str, Any]:
        source_path = resolve_workspace_path(source_file, workspace_root=self.workspace_root)
        clean_title = title.strip() or "Untitled Plan"
        document = PlanService(source_path).create_plan(clean_title)
        state = load_project_include_state(source_path)
        save_project_include_state(
            source_path,
            ProjectIncludeState(
                include_entries=state.include_entries,
                inactive_children=state.inactive_children,
                active_plan_id=document.entry.id,
            ),
        )
        return self._project_state_payload(source_path)

    async def delete_plan(self, *, source_file: str, plan_id: str) -> dict[str, Any]:
        source_path = resolve_workspace_path(source_file, workspace_root=self.workspace_root)
        service = PlanService(source_path)
        try:
            service.delete_plan(plan_id)
        except FileNotFoundError as exc:
            raise BrowserError("plan_not_found", f"Plan not found: {plan_id}") from exc
        state = load_project_include_state(source_path)
        save_project_include_state(
            source_path,
            ProjectIncludeState(
                include_entries=state.include_entries,
                inactive_children=state.inactive_children,
                active_plan_id=None if state.active_plan_id == plan_id else state.active_plan_id,
            ),
        )
        return self._project_state_payload(source_path)

    async def set_active_plan(
        self,
        *,
        source_file: str,
        plan_id: str | None,
    ) -> dict[str, Any]:
        source_path = resolve_workspace_path(source_file, workspace_root=self.workspace_root)
        state = load_project_include_state(source_path)
        active_plan_id: str | None = None
        if plan_id is not None:
            try:
                PlanService(source_path).get_plan(plan_id)
            except FileNotFoundError as exc:
                raise BrowserError("plan_not_found", f"Plan not found: {plan_id}") from exc
            active_plan_id = plan_id
        save_project_include_state(
            source_path,
            ProjectIncludeState(
                include_entries=state.include_entries,
                inactive_children=state.inactive_children,
                active_plan_id=active_plan_id,
            ),
        )
        return self._project_state_payload(source_path)

    async def add_include(
        self,
        *,
        source_file: str,
        target_path: str,
        recursive: bool = False,
    ) -> dict[str, Any]:
        source_path = resolve_workspace_path(source_file, workspace_root=self.workspace_root)
        target = resolve_workspace_path(target_path, workspace_root=self.workspace_root)
        if target == source_path:
            raise BrowserError("invalid_project_include", "The source file is already in project context.")
        is_dir = target.is_dir()
        state = load_project_include_state(source_path)
        if self._find_project_include_index(
            source_path,
            state.include_entries,
            raw_identifier=target_path,
            resolved_identifiers=(target,),
        ) is not None:
            return self._project_state_payload(source_path)

        include_entries = [
            *state.include_entries,
            IncludeEntry(
                path=normalize_project_path_for_storage(source_path, target, is_dir=is_dir),
                is_dir=is_dir,
                recursive=recursive if is_dir else False,
                active=True,
            ),
        ]
        save_project_include_state(
            source_path,
            ProjectIncludeState(
                include_entries=tuple(include_entries),
                inactive_children=state.inactive_children,
                active_plan_id=state.active_plan_id,
            ),
        )
        return self._project_state_payload(source_path)

    async def remove_include_entry(
        self,
        *,
        source_file: str,
        include_path: str,
    ) -> dict[str, Any]:
        source_path = resolve_workspace_path(source_file, workspace_root=self.workspace_root)
        state = load_project_include_state(source_path)
        resolved_identifiers = self._project_identifier_candidates(
            source_path,
            include_path,
            prefer_source_relative=False,
        )
        index = self._find_project_include_index(
            source_path,
            state.include_entries,
            raw_identifier=include_path,
            resolved_identifiers=resolved_identifiers,
        )
        if index is None:
            raise BrowserError("project_include_not_found", f"Not in include list: {include_path}")

        entry = state.include_entries[index]
        entry_target = resolve_include_entry_path(source_path, entry)
        include_entries = [
            current
            for current_index, current in enumerate(state.include_entries)
            if current_index != index
        ]
        inactive_children = tuple(
            raw
            for raw in state.inactive_children
            if not self._inactive_child_matches(source_path, raw, entry_target, entry.is_dir)
        )
        save_project_include_state(
            source_path,
            ProjectIncludeState(
                include_entries=tuple(include_entries),
                inactive_children=inactive_children,
                active_plan_id=state.active_plan_id,
            ),
        )
        return self._project_state_payload(source_path)

    async def set_include_entry_active(
        self,
        *,
        source_file: str,
        include_path: str,
        active: bool,
    ) -> dict[str, Any]:
        source_path = resolve_workspace_path(source_file, workspace_root=self.workspace_root)
        state = load_project_include_state(source_path)
        resolved_identifiers = self._project_identifier_candidates(
            source_path,
            include_path,
            prefer_source_relative=False,
        )
        index = self._find_project_include_index(
            source_path,
            state.include_entries,
            raw_identifier=include_path,
            resolved_identifiers=resolved_identifiers,
        )
        if index is None:
            raise BrowserError("project_include_not_found", f"Not in include list: {include_path}")
        include_entries = list(state.include_entries)
        entry = include_entries[index]
        include_entries[index] = IncludeEntry(
            path=entry.path,
            is_dir=entry.is_dir,
            recursive=entry.recursive,
            active=active,
        )
        save_project_include_state(
            source_path,
            ProjectIncludeState(
                include_entries=tuple(include_entries),
                inactive_children=state.inactive_children,
                active_plan_id=state.active_plan_id,
            ),
        )
        return self._project_state_payload(source_path)

    async def set_project_child_active(
        self,
        *,
        source_file: str,
        child_path: str,
        active: bool,
    ) -> dict[str, Any]:
        source_path = resolve_workspace_path(source_file, workspace_root=self.workspace_root)
        child = resolve_workspace_path(child_path, workspace_root=self.workspace_root)
        state = load_project_include_state(source_path)
        if not any(
            entry.is_dir and child.is_relative_to(resolve_include_entry_path(source_path, entry))
            for entry in state.include_entries
        ):
            raise BrowserError("project_child_not_found", f"File is not part of an included directory: {child_path}")

        normalized_child = normalize_project_path_for_storage(source_path, child, is_dir=False)
        inactive_children = [
            raw
            for raw in state.inactive_children
            if resolve_project_relative_path(source_path, raw) != child
        ]
        if not active:
            inactive_children.append(normalized_child)
        save_project_include_state(
            source_path,
            ProjectIncludeState(
                include_entries=state.include_entries,
                inactive_children=tuple(dict.fromkeys(inactive_children)),
                active_plan_id=state.active_plan_id,
            ),
        )
        return self._project_state_payload(source_path)

    def _handle_include_command(self, active_path: Path, arg: str) -> str:
        raw = arg.strip()
        recursive = False
        if raw.startswith("-r "):
            recursive = True
            raw = raw[3:].strip()
        if not raw:
            raise BrowserError("invalid_prompt_command", "Usage: /include [-r] <path>")

        target = _resolve_command_path(raw, base=active_path.parent)
        self._ensure_workspace_scoped(target, raw)
        is_dir = raw.endswith("/") or target.is_dir()
        if target == active_path:
            raise BrowserError("invalid_project_include", "The source file is already in project context.")
        state = load_project_include_state(active_path)
        if self._find_project_include_index(
            active_path,
            state.include_entries,
            raw_identifier=raw,
            resolved_identifiers=(target,),
        ) is not None:
            return f"Already included: {raw}"
        include_entries = [
            *state.include_entries,
            IncludeEntry(
                path=normalize_project_path_for_storage(active_path, target, is_dir=is_dir),
                is_dir=is_dir,
                recursive=recursive if is_dir else False,
                active=True,
            ),
        ]
        save_project_include_state(
            active_path,
            ProjectIncludeState(
                include_entries=tuple(include_entries),
                inactive_children=state.inactive_children,
                active_plan_id=state.active_plan_id,
            ),
        )
        kind = "directory" if is_dir else "file"
        return f"Included {kind}: {raw}"

    def _handle_exclude_command(self, active_path: Path, arg: str) -> str:
        raw = arg.strip()
        if not raw:
            raise BrowserError("invalid_prompt_command", "Usage: /exclude <path>")
        state = load_project_include_state(active_path)
        resolved_identifiers = self._project_identifier_candidates(
            active_path,
            raw,
            prefer_source_relative=True,
        )
        index = self._find_project_include_index(
            active_path,
            state.include_entries,
            raw_identifier=raw,
            resolved_identifiers=resolved_identifiers,
        )
        if index is None:
            raise BrowserError("invalid_prompt_command", f"Not in include list: {raw}")
        entry = state.include_entries[index]
        entry_target = resolve_include_entry_path(active_path, entry)
        include_entries = [
            current
            for current_index, current in enumerate(state.include_entries)
            if current_index != index
        ]
        inactive_children = tuple(
            child
            for child in state.inactive_children
            if not self._inactive_child_matches(active_path, child, entry_target, entry.is_dir)
        )
        save_project_include_state(
            active_path,
            ProjectIncludeState(
                include_entries=tuple(include_entries),
                inactive_children=inactive_children,
                active_plan_id=state.active_plan_id,
            ),
        )
        return f"Excluded: {raw}"

    def _handle_map_command(self, active_path: Path, remaining: str) -> str:
        tokens = remaining.split(None, 1)
        first = tokens[0] if tokens else ""

        if first == "--generate-summary":
            raise BrowserError(
                "invalid_prompt_command",
                "--generate-summary is deferred to a follow-up; use --set-summary <text> for now.",
            )

        if first == "--set-summary":
            text = tokens[1].strip() if len(tokens) > 1 else ""
            if not text:
                raise BrowserError("invalid_prompt_command", "Usage: /map --set-summary <text>")
            from aunic.map.builder import set_summary

            set_summary(active_path, text, fallback_root=self.workspace_root)
            return f"Summary locked for {active_path.name}."

        if first == "--clear-summary":
            from aunic.map.builder import clear_summary

            clear_summary(active_path, fallback_root=self.workspace_root)
            return f"Summary cleared for {active_path.name}."

        scope: Path | None = None
        if first:
            raw = Path(first).expanduser()
            if not raw.is_absolute():
                raw = active_path.parent / raw
            scope = raw.resolve()
            if not scope.exists() or not scope.is_dir():
                raise BrowserError(
                    "invalid_prompt_command",
                    f"/map: path not found or not a directory: {scope}",
                )

        from aunic.map.builder import build_map

        result = build_map(
            scope,
            subject_path=scope or active_path,
            fallback_root=self.workspace_root,
        )
        scope_label = f" under {scope}" if scope is not None else ""
        return (
            f"Mapped {result.entry_count} notes{scope_label}"
            f" (+{result.entries_added} -{result.entries_removed}"
            f", {result.entries_reused_from_cache} unchanged)"
            f" in {result.elapsed_seconds:.1f}s."
        )

    def _merge_included_context(
        self,
        active_path: Path,
        explicit_paths: tuple[Path, ...],
    ) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
        project_state = load_project_include_state(active_path)
        resolved = resolve_project_context_paths(
            active_path,
            project_state.include_entries,
            inactive_children=project_state.inactive_children,
        )
        persisted_text_paths = tuple(
            path for path in resolved.text_files if self._is_workspace_scoped(path)
        )
        persisted_image_paths = tuple(
            path for path in resolved.image_files if self._is_workspace_scoped(path)
        )

        explicit_text_paths = tuple(
            path for path in explicit_paths if not is_supported_image_path(path)
        )
        explicit_image_paths = tuple(
            path for path in explicit_paths if is_supported_image_path(path)
        )

        return (
            _merge_unique_paths((*persisted_text_paths, *explicit_text_paths), exclude=active_path),
            _merge_unique_paths((*persisted_image_paths, *explicit_image_paths)),
        )

    async def _prepare_browser_prompt_images(
        self,
        attachments: tuple[dict[str, Any], ...],
    ) -> tuple[ProviderImageInput, ...]:
        prepared: list[ProviderImageInput] = []
        for attachment in attachments:
            name = str(attachment.get("name", "")).strip()
            data_base64 = str(attachment.get("data_base64", "")).strip()
            if not name or not data_base64:
                raise BrowserError(
                    "invalid_image_attachment",
                    "Image attachments must include a filename and base64 data.",
                )
            try:
                prepared.append(
                    await prepare_image_input_from_base64(
                        name=name,
                        data_base64=data_base64,
                        persistent=False,
                    )
                )
            except FileReadError as exc:
                raise BrowserError("invalid_image_attachment", str(exc)) from exc
        return tuple(prepared)

    def _ensure_selected_model_supports_images(
        self,
        *,
        persistent_image_paths: tuple[Path, ...],
        prompt_images: tuple[ProviderImageInput, ...],
    ) -> None:
        if not persistent_image_paths and not prompt_images:
            return
        if self.selected_model.supports_images and self.selected_model.image_transport != "unsupported":
            return
        raise BrowserError(
            "images_unsupported",
            f"{self.selected_model.label} does not support image inputs.",
        )

    def _resolve_run_plan_context(
        self,
        active_path: Path,
    ) -> tuple[WorkMode, str | None, Path | None, str]:
        if self.agent_mode != "plan":
            return self.work_mode, None, None, "none"

        state = load_project_include_state(active_path)
        if not state.active_plan_id:
            raise BrowserError("no_active_plan", "Select or create a plan before using Agent: Plan.")

        try:
            document = PlanService(active_path).get_plan(state.active_plan_id)
        except FileNotFoundError as exc:
            save_project_include_state(
                active_path,
                ProjectIncludeState(
                    include_entries=state.include_entries,
                    inactive_children=state.inactive_children,
                    active_plan_id=None,
                ),
            )
            raise BrowserError("plan_not_found", "The selected plan no longer exists.") from exc

        planning_status = {
            "draft": "drafting",
            "awaiting_approval": "awaiting_approval",
            "approved": "approved",
        }.get(document.entry.status, "none")
        request_work_mode: WorkMode = "work" if planning_status == "approved" else self.work_mode
        return request_work_mode, document.entry.id, document.path, planning_status

    def _project_state_payload(self, source_path: Path) -> dict[str, Any]:
        state = load_project_include_state(source_path)
        inactive_children = {
            resolve_project_relative_path(source_path, raw).resolve()
            for raw in state.inactive_children
            if isinstance(raw, str) and raw.strip()
        }
        plan_service = PlanService(source_path)
        plans = list(plan_service.list_plans_for_source_note())
        active_plan_id = state.active_plan_id if any(entry.id == state.active_plan_id for entry in plans) else None
        if active_plan_id != state.active_plan_id:
            save_project_include_state(
                source_path,
                ProjectIncludeState(
                    include_entries=state.include_entries,
                    inactive_children=state.inactive_children,
                    active_plan_id=active_plan_id,
                ),
            )
        entries = [
            self._project_entry_payload(source_path, entry, inactive_children)
            for entry in state.include_entries
        ]
        return {
            "source_file": workspace_relative_path(source_path, workspace_root=self.workspace_root),
            "entries": entries,
            "plans": [
                self._project_plan_payload(plan_service, entry, active_plan_id=active_plan_id)
                for entry in plans
            ],
            "active_plan_id": active_plan_id,
        }

    def _project_plan_payload(
        self,
        plan_service: PlanService,
        entry: Any,
        *,
        active_plan_id: str | None,
    ) -> dict[str, Any]:
        path = entry.file_path(plan_service.plans_dir)
        in_workspace = self._is_workspace_scoped(path)
        actual_path = workspace_relative_path(path, workspace_root=self.workspace_root) if in_workspace else str(path)
        exists = path.exists()
        return {
            "id": f"plan:{entry.id}",
            "plan_id": entry.id,
            "path": actual_path,
            "name": path.name,
            "title": entry.title,
            "status": entry.status,
            "active": entry.id == active_plan_id,
            "exists": exists and in_workspace,
            "openable": exists and in_workspace,
        }

    def _project_entry_payload(
        self,
        source_path: Path,
        entry: IncludeEntry,
        inactive_children: set[Path],
    ) -> dict[str, Any]:
        target = resolve_include_entry_path(source_path, entry)
        in_workspace = self._is_workspace_scoped(target)
        actual_path = (
            workspace_relative_path(target, workspace_root=self.workspace_root)
            if in_workspace
            else entry.path
        )
        exists = target.exists()
        is_dir = entry.is_dir
        return {
            "id": f"entry:{entry.path}",
            "path": actual_path,
            "name": target.name or Path(entry.path.rstrip("/")).name or entry.path,
            "kind": "dir" if is_dir else "file",
            "scope": "entry",
            "active": entry.active,
            "effective_active": entry.active,
            "checkable": True,
            "removable": True,
            "exists": exists and in_workspace,
            "openable": (not is_dir) and exists and in_workspace and target.suffix.lower() == ".md",
            "recursive": entry.recursive,
            "children": (
                self._project_directory_children(
                    source_path,
                    top_level_key=entry.path,
                    directory=target,
                    recursive=entry.recursive,
                    parent_active=entry.active,
                    inactive_children=inactive_children,
                )
                if is_dir and exists and target.is_dir() and in_workspace
                else []
            ),
        }

    def _project_directory_children(
        self,
        source_path: Path,
        *,
        top_level_key: str,
        directory: Path,
        recursive: bool,
        parent_active: bool,
        inactive_children: set[Path],
    ) -> list[dict[str, Any]]:
        try:
            items = sorted(directory.iterdir(), key=_project_tree_sort_key)
        except OSError:
            return []

        children: list[dict[str, Any]] = []
        for item in items:
            resolved = item.resolve()
            if item.is_dir():
                if not recursive:
                    continue
                nested = self._project_directory_children(
                    source_path,
                    top_level_key=top_level_key,
                    directory=resolved,
                    recursive=True,
                    parent_active=parent_active,
                    inactive_children=inactive_children,
                )
                if not nested:
                    continue
                rel_path = workspace_relative_path(resolved, workspace_root=self.workspace_root)
                children.append(
                    {
                        "id": f"child-dir:{top_level_key}:{rel_path}",
                        "path": rel_path,
                        "name": resolved.name,
                        "kind": "dir",
                        "scope": "child",
                        "active": True,
                        "effective_active": parent_active,
                        "checkable": False,
                        "removable": False,
                        "exists": True,
                        "openable": False,
                        "recursive": False,
                        "children": nested,
                    }
                )
                continue
            is_markdown = item.suffix.lower() == ".md"
            is_image = is_supported_image_path(item)
            if not is_markdown and not is_image:
                continue
            rel_path = workspace_relative_path(resolved, workspace_root=self.workspace_root)
            active = resolved not in inactive_children
            children.append(
                {
                    "id": f"child-file:{top_level_key}:{rel_path}",
                    "path": rel_path,
                    "name": resolved.name,
                    "kind": "file",
                    "scope": "child",
                    "active": active,
                    "effective_active": parent_active and active,
                    "checkable": True,
                    "removable": True,
                    "exists": True,
                    "openable": is_markdown,
                    "recursive": False,
                    "children": [],
                }
            )
        return children

    def _find_project_include_index(
        self,
        source_path: Path,
        entries: Iterable[IncludeEntry],
        *,
        raw_identifier: str | None,
        resolved_identifiers: tuple[Path, ...],
    ) -> int | None:
        for index, entry in enumerate(entries):
            if raw_identifier is not None and entry.path == raw_identifier:
                return index
            if resolved_identifiers and any(
                resolve_include_entry_path(source_path, entry) == candidate.resolve()
                for candidate in resolved_identifiers
            ):
                return index
        return None

    def _project_identifier_candidates(
        self,
        source_path: Path,
        identifier: str,
        *,
        prefer_source_relative: bool,
    ) -> tuple[Path, ...]:
        if not identifier.strip():
            return ()
        candidates: list[Path] = []
        if prefer_source_relative:
            try:
                candidates.append(resolve_project_relative_path(source_path, identifier))
            except Exception:
                pass
        try:
            workspace_candidate = resolve_workspace_path(identifier, workspace_root=self.workspace_root)
        except WorkspacePathError:
            workspace_candidate = None
        if workspace_candidate is not None:
            candidates.append(workspace_candidate)
        if not prefer_source_relative:
            try:
                candidates.append(resolve_project_relative_path(source_path, identifier))
            except Exception:
                pass
        unique: list[Path] = []
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved not in unique:
                unique.append(resolved)
        return tuple(unique)

    def _inactive_child_matches(
        self,
        source_path: Path,
        raw_child: str,
        target_path: Path,
        target_is_dir: bool,
    ) -> bool:
        child = resolve_project_relative_path(source_path, raw_child)
        if target_is_dir:
            return child == target_path or child.is_relative_to(target_path)
        return child == target_path

    def _ensure_workspace_scoped(self, path: Path, raw: str) -> None:
        try:
            workspace_relative_path(path, workspace_root=self.workspace_root)
        except WorkspacePathError as exc:
            raise BrowserError(exc.reason, f"Included path resolves outside the workspace: {raw}") from exc

    def _is_workspace_scoped(self, path: Path) -> bool:
        try:
            workspace_relative_path(path, workspace_root=self.workspace_root)
            return True
        except WorkspacePathError:
            return False

    async def _clear_transcript(self, active_path: Path) -> dict[str, Any]:
        current = await self.file_manager.read_snapshot(active_path)
        note_text, transcript_text = split_note_and_transcript(current.raw_text)
        if not transcript_text:
            return serialize_file_snapshot(current, workspace_root=self.workspace_root)
        written = await self.file_manager.write_text(
            active_path,
            note_text,
            expected_revision=current.revision_id,
        )
        return serialize_file_snapshot(written, workspace_root=self.workspace_root)

    async def _run_web_search_command(self, active_path: Path, query: str) -> dict[str, Any]:
        self._set_research_busy("searching", source="web", query=query, scope=None)
        await self.broadcast_session_state()
        await self.broadcast(
            "progress_event",
            serialize_progress_event(
                ProgressEvent(kind="status", message="Searching...", path=active_path),
                workspace_root=self.workspace_root,
            ),
        )
        research_state = ResearchState()
        current = await self.file_manager.read_snapshot(active_path)
        try:
            batch = await self.search_service.search(
                queries=(query,),
                depth="quick",
                freshness="none",
                purpose=query,
                state=research_state,
            )
            if not batch.results and batch.failures:
                failure = batch.failures[0]
                self._clear_research_state()
                await self.broadcast_session_state()
                return await self._append_synthetic_tool_pair(
                    current,
                    tool_name="web_search",
                    tool_call_content={"queries": [query]},
                    tool_response_content=_tool_error_payload(
                        reason="search_failed",
                        message=failure.message,
                        queries=[query],
                    ),
                    response_type="tool_error",
                )
            payload = [
                {
                    "url": result.url,
                    "title": result.title,
                    "snippet": result.snippet,
                }
                for result in batch.results
            ]
            snapshot = await self._append_synthetic_tool_pair(
                current,
                tool_name="web_search",
                tool_call_content={"queries": [query]},
                tool_response_content=payload,
            )
            self._research_mode = "results"
            self._research_source = "web"
            self._research_query = query
            self._research_scope = None
            self._research_results = tuple(_web_result_to_browser_result(result) for result in batch.results)
            self._research_packet = None
            self._research_busy = None
            await self.broadcast_session_state()
            return snapshot
        except Exception as exc:
            self._clear_research_state()
            await self.broadcast_session_state()
            return await self._append_synthetic_tool_pair(
                current,
                tool_name="web_search",
                tool_call_content={"queries": [query]},
                tool_response_content=_tool_error_payload(
                    reason="search_failed",
                    message=str(exc),
                    queries=[query],
                ),
                response_type="tool_error",
            )

    async def _run_rag_search_command(
        self,
        active_path: Path,
        query: str,
        *,
        scope: str | None,
    ) -> dict[str, Any]:
        self._set_research_busy("searching", source="rag", query=query, scope=scope)
        await self.broadcast_session_state()
        current = await self.file_manager.read_snapshot(active_path)
        try:
            from aunic.rag.client import RagClient
            from aunic.rag.config import load_rag_config

            cfg = load_rag_config(self.cwd)
            if cfg is None:
                raise BrowserError(
                    "rag_not_configured",
                    'RAG not configured. Add a "rag" section to proto-settings.json with a server URL.',
                )
            client = RagClient(cfg.server)
            results = await client.search(query, scope=scope, limit=10)
            payload = [
                {
                    "doc_id": result.doc_id,
                    "result_id": result.result_id,
                    "chunk_id": result.chunk_id,
                    "corpus": result.corpus,
                    "title": result.title,
                    "source": result.source,
                    "snippet": result.snippet,
                    "score": result.score,
                    "heading_path": list(result.heading_path),
                    "url": result.url,
                    "local_path": result.local_path,
                }
                for result in results
            ]
            snapshot = await self._append_synthetic_tool_pair(
                current,
                tool_name="rag_search",
                tool_call_content={"query": query, "scope": scope},
                tool_response_content=payload,
            )
            self._research_mode = "results"
            self._research_source = "rag"
            self._research_query = query
            self._research_scope = scope
            self._research_results = tuple(_rag_result_to_browser_result(result) for result in results)
            self._research_packet = None
            self._research_busy = None
            await self.broadcast_session_state()
            return snapshot
        except BrowserError:
            self._clear_research_state()
            await self.broadcast_session_state()
            raise
        except Exception as exc:
            self._clear_research_state()
            await self.broadcast_session_state()
            return await self._append_synthetic_tool_pair(
                current,
                tool_name="rag_search",
                tool_call_content={"query": query, "scope": scope},
                tool_response_content=_tool_error_payload(
                    reason="search_failed",
                    message=str(exc),
                    query=query,
                    scope=scope,
                ),
                response_type="tool_error",
            )

    async def research_fetch_result(self, *, active_file: str, result_index: int) -> dict[str, Any]:
        if self.run_active:
            raise BrowserError("run_active", "Wait for the current run to finish before fetching.")
        if self._research_mode != "results" or self._research_source is None:
            raise BrowserError("research_inactive", "No active research results are available.")
        if result_index < 0 or result_index >= len(self._research_results):
            raise BrowserError("invalid_result_index", "Result index is out of range.")

        active_path = resolve_workspace_path(active_file, workspace_root=self.workspace_root)
        result = self._research_results[result_index]
        self._research_busy = "fetching"
        await self.broadcast_session_state()
        try:
            if self._research_source == "web":
                return await self._research_fetch_web_result(active_path, result)
            return await self._research_fetch_rag_result(active_path, result)
        finally:
            if self._research_busy == "fetching":
                self._research_busy = None
                await self.broadcast_session_state()

    async def _research_fetch_web_result(
        self,
        active_path: Path,
        result: BrowserResearchResult,
    ) -> dict[str, Any]:
        if not result.url:
            raise BrowserError("invalid_result", "Selected web result does not have a URL.")
        await self.broadcast(
            "progress_event",
            serialize_progress_event(
                ProgressEvent(kind="status", message="Fetching...", path=active_path),
                workspace_root=self.workspace_root,
            ),
        )
        current = await self.file_manager.read_snapshot(active_path)
        try:
            research_state = ResearchState()
            packet = await self.fetch_service.fetch_for_user_selection(
                query=self._research_query,
                url=result.url,
                state=research_state,
                active_file=active_path,
            )
            summary = research_state.summary().fetched_pages[-1]
            snapshot = await self._append_synthetic_tool_pair(
                current,
                tool_name="web_fetch",
                tool_call_content={"url": result.url},
                tool_response_content={
                    "url": summary.url,
                    "title": summary.title,
                    "snippet": summary.snippet,
                },
            )
            self._research_mode = "chunks"
            self._research_packet = _fetch_packet_to_browser_packet(packet)
            self._research_busy = None
            await self.broadcast_session_state()
            return snapshot
        except Exception as exc:
            snapshot = await self._append_synthetic_tool_pair(
                current,
                tool_name="web_fetch",
                tool_call_content={"url": result.url},
                tool_response_content=_tool_error_payload(
                    reason="fetch_failed",
                    message=str(exc),
                    url=result.url,
                ),
                response_type="tool_error",
            )
            self._research_busy = None
            await self.broadcast_session_state()
            return snapshot

    async def _research_fetch_rag_result(
        self,
        active_path: Path,
        result: BrowserResearchResult,
    ) -> dict[str, Any]:
        if not result.result_id:
            raise BrowserError("invalid_result", "Selected RAG result does not have a result_id.")
        await self.broadcast(
            "progress_event",
            serialize_progress_event(
                ProgressEvent(kind="status", message="Fetching RAG result...", path=active_path),
                workspace_root=self.workspace_root,
            ),
        )
        current = await self.file_manager.read_snapshot(active_path)
        try:
            from aunic.rag.client import RagClient
            from aunic.rag.config import load_rag_config

            cfg = load_rag_config(self.cwd)
            if cfg is None:
                raise BrowserError(
                    "rag_not_configured",
                    'RAG not configured. Add a "rag" section to proto-settings.json with a server URL.',
                )
            client = RagClient(cfg.server)
            fetch_result = await client.fetch(
                result.result_id,
                mode="document_chunks",
                max_chunks=20,
            )
            snapshot = await self._append_synthetic_tool_pair(
                current,
                tool_name="rag_fetch",
                tool_call_content={
                    "result_id": result.result_id,
                    "source": result.source,
                },
                tool_response_content={
                    "title": fetch_result.title,
                    "sections": len(fetch_result.sections),
                },
            )
            chunks = tuple(
                FetchedChunk(
                    source_id=f"r{i}",
                    title=section.heading,
                    url=fetch_result.url or fetch_result.local_path or result.result_id,
                    canonical_url=result.result_id,
                    text=section.text,
                    score=1.0 if section.is_match else 0.0,
                    heading_path=section.heading_path,
                    chunk_id=section.chunk_id,
                    chunk_order=section.chunk_order,
                    is_match=section.is_match,
                )
                for i, section in enumerate(fetch_result.sections)
            )
            self._research_mode = "chunks"
            self._research_packet = BrowserResearchPacket(
                title=fetch_result.title,
                url=fetch_result.url or fetch_result.local_path or result.result_id,
                full_text=fetch_result.full_text,
                chunks=chunks,
                source=fetch_result.source,
                result_id=fetch_result.result_id,
                total_chunks=fetch_result.total_chunks,
                truncated=fetch_result.truncated,
            )
            self._research_busy = None
            await self.broadcast_session_state()
            return snapshot
        except BrowserError:
            raise
        except Exception as exc:
            snapshot = await self._append_synthetic_tool_pair(
                current,
                tool_name="rag_fetch",
                tool_call_content={"result_id": result.result_id, "source": result.source},
                tool_response_content=_tool_error_payload(
                    reason="fetch_failed",
                    message=str(exc),
                    result_id=result.result_id,
                ),
                response_type="tool_error",
            )
            self._research_busy = None
            await self.broadcast_session_state()
            return snapshot

    async def research_insert_chunks(
        self,
        *,
        active_file: str,
        mode: str,
        chunk_indices: list[int] | None,
    ) -> dict[str, Any]:
        if self.run_active:
            raise BrowserError("run_active", "Wait for the current run to finish before inserting.")
        if self._research_mode != "chunks" or self._research_packet is None:
            raise BrowserError("research_inactive", "No fetched research chunks are available.")
        if mode not in {"selected_chunks", "full_page"}:
            raise BrowserError("invalid_insert_mode", "Insert mode must be selected_chunks or full_page.")

        packet = self._research_packet
        if mode == "full_page":
            body = packet.full_text
            label = "full page"
        else:
            indices = sorted(set(chunk_indices or []))
            if not indices:
                raise BrowserError("no_chunks_selected", "Select chunks before inserting.")
            if any(index < 0 or index >= len(packet.chunks) for index in indices):
                raise BrowserError("invalid_chunk_index", "Chunk index is out of range.")
            body = "\n\n".join(packet.chunks[index].text for index in indices)
            label = f"{len(indices)} chunk{'s' if len(indices) != 1 else ''}"
        if not body.strip():
            raise BrowserError("empty_research_content", "Fetched research content is empty.")

        active_path = resolve_workspace_path(active_file, workspace_root=self.workspace_root)
        self._research_busy = "inserting"
        await self.broadcast_session_state()
        try:
            current = await self.file_manager.read_snapshot(active_path)
            note_text, transcript_text = split_note_and_transcript(current.raw_text)
            content_block = f"# {packet.title}\n\n{body}"
            updated_note = _append_block_to_note_content(note_text, content_block)
            written = await self.file_manager.write_text(
                active_path,
                join_note_and_transcript(updated_note, transcript_text),
                expected_revision=current.revision_id,
            )
            snapshot = serialize_file_snapshot(written, workspace_root=self.workspace_root)
            title = packet.title[:40]
            await self.broadcast(
                "progress_event",
                serialize_progress_event(
                    ProgressEvent(
                        kind="status",
                        message=f"Inserted {label} from \"{title}\".",
                        path=active_path,
                    ),
                    workspace_root=self.workspace_root,
                ),
            )
            self._clear_research_state()
            await self.broadcast_session_state()
            return snapshot
        except OptimisticWriteError as exc:
            raise RevisionConflict(
                "revision_conflict",
                "File changed before research content was inserted.",
            ) from exc
        finally:
            if self._research_busy == "inserting":
                self._research_busy = None
                await self.broadcast_session_state()

    async def research_back(self) -> dict[str, Any]:
        if self._research_mode != "chunks":
            raise BrowserError("research_inactive", "Research is not showing fetched chunks.")
        self._research_mode = "results"
        self._research_packet = None
        self._research_busy = None
        await self.broadcast_session_state()
        return {"ok": True}

    async def research_cancel(self) -> dict[str, Any]:
        self._clear_research_state()
        await self.broadcast_session_state()
        return {"ok": True}

    async def _append_synthetic_tool_pair(
        self,
        snapshot: Any,
        *,
        tool_name: str,
        tool_call_content: Any,
        tool_response_content: Any,
        response_type: Literal["tool_result", "tool_error"] = "tool_result",
    ) -> dict[str, Any]:
        updated_text, _tool_id, _row_numbers = append_synthetic_tool_pair(
            snapshot.raw_text,
            tool_name=tool_name,
            tool_call_content=tool_call_content,
            tool_response_content=tool_response_content,
            response_type=response_type,
        )
        written = await self.file_manager.write_text(
            snapshot.path,
            updated_text,
            expected_revision=snapshot.revision_id,
        )
        return serialize_file_snapshot(written, workspace_root=self.workspace_root)

    def _set_research_busy(
        self,
        busy: Literal["searching", "fetching", "inserting"],
        *,
        source: Literal["web", "rag"],
        query: str,
        scope: str | None,
    ) -> None:
        self._research_mode = "idle"
        self._research_source = source
        self._research_query = query
        self._research_scope = scope
        self._research_results = ()
        self._research_packet = None
        self._research_busy = busy

    def _clear_research_state(self) -> None:
        self._research_mode = "idle"
        self._research_source = None
        self._research_query = ""
        self._research_scope = None
        self._research_results = ()
        self._research_packet = None
        self._research_busy = None

    async def broadcast(self, message_type: str, payload: dict[str, Any]) -> None:
        connections = tuple(self._connections)
        if not connections:
            return
        await asyncio.gather(
            *(conn.send_event(message_type, payload) for conn in connections),
            return_exceptions=True,
        )

    async def _run_prompt(
        self,
        *,
        run_id: str,
        active_file: Path,
        included_files: tuple[Path, ...],
        included_image_files: tuple[Path, ...],
        prompt_images: tuple[ProviderImageInput, ...],
        text: str,
        request_work_mode: WorkMode,
        active_plan_id: str | None,
        active_plan_path: Path | None,
        planning_status: str,
    ) -> None:
        try:
            provider = self.provider_factory(self.selected_model, self.cwd)
            if self.mode == "note":
                result = await self.note_runner.run(
                    NoteModeRunRequest(
                        active_file=active_file,
                        included_files=included_files,
                        included_image_files=included_image_files,
                        prompt_images=prompt_images,
                        active_plan_id=active_plan_id,
                        active_plan_path=active_plan_path,
                        planning_status=planning_status,
                        provider=provider,
                        user_prompt=text,
                        total_turn_budget=self.total_turn_budget,
                        model=self.selected_model.model,
                        reasoning_effort=self.reasoning_effort,
                        display_root=self.workspace_root,
                        progress_sink=self.handle_progress_event,
                        metadata={"cwd": str(self.cwd), "browser_run_id": run_id},
                        work_mode=request_work_mode,
                        permission_handler=self.request_permission,
                    )
                )
                await self._broadcast_note_tool_result(active_file, result)
            else:
                await self.chat_runner.run(
                    ChatModeRunRequest(
                        active_file=active_file,
                        included_files=included_files,
                        included_image_files=included_image_files,
                        prompt_images=prompt_images,
                        active_plan_id=active_plan_id,
                        active_plan_path=active_plan_path,
                        planning_status=planning_status,
                        provider=provider,
                        user_prompt=text,
                        total_turn_budget=self.total_turn_budget,
                        model=self.selected_model.model,
                        reasoning_effort=self.reasoning_effort,
                        display_root=self.workspace_root,
                        progress_sink=self.handle_progress_event,
                        metadata={"cwd": str(self.cwd), "browser_run_id": run_id},
                        work_mode=request_work_mode,
                        permission_handler=self.request_permission,
                    )
                )
        except asyncio.CancelledError:
            await self.broadcast(
                "progress_event",
                {
                    "kind": "error",
                    "message": "Forced Stop!",
                    "path": workspace_relative_path(active_file, workspace_root=self.workspace_root),
                    "details": {"run_id": run_id, "cancelled": True},
                },
            )
        except (NoteModeError, ChatModeError, FileReadError) as exc:
            await self.broadcast(
                "progress_event",
                {
                    "kind": "error",
                    "message": str(exc),
                    "path": workspace_relative_path(active_file, workspace_root=self.workspace_root),
                    "details": {"run_id": run_id},
                },
            )
        except Exception as exc:
            logger.exception("Browser run failed")
            await self.broadcast(
                "progress_event",
                {
                    "kind": "error",
                    "message": f"Browser run failed: {exc}",
                    "path": workspace_relative_path(active_file, workspace_root=self.workspace_root),
                    "details": {"run_id": run_id},
                },
            )
        finally:
            async with self._run_lock:
                if self._run_id == run_id:
                    self._run_task = None
                    self._run_id = None
                    self._force_stopped = False
            await self._release_run_file_lease()
            await self.broadcast_session_state()

    async def _release_run_file_lease(self) -> None:
        leased_run_file = self._leased_run_file
        self._leased_run_file = None
        if leased_run_file is None or self._release_run_file is None:
            return
        await self._release_run_file(leased_run_file)

    async def handle_progress_event(self, event: ProgressEvent) -> None:
        await self.broadcast(
            "progress_event",
            serialize_progress_event(event, workspace_root=self.workspace_root),
        )
        if await self._update_context_usage_from_progress(event):
            await self.broadcast_session_state()
        if event.kind == "file_written" and event.path is not None:
            await self._broadcast_transcript_row_for_file_write(event)

    async def _update_context_usage_from_progress(self, event: ProgressEvent) -> bool:
        if event.kind != "loop_event" or event.details.get("loop_kind") != "provider_response":
            return False
        usage = event.details.get("usage")
        if not isinstance(usage, dict):
            return False
        input_tokens = _coerce_positive_int(usage.get("input_tokens"))
        context_window = _coerce_positive_int(usage.get("model_context_window"))
        changed = False
        if input_tokens is not None:
            self._ctx_tokens_used = input_tokens
            self._ctx_last_note_chars = await self._note_content_length(event.path)
            changed = True
        if context_window is not None:
            self._ctx_window_size = context_window
            changed = True
        elif self._ctx_window_size is None:
            known = _known_context_window(self.selected_model)
            if known is not None:
                self._ctx_window_size = known
                changed = True
        return changed

    async def _note_content_length(self, path: Path | None) -> int | None:
        if path is None:
            return None
        try:
            snapshot = await self.file_manager.read_snapshot(path)
        except Exception:
            return None
        note_text, _transcript_text = split_note_and_transcript(snapshot.raw_text)
        return len(note_text)

    async def _broadcast_transcript_row_for_file_write(self, event: ProgressEvent) -> None:
        row_number = event.details.get("row_number")
        try:
            snapshot = await self.file_manager.read_snapshot(event.path)
            _, transcript_text = split_note_and_transcript(snapshot.raw_text)
            if not transcript_text:
                return
            rows = parse_transcript_rows(transcript_text)
            if not rows:
                return
            row = next(
                (item for item in rows if item.row_number == row_number),
                rows[-1],
            )
            await self.broadcast(
                "transcript_row",
                {
                    "path": workspace_relative_path(event.path, workspace_root=self.workspace_root),
                    "row": serialize_transcript_row(row),
                },
            )
        except Exception:
            logger.exception("Failed to broadcast transcript row")

    async def _broadcast_note_tool_result(
        self,
        active_file: Path,
        result: NoteModeRunResult,
    ) -> None:
        latest = _latest_note_tool_result(result)
        if latest is None:
            return

        tool_name, content = latest
        snapshot = next((item for item in result.final_file_snapshots if item.path == active_file), None)
        if snapshot is None:
            try:
                snapshot = await self.file_manager.read_snapshot(active_file)
            except Exception:
                logger.exception("Failed to read active file snapshot for note tool result")
                return

        await self.broadcast(
            "note_tool_result",
            {
                "path": workspace_relative_path(active_file, workspace_root=self.workspace_root),
                "tool_name": tool_name,
                "content": content,
                "snapshot": serialize_file_snapshot(snapshot, workspace_root=self.workspace_root),
            },
        )

    async def _handle_file_change(self, change: FileChange) -> None:
        try:
            payload = serialize_file_change(change, workspace_root=self.workspace_root)
        except WorkspacePathError:
            return
        await self.broadcast("file_changed", payload)

    def _list_directory(self, directory: Path) -> list[dict[str, str]]:
        entries: list[dict[str, str]] = []
        for entry in directory.iterdir():
            try:
                relative = workspace_relative_path(entry, workspace_root=self.workspace_root)
            except WorkspacePathError:
                continue
            try:
                kind = "dir" if entry.is_dir() else "file"
            except OSError:
                continue
            entries.append({"name": entry.name, "kind": kind, "path": relative})
        return sorted(entries, key=lambda item: (item["kind"] != "dir", item["name"].lower()))


def _validate_new_entry_name(name: str) -> None:
    if len(name.encode("utf-8")) > _MAX_ENTRY_NAME_BYTES:
        raise BrowserError(
            "name_too_long",
            f"Entry names must be {_MAX_ENTRY_NAME_BYTES} bytes or less.",
        )


def _parse_prompt_command(text: str) -> tuple[str, str] | None:
    match = _PROMPT_COMMAND_RE.search(text)
    if match is None:
        return None
    command = match.group(0)
    remaining = f"{text[:match.start()]}{text[match.end():]}".strip()
    return command, remaining


def _command_response(
    *,
    draft: str,
    message: str,
    snapshot: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    return {
        "handled": True,
        "draft": draft,
        "message": message,
        "run_id": run_id,
        "snapshot": snapshot,
    }


def _latest_note_tool_result(result: NoteModeRunResult) -> tuple[str, dict[str, Any]] | None:
    candidate_loop_results = [prompt_result.loop_result for prompt_result in result.prompt_results]
    if result.synthesis_loop_result is not None:
        candidate_loop_results.append(result.synthesis_loop_result)

    for loop_result in reversed(candidate_loop_results):
        for row in reversed(loop_result.run_log):
            if row.type != "tool_result" or row.tool_name not in {"note_edit", "note_write"}:
                continue
            if isinstance(row.content, dict):
                return row.tool_name, row.content
    return None


def _web_result_to_browser_result(result: SearchResult) -> BrowserResearchResult:
    return BrowserResearchResult(
        title=result.title,
        url=result.url,
        snippet=result.snippet,
        score=result.refined_score,
    )


def _rag_result_to_browser_result(result: Any) -> BrowserResearchResult:
    return BrowserResearchResult(
        title=str(getattr(result, "title", "")),
        url=getattr(result, "url", None),
        snippet=str(getattr(result, "snippet", "")),
        source=getattr(result, "source", None),
        result_id=getattr(result, "result_id", None),
        local_path=getattr(result, "local_path", None),
        score=getattr(result, "score", None),
        heading_path=tuple(getattr(result, "heading_path", ()) or ()),
    )


def _fetch_packet_to_browser_packet(packet: FetchPacket) -> BrowserResearchPacket:
    return BrowserResearchPacket(
        title=packet.title,
        url=packet.url,
        full_text=packet.full_markdown,
        chunks=packet.chunks,
    )


def _serialize_research_result(result: BrowserResearchResult) -> dict[str, Any]:
    return {
        "title": result.title,
        "url": result.url,
        "snippet": result.snippet,
        "source": result.source,
        "result_id": result.result_id,
        "local_path": result.local_path,
        "score": result.score,
        "heading_path": list(result.heading_path),
    }


def _serialize_research_packet(packet: BrowserResearchPacket) -> dict[str, Any]:
    return {
        "title": packet.title,
        "url": packet.url,
        "full_text_available": bool(packet.full_text.strip()),
        "source": packet.source,
        "result_id": packet.result_id,
        "total_chunks": packet.total_chunks,
        "truncated": packet.truncated,
        "chunks": [
            {
                "title": chunk.title,
                "url": chunk.url,
                "text": chunk.text,
                "score": chunk.score,
                "heading_path": list(chunk.heading_path),
                "chunk_id": chunk.chunk_id,
                "chunk_order": chunk.chunk_order,
                "is_match": chunk.is_match,
            }
            for chunk in packet.chunks
        ],
    }


def _append_block_to_note_content(note_text: str, block: str) -> str:
    normalized_note = note_text.rstrip("\n")
    normalized_block = block.strip("\n")
    if not normalized_note:
        return normalized_block
    return f"{normalized_note}\n\n{normalized_block}"


def _resolve_command_path(raw_path: str, *, base: Path) -> Path:
    path = Path(raw_path)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _merge_unique_paths(
    paths: Iterable[Path],
    *,
    exclude: Path | None = None,
) -> tuple[Path, ...]:
    excluded = exclude.resolve() if exclude is not None else None
    seen: set[Path] = set()
    merged: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved == excluded or resolved in seen:
            continue
        seen.add(resolved)
        merged.append(resolved)
    return tuple(merged)


def _project_tree_sort_key(path: Path) -> tuple[int, str]:
    try:
        is_dir = path.is_dir()
    except OSError:
        is_dir = False
    return (0 if is_dir else 1, path.name.lower())


def _tool_error_payload(*, reason: str, message: str, **details: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "category": "validation_error",
        "reason": reason,
        "message": message,
    }
    payload.update(details)
    return payload


_KNOWN_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-opus": 200_000,
    "claude-sonnet": 200_000,
    "claude-haiku": 200_000,
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "o1": 200_000,
    "o3": 200_000,
}


def _known_context_window(model_option: ModelOption) -> int | None:
    if model_option.context_window is not None:
        return model_option.context_window
    name = model_option.model.lower()
    for prefix, size in _KNOWN_CONTEXT_WINDOWS.items():
        if prefix in name:
            return size
    return None


def _coerce_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _build_provider(option: ModelOption, cwd: Path):
    if option.provider_name == "codex":
        return CodexProvider()
    if option.provider_name == "claude":
        return ClaudeProvider()
    return OpenAICompatibleProvider(project_root=cwd, profile_id=option.profile_id)
