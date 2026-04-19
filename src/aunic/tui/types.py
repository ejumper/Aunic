from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from aunic.domain import ReasoningEffort, WorkMode

TuiMode = Literal["note", "chat"]
DialogMode = Literal["file_menu", "file_switch_confirm", "reload_confirm", "model_picker", "permission_prompt", "note_conflict"]
PlanningStatus = Literal["none", "drafting", "awaiting_approval", "approved", "implementing"]
WebMode = Literal["idle", "results", "chunks"]
TranscriptFilter = Literal["all", "chat", "tools", "search"]
TranscriptSortOrder = Literal["descending", "ascending"]
FindField = Literal["find", "replace", "buttons"]


@dataclass(frozen=True)
class PermissionPromptState:
    message: str
    target: str
    tool_name: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NoteConflictState:
    tool_name: Literal["note_edit", "note_write"]
    model_note_content: str
    user_note_content: str
    model_revision_id: str
    transcript_text: str | None = None


@dataclass(frozen=True)
class ModelOption:
    label: str
    provider_name: str
    model: str
    profile_id: str | None = None
    context_window: int | None = None


@dataclass
class TranscriptViewState:
    filter_mode: TranscriptFilter = "all"
    sort_order: TranscriptSortOrder = "descending"
    expanded_rows: set[int] = field(default_factory=set)
    maximized: bool = False


@dataclass
class IncludeEntry:
    path: str        # as typed by the user (relative or absolute)
    is_dir: bool     # True → directory include
    recursive: bool  # True → recursive glob (only when is_dir=True)
    active: bool = True


@dataclass(frozen=True)
class PlanMenuEntry:
    id: str
    title: str
    status: str
    path: Path


@dataclass(frozen=True)
class SleepStatusState:
    started_monotonic: float
    deadline_monotonic: float
    reason: str | None = None


@dataclass
class FindUiState:
    active: bool = False
    replace_mode: bool = False
    case_sensitive: bool = False
    find_text: str = ""
    replace_text: str = ""
    active_field: FindField = "find"
    button_index: int = 0
    saved_prompt_text: str = ""
    current_match_index: int | None = None
    current_match_start: int | None = None
    current_match_end: int | None = None
    match_count: int = 0


@dataclass
class TuiState:
    active_file: Path
    available_files: tuple[Path, ...]
    context_file: Path | None = None
    display_file: Path | None = None
    active_plan_id: str | None = None
    active_plan_path: Path | None = None
    planning_status: PlanningStatus = "none"
    pre_plan_work_mode: WorkMode | None = None
    mode: TuiMode = "note"
    selected_model_index: int = 0
    model_options: tuple[ModelOption, ...] = ()
    reasoning_effort: ReasoningEffort | None = None
    editor_dirty: bool = False
    prompt_text: str = ""
    run_in_progress: bool = False
    sleep_status: SleepStatusState | None = None
    indicator_message: str = ""
    indicator_kind: Literal["status", "error"] = "status"
    fold_state: dict[Path, set[str]] = field(default_factory=dict)
    web_mode: WebMode = "idle"
    active_dialog: DialogMode | None = None
    pending_switch_path: Path | None = None
    pending_external_reload: bool = False
    work_mode: WorkMode = "off"
    dialog_selection_index: int = 0
    ignored_external_revision: str | None = None
    permission_prompt: PermissionPromptState | None = None
    note_conflict: NoteConflictState | None = None
    transcript_open: bool = True
    active_file_missing_on_disk: bool = False
    create_parents_on_first_save: bool = False
    find_ui: FindUiState = field(default_factory=FindUiState)
    include_entries: list[IncludeEntry] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.context_file is None:
            self.context_file = self.active_file
        if self.display_file is None:
            self.display_file = self.context_file

    @property
    def included_files(self) -> tuple[Path, ...]:
        return tuple(path for path in self.available_files if path != self.context_file)

    @property
    def selected_model(self) -> ModelOption:
        return self.model_options[self.selected_model_index]
