from __future__ import annotations

import asyncio
import logging
import re
import shutil
from collections.abc import Callable, Iterable
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
from aunic.domain import ReasoningEffort, WorkMode
from aunic.errors import ChatModeError, FileReadError, NoteModeError, OptimisticWriteError
from aunic.loop import ToolLoop
from aunic.model_options import ModelOption, build_model_options
from aunic.modes import (
    ChatModeRunRequest,
    ChatModeRunner,
    NoteModeRunRequest,
    NoteModeRunResult,
    NoteModeRunner,
)
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
ProviderFactory = Callable[[ModelOption, Path], Any]
_MAX_ENTRY_NAME_BYTES = 255
_PROMPT_COMMANDS = (
    "/context",
    "/note",
    "/chat",
    "/work",
    "/read",
    "/off",
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
class BrowserIncludeEntry:
    path: str
    is_dir: bool
    recursive: bool = False
    active: bool = True


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
    ) -> None:
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
        self.reasoning_effort = reasoning_effort
        self.total_turn_budget = total_turn_budget
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
        self._include_entries: list[BrowserIncludeEntry] = []
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
        if self._watch_unsubscribe is None:
            self._watch_unsubscribe = self._watch_hub.subscribe(self._handle_file_change)
        await self._watch_hub.start((self.workspace_root,))

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

    def session_state(self) -> dict[str, Any]:
        return {
            "run_active": self.run_active,
            "run_id": self._run_id,
            "workspace_root": str(self.workspace_root),
            "default_mode": self.mode,
            "mode": self.mode,
            "work_mode": self.work_mode,
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
        if work_mode not in {"off", "read", "work"}:
            raise BrowserError("invalid_work_mode", "Work mode must be off, read, or work.")
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
    ) -> str:
        if not text.strip():
            raise BrowserError("empty_prompt", "Prompt text cannot be empty.")

        active_path = resolve_workspace_path(active_file, workspace_root=self.workspace_root)
        included_paths = self._merge_included_paths(
            active_path,
            tuple(resolve_workspace_path(item, workspace_root=self.workspace_root) for item in included_files),
        )
        run_id = uuid4().hex
        async with self._run_lock:
            if self.run_active:
                raise RunInProgress()
            self._run_id = run_id
            self._force_stopped = False
            self._run_task = asyncio.create_task(
                self._run_prompt(
                    run_id=run_id,
                    active_file=active_path,
                    included_files=included_paths,
                    text=text,
                ),
                name=f"aunic-browser-run-{run_id}",
            )
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

        if command == "/include":
            message = self._handle_include_command(active_path, remaining)
            await self.broadcast_session_state()
            return _command_response(draft="", message=message)

        if command == "/exclude":
            message = self._handle_exclude_command(remaining)
            await self.broadcast_session_state()
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

    def _handle_include_command(self, active_path: Path, arg: str) -> str:
        raw = arg.strip()
        recursive = False
        if raw.startswith("-r "):
            recursive = True
            raw = raw[3:].strip()
        if not raw:
            raise BrowserError("invalid_prompt_command", "Usage: /include [-r] <path>")

        target = _resolve_command_path(raw, base=active_path.parent)
        is_dir = raw.endswith("/") or target.is_dir()
        if raw in {entry.path for entry in self._include_entries}:
            return f"Already included: {raw}"
        self._include_entries.append(
            BrowserIncludeEntry(path=raw, is_dir=is_dir, recursive=recursive)
        )
        kind = "directory" if is_dir else "file"
        return f"Included {kind}: {raw}"

    def _handle_exclude_command(self, arg: str) -> str:
        raw = arg.strip()
        if not raw:
            raise BrowserError("invalid_prompt_command", "Usage: /exclude <path>")
        before = len(self._include_entries)
        self._include_entries = [entry for entry in self._include_entries if entry.path != raw]
        if len(self._include_entries) == before:
            raise BrowserError("invalid_prompt_command", f"Not in include list: {raw}")
        return f"Excluded: {raw}"

    def _merge_included_paths(
        self,
        active_path: Path,
        explicit_paths: tuple[Path, ...],
    ) -> tuple[Path, ...]:
        seen: set[Path] = set()
        merged: list[Path] = []
        for path in (*self._resolve_included_paths(active_path), *explicit_paths):
            resolved = path.resolve()
            if resolved == active_path or resolved in seen:
                continue
            seen.add(resolved)
            merged.append(resolved)
        return tuple(merged)

    def _resolve_included_paths(self, active_path: Path) -> tuple[Path, ...]:
        base = active_path.parent
        seen: set[Path] = set()
        resolved: list[Path] = []
        for entry in self._include_entries:
            if not entry.active:
                continue
            target = _resolve_command_path(entry.path, base=base)
            if entry.is_dir:
                pattern = "**/*.md" if entry.recursive else "*.md"
                candidates = sorted(target.glob(pattern)) if target.is_dir() else []
            else:
                candidates = [target]
            for candidate in candidates:
                item = candidate.resolve()
                if item == active_path or item in seen:
                    continue
                seen.add(item)
                resolved.append(item)
        return tuple(resolved)

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
        text: str,
    ) -> None:
        try:
            provider = self.provider_factory(self.selected_model, self.cwd)
            if self.mode == "note":
                result = await self.note_runner.run(
                    NoteModeRunRequest(
                        active_file=active_file,
                        included_files=included_files,
                        provider=provider,
                        user_prompt=text,
                        total_turn_budget=self.total_turn_budget,
                        model=self.selected_model.model,
                        reasoning_effort=self.reasoning_effort,
                        display_root=self.workspace_root,
                        progress_sink=self.handle_progress_event,
                        metadata={"cwd": str(self.cwd), "browser_run_id": run_id},
                        work_mode=self.work_mode,
                        permission_handler=self.request_permission,
                    )
                )
                await self._broadcast_note_tool_result(active_file, result)
            else:
                await self.chat_runner.run(
                    ChatModeRunRequest(
                        active_file=active_file,
                        included_files=included_files,
                        provider=provider,
                        user_prompt=text,
                        total_turn_budget=self.total_turn_budget,
                        model=self.selected_model.model,
                        reasoning_effort=self.reasoning_effort,
                        display_root=self.workspace_root,
                        progress_sink=self.handle_progress_event,
                        metadata={"cwd": str(self.cwd), "browser_run_id": run_id},
                        work_mode=self.work_mode,
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
            await self.broadcast_session_state()

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
