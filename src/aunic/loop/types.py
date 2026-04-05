from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from aunic.context.types import ContextBuildResult, FileSnapshot, PromptRun, StructuralNode, TextSpan
from aunic.domain import ReasoningEffort, TranscriptRow, UsageLog, WorkMode
from aunic.progress import ProgressSink
from aunic.providers.base import LLMProvider
from aunic.research.types import ResearchSummary

if TYPE_CHECKING:
    from aunic.tools.base import ToolDefinition

LoopStopReason = Literal[
    "finished",
    "turn_cap_reached",
    "malformed_turn_limit",
    "protected_rejection_limit",
    "provider_error",
]


@dataclass(frozen=True)
class ToolFailure:
    category: Literal[
        "malformed_turn",
        "validation_error",
        "protected_rejection",
        "conflict",
        "provider_error",
        "permission_denied",
        "execution_error",
        "timeout",
    ]
    reason: str
    tool_name: str | None
    message: str
    target_identifier: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LoopEvent:
    kind: Literal[
        "provider_request",
        "provider_response",
        "malformed_turn",
        "tool_result",
        "stop",
    ]
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LoopMetrics:
    valid_turn_count: int = 0
    malformed_repair_count: int = 0
    protected_rejection_count: int = 0
    conflict_rejection_count: int = 0
    successful_edit_count: int = 0
    main_turn_cap: int | None = None
    stop_reason: LoopStopReason | None = None


@dataclass(frozen=True)
class LoopRunRequest:
    provider: LLMProvider
    prompt_run: PromptRun
    context_result: ContextBuildResult
    active_file: Path | None = None
    included_files: tuple[Path, ...] = ()
    tool_registry: tuple["ToolDefinition[Any]", ...] | None = None
    model: str | None = None
    reasoning_effort: ReasoningEffort | None = None
    system_prompt: str | None = None
    display_root: Path | None = None
    progress_sink: ProgressSink | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    work_mode: WorkMode = "off"
    permission_handler: Any | None = None
    persist_message_rows: bool = True


@dataclass(frozen=True)
class LoopRunResult:
    stop_reason: LoopStopReason
    events: tuple[LoopEvent, ...]
    metrics: LoopMetrics
    tool_failures: tuple[ToolFailure, ...]
    final_file_snapshots: tuple[FileSnapshot, ...]
    research_summary: ResearchSummary = field(default_factory=ResearchSummary)
    usage_log: UsageLog = field(default_factory=UsageLog)
    run_log: tuple[TranscriptRow, ...] = ()
    run_log_new_start: int = 0
