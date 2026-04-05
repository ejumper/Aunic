from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

from aunic.context.types import ContextBuildResult, PromptRun
from aunic.domain import ReasoningEffort, TranscriptRow, UsageLog
from aunic.loop import LoopEvent, LoopRunRequest, LoopRunResult, ToolLoop
from aunic.providers.base import LLMProvider
from aunic.tools import OUTSIDE_NOTE_TOOL_NAMES, build_note_only_registry

SYNTHESIS_SYSTEM_PROMPT = "\n".join(
    [
        "You are operating inside Aunic note-mode synthesis pass.",
        "",
        "A note-mode run has just completed. During that run, work was done outside the note-content.",
        "Your task is to update the current note-content so it accurately reflects what happened.",
        "",
        "You will be given:",
        "- The current note-content (NOTE SNAPSHOT)",
        "- A readable run log showing the completed work (RUN LOG)",
        "",
        "Your job:",
        "1. Add new information from the run log where it fits best in note-content.",
        "2. Update stale note-content based on the run results.",
        "3. Remove note-content that the run results made irrelevant.",
        "",
        "Use note_edit for targeted changes or note_write for a full rewrite if many changes are needed.",
        "Complete all updates in a single pass.",
        "When done, reply with a brief plain-text summary.",
        "Do not create new sections unless necessary. Prefer integrating into the existing structure.",
    ]
)


@dataclass(frozen=True)
class SynthesisPassResult:
    ran: bool
    loop_result: LoopRunResult | None = None
    usage_log: UsageLog = field(default_factory=UsageLog)
    error_message: str | None = None


def work_read_tools_were_used(events: tuple[LoopEvent, ...]) -> bool:
    for event in events:
        if event.kind != "tool_result":
            continue
        tool_name = event.details.get("tool_name")
        status = event.details.get("status")
        if tool_name in OUTSIDE_NOTE_TOOL_NAMES and status == "completed":
            return True
    return False


def format_run_log_for_synthesis(rows: tuple[TranscriptRow, ...]) -> str:
    rendered: list[str] = []
    for row in rows:
        if row.type == "message":
            rendered.append(f"[{row.role}] {_content_str(row.content)}")
            continue
        if row.type == "tool_call":
            rendered.append(f"[assistant] tool_call {row.tool_name}: {_content_str(row.content)}")
            continue
        if row.type == "tool_result":
            rendered.append(f"[tool_result {row.tool_name}] {_content_str(row.content)}")
            continue
        if row.type == "tool_error":
            rendered.append(f"[tool_error {row.tool_name}] {_content_str(row.content)}")
    return "\n".join(rendered) if rendered else "(no run log rows)"


async def run_synthesis_pass(
    *,
    tool_loop: ToolLoop,
    provider: LLMProvider,
    context_result: ContextBuildResult,
    prompt_run: PromptRun,
    active_file,
    included_files,
    model: str | None,
    reasoning_effort: ReasoningEffort | None,
    progress_sink: Any,
    metadata: dict[str, Any],
    note_snapshot_text: str,
    run_log_rows: tuple[TranscriptRow, ...],
    permission_handler: Any | None,
) -> SynthesisPassResult:
    formatted_run_log = format_run_log_for_synthesis(run_log_rows)
    synthesis_user_prompt = "\n".join(
        [
            "Synthesize the completed run into note-content.",
            "Use the run log below as the source of truth for what happened outside the note.",
            "",
            "RUN LOG",
            formatted_run_log,
        ]
    )
    synthesis_prompt_run = _build_synthesis_prompt_run(
        prompt_run,
        note_snapshot_text=note_snapshot_text,
        user_prompt_text=synthesis_user_prompt,
    )
    synthesis_context_result = replace(
        context_result,
        prompt_runs=(synthesis_prompt_run,),
        transcript_rows=[],
    )
    loop_result = await tool_loop.run(
        LoopRunRequest(
            provider=provider,
            prompt_run=synthesis_prompt_run,
            context_result=synthesis_context_result,
            active_file=active_file,
            included_files=included_files,
            tool_registry=build_note_only_registry(),
            model=model,
            reasoning_effort=reasoning_effort,
            system_prompt=SYNTHESIS_SYSTEM_PROMPT,
            progress_sink=progress_sink,
            metadata={**metadata, "synthesis_pass": True},
            work_mode="off",
            permission_handler=permission_handler,
            persist_message_rows=False,
        )
    )
    return SynthesisPassResult(
        ran=True,
        loop_result=loop_result,
        usage_log=loop_result.usage_log,
        error_message=(
            None
            if loop_result.stop_reason == "finished"
            else f"Synthesis pass stopped: {loop_result.stop_reason}"
        ),
    )


def _build_synthesis_prompt_run(
    prompt_run: PromptRun,
    *,
    note_snapshot_text: str,
    user_prompt_text: str,
) -> PromptRun:
    return replace(
        prompt_run,
        prompt_text="Synthesize the completed run into note-content.",
        per_prompt_budget=4,
        model_input_text=user_prompt_text,
        note_snapshot_text=note_snapshot_text,
        user_prompt_text=user_prompt_text,
    )


def _content_str(content: object) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
