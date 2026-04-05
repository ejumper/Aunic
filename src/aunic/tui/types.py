from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from aunic.domain import ReasoningEffort, WorkMode

TuiMode = Literal["note", "chat"]
DialogMode = Literal["file_menu", "file_switch_confirm", "reload_confirm", "model_picker", "permission_prompt"]
WebMode = Literal["idle", "results", "chunks"]
TranscriptFilter = Literal["all", "chat", "tools", "search"]
TranscriptSortOrder = Literal["descending", "ascending"]


@dataclass(frozen=True)
class PermissionPromptState:
    message: str
    target: str
    tool_name: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelOption:
    label: str
    provider_name: str
    model: str


@dataclass
class TranscriptViewState:
    filter_mode: TranscriptFilter = "all"
    sort_order: TranscriptSortOrder = "descending"
    expanded_rows: set[int] = field(default_factory=set)


@dataclass
class TuiState:
    active_file: Path
    available_files: tuple[Path, ...]
    mode: TuiMode = "note"
    selected_model_index: int = 0
    model_options: tuple[ModelOption, ...] = ()
    reasoning_effort: ReasoningEffort | None = None
    editor_dirty: bool = False
    prompt_text: str = ""
    run_in_progress: bool = False
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
    transcript_open: bool = True

    @property
    def included_files(self) -> tuple[Path, ...]:
        return tuple(path for path in self.available_files if path != self.active_file)

    @property
    def selected_model(self) -> ModelOption:
        return self.model_options[self.selected_model_index]
