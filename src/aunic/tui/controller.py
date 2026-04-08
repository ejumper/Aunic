from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document

from aunic.config import SETTINGS
from aunic.context import FileManager
from aunic.context.types import TextSpan
from aunic.domain import TranscriptRow
from aunic.errors import ChatModeError, FileReadError, NoteModeError, OptimisticWriteError
from aunic.modes import ChatModeRunRequest, ChatModeRunner, NoteModeRunRequest, NoteModeRunner, NoteModeRunResult
from aunic.progress import ProgressEvent
from aunic.proto_settings import get_openai_compatible_profiles, resolve_openai_compatible_profile
from aunic.providers import ClaudeProvider, CodexProvider, OpenAICompatibleProvider
from aunic.research.fetch import FetchService
from aunic.research.search import SearchService, canonicalize_url
from aunic.research.types import FetchPacket, ResearchState, SearchResult
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
from aunic.tui.rendering import soft_wrap_prefix_for_line
from aunic.tui.types import (
    ModelOption,
    NoteConflictState,
    PermissionPromptState,
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

        self._editor_buffer: Buffer | None = None
        self._prompt_buffer: Buffer | None = None
        self._invalidate: Callable[[], None] = lambda: None
        self._watch_task: asyncio.Task[None] | None = None
        self._run_task: asyncio.Task[None] | None = None
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
        self._cached_fetch_urls: set[str] = set()
        self._recent_display_change_spans: tuple[TextSpan, ...] = ()
        # @web ephemeral navigation state
        self._web_query: str = ""
        self._web_results: tuple[SearchResult, ...] = ()
        self._web_result_cursor: int = 0
        self._web_result_expanded: set[int] = set()
        self._web_selected_result: int | None = None
        self._web_packets: tuple[FetchPacket, ...] = ()
        self._web_chunk_cursor: int = 0
        self._web_chunk_selected: set[int] = set()
        self._permission_future: asyncio.Future[str] | None = None
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
        path = self.state.active_file
        if self._display_root is None:
            label = path.name
        else:
            try:
                label = str(path.relative_to(self._display_root))
            except ValueError:
                label = str(path)
        return f"{label}{' *' if self.state.editor_dirty else ''}"

    def prompt_height(self) -> int:
        if self._prompt_buffer is None:
            return 3
        return max(1, min(10, self._prompt_buffer.document.line_count))

    def indicator_height(self) -> int:
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

    def open_file_menu(self) -> None:
        self.state.active_dialog = "file_menu"
        self.state.dialog_selection_index = self.state.available_files.index(self.state.active_file)
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
        self.state.active_dialog = None
        self.state.pending_switch_path = None
        self.state.permission_prompt = None
        self.state.note_conflict = None
        self._invalidate()

    def move_dialog_selection(self, delta: int) -> None:
        if self.state.active_dialog == "file_menu":
            size = len(self.state.available_files)
        elif self.state.active_dialog == "model_picker":
            size = len(self.state.model_options)
        elif self.state.active_dialog == "permission_prompt":
            size = 3
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
            options = ("once", "always", "reject")
            self.resolve_permission_prompt(options[self.state.dialog_selection_index])

    async def request_file_switch(self, path: Path) -> None:
        if path == self.state.active_file:
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
            await self._persist_active_file_text(
                updated_text,
                expected_revision=conflict.model_revision_id,
            )
            await self._load_active_file(reset_dirty=True, highlight_recent=prefer_model)
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

    async def send_prompt(self) -> None:
        # Dispatch to web flow if already in web mode (prompt is hidden, Ctrl+R = confirm)
        if self.state.web_mode != "idle":
            self._handle_web_send()
            return

        if self.state.run_in_progress:
            self._set_error("A model run is already in progress.")
            self._invalidate()
            return

        prompt_text = self.current_prompt_text()
        stripped = prompt_text.strip()

        if self.state.active_file_missing_on_disk and not await self.save_active_file():
            return

        # Intercept @web prefix
        if stripped.startswith("@web"):
            query = stripped[len("@web"):].strip()
            if not query:
                self._set_error("Usage: @web <search query>")
                self._invalidate()
                return
            if self.state.editor_dirty and not await self.save_active_file():
                return
            self._sync_prompt_text("")
            self._run_task = asyncio.create_task(self._run_web_search(query))
            return

        if stripped == "/context":
            self._handle_context_command()
            self._sync_prompt_text("")
            self._invalidate()
            return

        if stripped.startswith("/"):
            self._set_error("That slash command is not available in the terminal UI yet.")
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

    async def toggle_mode(self) -> None:
        if self.state.run_in_progress:
            self._set_error("Wait for the current run to finish before switching modes.")
            self._invalidate()
            return
        self.state.mode = "chat" if self.state.mode == "note" else "note"
        self._set_status(f"Switched to {self.state.mode} mode.")
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
            self.state.active_file,
            default_folded_anchor_ids(self._note_content_text),
        )
        updated = toggle_fold_for_line(self._note_content_text, folded, line_number)
        if updated == folded:
            return
        self.state.fold_state[self.state.active_file] = updated
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
        elif event.kind == "loop_event":
            loop_kind = event.details.get("loop_kind")
            if loop_kind == "provider_request":
                self._set_status(f"Pontificating... ({self._run_turn_count})")
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
                    verb = _TOOL_VERBS.get(tool_calls[0], tool_calls[0].replace("_", " ").title())
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
        if event.kind == "file_written" and event.path == self.state.active_file:
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
        self._note_content_text = reconstruct_full_text(
            self._editor_buffer.text,
            self._fold_render.placeholder_map,
        )
        self._full_text = self._compose_full_text(self._note_content_text)
        self.state.editor_dirty = self._full_text != self._last_saved_text
        self._invalidate()

    def _handle_prompt_buffer_changed(self, _event) -> None:
        if self._syncing_prompt or self._prompt_buffer is None:
            return
        self.state.prompt_text = self._prompt_buffer.text
        self._invalidate()

    def _handle_cursor_moved(self, _event) -> None:
        self._invalidate()

    async def _run_current_mode(self, prompt_text: str) -> None:
        try:
            provider = self._build_provider()
            if self.state.mode == "note":
                result = await self._note_runner.run(
                    NoteModeRunRequest(
                        active_file=self.state.active_file,
                        included_files=self.state.included_files,
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
                await self._reconcile_note_mode_result(result)
            else:
                result = await self._chat_runner.run(
                    ChatModeRunRequest(
                        active_file=self.state.active_file,
                        included_files=self.state.included_files,
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
        except (NoteModeError, ChatModeError, FileReadError) as exc:
            self._set_error(str(exc))
        except Exception as exc:  # pragma: no cover - live provider/path safety
            self._set_error(f"TUI run failed: {exc}")
        finally:
            self.state.run_in_progress = False
            self._run_note_baseline_content = None
            self._invalidate()

    async def _reconcile_note_mode_result(self, result: NoteModeRunResult) -> None:
        baseline_note = self._run_note_baseline_content
        note_result = _latest_note_tool_result(result)

        if note_result is None:
            if baseline_note is None or self._note_content_text == baseline_note:
                await self._load_active_file(reset_dirty=True)
            return

        snapshot = await self._file_manager.read_snapshot(self.state.active_file)
        model_note_content, transcript_text = split_note_and_transcript(snapshot.raw_text)
        current_user_note = self._note_content_text

        if baseline_note is None or current_user_note == baseline_note or current_user_note == model_note_content:
            await self._load_active_file(reset_dirty=True, highlight_recent=True)
            return

        tool_name, payload = note_result
        if tool_name == "note_edit":
            merged_note = reapply_note_edit_payload_to_note_content(current_user_note, payload)
            if merged_note is not None:
                updated_text = join_note_and_transcript(merged_note, transcript_text)
                await self._persist_active_file_text(updated_text, expected_revision=snapshot.revision_id)
                await self._load_active_file(reset_dirty=True, highlight_recent=True)
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
            base_name = self.state.active_file.stem or "note"
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
        snapshot = await self._file_manager.read_snapshot(self.state.active_file)
        previous_text = self._note_content_text
        previous_folded = set(self.state.fold_state.get(self.state.active_file, set()))
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
        if self.state.active_file not in self.state.fold_state:
            self.state.fold_state[self.state.active_file] = default_folded_anchor_ids(self._note_content_text)
        else:
            self.state.fold_state[self.state.active_file] = carry_forward_managed_section_folds(
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

    async def _switch_active_file(self, path: Path) -> None:
        self.state.active_file = path
        self.state.active_dialog = None
        self.state.pending_switch_path = None
        self.state.pending_external_reload = False
        self.state.ignored_external_revision = None
        self.state.active_file_missing_on_disk = not path.exists()
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
                    if change.path != self.state.active_file:
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
            self.state.active_file,
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

    def _set_fold_at_cursor(self, *, folded: bool) -> None:
        if self._editor_buffer is None:
            return
        line_number = self._editor_buffer.document.cursor_position_row
        folded_ids = self.state.fold_state.setdefault(
            self.state.active_file,
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

        self.state.fold_state[self.state.active_file] = updated
        self._sync_editor_from_note_content(preserve_cursor=True)
        self._invalidate()

    async def delete_transcript_row(self, row_number: int) -> None:
        updated = delete_row_by_number(self._full_text, row_number)
        if updated == self._full_text:
            self._set_error(f"Transcript row {row_number} was not found.")
            self._invalidate()
            return
        snapshot = await self._file_manager.write_text(
            self.state.active_file,
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
            self.state.active_file,
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
                active_file=self.state.active_file,
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
        if status_message is not None:
            self._set_status(status_message)
        self._invalidate()

    def web_view_preferred_height(self) -> int:
        if self.state.web_mode == "results":
            return sum(
                3 if i in self._web_result_expanded else 2
                for i in range(len(self._web_results))
            )
        if self.state.web_mode == "chunks" and self._web_packets:
            return 1 + len(self._web_packets[0].chunks) * 4  # 1 full-page row + 4 lines/chunk
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
            str(self.state.active_file.expanduser().resolve()).encode("utf-8")
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
            str(self.state.active_file.expanduser().resolve()).encode("utf-8")
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
        self.state.editor_dirty = False
        self.state.note_conflict = None
        self.state.pending_external_reload = False
        self.state.ignored_external_revision = None
        self.state.fold_state[self.state.active_file] = default_folded_anchor_ids(self._note_content_text)
        self._sync_editor_from_note_content(preserve_cursor=False)
        self._set_status("New file: will be created on first save.")

    async def _persist_active_file_text(
        self,
        updated_text: str,
        *,
        expected_revision: str | None = None,
    ):
        if self.state.active_file_missing_on_disk and not self.state.active_file.parent.exists():
            if not self.state.create_parents_on_first_save:
                raise FileReadError(
                    "Parent directory does not exist. Reopen with -p/--parents to create it on first save."
                )
            try:
                self.state.active_file.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise FileReadError(f"Could not create parent directories for: {self.state.active_file}") from exc

        if expected_revision is None and not self.state.active_file_missing_on_disk and not self.state.ignored_external_revision:
            expected_revision = self._last_revision_id

        try:
            snapshot = await self._file_manager.write_text(
                self.state.active_file,
                updated_text,
                expected_revision=expected_revision,
            )
        except OSError as exc:
            raise FileReadError(f"Could not write file: {self.state.active_file}") from exc
        self._last_revision_id = snapshot.revision_id
        self._last_saved_text = snapshot.raw_text
        self._full_text = snapshot.raw_text
        self.state.editor_dirty = False
        self.state.ignored_external_revision = None
        was_missing = self.state.active_file_missing_on_disk
        self.state.active_file_missing_on_disk = False
        if was_missing:
            await self.start_watch_task()
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


def _should_highlight_recent_change(event: ProgressEvent) -> bool:
    if event.kind != "file_written":
        return False
    reason = event.details.get("reason")
    if reason in {"chat_prompt_append"}:
        return False
    if reason in {"chat_response_append", "search_history_append"}:
        return True
    return "tool_name" in event.details
