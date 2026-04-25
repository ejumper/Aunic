from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from typing import Literal

from aunic.context.types import FileSnapshot, ParseWarning, PromptMode, PromptRun
from aunic.domain import ProviderImageInput, ReasoningEffort, UsageLog, WorkMode
from aunic.loop.types import LoopEvent, LoopRunResult, LoopStopReason, ToolFailure
from aunic.progress import ProgressSink
from aunic.research.types import ResearchSummary

if TYPE_CHECKING:
    from aunic.providers.base import LLMProvider


@dataclass(frozen=True)
class NoteModeRunRequest:
    active_file: Path
    provider: "LLMProvider"
    included_files: tuple[Path, ...] = ()
    included_image_files: tuple[Path, ...] = ()
    prompt_images: tuple[ProviderImageInput, ...] = ()
    active_plan_id: str | None = None
    active_plan_path: Path | None = None
    planning_status: str = "none"
    user_prompt: str = ""
    prompt_mode: PromptMode = "direct"
    total_turn_budget: int = 100_000
    model: str | None = None
    reasoning_effort: ReasoningEffort | None = None
    display_root: Path | None = None
    progress_sink: ProgressSink | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    work_mode: WorkMode = "off"
    permission_handler: Any | None = None


@dataclass(frozen=True)
class NoteModePromptResult:
    prompt_index: int
    prompt_run: PromptRun
    loop_result: LoopRunResult


@dataclass(frozen=True)
class NoteModeRunResult:
    initial_warnings: tuple[ParseWarning, ...]
    prompt_results: tuple[NoteModePromptResult, ...]
    completed_prompt_runs: int
    completed_all_prompts: bool
    final_file_snapshots: tuple[FileSnapshot, ...]
    stop_reason: LoopStopReason
    synthesis_loop_result: LoopRunResult | None = None
    synthesis_ran: bool = False
    synthesis_error: str | None = None
    usage_log: UsageLog = field(default_factory=UsageLog)
    usage_log_path: str | None = None

@dataclass(frozen=True)
class ChatModeRunRequest:
    active_file: Path
    provider: "LLMProvider"
    user_prompt: str
    included_files: tuple[Path, ...] = ()
    included_image_files: tuple[Path, ...] = ()
    prompt_images: tuple[ProviderImageInput, ...] = ()
    active_plan_id: str | None = None
    active_plan_path: Path | None = None
    planning_status: str = "none"
    total_turn_budget: int = 100_000
    model: str | None = None
    reasoning_effort: ReasoningEffort | None = None
    display_root: Path | None = None
    progress_sink: ProgressSink | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    work_mode: WorkMode = "off"
    permission_handler: Any | None = None


ChatModeStopReason = Literal[
    "finished",
    "provider_error",
    "turn_cap_reached",
    "malformed_turn_limit",
]


@dataclass(frozen=True)
class ChatModeMetrics:
    valid_turn_count: int = 0
    malformed_repair_count: int = 0
    citation_repair_count: int = 0
    stop_reason: ChatModeStopReason | None = None


@dataclass(frozen=True)
class ChatModeRunResult:
    initial_warnings: tuple[ParseWarning, ...]
    response_text: str
    assistant_response_appended: bool
    final_file_snapshots: tuple[FileSnapshot, ...]
    stop_reason: ChatModeStopReason
    metrics: ChatModeMetrics = field(default_factory=ChatModeMetrics)
    events: tuple[LoopEvent, ...] = ()
    tool_failures: tuple[ToolFailure, ...] = ()
    research_summary: ResearchSummary = field(default_factory=ResearchSummary)
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    usage_log: UsageLog = field(default_factory=UsageLog)
    usage_log_path: str | None = None
