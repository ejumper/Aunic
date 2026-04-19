from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.selection import SelectionType

from aunic.config import SETTINGS
from aunic.context import FileManager
from aunic.context.types import TextSpan
from aunic.domain import TranscriptRow
from aunic.errors import ChatModeError, FileReadError, NoteModeError, OptimisticWriteError
from aunic.modes import ChatModeRunRequest, ChatModeRunner, NoteModeRunRequest, NoteModeRunner, NoteModeRunResult
from aunic.plans import PlanService
from aunic.progress import ProgressEvent
from aunic.proto_settings import get_openai_compatible_profiles, resolve_openai_compatible_profile
from aunic.providers import ClaudeProvider, CodexProvider, OpenAICompatibleProvider
from aunic.research.fetch import FetchService
from aunic.research.search import SearchService, canonicalize_url
from aunic.research.types import FetchedChunk, FetchPacket, ResearchState, SearchResult
from aunic.tools.note_edit import reapply_note_edit_payload_to_note_content
from aunic.tools.runtime import PermissionRequest, join_note_and_transcript
from aunic.transcript.parser import parse_transcript_rows, split_note_and_transcript
from aunic.transcript.writer import append_synthetic_tool_pair, delete_row_by_number, delete_search_result_item
from aunic.tui.folding import (
    FoldRender,
    apply_folds,
    carry_forward_managed_section_folds,
    default_folded_anchor_ids,
    is_fold_placeholder_line,
    reconstruct_full_text,
    toggle_fold_for_line,
)
from aunic.tui import rendering as tui_rendering
from aunic.tui.rendering import soft_wrap_prefix_for_line, register_rag_scopes
from aunic.tui.types import (
    FindField,
    IncludeEntry,
    ModelOption,
    NoteConflictState,
    PlanMenuEntry,
    PermissionPromptState,
    SleepStatusState,
    TranscriptFilter,
    TranscriptViewState,
    TuiMode,
    TuiState,
)
from aunic.usage import format_usage_brief
from aunic.usage_log import resolve_usage_root

_URL_OPENER: list[str] | None = None


