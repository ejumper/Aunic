from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from aunic.domain import TranscriptRow

EditLabel = Literal["WRITE-EDIT_ALLOWED", "READ_ONLY-NO_EDITS", "READ_ONLY-SEARCH_RESULTS", "HIDDEN"]
MarkerType = Literal["exclude", "include_only", "read_only", "write_scope"]
PromptMode = Literal["direct"]


@dataclass(frozen=True)
class TextSpan:
    start: int
    end: int


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    raw_text: str
    revision_id: str
    content_hash: str
    mtime_ns: int
    size_bytes: int
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class FileMetadata:
    path: Path
    revision_id: str
    mtime_ns: int
    size_bytes: int
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class FileChange:
    path: Path
    change: Literal["added", "modified", "deleted"]
    exists: bool
    revision_id: str | None
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class MarkerSpan:
    marker_type: MarkerType
    open_span: TextSpan
    content_span: TextSpan
    close_span: TextSpan


@dataclass(frozen=True)
class ParseWarning:
    path: Path
    code: str
    message: str
    line: int
    column: int
    offset: int


@dataclass(frozen=True)
class SourceMapSegment:
    parsed_span: TextSpan
    raw_span: TextSpan


@dataclass(frozen=True)
class ParsedNoteFile:
    snapshot: FileSnapshot
    display_path: str
    parsed_text: str
    marker_spans: tuple[MarkerSpan, ...]
    prompt_spans: tuple[MarkerSpan, ...]
    warnings: tuple[ParseWarning, ...]
    source_map: tuple[SourceMapSegment, ...]
    note_text: str = ""
    transcript_text: str | None = None
    transcript_start_offset: int | None = None


@dataclass(frozen=True)
class StructuralNode:
    target_id: str
    file_path: Path
    file_label: str
    kind: str
    label: EditLabel
    heading_path: tuple[str, ...]
    line_start: int
    line_end: int
    raw_span: TextSpan
    parsed_span: TextSpan | None
    preview: str
    heading_id: str | None = None
    heading_level: int | None = None
    anchor_id: str | None = None
    is_focus_area: bool = False


@dataclass(frozen=True)
class PromptRun:
    index: int
    prompt_text: str
    mode: PromptMode
    per_prompt_budget: int
    target_map_text: str
    model_input_text: str
    read_only_map_text: str = ""
    note_snapshot_text: str = ""
    user_prompt_text: str = ""
    source_path: Path | None = None
    source_target_id: str | None = None
    source_raw_span: TextSpan | None = None
    source_parsed_span: TextSpan | None = None


@dataclass(frozen=True)
class ContextBuildRequest:
    active_file: Path
    included_files: tuple[Path, ...] = ()
    user_prompt: str = ""
    prompt_mode: PromptMode = "direct"
    total_turn_budget: int = 8
    display_root: Path | None = None


@dataclass(frozen=True)
class ContextBuildResult:
    prompt_runs: tuple[PromptRun, ...]
    file_snapshots: tuple[FileSnapshot, ...]
    parsed_files: tuple[ParsedNoteFile, ...]
    structural_nodes: tuple[StructuralNode, ...]
    parsed_note_text: str
    target_map_text: str
    read_only_map_text: str
    model_input_text: str
    warnings: tuple[ParseWarning, ...]
    transcript_text: str | None = None
    transcript_rows: list[TranscriptRow] | None = None