def _open_url_focused(url: str) -> None:
    """Open a URL in the default browser, suppressing stderr noise and raising the window."""
    global _URL_OPENER
    if _URL_OPENER is None:
        for cmd in ("kde-open", "kde-open5", "xdg-open"):
            if shutil.which(cmd):
                _URL_OPENER = [cmd]
                break
        else:
            _URL_OPENER = []
    if _URL_OPENER:
        subprocess.Popen(
            [*_URL_OPENER, url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    else:
        import webbrowser
        webbrowser.open(url)


def _copy_to_system_clipboard(text: str) -> None:
    """Write text to the system clipboard using wl-copy (Wayland) or xclip (X11)."""
    if shutil.which("wl-copy"):
        cmd = ["wl-copy"]
    elif shutil.which("xclip"):
        cmd = ["xclip", "-selection", "clipboard"]
    else:
        return
    try:
        subprocess.run(cmd, input=text.encode(), check=False, timeout=2)
    except Exception:
        pass


class TuiController:
    def __init__(
        self,
        *,
        active_file: Path,
        included_files: tuple[Path, ...] = (),
        initial_provider: str = "codex",
        initial_model: str | None = None,
        initial_profile_id: str | None = None,
        reasoning_effort=None,
        display_root: Path | None = None,
        cwd: Path | None = None,
        allow_missing_active_file: bool = False,
        create_missing_parents_on_save: bool = False,
        file_manager: FileManager | None = None,
        note_runner: NoteModeRunner | None = None,
        chat_runner: ChatModeRunner | None = None,
        search_service: SearchService | None = None,
        fetch_service: FetchService | None = None,
    ) -> None:
        self._file_manager = file_manager or FileManager()
        self._note_runner = note_runner or NoteModeRunner(file_manager=self._file_manager)
        self._chat_runner = chat_runner or ChatModeRunner(file_manager=self._file_manager)
        self._search_service = search_service or SearchService()
        self._fetch_service = fetch_service or FetchService()
        self._display_root = display_root
        self._cwd = cwd or Path.cwd()
        self.state = TuiState(
            active_file=active_file,
            available_files=tuple([active_file, *[path for path in included_files if path != active_file]]),
            context_file=active_file,
            display_file=active_file,
            mode="note",
            selected_model_index=0,
            model_options=_build_model_options(self._cwd, initial_provider, initial_model),
            reasoning_effort=reasoning_effort,
            indicator_message="Ready.",
            active_file_missing_on_disk=allow_missing_active_file,
            create_parents_on_first_save=create_missing_parents_on_save,
        )
        self.state.selected_model_index = _selected_model_index(
            self.state.model_options,
            initial_provider,
            initial_model,
            initial_profile_id,
        )

        # Register RAG scopes for prompt highlighting (optional — RAG config may not exist)
        try:
            from aunic.rag.config import load_rag_config
            _rag_cfg = load_rag_config(self._cwd)
            if _rag_cfg is not None:
                _tui_scopes = _rag_cfg.tui_scopes if _rag_cfg.tui_scopes is not None else _rag_cfg.scopes
                register_rag_scopes(tuple(s.name for s in _tui_scopes))
        except Exception:
            pass

        self._editor_buffer: Buffer | None = None
        self._prompt_buffer: Buffer | None = None
        self._invalidate: Callable[[], None] = lambda: None
        self._watch_task: asyncio.Task[None] | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._force_stopped: bool = False
        self._tool_status_set_at: float = 0.0
        self._pontificating_task: asyncio.Task[None] | None = None
        self._syncing_editor = False
        self._syncing_prompt = False
        self._full_text = ""
        self._note_content_text = ""
        self._transcript_text: str | None = None
        self._transcript_rows = []
        self._last_saved_text = ""
        self._last_revision_id: str | None = None
        self._fold_render = FoldRender("", {}, {}, (), ())
        self.transcript_view_state = TranscriptViewState()
        self._on_transcript_open_changed: Callable[[bool], None] | None = None
        self._on_transcript_maximized_changed: Callable[[bool], None] | None = None
        self._on_includes_changed: Callable[[], None] | None = None
        self._on_file_switched: Callable[[Path], None] | None = None
        self._on_mode_changed: Callable[[], None] | None = None
        self._isolate_override: tuple[Path, ...] | None = None
        self._cached_fetch_urls: set[str] = set()
        self._recent_display_change_spans: tuple[TextSpan, ...] = ()
        self._model_insert_display_change_spans: tuple[TextSpan, ...] = ()
        # @web ephemeral navigation state
        self._web_query: str = ""
        self._web_results: tuple[SearchResult, ...] = ()
        self._web_result_cursor: int = 0
        self._web_result_expanded: set[int] = set()
        self._web_selected_result: int | None = None
        self._web_packets: tuple[FetchPacket, ...] = ()
        self._web_chunk_cursor: int = 0
        self._web_chunk_selected: set[int] = set()
        self._web_chunk_expanded: set[int] = set()
        # @rag ephemeral navigation state
        self._rag_active: bool = False
        self._rag_scope: str | None = None
        self._rag_results: tuple = ()  # tuple[RagSearchResult, ...]
        self._rag_client = None  # RagClient | None, lazy-initialized
        self._permission_future: asyncio.Future[str] | None = None
        self._pending_prompt_restore: str | None = None
        self._run_start_time: float | None = None
        self._run_turn_count: int = 0
        self._run_error_count: int = 0
        self._run_note_baseline_content: str | None = None
        self._ctx_tokens_used: int | None = None
        self._ctx_window_size: int | None = None
        self._ctx_last_file_len: int | None = None
        self._ctx_fetched_profiles: set[str] = set()
        self._load_ctx_cache()

    def attach_buffers(self, *, editor_buffer: Buffer, prompt_buffer: Buffer) -> None:
        self._editor_buffer = editor_buffer
        self._prompt_buffer = prompt_buffer
        editor_buffer.on_text_changed += self._handle_editor_buffer_changed
        editor_buffer.on_cursor_position_changed += self._handle_cursor_moved
        prompt_buffer.on_text_changed += self._handle_prompt_buffer_changed

    def set_invalidator(self, callback: Callable[[], None]) -> None:
        self._invalidate = callback

    async def initialize(self) -> None:
        if self.state.active_file_missing_on_disk:
            self._bootstrap_missing_active_file()
        else:
            await self._load_active_file(reset_dirty=True)
        if self._prompt_buffer is not None:
            self._sync_prompt_text(self.state.prompt_text)

    async def start_watch_task(self) -> None:
        if self._watch_task is not None or self.state.active_file_missing_on_disk:
            return
        self._watch_task = asyncio.create_task(self._watch_files())

    async def shutdown(self) -> None:
        for task in (self._watch_task, self._run_task):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @property
    def active_file_label(self) -> str:
        path = self._display_file()
        if self._display_root is None:
            label = path.name
        else:
            try:
                label = str(path.relative_to(self._display_root))
            except ValueError:
                label = str(path)
        if path != self._context_file():
            label = f"Plan: {path.stem} (source: {self._context_file().name})"
        return f"{label}{' *' if self.state.editor_dirty else ''}"

    def _context_file(self) -> Path:
        return self.state.context_file or self.state.active_file

    def _display_file(self) -> Path:
        return self.state.display_file or self._context_file()

    def prompt_height(self) -> int:
        if self._prompt_buffer is None:
            return 3
        return max(1, min(10, self._prompt_buffer.document.line_count))

    def indicator_height(self) -> int:
        if self.state.sleep_status is not None and self.state.run_in_progress:
            return 1
        line_count = len(self.state.indicator_message.splitlines()) or 1
        return max(1, min(3, line_count))

    @property
    def context_fill_fraction(self) -> float | None:
        """Returns 0.0–1.0 fill fraction, or None if context size is unknown."""
        used = self._effective_ctx_tokens()
        window = self._ctx_window_size or _known_context_window(self.state.selected_model)
        if window is None:
            self._maybe_fetch_openrouter_context_window()
        if used is None or window is None or window == 0:
            return None
        return min(1.0, used / window)

    @property
    def context_is_estimate(self) -> bool:
        if self._ctx_tokens_used is None or self._ctx_last_file_len is None:
            return False
        return len(self._full_text) != self._ctx_last_file_len

    def _effective_ctx_tokens(self) -> int | None:
        if self._ctx_tokens_used is None:
            return None
        if self._ctx_last_file_len is None:
            return self._ctx_tokens_used
        delta = len(self._full_text) - self._ctx_last_file_len
        return max(0, self._ctx_tokens_used + delta // 4)

    def current_prompt_text(self) -> str:
        return self._prompt_buffer.text if self._prompt_buffer is not None else self.state.prompt_text

    def current_editor_text(self) -> str:
        return self._editor_buffer.text if self._editor_buffer is not None else self._fold_render.display_text

    def current_display_lines(self) -> list[str]:
        return self.current_editor_text().splitlines()

    def has_transcript(self) -> bool:
        return bool(self._transcript_rows)

    def visible_transcript_rows(self):
        rows = [row for row in self._transcript_rows if row.type != "tool_call"]
        filter_mode = self.transcript_view_state.filter_mode
        if filter_mode == "chat":
            rows = [row for row in rows if row.type == "message"]
        elif filter_mode == "tools":
            rows = [row for row in rows if row.type in {"tool_result", "tool_error"}]
        elif filter_mode == "search":
            rows = [
                row for row in rows
                if row.type in {"tool_result", "tool_error"}
                and row.tool_name in {"web_search", "web_fetch"}
            ]

        if self.transcript_view_state.sort_order == "ascending":
            return list(reversed(rows))
        return rows

    def tool_call_index(self) -> dict[str, object]:
        return {
            row.tool_id: row
            for row in self._transcript_rows
            if row.type == "tool_call" and row.tool_id
        }

    def cached_fetch_urls(self) -> set[str]:
        return set(self._cached_fetch_urls)

    def recent_display_change_spans(self) -> tuple[TextSpan, ...]:
        return self._recent_display_change_spans

    def model_insert_display_change_spans(self) -> tuple[TextSpan, ...]:
        return self._model_insert_display_change_spans

    def editor_is_read_only(self) -> bool:
        return self.cursor_on_placeholder_line()

    def cursor_on_placeholder_line(self) -> bool:
        if self._editor_buffer is None:
            return False
        return is_fold_placeholder_line(self._editor_buffer.document.current_line)

    def line_prefix(
        self,
        line_number: int,
        wrap_count: int,
        *,
        in_code_block: bool = False,
    ) -> str:
        lines = self.current_display_lines()
        if line_number >= len(lines):
            return ""
        return soft_wrap_prefix_for_line(
            lines[line_number],
            wrap_count,
            in_code_block=in_code_block,
        )

    def _rebuild_available_files(self) -> None:
        """Resolve include_entries to concrete paths and update available_files."""
        active = self._context_file()
        base = active.parent
        seen: set[Path] = set()
        resolved: list[Path] = []
        for entry in self.state.include_entries:
            if not entry.active:
                continue
            raw = Path(entry.path)
            target = (base / raw).resolve() if not raw.is_absolute() else raw.resolve()
            if entry.is_dir:
                pattern = "**/*.md" if entry.recursive else "*.md"
                for p in sorted(target.glob(pattern)):
                    rp = p.resolve()
                    if rp != active and rp not in seen:
                        seen.add(rp)
                        resolved.append(rp)
            else:
                if target != active and target not in seen:
                    seen.add(target)
                    resolved.append(target)
        self.state.available_files = (active, *resolved)
        if self._on_includes_changed is not None:
            self._on_includes_changed()

    def toggle_include_active(self, index: int) -> None:
        entry = self.state.include_entries[index]
        self.state.include_entries[index] = IncludeEntry(
            path=entry.path, is_dir=entry.is_dir, recursive=entry.recursive, active=not entry.active
        )
        self._rebuild_available_files()
        self._invalidate()

    def remove_include(self, index: int) -> None:
        self.state.include_entries.pop(index)
        self._rebuild_available_files()
        self._invalidate()

    def open_file_menu(self) -> None:
        self.state.active_dialog = "file_menu"
        self.state.dialog_selection_index = self.state.available_files.index(self._context_file())
        self._invalidate()

    def open_model_picker(self) -> None:
        self.state.active_dialog = "model_picker"
        self.state.dialog_selection_index = self.state.selected_model_index
        self._invalidate()

    def close_dialog(self) -> None:
        if self.state.active_dialog == "permission_prompt":
            self.resolve_permission_prompt("reject")
            return
        if self.state.active_dialog == "note_conflict":
            return
        was_model_picker = self.state.active_dialog == "model_picker"
        self.state.active_dialog = None
        self.state.pending_switch_path = None
        self.state.permission_prompt = None
        self.state.note_conflict = None
        if was_model_picker and self._pending_prompt_restore is not None:
            self._sync_prompt_text(self._pending_prompt_restore)
            self._pending_prompt_restore = None
        self._invalidate()

    def move_dialog_selection(self, delta: int) -> None:
        if self.state.active_dialog == "file_menu":
            size = len(self.state.available_files)
        elif self.state.active_dialog == "model_picker":
            size = len(self.state.model_options)
        elif self.state.active_dialog == "permission_prompt":
            prompt = self.state.permission_prompt
            size = 2 if prompt is not None and prompt.details.get("kind") == "plan_approval" else 3
        else:
            return
        self.state.dialog_selection_index = (self.state.dialog_selection_index + delta) % max(size, 1)
        self._invalidate()

    async def activate_dialog_selection(self) -> None:
        if self.state.active_dialog == "file_menu":
            selected = self.state.available_files[self.state.dialog_selection_index]
            await self.request_file_switch(selected)
            return
        if self.state.active_dialog == "model_picker":
            self.state.selected_model_index = self.state.dialog_selection_index
            self._set_status(
                f"Selected model: {self.state.selected_model.label}.",
            )
            self.state.active_dialog = None
            self._invalidate()
            return
        if self.state.active_dialog == "permission_prompt":
            prompt = self.state.permission_prompt
            options = (
                ("once", "reject")
                if prompt is not None and prompt.details.get("kind") == "plan_approval"
                else ("once", "always", "reject")
            )
            self.resolve_permission_prompt(options[self.state.dialog_selection_index])

    async def request_file_switch(self, path: Path) -> None:
        if path == self._context_file():
            self.close_dialog()
            return
        self.state.pending_switch_path = path
        if self.state.editor_dirty:
            self.state.active_dialog = "file_switch_confirm"
            self._invalidate()
            return
        await self._switch_active_file(path)

    async def confirm_file_switch(self, *, save_changes: bool | None) -> None:
        target = self.state.pending_switch_path
        if target is None:
            self.close_dialog()
            return
        if save_changes is None:
            self.close_dialog()
            return
        if save_changes:
            if not await self.save_active_file():
                return
        await self._switch_active_file(target)

    def plan_menu_entries(self) -> tuple[PlanMenuEntry, ...]:
        service = PlanService(self._context_file())
        return tuple(
            PlanMenuEntry(
                id=entry.id,
                title=entry.title,
                status=entry.status,
                path=entry.file_path(service.plans_dir),
            )
            for entry in service.list_plans_for_source_note()
        )

    async def create_plan(self, title: str) -> None:
        if self.state.editor_dirty and not await self.save_active_file():
            return
        if self.state.pre_plan_work_mode is None:
            self.state.pre_plan_work_mode = self.state.work_mode
        document = PlanService(self._context_file()).create_plan(title)
        self.state.active_plan_id = document.entry.id
        self.state.active_plan_path = document.path
        self.state.planning_status = "drafting"
        self.state.display_file = document.path
        self.state.active_dialog = None
        await self._load_active_file(reset_dirty=True)
        self._set_status(f"Created plan: {document.entry.title}.")
        self._invalidate()

    async def open_plan(self, plan_id: str) -> None:
        if self.state.editor_dirty and not await self.save_active_file():
            return
        document = PlanService(self._context_file()).get_plan(plan_id)
        self.state.active_plan_id = document.entry.id
        self.state.active_plan_path = document.path
        if document.entry.status in {"draft", "awaiting_approval"}:
            if self.state.pre_plan_work_mode is None:
                self.state.pre_plan_work_mode = self.state.work_mode
            self.state.planning_status = (
                "awaiting_approval" if document.entry.status == "awaiting_approval" else "drafting"
            )
        self.state.display_file = document.path
        self.state.active_dialog = None
        await self._load_active_file(reset_dirty=True)
        self._set_status(f"Opened plan: {document.entry.title}.")
        self._invalidate()

    async def _handle_plan_command(self, remaining: str) -> None:
        command = remaining.strip()
        if command == "list":
            self.open_file_menu()
            self._set_status("Plans are listed in the file menu.")
            return
        if command == "open":
            if self.state.active_plan_path is None:
                self._set_error("No active plan to open.")
            else:
                self._set_status(f"Active plan: {self.state.active_plan_path}")
            return
        if command:
            await self.create_plan(command)
            return

        if self.state.active_plan_id and self.state.active_plan_path:
            if self._display_file() != self.state.active_plan_path:
                await self.open_plan(self.state.active_plan_id)
            else:
                self._set_status(f"Planning: {self.state.active_plan_path}")
            return

        drafts = [
            entry
            for entry in PlanService(self._context_file()).list_plans_for_source_note()
            if entry.status in {"draft", "awaiting_approval"}
        ]
        if len(drafts) == 1:
            await self.open_plan(drafts[0].id)
            return
        if len(drafts) > 1:
            self.open_file_menu()
            self._set_status("Multiple draft plans found. Choose one from the file menu.")
            return
        await self.create_plan("Untitled Plan")

    async def confirm_external_reload(self, *, reload_file: bool) -> None:
        self.state.active_dialog = None
        self.state.pending_external_reload = False
        if reload_file:
            await self._load_active_file(reset_dirty=True)
            self._set_status("Reloaded the active file from disk.")
        else:
            self.state.ignored_external_revision = self._last_revision_id
            self._set_status("Ignored the external file change for now.")
        self._invalidate()

    async def confirm_note_conflict(self, *, prefer_model: bool) -> None:
        conflict = self.state.note_conflict
        if conflict is None:
            self.state.active_dialog = None
            self._invalidate()
            return

        if prefer_model:
            chosen_note = conflict.model_note_content
            discarded_note = conflict.user_note_content
            discarded_label = "user"
            resolution = "model"
        else:
            chosen_note = conflict.user_note_content
            discarded_note = conflict.model_note_content
            discarded_label = "model"
            resolution = "user"

        backup_path = self._write_note_conflict_backup(
            discarded_note,
            discarded_label=discarded_label,
        )
        updated_text = join_note_and_transcript(chosen_note, conflict.transcript_text)
        try:
            previous_display_text = self._fold_render.display_text
            await self._persist_active_file_text(
                updated_text,
                expected_revision=conflict.model_revision_id,
            )
            await self._load_active_file(
                reset_dirty=True,
                highlight_recent=prefer_model and conflict.tool_name != "note_edit",
            )
            if prefer_model and conflict.tool_name == "note_edit":
                self._set_model_insert_highlight_from_display_diff(
                    previous_display_text,
                    self._fold_render.display_text,
                )
        except OptimisticWriteError:
            self.state.pending_external_reload = True
            self.state.active_dialog = "reload_confirm"
            self._set_error("The file changed on disk. Reload or ignore before saving.")
            self._invalidate()
            return
        except FileReadError as exc:
            self._set_error(str(exc))
            self._invalidate()
            return

        self.state.active_dialog = None
        self.state.note_conflict = None
        backup_suffix = f" Backup saved to {backup_path}." if backup_path is not None else ""
        self._set_status(f"Conflict resolved: {resolution} wins.{backup_suffix}")
        self._invalidate()

    async def save_active_file(self) -> bool:
        if self.state.run_in_progress:
            self._set_error("Wait for the current run to finish before saving.")
            self._invalidate()
            return False
        try:
            snapshot = await self._persist_active_file_text(self._full_text)
        except OptimisticWriteError:
            self.state.pending_external_reload = True
            self.state.active_dialog = "reload_confirm"
            self._set_error("The file changed on disk. Reload or ignore before saving.")
            self._invalidate()
            return False
        except FileReadError as exc:
            self._set_error(str(exc))
            self._invalidate()
            return False
        self._set_status("Saved active file.")
        self._invalidate()
        return True

    def open_find_ui(
        self,
        *,
        replace_mode: bool = False,
        saved_prompt_text: str | None = None,
        find_text: str | None = None,
        replace_text: str | None = None,
    ) -> None:
        state = self.state.find_ui
        was_active = state.active
        if not was_active:
            state.saved_prompt_text = self.current_prompt_text() if saved_prompt_text is None else saved_prompt_text
            state.case_sensitive = False
        elif saved_prompt_text is not None:
            state.saved_prompt_text = saved_prompt_text
        state.active = True
        state.replace_mode = replace_mode
        state.active_field = "find"
        state.button_index = 0
        if find_text is not None:
            state.find_text = find_text
        if replace_text is not None:
            state.replace_text = replace_text
        self._refresh_find_matches(auto_select=bool(state.find_text))
        if replace_mode:
            self._set_status("Find/replace active. Esc closes. Enter replaces. Replace all is on the button row.")
        else:
            self._set_status("Find active. Enter/Down jumps next. Up jumps previous. Esc closes.")
        self._invalidate()

    def close_find_ui(self) -> None:
        state = self.state.find_ui
        if not state.active:
            return
        restored_prompt = state.saved_prompt_text
        state.active = False
        state.replace_mode = False
        state.case_sensitive = False
        state.find_text = ""
        state.replace_text = ""
        state.active_field = "find"
        state.button_index = 0
        state.saved_prompt_text = ""
        state.current_match_index = None
        state.current_match_start = None
        state.current_match_end = None
        state.match_count = 0
        self._clear_editor_selection()
        self._sync_prompt_text(restored_prompt)
        self._set_status("Closed find.")
        self._invalidate()

    def set_find_field_text(self, field: FindField, text: str) -> None:
        if field == "find":
            self.state.find_ui.find_text = text
            self._refresh_find_matches(auto_select=bool(text))
        else:
            self.state.find_ui.replace_text = text
        self._invalidate()

    def toggle_find_case_sensitive(self) -> None:
        self.state.find_ui.case_sensitive = not self.state.find_ui.case_sensitive
        self._refresh_find_matches(auto_select=bool(self.state.find_ui.find_text))
        self._invalidate()

    def set_find_active_field(self, field: FindField) -> None:
        if field == "replace" and not self.state.find_ui.replace_mode:
            return
        self.state.find_ui.active_field = field
        self._invalidate()

    def set_find_replace_mode(self, replace_mode: bool) -> None:
        self.state.find_ui.replace_mode = replace_mode
        self.state.find_ui.active_field = "find"
        self.state.find_ui.button_index = 0
        if replace_mode:
            self._set_status("Find/replace active. Esc closes. Enter replaces. Replace all is on the button row.")
        else:
            self._set_status("Find active. Enter/Down jumps next. Up jumps previous. Esc closes.")
        self._invalidate()

    def find_next_match(self) -> None:
        self._move_find_match(direction=1)

    def find_previous_match(self) -> None:
        self._move_find_match(direction=-1)

    def replace_current_find_match(self) -> bool:
        state = self.state.find_ui
        matches = self._find_matches()
        match_index = state.current_match_index
        if match_index is None or match_index >= len(matches):
            self._set_error("No active match to replace.")
            self._invalidate()
            return False
        match = matches[match_index]
        replacement = state.replace_text
        updated_note = (
            self._note_content_text[:match.start]
            + replacement
            + self._note_content_text[match.end:]
        )
        next_start = match.start + len(replacement)
        self._apply_note_content_edit(updated_note)
        updated_matches = _find_literal_matches(updated_note, state.find_text, case_sensitive=state.case_sensitive)
        if updated_matches:
            next_index = next((i for i, span in enumerate(updated_matches) if span.start >= next_start), 0)
            self._activate_find_match(updated_matches, next_index)
        else:
            self._clear_find_match_state()
            self._clear_editor_selection()
            self._set_status("Replaced match. No more matches.")
        self._invalidate()
        return True

    def replace_all_find_matches(self) -> int:
        state = self.state.find_ui
        matches = self._find_matches()
        if not matches:
            self._set_error("No matches to replace.")
            self._invalidate()
            return 0

        replacement = state.replace_text
        parts: list[str] = []
        last_end = 0
        replaced = 0
        for match in matches:
            matched_text = self._note_content_text[match.start:match.end]
            parts.append(self._note_content_text[last_end:match.start])
            if matched_text != replacement:
                parts.append(replacement)
                replaced += 1
            else:
                parts.append(matched_text)
            last_end = match.end
        parts.append(self._note_content_text[last_end:])

        if replaced <= 0:
            self._set_status("All matches already use the replacement text.")
            self._invalidate()
            return 0

        updated_note = "".join(parts)
        self._apply_note_content_edit(updated_note)
        updated_matches = _find_literal_matches(updated_note, state.find_text, case_sensitive=state.case_sensitive)
        if updated_matches:
            self._activate_find_match(updated_matches, 0)
        else:
            self._clear_find_match_state()
            self._clear_editor_selection()
        self._set_status(f"Replaced {replaced} matches.")
        self._invalidate()
        return replaced

    async def send_prompt(self) -> None:
        # Dispatch to web flow if already in web mode (prompt is hidden, Ctrl+R = confirm)
        if self.state.web_mode != "idle":
            self._handle_web_send()
            return

        if self.state.find_ui.active:
            self._set_error("Close find before running the model.")
            self._invalidate()
            return

        if self.state.run_in_progress:
            self._set_error("A model run is already in progress.")
            self._invalidate()
            return

        prompt_text = self.current_prompt_text()
        stripped = prompt_text.strip()

        if self.state.active_file_missing_on_disk and not await self.save_active_file():
            return

        # Detect active slash/@ commands anywhere in the prompt text. Read the
        # regex from the module so runtime RAG scope registration is visible.
        cmd_match = tui_rendering._PROMPT_COMMAND_RE.search(prompt_text)
        if cmd_match is not None:
            cmd = cmd_match.group(0)
            remaining = (prompt_text[:cmd_match.start()] + prompt_text[cmd_match.end():]).strip()

            if cmd == "@web":
                query = remaining
                if not query:
                    self._set_error("Usage: @web <search query>")
                    self._invalidate()
                    return
                if self.state.editor_dirty and not await self.save_active_file():
                    return
                self._sync_prompt_text("")
                self._run_task = asyncio.create_task(self._run_web_search(query))
                return

            if cmd == "@rag" or (cmd.startswith("@") and cmd[1:] in self._rag_scope_names()):
                scope = "rag" if cmd == "@rag" else cmd[1:]
                query = remaining
                if not query:
                    self._set_error(f"Usage: {cmd} <search query>")
                    self._invalidate()
                    return
                client = self._ensure_rag_client()
                if client is None:
                    self._set_error(
                        "RAG not configured. Add a \"rag\" section to proto-settings.json with a server URL."
                    )
                    self._invalidate()
                    return
                if self.state.editor_dirty and not await self.save_active_file():
                    return
                self._sync_prompt_text("")
                self._run_task = asyncio.create_task(self._run_rag_search(query, scope))
                return

            if cmd == "/context":
                self._handle_context_command()
                self._sync_prompt_text(remaining)
                self._invalidate()
                return

            if cmd in ("/note", "/chat"):
                new_mode = cmd[1:]
                self.state.mode = new_mode  # type: ignore[assignment]
                self._set_status(f"Switched to {new_mode} mode.")
                if self._on_mode_changed is not None:
                    self._on_mode_changed()
                self._sync_prompt_text(remaining)
                self._invalidate()
                return

            if cmd in ("/work", "/read", "/off"):
                new_work_mode = cmd[1:]
                self.state.work_mode = new_work_mode  # type: ignore[assignment]
                self._set_status(f"Agent mode set to {new_work_mode}.")
                if self._on_mode_changed is not None:
                    self._on_mode_changed()
                self._sync_prompt_text(remaining)
                self._invalidate()
                return

            if cmd == "/plan":
                await self._handle_plan_command(remaining)
                self._sync_prompt_text("")
                self._invalidate()
                return

            if cmd == "/model":
                self._pending_prompt_restore = remaining
                self._sync_prompt_text("")
                self.open_model_picker()
                return

            if cmd == "/clear-history":
                await self._handle_clear_history_command()
                self._sync_prompt_text(remaining)
                self._invalidate()
                return

            if cmd == "/find":
                find_text, replace_text = _parse_find_prompt_text(remaining)
                self.open_find_ui(
                    replace_mode=replace_text is not None,
                    saved_prompt_text="",
                    find_text=find_text,
                    replace_text=replace_text or "",
                )
                return

            if cmd == "/include":
                self._handle_include_command(remaining)
                self._sync_prompt_text("")
                self._invalidate()
                return

            if cmd == "/exclude":
                self._handle_exclude_command(remaining)
                self._sync_prompt_text("")
                self._invalidate()
                return

            if cmd == "/isolate":
                prompt_text = self._handle_isolate_command(remaining)
                self._sync_prompt_text("")
                if prompt_text:
                    if self.state.editor_dirty and not await self.save_active_file():
                        return
                    self._run_note_baseline_content = self._note_content_text
                    self._clear_recent_change_highlight()
                    if self._prompt_buffer is not None:
                        self._sync_prompt_text("")
                    self.state.run_in_progress = True
                    self._set_status("Starting model run (isolate).")
                    self._invalidate()
                    self._run_task = asyncio.create_task(self._run_current_mode(prompt_text))
                self._invalidate()
                return

            if cmd == "/map":
                await self._handle_map_command(remaining)
                self._sync_prompt_text("")
                self._invalidate()
                return

        if self.state.editor_dirty and not await self.save_active_file():
            return

        self._run_note_baseline_content = self._note_content_text
        self._clear_recent_change_highlight()
        if self._prompt_buffer is not None:
            self._sync_prompt_text("")
        self.state.run_in_progress = True
        self._set_status("Starting model run.")
        self._invalidate()
        self._run_task = asyncio.create_task(self._run_current_mode(prompt_text))

    def force_stop_run(self) -> None:
        if self._pontificating_task is not None:
            self._pontificating_task.cancel()
            self._pontificating_task = None
        if not self.state.run_in_progress or self._run_task is None:
            return
        self._force_stopped = True
        self._run_task.cancel()
        self._invalidate()

    async def _delayed_set_status(self, message: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            self._pontificating_task = None
            self._set_status(message)
            self._invalidate()
        except asyncio.CancelledError:
            pass

    async def toggle_mode(self) -> None:
        if self.state.run_in_progress:
            self._set_error("Wait for the current run to finish before switching modes.")
            self._invalidate()
            return
        self.state.mode = "chat" if self.state.mode == "note" else "note"
        self._set_status(f"Switched to {self.state.mode} mode.")
        if self._on_mode_changed is not None:
            self._on_mode_changed()
        self._invalidate()

    async def toggle_work_mode(self) -> None:
        if self.state.run_in_progress:
            self._set_error("Wait for the current run to finish before switching work mode.")
            self._invalidate()
            return
        order = ("off", "read", "work")
        current = order.index(self.state.work_mode)
        self.state.work_mode = order[(current + 1) % len(order)]  # type: ignore[assignment]
        self._set_status(f"Work mode set to {self.state.work_mode}.")
        if self._on_mode_changed is not None:
            self._on_mode_changed()
        self._invalidate()

    def cycle_model(self) -> None:
        self.state.selected_model_index = (self.state.selected_model_index + 1) % len(self.state.model_options)
        self._invalidate()

    def toggle_transcript_expand(self, row_number: int) -> None:
        expanded = self.transcript_view_state.expanded_rows
        if row_number in expanded:
            expanded.remove(row_number)
        else:
            expanded.add(row_number)
        self._invalidate()

    def set_transcript_filter(self, filter_mode: TranscriptFilter) -> None:
        self.transcript_view_state.filter_mode = filter_mode
        self._set_status(f"Transcript filter: {filter_mode}.")
        self._invalidate()

    def cycle_transcript_filter(self) -> None:
        order: tuple[TranscriptFilter, ...] = ("all", "chat", "tools", "search")
        current = order.index(self.transcript_view_state.filter_mode)
        self.set_transcript_filter(order[(current + 1) % len(order)])

    def toggle_transcript_sort(self) -> None:
        self.transcript_view_state.sort_order = (
            "ascending"
            if self.transcript_view_state.sort_order == "descending"
            else "descending"
        )
        self._set_status(f"Transcript order: {self.transcript_view_state.sort_order}.")
        self._invalidate()

    def toggle_transcript_open(self) -> None:
        self.state.transcript_open = not self.state.transcript_open
        if self._on_transcript_open_changed is not None:
            self._on_transcript_open_changed(self.state.transcript_open)
        self._invalidate()

    def toggle_transcript_maximized(self) -> None:
        self.transcript_view_state.maximized = not self.transcript_view_state.maximized
        if self._on_transcript_maximized_changed is not None:
            self._on_transcript_maximized_changed(self.transcript_view_state.maximized)
        self._invalidate()

    def toggle_fold_at_cursor(self) -> None:
        if self._editor_buffer is None:
            return
        line_number = self._editor_buffer.document.cursor_position_row
        folded = self.state.fold_state.setdefault(
            self._display_file(),
            default_folded_anchor_ids(self._note_content_text),
        )
        updated = toggle_fold_for_line(self._note_content_text, folded, line_number)
        if updated == folded:
            return
        self.state.fold_state[self._display_file()] = updated
        self._sync_editor_from_note_content(preserve_cursor=True)
        self._invalidate()

    def fold_at_cursor(self) -> None:
        self._set_fold_at_cursor(folded=True)

    def unfold_at_cursor(self) -> None:
        self._set_fold_at_cursor(folded=False)

    async def handle_progress_event(self, event: ProgressEvent) -> None:
        if event.kind == "run_started":
            self._run_start_time = time.monotonic()
            self._run_turn_count = 0
            self._run_error_count = 0
            self._set_status(event.message)
        elif event.kind == "sleep_started":
            self.state.sleep_status = SleepStatusState(
                started_monotonic=_float_detail(
                    event.details.get("started_monotonic"),
                    fallback=time.monotonic(),
                ),
                deadline_monotonic=_float_detail(
                    event.details.get("deadline_monotonic"),
                    fallback=time.monotonic(),
                ),
                reason=_optional_str(event.details.get("reason")),
            )
            self._set_status(event.message)
        elif event.kind == "sleep_ended":
            self.state.sleep_status = None
            self._set_status(event.message)
        elif event.kind == "loop_event":
            loop_kind = event.details.get("loop_kind")
            if loop_kind == "provider_request":
                msg = f"Pontificating... ({self._run_turn_count})"
                elapsed = time.monotonic() - self._tool_status_set_at
                remaining = _MIN_TOOL_STATUS_SECONDS - elapsed
                if remaining > 0:
                    if self._pontificating_task is not None:
                        self._pontificating_task.cancel()
                    self._pontificating_task = asyncio.create_task(
                        self._delayed_set_status(msg, remaining)
                    )
                else:
                    self._set_status(msg)
            elif loop_kind == "provider_response":
                usage = event.details.get("usage") or {}
                input_tokens = usage.get("input_tokens")
                model_context_window = usage.get("model_context_window")
                if input_tokens is not None:
                    self._ctx_tokens_used = input_tokens
                    self._ctx_last_file_len = len(self._full_text)
                if model_context_window is not None:
                    self._ctx_window_size = model_context_window
                elif self._ctx_window_size is None:
                    self._ctx_window_size = _known_context_window(self.state.selected_model)
                if input_tokens is not None or model_context_window is not None:
                    self._save_ctx_cache()
                tool_calls = event.details.get("tool_calls") or []
                if tool_calls:
                    if self._pontificating_task is not None:
                        self._pontificating_task.cancel()
                        self._pontificating_task = None
                    verb = _TOOL_VERBS.get(tool_calls[0], tool_calls[0].replace("_", " ").title())
                    self._tool_status_set_at = time.monotonic()
                    self._set_status(f"{verb}...")
                else:
                    self._set_status(event.message)
            elif loop_kind == "tool_result":
                self._run_turn_count += 1
                status = event.details.get("status", "completed")
                if status != "completed":
                    tool_name = event.details.get("tool_name") or "tool"
                    self._set_error(f"{tool_name} failed.")
                else:
                    self._set_status(event.message)
            else:
                self._set_status(event.message)
        elif event.kind == "error":
            self._run_error_count += 1
            self._set_error(event.message)
        else:
            self._set_status(event.message)
        if event.kind == "file_written" and event.path == self._display_file():
            if self.state.run_in_progress and self.state.mode == "note":
                self._invalidate()
                return
            await self._load_active_file(
                reset_dirty=True,
                highlight_recent=_should_highlight_recent_change(event),
            )
        self._invalidate()

    def _handle_editor_buffer_changed(self, _event) -> None:
        if self._syncing_editor or self._editor_buffer is None:
            return
        self._clear_recent_change_highlight()
        self._clear_model_insert_highlight()
        self._note_content_text = reconstruct_full_text(
            self._editor_buffer.text,
            self._fold_render.placeholder_map,
        )
        self._full_text = self._compose_full_text(self._note_content_text)
        self.state.editor_dirty = self._full_text != self._last_saved_text
        if self.state.find_ui.active:
            self._refresh_find_matches(auto_select=False)
        self._invalidate()

    def _handle_prompt_buffer_changed(self, _event) -> None:
        if self._syncing_prompt or self._prompt_buffer is None:
            return
        self.state.prompt_text = self._prompt_buffer.text
        self._invalidate()

    def _handle_cursor_moved(self, _event) -> None:
        self._invalidate()

    async def _run_current_mode(self, prompt_text: str) -> None:
        self._force_stopped = False
        isolate = self._isolate_override
        self._isolate_override = None
        if isolate is not None:
            run_included = isolate  # may be empty (active file only) or explicit list
        else:
            run_included = self.state.included_files
        try:
            provider = self._build_provider()
            if self.state.mode == "note":
                result = await self._note_runner.run(
                    NoteModeRunRequest(
                        active_file=self._context_file(),
                        included_files=run_included,
                        active_plan_id=self.state.active_plan_id,
                        active_plan_path=self.state.active_plan_path,
                        planning_status=self.state.planning_status,
                        provider=provider,
                        user_prompt=prompt_text,
                        model=self.state.selected_model.model,
                        reasoning_effort=self.state.reasoning_effort,
                        display_root=self._display_root,
                        progress_sink=self.handle_progress_event,
                        metadata={"cwd": str(self._cwd)},
                        work_mode=self.state.work_mode,
                        permission_handler=self.request_tool_permission,
                    )
                )
                elapsed = _format_elapsed(self._run_start_time)
                suffix = ""
                if result.synthesis_ran:
                    suffix = " Edit error." if result.synthesis_error else " Edit complete."
                stats = (
                    f" [time={elapsed}]"
                    f" [{format_usage_brief(result.usage_log.total)}]"
                    f" [turns={self._run_turn_count} errors={self._run_error_count}]"
                )
                if result.stop_reason != "finished":
                    self._set_error(f"Run stopped: {result.stop_reason}.{suffix}{stats}")
                else:
                    self._set_status(f"Finished!{suffix}{stats}")
                self._sync_plan_state_from_note_result(result)
                await self._reconcile_note_mode_result(result)
            else:
                result = await self._chat_runner.run(
                    ChatModeRunRequest(
                        active_file=self._context_file(),
                        included_files=run_included,
                        active_plan_id=self.state.active_plan_id,
                        active_plan_path=self.state.active_plan_path,
                        planning_status=self.state.planning_status,
                        provider=provider,
                        user_prompt=prompt_text,
                        model=self.state.selected_model.model,
                        reasoning_effort=self.state.reasoning_effort,
                        display_root=self._display_root,
                        progress_sink=self.handle_progress_event,
                        metadata={"cwd": str(self._cwd)},
                        work_mode=self.state.work_mode,
                        permission_handler=self.request_tool_permission,
                    )
                )
                elapsed = _format_elapsed(self._run_start_time)
                stats = (
                    f" [time={elapsed}]"
                    f" [{format_usage_brief(result.usage_log.total)}]"
                    f" [turns={self._run_turn_count} errors={self._run_error_count}]"
                )
                if result.stop_reason != "finished":
                    self._set_error(result.error_message or f"Run stopped: {result.stop_reason}.{stats}")
                else:
                    self._set_status(f"Finished!{stats}")
                await self._load_active_file(reset_dirty=True)
            if self._ctx_tokens_used is not None:
                self._ctx_last_file_len = len(self._full_text)
                self._save_ctx_cache()
        except asyncio.CancelledError:
            elapsed = _format_elapsed(self._run_start_time)
            stats = f" [time={elapsed}] [turns={self._run_turn_count} errors={self._run_error_count}]"
            self._set_error(f"Forced Stop!{stats}")
            await self._load_active_file(reset_dirty=True)
        except (NoteModeError, ChatModeError, FileReadError) as exc:
            self._set_error(str(exc))
        except Exception as exc:  # pragma: no cover - live provider/path safety
            self._set_error(f"TUI run failed: {exc}")
        finally:
            if self._pontificating_task is not None:
                self._pontificating_task.cancel()
                self._pontificating_task = None
            self.state.sleep_status = None
            self.state.run_in_progress = False
            self._force_stopped = False
            self._run_note_baseline_content = None
            self._invalidate()

    def _sync_plan_state_from_note_result(self, result: NoteModeRunResult) -> None:
        rows: list[TranscriptRow] = []
        for prompt_result in result.prompt_results:
            loop_result = prompt_result.loop_result
            rows.extend(loop_result.run_log[loop_result.run_log_new_start :])
        for row in rows:
            if row.type not in {"tool_result", "tool_error"}:
                continue
            if row.tool_name not in {"plan_create", "enter_plan_mode", "exit_plan"}:
                continue
            content = row.content
            if not isinstance(content, dict):
                continue
            if row.tool_name == "enter_plan_mode" and row.type == "tool_result":
                self.state.planning_status = "drafting"
                if self.state.pre_plan_work_mode is None:
                    self.state.pre_plan_work_mode = self.state.work_mode
                continue
            path_value = content.get("path") or content.get("active_plan_path") or content.get("target_identifier")
            if isinstance(content.get("plan_id"), str):
                self.state.active_plan_id = content["plan_id"]
            if isinstance(path_value, str) and path_value:
                self.state.active_plan_path = Path(path_value).expanduser().resolve()
                self.state.display_file = self.state.active_plan_path
            if row.tool_name == "plan_create" and row.type == "tool_result":
                self.state.planning_status = "drafting"
                if self.state.pre_plan_work_mode is None:
                    self.state.pre_plan_work_mode = self.state.work_mode
            elif row.tool_name == "exit_plan" and row.type == "tool_result":
                self.state.planning_status = "approved"
                self.state.work_mode = "work"
            elif row.tool_name == "exit_plan" and row.type == "tool_error":
                self.state.planning_status = "drafting"

    async def _reconcile_note_mode_result(self, result: NoteModeRunResult) -> None:
        baseline_note = self._run_note_baseline_content
        note_result = _latest_note_tool_result(result)
        previous_display_text = self._fold_render.display_text

        if note_result is None:
            if baseline_note is None or self._note_content_text == baseline_note:
                await self._load_active_file(reset_dirty=True)
            return

        tool_name, payload = note_result

        snapshot = await self._file_manager.read_snapshot(self._context_file())
        model_note_content, transcript_text = split_note_and_transcript(snapshot.raw_text)
        current_user_note = (
            self._note_content_text
            if self._display_file() == self._context_file()
            else model_note_content
        )

        if baseline_note is None or current_user_note == baseline_note or current_user_note == model_note_content:
            await self._load_active_file(reset_dirty=True, highlight_recent=tool_name != "note_edit")
            if tool_name == "note_edit":
                self._set_model_insert_highlight_from_display_diff(
                    previous_display_text,
                    self._fold_render.display_text,
                )
            return

        if tool_name == "note_edit":
            merged_note = reapply_note_edit_payload_to_note_content(current_user_note, payload)
            if merged_note is not None:
                updated_text = join_note_and_transcript(merged_note, transcript_text)
                await self._persist_active_file_text(updated_text, expected_revision=snapshot.revision_id)
                await self._load_active_file(reset_dirty=True)
                self._set_model_insert_highlight_from_display_diff(
                    previous_display_text,
                    self._fold_render.display_text,
                )
                return

        self.state.note_conflict = NoteConflictState(
            tool_name=tool_name,
            model_note_content=model_note_content,
            user_note_content=current_user_note,
            model_revision_id=snapshot.revision_id,
            transcript_text=transcript_text,
        )
        self.state.active_dialog = "note_conflict"
        self._set_error(
            "Your note changed during the run. Choose whether the model update or your edits should win."
        )
        self._invalidate()

    def _write_note_conflict_backup(self, note_content: str, *, discarded_label: str) -> Path | None:
        try:
            backup_dir = resolve_usage_root(self._cwd) / "conflicts"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
            base_name = self._context_file().stem or "note"
            backup_path = backup_dir / f"{base_name}-{stamp}-{discarded_label}.md"
            counter = 1
            while backup_path.exists():
                backup_path = backup_dir / f"{base_name}-{stamp}-{discarded_label}-{counter}.md"
                counter += 1
            backup_path.write_text(note_content, encoding="utf-8")
            return backup_path
        except Exception:
            return None

    async def _load_active_file(self, *, reset_dirty: bool, highlight_recent: bool = False) -> None:
        display_file = self._display_file()
        snapshot = await self._file_manager.read_snapshot(display_file)
        previous_text = self._note_content_text
        previous_folded = set(self.state.fold_state.get(display_file, set()))
        previous_display_text = self._fold_render.display_text
        self._last_revision_id = snapshot.revision_id
        self._full_text = snapshot.raw_text
        self._note_content_text, self._transcript_text = split_note_and_transcript(self._full_text)
        if self._transcript_text:
            self._transcript_rows = parse_transcript_rows(self._transcript_text)
        else:
            self._transcript_rows = []
        self.transcript_view_state.expanded_rows.intersection_update(
            {row.row_number for row in self._transcript_rows}
        )
        self._refresh_cached_fetch_urls()
        self.state.active_file_missing_on_disk = False
        self.state.note_conflict = None
        if reset_dirty:
            self._last_saved_text = snapshot.raw_text
            self.state.editor_dirty = False
        if display_file not in self.state.fold_state:
            self.state.fold_state[display_file] = default_folded_anchor_ids(self._note_content_text)
        else:
            self.state.fold_state[display_file] = carry_forward_managed_section_folds(
                previous_text,
                self._note_content_text,
                previous_folded,
                title="search results",
            )
        self._sync_editor_from_note_content(preserve_cursor=not reset_dirty)
        current_display_text = self._fold_render.display_text
        if highlight_recent:
            self._recent_display_change_spans = _display_change_spans(
                previous_display_text,
                current_display_text,
            )
        elif current_display_text != previous_display_text:
            self._recent_display_change_spans = ()
        self._model_insert_display_change_spans = ()
        if self.state.find_ui.active:
            self._refresh_find_matches(auto_select=False)
        else:
            self._clear_editor_selection()

        try:
            from aunic.map.builder import refresh_map_entry_if_stale
            if display_file == self._context_file():
                refresh_map_entry_if_stale(self._context_file())
        except Exception as exc:
            logger.warning("map refresh on file open failed: %s", exc)

    async def _switch_active_file(self, path: Path) -> None:
        try:
            from aunic.map.builder import refresh_map_entry_if_stale
            refresh_map_entry_if_stale(self._context_file())
        except Exception as exc:
            logger.warning("map refresh on file switch-away failed: %s", exc)

        self.state.active_file = path
        self.state.context_file = path
        self.state.display_file = path
        self.state.active_plan_id = None
        self.state.active_plan_path = None
        self.state.planning_status = "none"
        self.state.pre_plan_work_mode = None
        self.state.active_dialog = None
        self.state.pending_switch_path = None
        self.state.pending_external_reload = False
        self.state.ignored_external_revision = None
        self.state.active_file_missing_on_disk = not path.exists()
        # Load per-file include entries for the new active file
        if self._on_file_switched is not None:
            self._on_file_switched(path)
        if self.state.active_file_missing_on_disk:
            self._bootstrap_missing_active_file()
        else:
            await self._load_active_file(reset_dirty=True)
        self._set_status(f"Opened {path.name}.")
        self._invalidate()

    async def _watch_files(self) -> None:
        try:
            async for batch in self._file_manager.watch(self.state.available_files):
                for change in batch:
                    if change.path != self._display_file():
                        self._set_status(f"{change.path.name} changed on disk.")
                        continue
                    if self.state.run_in_progress:
                        continue
                    if (change.revision_id or "").split(":")[0] == (self._last_revision_id or "").split(":")[0]:
                        continue
                    if self.state.editor_dirty:
                        self.state.pending_external_reload = True
                        if self.state.active_dialog is None:
                            self.state.active_dialog = "reload_confirm"
                        self._set_error("The active file changed on disk while you had unsaved edits.")
                    else:
                        await self._load_active_file(reset_dirty=True)
                        self._set_status("Reloaded active file after an external change.")
                self._invalidate()
        except asyncio.CancelledError:  # pragma: no cover - normal shutdown
            raise

    def _sync_editor_from_note_content(self, *, preserve_cursor: bool) -> None:
        if self._editor_buffer is None:
            return
        cursor_row = self._editor_buffer.document.cursor_position_row
        cursor_col = self._editor_buffer.document.cursor_position_col
        folded_ids = self.state.fold_state.setdefault(
            self._display_file(),
            default_folded_anchor_ids(self._note_content_text),
        )
        self._fold_render = apply_folds(self._note_content_text, folded_ids)
        self._syncing_editor = True
        try:
            cursor_position = 0
            if preserve_cursor:
                cursor_position = _cursor_position_for_row_col(
                    self._fold_render.display_text,
                    cursor_row,
                    cursor_col,
                )
            self._editor_buffer.set_document(
                Document(
                    text=self._fold_render.display_text,
                    cursor_position=cursor_position,
                ),
                bypass_readonly=True,
            )
        finally:
            self._syncing_editor = False

    def _sync_prompt_text(self, text: str) -> None:
        if self._prompt_buffer is None:
            return
        self._syncing_prompt = True
        try:
            self._prompt_buffer.set_document(
                Document(text=text, cursor_position=len(text)),
                bypass_readonly=True,
            )
        finally:
            self._syncing_prompt = False
        self.state.prompt_text = text

    def _find_matches(self) -> tuple[TextSpan, ...]:
        state = self.state.find_ui
        return _find_literal_matches(
            self._note_content_text,
            state.find_text,
            case_sensitive=state.case_sensitive,
        )

    def _refresh_find_matches(self, *, auto_select: bool) -> None:
        matches = self._find_matches()
        self.state.find_ui.match_count = len(matches)
        if not matches:
            self._clear_find_match_state()
            self._clear_editor_selection()
            if self.state.find_ui.find_text:
                self._set_error("No matches found.")
            return
        if auto_select:
            self._activate_find_match(matches, 0)
            return
        self._clear_find_match_state()
        self._clear_editor_selection()
        self._set_status(f"Found {len(matches)} matches.")

    def _move_find_match(self, *, direction: int) -> None:
        matches = self._find_matches()
        self.state.find_ui.match_count = len(matches)
        if not matches:
            self._clear_find_match_state()
            self._clear_editor_selection()
            self._set_error("No matches found.")
            self._invalidate()
            return
        current = self.state.find_ui.current_match_index
        if current is None or current >= len(matches):
            next_index = 0 if direction >= 0 else len(matches) - 1
        else:
            next_index = (current + direction) % len(matches)
        self._activate_find_match(matches, next_index)
        self._invalidate()

    def _activate_find_match(self, matches: tuple[TextSpan, ...], index: int) -> None:
        index = max(0, min(index, len(matches) - 1))
        match = matches[index]
        self._ensure_editor_visible_for_find()
        self._unfold_for_raw_note_span(match.start, match.end)
        self._select_raw_note_span(match.start, match.end)
        self.state.find_ui.current_match_index = index
        self.state.find_ui.current_match_start = match.start
        self.state.find_ui.current_match_end = match.end
        self.state.find_ui.match_count = len(matches)
        self._set_status(f"Match {index + 1}/{len(matches)}.")

    def _clear_find_match_state(self) -> None:
        self.state.find_ui.current_match_index = None
        self.state.find_ui.current_match_start = None
        self.state.find_ui.current_match_end = None

    def _apply_note_content_edit(self, new_note_content: str) -> None:
        self._clear_recent_change_highlight()
        self._clear_model_insert_highlight()
        self._note_content_text = new_note_content
        self._full_text = self._compose_full_text(self._note_content_text)
        self.state.editor_dirty = self._full_text != self._last_saved_text
        self._sync_editor_from_note_content(preserve_cursor=False)

    def _ensure_editor_visible_for_find(self) -> None:
        if not self.transcript_view_state.maximized:
            return
        self.transcript_view_state.maximized = False
        if self._on_transcript_maximized_changed is not None:
            self._on_transcript_maximized_changed(False)

    def _unfold_for_raw_note_span(self, start: int, end: int) -> None:
        if start >= end:
            return
        document = Document(text=self._note_content_text)
        start_row, _ = document.translate_index_to_position(start)
        end_row, _ = document.translate_index_to_position(max(start, end - 1))
        folded_ids = set(
            self.state.fold_state.setdefault(
                self._display_file(),
                default_folded_anchor_ids(self._note_content_text),
            )
        )
        changed = True
        while changed:
            changed = False
            render = apply_folds(self._note_content_text, folded_ids)
            for region in render.regions:
                if (
                    region.anchor_id in folded_ids
                    and region.hidden_start_line <= end_row
                    and region.hidden_end_line >= start_row
                ):
                    folded_ids.remove(region.anchor_id)
                    changed = True
        if folded_ids != set(self.state.fold_state.get(self._display_file(), set())):
            self.state.fold_state[self._display_file()] = folded_ids
            self._sync_editor_from_note_content(preserve_cursor=False)

    def _select_raw_note_span(self, start: int, end: int) -> None:
        if self._editor_buffer is None or start >= end:
            return
        document = Document(text=self._note_content_text)
        start_row, start_col = document.translate_index_to_position(start)
        end_row, end_col = document.translate_index_to_position(end)
        folded_ids = set(self.state.fold_state.get(self._display_file(), set()))
        start_display_row = _display_row_for_raw_row(self._note_content_text, folded_ids, start_row)
        end_display_row = _display_row_for_raw_row(self._note_content_text, folded_ids, end_row)
        start_position = _cursor_position_for_row_col(self._fold_render.display_text, start_display_row, start_col)
        end_position = _cursor_position_for_row_col(self._fold_render.display_text, end_display_row, end_col)
        self._editor_buffer.exit_selection()
        self._editor_buffer.cursor_position = start_position
        self._editor_buffer.start_selection(selection_type=SelectionType.CHARACTERS)
        self._editor_buffer.cursor_position = end_position

    def _clear_editor_selection(self) -> None:
        if self._editor_buffer is None:
            return
        if self._editor_buffer.selection_state is not None:
            self._editor_buffer.exit_selection()

    def _maybe_fetch_openrouter_context_window(self) -> None:
        model = self.state.selected_model
        if model.provider_name != "openai_compatible" or model.profile_id is None:
            return
        if model.profile_id in self._ctx_fetched_profiles:
            return
        profile = resolve_openai_compatible_profile(self._cwd, profile_id=model.profile_id)
        if profile is None or "openrouter.ai" not in profile.base_url:
            return
        self._ctx_fetched_profiles.add(model.profile_id)
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(self._fetch_openrouter_context_window(profile.base_url, profile.api_key, model.model))
        except RuntimeError:
            pass

    async def _fetch_openrouter_context_window(self, base_url: str, api_key: str | None, model_id: str) -> None:
        try:
            import urllib.request
            url = base_url.rstrip("/") + "/models"
            req = urllib.request.Request(url)
            if api_key:
                req.add_header("Authorization", f"Bearer {api_key}")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode())
            models = payload.get("data") or []
            for entry in models:
                if entry.get("id") != model_id:
                    continue
                top = entry.get("top_provider") or {}
                size = top.get("context_length") or entry.get("context_length")
                if isinstance(size, int) and size > 0:
                    self._ctx_window_size = size
                    self._invalidate()
                break
        except Exception:
            pass

    async def _handle_clear_history_command(self) -> None:
        if not self._transcript_rows:
            self._set_status("Transcript is already empty.")
            return
        count = len(self._transcript_rows)
        new_text = self._note_content_text
        await self._persist_active_file_text(new_text)
        await self._load_active_file(reset_dirty=True)
        self._set_status(f"Cleared {count} transcript item(s).")

    def _handle_include_command(self, arg: str) -> None:
        arg = arg.strip()
        recursive = False
        if arg.startswith("-r "):
            recursive = True
            arg = arg[3:].strip()
        if not arg:
            self._set_error("Usage: /include [-r] <path>")
            return
        raw_path = arg
        base = self._context_file().parent
        target = (base / raw_path).resolve() if not Path(raw_path).is_absolute() else Path(raw_path).resolve()
        is_dir = raw_path.endswith("/") or target.is_dir()
        # Normalize stored path (keep as typed)
        existing_paths = {e.path for e in self.state.include_entries}
        if raw_path in existing_paths:
            self._set_status(f"Already included: {raw_path}")
            return
        self.state.include_entries.append(IncludeEntry(path=raw_path, is_dir=is_dir, recursive=recursive))
        self._rebuild_available_files()
        kind = "directory" if is_dir else "file"
        self._set_status(f"Included {kind}: {raw_path}")

    def _handle_exclude_command(self, arg: str) -> None:
        arg = arg.strip()
        if not arg:
            self._set_error("Usage: /exclude <path>")
            return
        before = len(self.state.include_entries)
        self.state.include_entries = [e for e in self.state.include_entries if e.path != arg]
        if len(self.state.include_entries) == before:
            self._set_error(f"Not in include list: {arg}")
            return
        self._rebuild_available_files()
        self._set_status(f"Excluded: {arg}")

    def _handle_isolate_command(self, remaining: str) -> str:
        """Parse /isolate args, set _isolate_override, return prompt text."""
        tokens = remaining.split()
        path_tokens: list[str] = []
        text_tokens: list[str] = []
        for tok in tokens:
            if (tok.startswith("/") or tok.startswith("./") or tok.startswith("../")) and not text_tokens:
                path_tokens.append(tok)
            else:
                text_tokens.append(tok)
        base = self._context_file().parent
        if path_tokens:
            paths = []
            for pt in path_tokens:
                p = Path(pt)
                resolved = (base / p).resolve() if not p.is_absolute() else p.resolve()
                paths.append(resolved)
            self._isolate_override = tuple(paths)
        else:
            self._isolate_override = ()  # empty → only active file
        prompt_text = " ".join(text_tokens)
        if prompt_text:
            self._set_status("Isolating run to specified files.")
        else:
            self._set_status("Isolate set; enter a prompt to run.")
        return prompt_text

    async def _handle_map_command(self, remaining: str) -> None:
        """Dispatch /map subcommands: walk, --set-summary, --clear-summary, --generate-summary."""
        tokens = remaining.split(None, 1)
        first = tokens[0] if tokens else ""

        if first == "--generate-summary":
            self._set_error("--generate-summary is deferred to a follow-up; use --set-summary <text> for now.")
            return

        if first == "--set-summary":
            text = tokens[1].strip() if len(tokens) > 1 else ""
            if not text:
                self._set_error("Usage: /map --set-summary <text>")
                return
            try:
                from aunic.map.builder import set_summary
                set_summary(self._context_file(), text)
                self._set_status(f"Summary locked for {self._context_file().name}.")
            except Exception as exc:
                self._set_error(f"/map --set-summary failed: {exc}")
            return

        if first == "--clear-summary":
            try:
                from aunic.map.builder import clear_summary
                clear_summary(self._context_file())
                self._set_status(f"Summary cleared for {self._context_file().name}.")
            except Exception as exc:
                self._set_error(f"/map --clear-summary failed: {exc}")
            return

        # Walk subcommand: /map or /map <path>
        scope: Path | None = None
        if first:
            raw = Path(first).expanduser()
            if not raw.is_absolute():
                raw = self._context_file().parent / raw
            scope = raw.resolve()
            if not scope.exists() or not scope.is_dir():
                self._set_error(f"/map: path not found or not a directory: {scope}")
                return

        try:
            from aunic.map.builder import build_map
            result = build_map(scope)
            scope_label = f" under {scope}" if scope is not None else ""
            self._set_status(
                f"Mapped {result.entry_count} notes{scope_label}"
                f" (+{result.entries_added} -{result.entries_removed}"
                f", {result.entries_reused_from_cache} unchanged)"
                f" in {result.elapsed_seconds:.1f}s."
            )
        except Exception as exc:
            self._set_error(f"/map failed: {exc}")

    def _handle_context_command(self) -> None:
        used = self._effective_ctx_tokens()
        window = self._ctx_window_size or _known_context_window(self.state.selected_model)
        if used is None or window is None:
            self._set_status("Context window: unknown (run the model first)")
            return
        prefix = "~" if self.context_is_estimate else ""
        self._set_status(f"Context window: {prefix}{used:,}/{window:,}")

    def _set_status(self, message: str) -> None:
        self.state.indicator_kind = "status"
        self.state.indicator_message = message

    def _set_error(self, message: str) -> None:
        self.state.indicator_kind = "error"
        self.state.indicator_message = message

    async def request_tool_permission(self, request: PermissionRequest) -> str:
        if self._permission_future is not None:
            return "reject"
        loop = asyncio.get_running_loop()
        self._permission_future = loop.create_future()
        self.state.permission_prompt = PermissionPromptState(
            message=request.message,
            target=request.target,
            tool_name=request.tool_name,
            details=request.details,
        )
        self.state.dialog_selection_index = 0
        self.state.active_dialog = "permission_prompt"
        self._invalidate()
        try:
            return await self._permission_future
        finally:
            self._permission_future = None
            self.state.permission_prompt = None
            if self.state.active_dialog == "permission_prompt":
                self.state.active_dialog = None
            self._invalidate()

    def resolve_permission_prompt(self, resolution: str) -> None:
        if self._permission_future is None or self._permission_future.done():
            return
        self._permission_future.set_result(resolution)

    def _clear_recent_change_highlight(self) -> None:
        self._recent_display_change_spans = ()

    def _clear_model_insert_highlight(self) -> None:
        self._model_insert_display_change_spans = ()

    def _set_model_insert_highlight_from_display_diff(
        self,
        previous_display_text: str,
        current_display_text: str,
    ) -> None:
        self._model_insert_display_change_spans = _novel_text_spans(
            previous_display_text,
            current_display_text,
        )

    def _set_fold_at_cursor(self, *, folded: bool) -> None:
        if self._editor_buffer is None:
            return
        line_number = self._editor_buffer.document.cursor_position_row
        folded_ids = self.state.fold_state.setdefault(
            self._display_file(),
            default_folded_anchor_ids(self._note_content_text),
        )
        render = apply_folds(self._note_content_text, folded_ids)
        anchor_id = render.anchor_for_display_line.get(line_number)
        if anchor_id is None:
            return

        updated = set(folded_ids)
        if folded:
            if anchor_id in updated:
                return
            updated.add(anchor_id)
        else:
            if anchor_id not in updated:
                return
            updated.remove(anchor_id)

        self.state.fold_state[self._display_file()] = updated
        self._sync_editor_from_note_content(preserve_cursor=True)
        self._invalidate()

    async def delete_transcript_row(self, row_number: int) -> None:
        updated = delete_row_by_number(self._full_text, row_number)
        if updated == self._full_text:
            self._set_error(f"Transcript row {row_number} was not found.")
            self._invalidate()
            return
        snapshot = await self._file_manager.write_text(
            self._display_file(),
            updated,
            expected_revision=self._last_revision_id,
        )
        self._last_revision_id = snapshot.revision_id
        self._last_saved_text = snapshot.raw_text
        self.state.editor_dirty = False
        await self._load_active_file(reset_dirty=True)
        self._set_status(f"Deleted transcript row {row_number}.")
        self._invalidate()

    async def delete_search_result(self, row_number: int, result_index: int) -> None:
        updated = delete_search_result_item(self._full_text, row_number, result_index)
        if updated == self._full_text:
            self._set_error(f"Search result {result_index} in row {row_number} was not found.")
            self._invalidate()
            return
        snapshot = await self._file_manager.write_text(
            self._display_file(),
            updated,
            expected_revision=self._last_revision_id,
        )
        self._last_revision_id = snapshot.revision_id
        self._last_saved_text = snapshot.raw_text
        self.state.editor_dirty = False
        await self._load_active_file(reset_dirty=True)
        self._set_status(f"Deleted search result {result_index + 1}.")
        self._invalidate()

    def copy_text_to_clipboard(self, text: str) -> None:
        _copy_to_system_clipboard(text)
        try:
            get_app().clipboard.set_text(text)
        except Exception:
            pass
        self._set_status("Copied to clipboard.")
        self._invalidate()

    def open_transcript_url(self, url: str) -> None:
        if not url:
            return
        _open_url_focused(url)
        self._set_status(f"Opened {url}.")
        self._invalidate()

    def copy_cached_fetch_url(self, url: str) -> None:
        markdown = self._read_cached_fetch_markdown(url)
        if markdown is None:
            self._set_error("Cached fetch content was not found.")
            self._invalidate()
            return
        self.copy_text_to_clipboard(markdown)

    # ── @web flow ──────────────────────────────────────────────────────────────

    def _handle_web_send(self) -> None:
        if self.state.run_in_progress:
            self._set_error("Search/fetch in progress.")
            self._invalidate()
            return
        if self._rag_active:
            if self.state.web_mode == "results":
                if self._web_selected_result is None:
                    self._set_error("Select a result with [Space] first.")
                    self._invalidate()
                    return
                self._run_task = asyncio.create_task(self._run_rag_fetch())
            elif self.state.web_mode == "chunks":
                if self._web_chunk_cursor == -1 or self._web_chunk_selected:
                    self._run_task = asyncio.create_task(self._insert_web_chunks())
                else:
                    self._set_error("Select chunks with [Space] or navigate to 'Fetch full page'.")
                    self._invalidate()
            return
        if self.state.web_mode == "results":
            if self._web_selected_result is None:
                self._set_error("Select a result with [Space] first.")
                self._invalidate()
                return
            self._run_task = asyncio.create_task(self._run_web_fetch())
        elif self.state.web_mode == "chunks":
            if self._web_chunk_cursor == -1 or self._web_chunk_selected:
                self._run_task = asyncio.create_task(self._insert_web_chunks())
            else:
                self._set_error("Select chunks with [Space] or navigate to 'Fetch full page'.")
                self._invalidate()
                return

    async def _run_web_search(self, query: str) -> None:
        self._web_query = query
        self.state.run_in_progress = True
        self._set_status("Searching...")
        self._invalidate()
        research_state = ResearchState()
        try:
            batch = await self._search_service.search(
                queries=(query,),
                depth="quick",
                freshness="none",
                purpose=query,
                state=research_state,
            )
            tool_call = {"queries": [query]}
            if not batch.results and batch.failures:
                message = batch.failures[0].message
                await self._append_user_tool_transcript_pair(
                    "web_search",
                    tool_call,
                    _tool_error_payload(
                        reason="search_failed",
                        message=message,
                        queries=[query],
                    ),
                    response_type="tool_error",
                )
                self._set_error(f"Search failed: {message}")
                self._web_cancel(status_message=None)
                return

            payload = [
                {
                    "url": result.url,
                    "title": result.title,
                    "snippet": result.snippet,
                }
                for result in batch.results
            ]
            if not await self._append_user_tool_transcript_pair(
                "web_search",
                tool_call,
                payload,
            ):
                self._web_cancel(status_message=None)
                return
            if not batch.results and batch.failures:
                self._web_cancel(status_message=None)
                return
            self._web_results = batch.results
            self._web_result_cursor = 0
            self._web_result_expanded = set()
            self._web_selected_result = None
            self.state.web_mode = "results"
            count = len(batch.results)
            self._set_status(
                f"Found {count} result{'s' if count != 1 else ''}. "
                "Space=select  Enter=open URL  Ctrl+R=fetch  Esc=cancel"
            )
        except Exception as exc:
            await self._append_user_tool_transcript_pair(
                "web_search",
                {"queries": [query]},
                _tool_error_payload(
                    reason="search_failed",
                    message=str(exc),
                    queries=[query],
                ),
                response_type="tool_error",
            )
            self._set_error(f"Search error: {exc}")
            self._web_cancel(status_message=None)
        finally:
            self.state.run_in_progress = False
            self._invalidate()

    async def _run_web_fetch(self) -> None:
        result = self._web_results[self._web_selected_result]  # type: ignore[index]
        self.state.run_in_progress = True
        self._set_status(f"Fetching \"{result.title[:50]}\"...")
        self._invalidate()
        research_state = ResearchState()
        try:
            packet = await self._fetch_service.fetch_for_user_selection(
                query=self._web_query,
                url=result.url,
                state=research_state,
                active_file=self._context_file(),
            )
            summary = research_state.summary().fetched_pages[-1]
            if not await self._append_user_tool_transcript_pair(
                "web_fetch",
                {"url": result.url},
                {
                    "url": summary.url,
                    "title": summary.title,
                    "snippet": summary.snippet,
                },
            ):
                return
            self._web_packets = (packet,)
            self._web_chunk_cursor = 0
            self._web_chunk_selected = set()
            self.state.web_mode = "chunks"
            count = len(packet.chunks)
            self._set_status(
                f"{count} chunk{'s' if count != 1 else ''} from \"{packet.title[:40]}\". "
                "Space=select  Ctrl+R=insert  Esc=back"
            )
        except Exception as exc:
            await self._append_user_tool_transcript_pair(
                "web_fetch",
                {"url": result.url},
                _tool_error_payload(
                    reason="fetch_failed",
                    message=str(exc),
                    url=result.url,
                ),
                response_type="tool_error",
            )
            self._set_error(f"Fetch error: {exc}")
        finally:
            self.state.run_in_progress = False
            self._invalidate()

    async def _insert_web_chunks(self) -> None:
        packet = self._web_packets[0]
        if self._web_chunk_cursor == -1:
            content_block = f"# {packet.title}\n\n{packet.full_markdown}"
            label = "full page"
        else:
            selected_texts = [packet.chunks[i].text for i in sorted(self._web_chunk_selected)]
            content_block = f"# {packet.title}\n\n" + "\n\n".join(selected_texts)
            label = f"{len(selected_texts)} chunk(s)"
        updated_note = _append_block_to_note_content(self._note_content_text, content_block)
        updated = join_note_and_transcript(updated_note, self._transcript_text)
        if not await self._write_active_file_text(updated):
            return
        self._set_status(f"Inserted {label} from \"{packet.title[:40]}\".")
        self._web_cancel(status_message=None)

    def web_move_cursor(self, delta: int) -> None:
        if self.state.web_mode == "results":
            n = len(self._web_results)
            if n:
                self._web_result_cursor = max(0, min(n - 1, self._web_result_cursor + delta))
        elif self.state.web_mode == "chunks" and self._web_packets:
            n = len(self._web_packets[0].chunks)
            if n:
                self._web_chunk_cursor = max(-1, min(n - 1, self._web_chunk_cursor + delta))
        self._invalidate()

    def web_toggle_expand(self) -> None:
        if self.state.web_mode == "chunks":
            i = self._web_chunk_cursor
            if i < 0:
                return
            if i in self._web_chunk_expanded:
                self._web_chunk_expanded.discard(i)
            else:
                self._web_chunk_expanded.add(i)
        else:
            i = self._web_result_cursor
            if i in self._web_result_expanded:
                self._web_result_expanded.discard(i)
            else:
                self._web_result_expanded.add(i)
        self._invalidate()

    def web_space_pressed(self) -> None:
        if self.state.web_mode == "results":
            i = self._web_result_cursor
            self._web_selected_result = None if self._web_selected_result == i else i
        elif self.state.web_mode == "chunks":
            i = self._web_chunk_cursor
            if i == -1:
                return  # no toggle for full-page option
            if i in self._web_chunk_selected:
                self._web_chunk_selected.discard(i)
            else:
                self._web_chunk_selected.add(i)
        self._invalidate()

    def web_open_url(self) -> None:
        if self.state.web_mode == "results" and self._web_results:
            _open_url_focused(self._web_results[self._web_result_cursor].url)

    def web_escape(self) -> None:
        if self.state.web_mode == "chunks":
            self.state.web_mode = "results"
            self._set_status(
                "Back to search results. Space=select  Enter=open URL  Ctrl+R=fetch  Esc=cancel"
            )
            self._invalidate()
        else:
            self._web_cancel()

    def _web_cancel(self, *, status_message: str | None = "Web search cancelled.") -> None:
        self.state.web_mode = "idle"
        self._web_query = ""
        self._web_results = ()
        self._web_result_cursor = 0
        self._web_result_expanded = set()
        self._web_selected_result = None
        self._web_packets = ()
        self._web_chunk_cursor = 0
        self._web_chunk_selected = set()
        self._web_chunk_expanded = set()
        self._rag_active = False
        self._rag_scope = None
        self._rag_results = ()
        if status_message is not None:
            self._set_status(status_message)
        self._invalidate()

    # ── @rag flow ──────────────────────────────────────────────────────────────

    def _rag_scope_names(self) -> frozenset[str]:
        try:
            from aunic.rag.config import load_rag_config
            cfg = load_rag_config(self._cwd)
            if cfg is None:
                return frozenset()
            scopes = cfg.tui_scopes if cfg.tui_scopes is not None else cfg.scopes
            return frozenset(s.name for s in scopes)
        except Exception:
            return frozenset()

    def _ensure_rag_client(self):
        if self._rag_client is not None:
            return self._rag_client
        try:
            from aunic.rag.config import load_rag_config
            cfg = load_rag_config(self._cwd)
            if cfg is None:
                return None
            from aunic.rag.client import RagClient
            self._rag_client = RagClient(cfg.server)
            return self._rag_client
        except Exception:
            return None

    async def _run_rag_search(self, query: str, scope: str | None) -> None:
        self._rag_active = True
        self._rag_scope = scope
        self._web_query = query
        self.state.run_in_progress = True
        scope_label = f"@{scope}" if scope else "@rag"
        self._set_status(f"Searching {scope_label}...")
        self._invalidate()
        client = self._ensure_rag_client()
        if client is None:
            self._set_error("RAG client unavailable.")
            self._web_cancel(status_message=None)
            self.state.run_in_progress = False
            self._invalidate()
            return
        try:
            rag_results = await client.search(query, scope=scope, limit=10)
            tool_call = {"query": query, "scope": scope}
            if not rag_results:
                await self._append_user_tool_transcript_pair(
                    "rag_search",
                    tool_call,
                    {"results": []},
                )
                self._set_error(f"No results for '{query}'.")
                self._web_cancel(status_message=None)
                return
            self._rag_results = rag_results
            self._web_results = tuple(
                SearchResult(
                    source_id=f"r{i}",
                    title=r.title,
                    url=r.url or r.local_path or f"[{r.source}] {r.result_id}",
                    canonical_url=r.result_id,
                    snippet=r.snippet,
                    rank=i,
                    refined_score=r.score,
                )
                for i, r in enumerate(rag_results)
            )
            payload = [
                {
                    "doc_id": r.doc_id,
                    "result_id": r.result_id,
                    "chunk_id": r.chunk_id,
                    "title": r.title,
                    "source": r.source,
                    "snippet": r.snippet,
                    "score": r.score,
                    "url": r.url,
                    "local_path": r.local_path,
                }
                for r in rag_results
            ]
            if not await self._append_user_tool_transcript_pair("rag_search", tool_call, payload):
                self._web_cancel(status_message=None)
                return
            self._web_result_cursor = 0
            self._web_result_expanded = set()
            self._web_selected_result = None
            self.state.web_mode = "results"
            count = len(rag_results)
            self._set_status(
                f"Found {count} result{'s' if count != 1 else ''} from {scope_label}. "
                "Space=select  Ctrl+R=fetch  Esc=cancel"
            )
        except Exception as exc:
            await self._append_user_tool_transcript_pair(
                "rag_search",
                {"query": query, "scope": scope},
                _tool_error_payload(
                    reason="search_failed",
                    message=str(exc),
                    query=query,
                    scope=scope,
                ),
                response_type="tool_error",
            )
            self._set_error(f"RAG search error: {exc}")
            self._web_cancel(status_message=None)
        finally:
            self.state.run_in_progress = False
            self._invalidate()

    async def _run_rag_fetch(self) -> None:
        rag_result = self._rag_results[self._web_selected_result]  # type: ignore[index]
        self.state.run_in_progress = True
        self._set_status(f"Fetching \"{rag_result.title[:50]}\"...")
        self._invalidate()
        client = self._ensure_rag_client()
        if client is None:
            self._set_error("RAG client unavailable.")
            self.state.run_in_progress = False
            self._invalidate()
            return
        try:
            fetch_result = await client.fetch(rag_result.result_id, mode="document_chunks", max_chunks=20)
            chunks = tuple(
                FetchedChunk(
                    source_id=f"r{i}",
                    title=sec.heading,
                    url=rag_result.url or rag_result.local_path or rag_result.result_id,
                    canonical_url=rag_result.result_id,
                    text=sec.text,
                    score=1.0 if sec.is_match else 0.0,
                    heading_path=sec.heading_path,
                    chunk_id=sec.chunk_id,
                    chunk_order=sec.chunk_order,
                    is_match=sec.is_match,
                )
                for i, sec in enumerate(fetch_result.sections)
            )
            packet = FetchPacket(
                source_id="r0",
                title=fetch_result.title,
                url=fetch_result.url or fetch_result.local_path or rag_result.result_id,
                canonical_url=rag_result.result_id,
                desired_info=self._web_query,
                chunks=chunks,
                full_markdown=fetch_result.full_text,
            )
            if not await self._append_user_tool_transcript_pair(
                "rag_fetch",
                {"result_id": rag_result.result_id, "source": rag_result.source},
                {"title": fetch_result.title, "sections": len(fetch_result.sections)},
            ):
                return
            self._web_packets = (packet,)
            self._web_chunk_cursor = next((i for i, chunk in enumerate(chunks) if chunk.is_match), 0)
            self._web_chunk_selected = set()
            self.state.web_mode = "chunks"
            count = len(chunks)
            if fetch_result.truncated and fetch_result.total_chunks:
                chunk_count_label = f"{count} of {fetch_result.total_chunks} chunks"
            else:
                chunk_count_label = f"{count} chunk{'s' if count != 1 else ''}"
            self._set_status(
                f"{chunk_count_label} from \"{fetch_result.title[:40]}\". "
                "Matched chunk is highlighted. Space=select  Ctrl+R=insert  Esc=back"
            )
        except Exception as exc:
            await self._append_user_tool_transcript_pair(
                "rag_fetch",
                {"result_id": rag_result.result_id, "source": rag_result.source},
                _tool_error_payload(
                    reason="fetch_failed",
                    message=str(exc),
                    result_id=rag_result.result_id,
                ),
                response_type="tool_error",
            )
            self._set_error(f"RAG fetch error: {exc}")
        finally:
            self.state.run_in_progress = False
            self._invalidate()

    def web_view_preferred_height(self) -> int:
        if self.state.web_mode == "results":
            return sum(
                3 if i in self._web_result_expanded else 2
                for i in range(len(self._web_results))
            )
        if self.state.web_mode == "chunks" and self._web_packets:
            chunks = self._web_packets[0].chunks
            return 1 + sum(
                (11 if i in self._web_chunk_expanded else 6)
                for i in range(len(chunks))
            )
        return 3

    # ── provider ───────────────────────────────────────────────────────────────

    def _build_provider(self):
        option = self.state.selected_model
        if option.provider_name == "codex":
            return CodexProvider()
        if option.provider_name == "claude":
            return ClaudeProvider()
        return OpenAICompatibleProvider(project_root=self._cwd, profile_id=option.profile_id)

    def _refresh_cached_fetch_urls(self) -> None:
        manifest = self._read_fetch_manifest()
        entries = manifest.get("entries", {})
        urls: set[str] = set()
        if isinstance(entries, dict):
            for entry in entries.values():
                if not isinstance(entry, dict):
                    continue
                canonical_url = entry.get("canonical_url")
                if isinstance(canonical_url, str) and canonical_url:
                    urls.add(canonicalize_url(canonical_url))
        self._cached_fetch_urls = urls

    def _read_cached_fetch_markdown(self, url: str) -> str | None:
        manifest = self._read_fetch_manifest()
        aliases = manifest.get("aliases", {})
        entries = manifest.get("entries", {})
        if not isinstance(aliases, dict) or not isinstance(entries, dict):
            return None
        canonical_url = canonicalize_url(url)
        url_hash = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
        entry_hash = aliases.get(url_hash)
        if not isinstance(entry_hash, str):
            if url_hash in entries:
                entry_hash = url_hash
            else:
                entry_hash = next(
                    (
                        candidate_hash
                        for candidate_hash, entry in entries.items()
                        if isinstance(entry, dict) and entry.get("canonical_url") == canonical_url
                    ),
                    None,
                )
        if not isinstance(entry_hash, str):
            return None
        markdown_path = self._fetch_cache_dir() / f"{entry_hash}.md"
        if not markdown_path.exists():
            return None
        return markdown_path.read_text(encoding="utf-8")

    def _read_fetch_manifest(self) -> dict[str, object]:
        manifest_path = self._fetch_cache_dir() / "manifest.json"
        if not manifest_path.exists():
            return {}
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _ctx_cache_path(self) -> Path:
        from os import environ
        xdg = Path(environ["XDG_CACHE_HOME"]).expanduser() if environ.get("XDG_CACHE_HOME") else Path.home() / ".cache"
        note_hash = hashlib.sha256(
            str(self._context_file().expanduser().resolve()).encode("utf-8")
        ).hexdigest()
        return xdg / "aunic" / "context" / f"{note_hash}.json"

    def _load_ctx_cache(self) -> None:
        try:
            data = json.loads(self._ctx_cache_path().read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            tokens = data.get("tokens_used")
            window = data.get("window_size")
            file_len = data.get("file_len")
            if isinstance(tokens, int) and tokens > 0:
                self._ctx_tokens_used = tokens
            if isinstance(window, int) and window > 0:
                self._ctx_window_size = window
            if isinstance(file_len, int) and file_len >= 0:
                self._ctx_last_file_len = file_len
        except Exception:
            pass

    def _save_ctx_cache(self) -> None:
        try:
            path = self._ctx_cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({
                    "tokens_used": self._ctx_tokens_used,
                    "window_size": self._ctx_window_size,
                    "file_len": self._ctx_last_file_len,
                }),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _fetch_cache_dir(self) -> Path:
        xdg = Path.home() / ".cache"
        from os import environ

        if environ.get("XDG_CACHE_HOME"):
            xdg = Path(environ["XDG_CACHE_HOME"]).expanduser()
        note_hash = hashlib.sha256(
            str(self._context_file().expanduser().resolve()).encode("utf-8")
        ).hexdigest()
        return xdg / "aunic" / "fetch" / note_hash

    def _compose_full_text(self, note_content: str) -> str:
        if self._transcript_text is None:
            return note_content
        return join_note_and_transcript(note_content, self._transcript_text)

    async def _append_user_tool_transcript_pair(
        self,
        tool_name: str,
        tool_call_content: object,
        tool_response_content: object,
        *,
        response_type: str = "tool_result",
    ) -> bool:
        updated_text, _, _ = append_synthetic_tool_pair(
            self._full_text,
            tool_name=tool_name,
            tool_call_content=tool_call_content,
            tool_response_content=tool_response_content,
            response_type=response_type,  # type: ignore[arg-type]
        )
        return await self._write_active_file_text(updated_text)

    async def _write_active_file_text(self, updated_text: str) -> bool:
        try:
            await self._persist_active_file_text(updated_text)
        except OptimisticWriteError:
            self.state.pending_external_reload = True
            self.state.active_dialog = "reload_confirm"
            self._set_error("The file changed on disk. Reload or ignore before saving.")
            self._invalidate()
            return False
        except FileReadError as exc:
            self._set_error(str(exc))
            self._invalidate()
            return False

        await self._load_active_file(reset_dirty=True)
        return True

    def _bootstrap_missing_active_file(self) -> None:
        self._last_revision_id = None
        self._last_saved_text = ""
        self._full_text = ""
        self._note_content_text = ""
        self._transcript_text = None
        self._transcript_rows = []
        self.transcript_view_state.expanded_rows.clear()
        self._cached_fetch_urls = set()
        self._recent_display_change_spans = ()
        self._model_insert_display_change_spans = ()
        self.state.editor_dirty = False
        self.state.note_conflict = None
        self.state.pending_external_reload = False
        self.state.ignored_external_revision = None
        self.state.fold_state[self._display_file()] = default_folded_anchor_ids(self._note_content_text)
        self._sync_editor_from_note_content(preserve_cursor=False)
        self._set_status("New file: will be created on first save.")

    async def _persist_active_file_text(
        self,
        updated_text: str,
        *,
        expected_revision: str | None = None,
    ):
        display_file = self._display_file()
        if self.state.active_file_missing_on_disk and not display_file.parent.exists():
            if not self.state.create_parents_on_first_save:
                raise FileReadError(
                    "Parent directory does not exist. Reopen with -p/--parents to create it on first save."
                )
            try:
                display_file.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise FileReadError(f"Could not create parent directories for: {display_file}") from exc

        if expected_revision is None and not self.state.active_file_missing_on_disk and not self.state.ignored_external_revision:
            expected_revision = self._last_revision_id

        try:
            snapshot = await self._file_manager.write_text(
                display_file,
                updated_text,
                expected_revision=expected_revision,
            )
        except OSError as exc:
            raise FileReadError(f"Could not write file: {display_file}") from exc
        self._last_revision_id = snapshot.revision_id
        self._last_saved_text = snapshot.raw_text
        self._full_text = snapshot.raw_text
        self.state.editor_dirty = False
        self.state.ignored_external_revision = None
        was_missing = self.state.active_file_missing_on_disk
        self.state.active_file_missing_on_disk = False
        if was_missing:
            await self.start_watch_task()

        try:
            from aunic.map.builder import mark_map_entry_stale
            if display_file == self._context_file():
                mark_map_entry_stale(self._context_file())
        except Exception as exc:
            logger.warning("map stale-mark on save failed: %s", exc)

        return snapshot


def _build_model_options(
    cwd: Path,
    initial_provider: str,
    initial_model: str | None,
) -> tuple[ModelOption, ...]:
    codex_model = initial_model if initial_provider == "codex" and initial_model else SETTINGS.codex.default_model
    options: list[ModelOption] = [
        ModelOption(label=f"Codex ({codex_model})", provider_name="codex", model=codex_model),
    ]

    openai_profiles = get_openai_compatible_profiles(cwd)
    if openai_profiles:
        for profile in openai_profiles:
            model = (
                initial_model
                if initial_provider in {"openai_compatible", "llama"} and initial_model
                and profile.model == initial_model
                else profile.model
            )
            options.append(
                ModelOption(
                    label=profile.display_label,
                    provider_name="openai_compatible",
                    model=model,
                    profile_id=profile.profile_id,
                    context_window=profile.context_window,
                )
            )
    else:
        llama_model = (
            initial_model
            if initial_provider in {"openai_compatible", "llama"} and initial_model
            else SETTINGS.llama_cpp.default_model
        )
        options.append(
            ModelOption(
                label="Llama Addie",
                provider_name="openai_compatible",
                model=llama_model,
                profile_id="llama_addie",
            )
        )

    options.extend(
        [
            ModelOption(label="Claude Haiku", provider_name="claude", model=SETTINGS.claude.haiku_model),
            ModelOption(label="Claude Sonnet", provider_name="claude", model=SETTINGS.claude.sonnet_model),
            ModelOption(label="Claude Opus", provider_name="claude", model=SETTINGS.claude.opus_model),
        ]
    )
    return tuple(options)


def _selected_model_index(
    options: tuple[ModelOption, ...],
    provider_name: str,
    model: str | None = None,
    profile_id: str | None = None,
) -> int:
    if provider_name == "llama":
        provider_name = "openai_compatible"
        profile_id = profile_id or "llama_addie"
    if provider_name == "openai_compatible" and profile_id is not None:
        for index, option in enumerate(options):
            if option.provider_name == provider_name and option.profile_id == profile_id:
                return index
    if provider_name == "openai_compatible" and model is not None:
        for index, option in enumerate(options):
            if option.provider_name == provider_name and option.model == model:
                return index
    if model is not None:
        for index, option in enumerate(options):
            if option.provider_name == provider_name and option.model == model:
                return index
    for index, option in enumerate(options):
        if option.provider_name == provider_name:
            return index
    return 0


def _append_block_to_note_content(note_text: str, block: str) -> str:
    normalized_note = note_text.rstrip("\n")
    normalized_block = block.strip("\n")
    if not normalized_note:
        return normalized_block
    return f"{normalized_note}\n\n{normalized_block}"


def _latest_note_tool_result(result: NoteModeRunResult) -> tuple[str, dict[str, object]] | None:
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


def _parse_find_prompt_text(text: str) -> tuple[str, str | None]:
    if " /replace " not in text:
        return text.strip(), None
    find_text, replace_text = text.split(" /replace ", 1)
    return find_text.strip(), replace_text


def _literal_search_pattern(text: str, *, case_sensitive: bool) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(re.escape(text), flags)


def _find_literal_matches(text: str, query: str, *, case_sensitive: bool) -> tuple[TextSpan, ...]:
    if not query:
        return ()
    pattern = _literal_search_pattern(query, case_sensitive=case_sensitive)
    return tuple(TextSpan(match.start(), match.end()) for match in pattern.finditer(text))


def _tool_error_payload(*, reason: str, message: str, **details: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "category": "validation_error",
        "reason": reason,
        "message": message,
    }
    payload.update(details)
    return payload


def _cursor_position_for_row_col(text: str, row: int, col: int) -> int:
    lines = text.splitlines(keepends=True)
    if not lines:
        return 0
    clamped_row = min(max(row, 0), len(lines) - 1)
    position = sum(len(line) for line in lines[:clamped_row])
    return min(position + col, position + len(lines[clamped_row].rstrip("\r\n")))


def _display_row_for_raw_row(text: str, folded_ids: set[str], raw_row: int) -> int:
    raw_lines = text.splitlines(keepends=True)
    if not raw_lines:
        return 0
    raw_row = max(0, min(raw_row, len(raw_lines) - 1))
    render = apply_folds(text, folded_ids)
    hidden_start_map = {
        region.hidden_start_line: region
        for region in render.regions
        if region.anchor_id in folded_ids
    }

    display_row = 0
    line_index = 0
    while line_index < len(raw_lines):
        if line_index == raw_row:
            return display_row
        hidden_region = hidden_start_map.get(line_index)
        if hidden_region is not None:
            if raw_row <= hidden_region.hidden_end_line:
                return display_row
            display_row += 1
            line_index = hidden_region.hidden_end_line + 1
            continue
        display_row += 1
        line_index += 1
    return max(0, display_row - 1)


def _format_elapsed(start_time: float | None) -> str:
    if start_time is None:
        return "??"
    seconds = int(time.monotonic() - start_time)
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


_KNOWN_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-opus":   200_000,
    "claude-sonnet": 200_000,
    "claude-haiku":  200_000,
    "gpt-4o":        128_000,
    "gpt-4-turbo":   128_000,
    "o1":            200_000,
    "o3":            200_000,
}


def _known_context_window(model_option: ModelOption) -> int | None:
    if model_option.context_window is not None:
        return model_option.context_window
    name = model_option.model.lower()
    for prefix, size in _KNOWN_CONTEXT_WINDOWS.items():
        if prefix in name:
            return size
    return None


_MIN_TOOL_STATUS_SECONDS: float = 1.5

_TOOL_VERBS: dict[str, str] = {
    "bash": "Bashing",
    "read": "Reading",
    "edit": "Editing",
    "write": "Writing",
    "grep": "Grepping",
    "glob": "Globbing",
    "list": "Listing",
    "web_search": "Searching",
    "web_fetch": "Fetching",
    "note_edit": "Editing",
    "note_write": "Writing",
    "sleep": "Sleeping",
}


def _display_change_spans(previous_text: str, current_text: str) -> tuple[TextSpan, ...]:
    if previous_text == current_text:
        return ()

    spans: list[TextSpan] = []
    matcher = difflib.SequenceMatcher(a=previous_text, b=current_text, autojunk=False)
    for tag, _a0, _a1, b0, b1 in matcher.get_opcodes():
        if tag not in {"replace", "insert"} or b0 == b1:
            continue
        if spans and b0 <= spans[-1].end:
            spans[-1] = TextSpan(spans[-1].start, max(spans[-1].end, b1))
        else:
            spans.append(TextSpan(b0, b1))
    return tuple(spans)


def _novel_text_spans(previous_text: str, current_text: str) -> tuple[TextSpan, ...]:
    if previous_text == current_text:
        return ()

    spans: list[TextSpan] = []
    matcher = difflib.SequenceMatcher(a=previous_text, b=current_text, autojunk=False)
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == "insert":
            _append_span(spans, b0, b1)
            continue
        if tag != "replace" or b0 == b1:
            continue
        local_matcher = difflib.SequenceMatcher(
            a=previous_text[a0:a1],
            b=current_text[b0:b1],
            autojunk=False,
        )
        for local_tag, _la0, _la1, lb0, lb1 in local_matcher.get_opcodes():
            if local_tag not in {"replace", "insert"}:
                continue
            _append_span(spans, b0 + lb0, b0 + lb1)
    return tuple(spans)


def _append_span(spans: list[TextSpan], start: int, end: int) -> None:
    if start >= end:
        return
    if spans and start <= spans[-1].end:
        spans[-1] = TextSpan(spans[-1].start, max(spans[-1].end, end))
    else:
        spans.append(TextSpan(start, end))


def _should_highlight_recent_change(event: ProgressEvent) -> bool:
    if event.kind != "file_written":
        return False
    reason = event.details.get("reason")
    if reason in {"chat_prompt_append"}:
        return False
    if reason in {"chat_response_append", "search_history_append"}:
        return True
    return "tool_name" in event.details


def _float_detail(value: object, *, fallback: float) -> float:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        return float(value)
    return fallback


def _optional_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
