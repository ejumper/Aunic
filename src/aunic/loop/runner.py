from __future__ import annotations

from pathlib import Path
from typing import Any

from aunic.config import LoopSettings, SETTINGS
from aunic.context.file_manager import FileManager
from aunic.context.types import FileSnapshot, PromptRun
from aunic.domain import ProviderGeneratedRow, ProviderRequest, ProviderResponse, TranscriptRow, UsageLogEntry
from aunic.errors import ProviderError, StructuredOutputError
from aunic.loop.types import (
    LoopEvent,
    LoopMetrics,
    LoopRunRequest,
    LoopRunResult,
    ToolFailure,
)
from aunic.progress import ProgressEvent, emit_progress, progress_from_loop_event
from aunic.research import FetchService, ResearchState, SearchService
from aunic.tools import (
    RunToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolSessionState,
    build_note_tool_registry,
)
from aunic.tools.runtime import failure_from_payload
from aunic.usage import build_usage_log, format_usage_brief

NOTE_LOOP_SYSTEM_PROMPT = "\n".join(
    [
        "You are NOT chatting with the user. You are creating/editing a markdown note.",
        "Plain text output is FORBIDDEN, replace it with the note_edit or note_write",
        "Your job is to follow the explicit and implicit directions from the user using the tools provided, then end by using note_edit or note_write to integrate the resulting information into the file.",
        "The flow is NOT use tools then end on plain text, its use the tools then end with note_edit or note_write",
        "The NOTE SNAPSHOT is an implicit read of the active markdown note. note_edit and note_write always target that note's note-content.",
        "Only note-content is writable in note mode. Transcript rows, search results, fetch results, read output, and tool outputs are reference material only.",
        "For note_edit, old_string must come from the current active note-content, not from transcript rows, fetched content, read output, or drafted response text.",
        "Use the web_search and web_fetch tools as many times as needed to gather any information you need. Error on the side of using these.",
        "For note_edit and note_write, default to markdown formatting, but if any content already exists, follow the current formatting and writing style as closely as practical.",
        "Do not create new chat-style turns, fake user prompts, transcript separators, or assistant replies.",
        "Treat the '# Transcript' sections as source material, not as the place to continue writing.",
    ]
)


class ToolLoop:
    def __init__(
        self,
        file_manager: FileManager | None = None,
        search_service: SearchService | None = None,
        fetch_service: FetchService | None = None,
        *,
        settings: LoopSettings | None = None,
    ) -> None:
        self._settings = settings or SETTINGS.loop
        self._file_manager = file_manager or FileManager()
        self._search_service = search_service or SearchService()
        self._fetch_service = fetch_service or FetchService()
        self._session_state = ToolSessionState()

    async def run(self, request: LoopRunRequest) -> LoopRunResult:
        active_prompt_run = request.prompt_run
        registry = request.tool_registry or build_note_tool_registry(work_mode=request.work_mode)
        runtime = await RunToolContext.create(
            file_manager=self._file_manager,
            context_result=request.context_result,
            prompt_run=active_prompt_run,
            active_file=request.active_file or request.context_result.file_snapshots[0].path,
            session_state=self._session_state,
            search_service=self._search_service,
            fetch_service=self._fetch_service,
            research_state=ResearchState(),
            progress_sink=request.progress_sink,
            work_mode=request.work_mode,
            permission_handler=request.permission_handler,
            metadata=dict(request.metadata),
        )

        run_log: list[TranscriptRow] = list(request.context_result.transcript_rows or [])
        current_user_prompt_text = (
            active_prompt_run.user_prompt_text or active_prompt_run.prompt_text
        )
        events: list[LoopEvent] = []
        tool_failures: list[ToolFailure] = []

        def append_run_log_message(role: str, content: str) -> None:
            run_log.append(
                TranscriptRow(
                    row_number=_next_run_log_row_number(run_log),
                    role=role,  # type: ignore[arg-type]
                    type="message",
                    content=content,
                )
            )

        async def append_loop_event(event: LoopEvent) -> None:
            events.append(event)
            await emit_progress(
                request.progress_sink,
                progress_from_loop_event(
                    event,
                    path=request.active_file or request.context_result.file_snapshots[0].path,
                ),
            )

        total_valid_turns = 0
        current_loop_turns = 0
        current_turn_cap = active_prompt_run.per_prompt_budget
        malformed_repair_count = 0
        protected_rejection_count = 0
        conflict_rejection_count = 0
        successful_edit_count = 0
        stop_reason = "provider_error"
        usage_entries: list[UsageLogEntry] = []
        provider_response_index = 0
        run_log_start_index = len(run_log)

        def next_provider_response_index() -> int:
            nonlocal provider_response_index
            provider_response_index += 1
            return provider_response_index

        if request.persist_message_rows:
            user_msg_row_number = await runtime.write_transcript_row(
                "user",
                "message",
                None,
                None,
                current_user_prompt_text,
            )
        else:
            user_msg_row_number = _next_run_log_row_number(run_log)
        run_log.append(
            TranscriptRow(
                row_number=user_msg_row_number,
                role="user",
                type="message",
                content=current_user_prompt_text,
            )
        )

        while True:
            if current_loop_turns >= current_turn_cap:
                stop_reason = "turn_cap_reached"
                await append_loop_event(LoopEvent(kind="stop", message="Turn cap reached."))
                break

            tool_map = {definition.spec.name: definition for definition in registry}
            provider_request = ProviderRequest(
                messages=[],
                transcript_messages=list(run_log),
                note_snapshot=runtime.note_snapshot_text() or active_prompt_run.note_snapshot_text or None,
                user_prompt=current_user_prompt_text or None,
                tools=[definition.spec for definition in registry],
                system_prompt=_build_system_prompt(
                    request.system_prompt,
                    work_mode=request.work_mode,
                    registry=registry,
                    protected_paths=runtime.note_scope_paths(),
                ),
                model=request.model,
                reasoning_effort=request.reasoning_effort,
                metadata={
                    **dict(request.metadata),
                    "active_file": str(runtime.active_file),
                    "mode": "note",
                    "work_mode": request.work_mode,
                },
            )
            await append_loop_event(
                LoopEvent(
                    kind="provider_request",
                    message="Sent tool-loop turn to provider.",
                    details={"messages": len(provider_request.messages), "tools": len(provider_request.tools)},
                )
            )

            try:
                response = await request.provider.generate(provider_request)
            except StructuredOutputError as exc:
                malformed_repair_count += 1
                tool_failures.append(
                    ToolFailure(
                        category="malformed_turn",
                        reason="structured_output",
                        tool_name=None,
                        message=str(exc),
                    )
                )
                current_user_prompt_text = _repair_prompt(str(exc))
                append_run_log_message("user", current_user_prompt_text)
                await append_loop_event(
                    LoopEvent(
                        kind="malformed_turn",
                        message="Provider returned malformed structured output.",
                        details={"repair_count": malformed_repair_count},
                    )
                )
                if malformed_repair_count >= self._settings.malformed_turn_limit:
                    stop_reason = "malformed_turn_limit"
                    break
                continue
            except ProviderError as exc:
                tool_failures.append(
                    ToolFailure(
                        category="provider_error",
                        reason="provider_error",
                        tool_name=None,
                        message=str(exc),
                    )
                )
                await append_loop_event(LoopEvent(kind="stop", message=f"Provider error: {exc}"))
                stop_reason = "provider_error"
                break

            usage_entries.append(
                _usage_entry_from_response(
                    response,
                    index=next_provider_response_index(),
                    stage="tool_loop",
                )
            )
            await append_loop_event(_provider_response_event(response, stage="tool_loop"))

            (
                generated_valid_turns,
                generated_edit_count,
                generated_note_tool_success,
                generated_tool_failures,
            ) = await _append_provider_generated_rows(
                generated_rows=response.generated_rows,
                run_log=run_log,
                runtime=runtime,
                persist_message_rows=request.persist_message_rows,
                tool_map=tool_map,
                append_loop_event=append_loop_event,
            )
            tool_failures.extend(generated_tool_failures)
            total_valid_turns += generated_valid_turns
            current_loop_turns += generated_valid_turns
            successful_edit_count += generated_edit_count
            if generated_note_tool_success:
                active_prompt_run = _prompt_run_with_model_input(
                    active_prompt_run,
                    active_prompt_run.model_input_text,
                    per_prompt_budget=active_prompt_run.per_prompt_budget,
                    note_snapshot_text=runtime.note_snapshot_text(),
                    user_prompt_text=active_prompt_run.user_prompt_text,
                )
                current_user_prompt_text = (
                    active_prompt_run.user_prompt_text or active_prompt_run.prompt_text
                )
                stop_reason = "finished"
                await append_loop_event(
                    LoopEvent(
                        kind="stop",
                        message="Run completed (note updated).",
                        details={"note_tool_completed": True},
                    )
                )
                break

            # --- Dispatch: expect exactly one tool call ---
            malformed_message = _validate_provider_response(response, tool_map)
            if malformed_message is not None:
                malformed_repair_count += 1
                tool_failures.append(
                    ToolFailure(
                        category="malformed_turn",
                        reason="invalid_provider_response",
                        tool_name=response.tool_calls[0].name if response.tool_calls else None,
                        message=malformed_message,
                    )
                )
                append_run_log_message("assistant", response.text.strip() or "(empty response)")
                current_user_prompt_text = _repair_prompt(malformed_message)
                append_run_log_message("user", current_user_prompt_text)
                await append_loop_event(
                    LoopEvent(kind="malformed_turn", message=malformed_message, details={"repair_count": malformed_repair_count})
                )
                if malformed_repair_count >= self._settings.malformed_turn_limit:
                    stop_reason = "malformed_turn_limit"
                    break
                continue

            if not response.tool_calls:
                assistant_text = response.text.strip()
                if not assistant_text:
                    malformed_repair_count += 1
                    tool_failures.append(
                        ToolFailure(
                            category="malformed_turn",
                            reason="empty_response",
                            tool_name=None,
                            message="Empty response with no tool call.",
                        )
                    )
                    append_run_log_message("assistant", "(empty response)")
                    current_user_prompt_text = _repair_prompt("Empty response with no tool call.")
                    append_run_log_message("user", current_user_prompt_text)
                    await append_loop_event(
                        LoopEvent(
                            kind="malformed_turn",
                            message="Empty response with no tool call.",
                            details={"repair_count": malformed_repair_count},
                        )
                    )
                    if malformed_repair_count >= self._settings.malformed_turn_limit:
                        stop_reason = "malformed_turn_limit"
                        break
                    continue

                malformed_repair_count += 1
                tool_failures.append(
                    ToolFailure(
                        category="malformed_turn",
                        reason="note_mode_plain_text_requires_note_tool",
                        tool_name=None,
                        message=(
                            "Plain assistant text in note mode must be rewritten through "
                            "note_edit or note_write."
                        ),
                        details={"assistant_text": assistant_text},
                    )
                )
                append_run_log_message("assistant", assistant_text)
                current_user_prompt_text = _note_mode_redirect_prompt(
                    assistant_text,
                    runtime.active_file,
                )
                append_run_log_message("user", current_user_prompt_text)
                await append_loop_event(
                    LoopEvent(
                        kind="malformed_turn",
                        message="Note mode requires note_edit or note_write instead of plain text output.",
                        details={"repair_count": malformed_repair_count},
                    )
                )
                if malformed_repair_count >= self._settings.malformed_turn_limit:
                    stop_reason = "malformed_turn_limit"
                    break
                continue

            tool_call = response.tool_calls[0]
            definition = tool_map[tool_call.name]
            try:
                parsed_args = definition.parse_arguments(tool_call.arguments)
            except ValueError as exc:
                malformed_repair_count += 1
                tool_failures.append(
                    ToolFailure(category="malformed_turn", reason="invalid_arguments", tool_name=tool_call.name, message=str(exc))
                )
                append_run_log_message("assistant", response.text.strip() or "(empty response)")
                current_user_prompt_text = _repair_prompt(str(exc))
                append_run_log_message("user", current_user_prompt_text)
                await append_loop_event(
                    LoopEvent(kind="malformed_turn", message=f"Invalid arguments for {tool_call.name}.", details={"repair_count": malformed_repair_count})
                )
                if malformed_repair_count >= self._settings.malformed_turn_limit:
                    stop_reason = "malformed_turn_limit"
                    break
                continue

            malformed_repair_count = 0
            if definition.persistence == "persistent":
                row_number = await runtime.write_transcript_row(
                    "assistant",
                    "tool_call",
                    tool_call.name,
                    tool_call.id,
                    tool_call.arguments,
                )
            else:
                row_number = _next_run_log_row_number(run_log)
            run_log.append(
                TranscriptRow(
                    row_number=row_number,
                    role="assistant",
                    type="tool_call",
                    tool_name=tool_call.name,
                    tool_id=tool_call.id,
                    content=tool_call.arguments,
                )
            )
            try:
                result = await definition.execute(runtime, parsed_args)
            except ValueError as exc:
                malformed_repair_count += 1
                tool_failures.append(
                    ToolFailure(category="malformed_turn", reason="invalid_execution_arguments", tool_name=tool_call.name, message=str(exc))
                )
                current_user_prompt_text = _repair_prompt(str(exc))
                append_run_log_message("user", current_user_prompt_text)
                await append_loop_event(
                    LoopEvent(kind="malformed_turn", message=f"Invalid execution arguments for {tool_call.name}.", details={"repair_count": malformed_repair_count})
                )
                if malformed_repair_count >= self._settings.malformed_turn_limit:
                    stop_reason = "malformed_turn_limit"
                    break
                continue

            if result.tool_failure is not None:
                tool_failures.append(result.tool_failure)

            transcript_content = (
                result.in_memory_content
                if result.transcript_content is None
                else result.transcript_content
            )
            row_type = "tool_error" if result.status != "completed" else "tool_result"
            if definition.persistence == "persistent":
                row_number = await runtime.write_transcript_row(
                    "tool",
                    row_type,
                    tool_call.name,
                    tool_call.id,
                    transcript_content,
                )
            else:
                row_number = _next_run_log_row_number(run_log)
            run_log.append(
                TranscriptRow(
                    row_number=row_number,
                    role="tool",
                    type=row_type,  # type: ignore[arg-type]
                    tool_name=tool_call.name,
                    tool_id=tool_call.id,
                    content=result.in_memory_content,
                )
            )
            if tool_call.name in {"note_edit", "note_write"} and result.status == "completed":
                active_prompt_run = _prompt_run_with_model_input(
                    active_prompt_run,
                    active_prompt_run.model_input_text,
                    per_prompt_budget=active_prompt_run.per_prompt_budget,
                    note_snapshot_text=runtime.note_snapshot_text(),
                    user_prompt_text=active_prompt_run.user_prompt_text,
                )
            current_user_prompt_text = (
                active_prompt_run.user_prompt_text or active_prompt_run.prompt_text
            )
            await append_loop_event(
                LoopEvent(
                    kind="tool_result",
                    message=_tool_result_event_message(tool_call.name, result),
                    details={"tool_name": tool_call.name, "status": result.status},
                )
            )

            failure_category = result.tool_failure.category if result.tool_failure is not None else None
            if failure_category == "protected_rejection":
                protected_rejection_count += 1
                if protected_rejection_count >= self._settings.protected_rejection_limit:
                    stop_reason = "protected_rejection_limit"
                    await append_loop_event(
                        LoopEvent(kind="stop", message="Protected-zone rejection limit reached.")
                    )
                    break
                continue
            if failure_category == "conflict":
                conflict_rejection_count += 1

            protected_rejection_count = 0
            if tool_call.name in {"edit", "write", "note_edit", "note_write"} and result.status == "completed":
                successful_edit_count += 1
            total_valid_turns += 1
            current_loop_turns += 1
            if tool_call.name in {"note_edit", "note_write"} and result.status == "completed":
                stop_reason = "finished"
                await append_loop_event(
                    LoopEvent(
                        kind="stop",
                        message="Run completed (note updated).",
                        details={"tool_name": tool_call.name},
                    )
                )
                break

        final_snapshots = tuple(
            [
                await self._file_manager.read_snapshot(snapshot.path)
                for snapshot in request.context_result.file_snapshots
            ]
        )
        metrics = LoopMetrics(
            valid_turn_count=total_valid_turns,
            malformed_repair_count=sum(
                1 for failure in tool_failures if failure.category == "malformed_turn"
            ),
            protected_rejection_count=sum(
                1 for failure in tool_failures if failure.category == "protected_rejection"
            ),
            conflict_rejection_count=conflict_rejection_count,
            successful_edit_count=successful_edit_count,
            main_turn_cap=current_turn_cap,
            stop_reason=stop_reason,
        )
        return LoopRunResult(
            stop_reason=stop_reason,
            events=tuple(events),
            metrics=metrics,
            tool_failures=tuple(tool_failures),
            final_file_snapshots=final_snapshots,
            research_summary=runtime.research_state.summary(),
            usage_log=build_usage_log(usage_entries),
            run_log=tuple(run_log),
            run_log_new_start=run_log_start_index,
        )

def _build_system_prompt(
    extra_system_prompt: str | None,
    *,
    work_mode: str,
    registry: tuple[ToolDefinition[Any], ...],
    protected_paths: tuple[Path, ...],
) -> str:
    tool_names = ", ".join(definition.spec.name for definition in registry)
    parts = [
        NOTE_LOOP_SYSTEM_PROMPT,
        f"Current work mode: {work_mode}.",
        f"Available tools: {tool_names}.",
    ]
    if work_mode != "work":
        parts.append("Do not try to mutate files outside note-content in this work mode.")
    if protected_paths:
        joined = "\n".join(f"- {path}" for path in protected_paths)
        parts.append(
            "Protected note-content path(s). Do not use work-mode edit/write/bash to mutate them.\n"
            f"{joined}\n"
            "Use note_edit or note_write for note-content changes."
        )
    if extra_system_prompt:
        parts.append(f"Additional system guidance:\n{extra_system_prompt}")
    return "\n\n".join(part for part in parts if part.strip())


def _prompt_run_with_model_input(
    prompt_run: PromptRun,
    model_input_text: str,
    *,
    per_prompt_budget: int,
    note_snapshot_text: str = "",
    user_prompt_text: str = "",
) -> PromptRun:
    return PromptRun(
        index=prompt_run.index,
        prompt_text=prompt_run.prompt_text,
        mode=prompt_run.mode,
        per_prompt_budget=per_prompt_budget,
        target_map_text=prompt_run.target_map_text,
        model_input_text=model_input_text,
        read_only_map_text=prompt_run.read_only_map_text,
        note_snapshot_text=note_snapshot_text or prompt_run.note_snapshot_text,
        user_prompt_text=user_prompt_text or prompt_run.user_prompt_text,
        source_path=prompt_run.source_path,
        source_target_id=prompt_run.source_target_id,
        source_raw_span=prompt_run.source_raw_span,
        source_parsed_span=prompt_run.source_parsed_span,
    )


def _next_run_log_row_number(run_log: list[TranscriptRow]) -> int:
    if not run_log:
        return 1
    return run_log[-1].row_number + 1
def _validate_provider_response(
    response: ProviderResponse,
    tool_map: dict[str, ToolDefinition[Any]],
) -> str | None:
    if not response.tool_calls:
        return None
    if len(response.tool_calls) != 1:
        return "Expected at most one tool call per turn."
    tool_call = response.tool_calls[0]
    if tool_call.name not in tool_map:
        return f"Unknown tool {tool_call.name!r}. Use one of the available tools."
    return None


def _repair_prompt(message: str) -> str:
    return (
        "The previous response was invalid for Aunic note-edit mode.\n"
        f"Problem: {message}\n"
        "Reply with exactly one valid tool call or a final plain response."
    )


def _note_mode_redirect_prompt(draft_answer: str, active_file: Path) -> str:
    return (
        "Your response must be written into the active markdown note using note_edit or note_write.\n"
        f"Target note: {active_file}\n"
        "Only modify note-content. Do not edit transcript rows, search results, read output, or tool outputs.\n"
        "Use note_write or note_edit to integrate this content into note-content where it fits.\n\n"
        f"Draft answer:\n{draft_answer}"
    )


def _tool_result_event_message(tool_name: str, result: ToolExecutionResult) -> str:
    content = result.in_memory_content
    if isinstance(content, dict):
        if "message" in content:
            return str(content["message"])
        if tool_name == "web_search":
            return f"web_search returned {len(content) if isinstance(content, list) else 'results'}."
        if tool_name == "web_fetch":
            return f"web_fetch fetched {content.get('title') or content.get('url') or 'a page'}."
        if tool_name == "bash":
            return f"bash finished with status {result.status}."
        if tool_name == "read":
            return f"read returned {content.get('type', 'content')}."
        if tool_name in {"edit", "write", "note_edit", "note_write"}:
            return f"{tool_name} finished."
    if isinstance(content, list):
        return f"{tool_name} returned {len(content)} item(s)."
    return f"{tool_name} finished."


async def _append_provider_generated_rows(
    *,
    generated_rows: list[ProviderGeneratedRow],
    run_log: list[TranscriptRow],
    runtime: RunToolContext,
    persist_message_rows: bool,
    tool_map: dict[str, ToolDefinition[Any]],
    append_loop_event,
 ) -> tuple[int, int, bool, list[ToolFailure]]:
    valid_turns = 0
    successful_edit_count = 0
    successful_note_tool = False
    tool_failures: list[ToolFailure] = []

    for generated in generated_rows:
        row = generated.row
        transcript_content = (
            row.content if generated.transcript_content is None else generated.transcript_content
        )
        definition = tool_map.get(row.tool_name or "")

        if row.type == "message":
            if persist_message_rows:
                row_number = await runtime.write_transcript_row(
                    row.role,
                    row.type,
                    row.tool_name,
                    row.tool_id,
                    transcript_content,
                )
            else:
                row_number = _next_run_log_row_number(run_log)
            run_log.append(
                TranscriptRow(
                    row_number=row_number,
                    role=row.role,
                    type=row.type,
                    tool_name=row.tool_name,
                    tool_id=row.tool_id,
                    content=row.content,
                )
            )
            continue

        persistent = definition.persistence == "persistent" if definition is not None else True
        if persistent:
            row_number = await runtime.write_transcript_row(
                row.role,
                row.type,
                row.tool_name,
                row.tool_id,
                transcript_content,
            )
        else:
            row_number = _next_run_log_row_number(run_log)

        run_log.append(
            TranscriptRow(
                row_number=row_number,
                role=row.role,
                type=row.type,
                tool_name=row.tool_name,
                tool_id=row.tool_id,
                content=row.content,
            )
        )

        if row.role != "tool":
            continue

        valid_turns += 1
        if row.type == "tool_error" and isinstance(row.content, dict):
            tool_failures.append(failure_from_payload(row.content, tool_name=row.tool_name))
        if row.tool_name in {"edit", "write", "note_edit", "note_write"} and row.type == "tool_result":
            successful_edit_count += 1
        if row.tool_name in {"note_edit", "note_write"} and row.type == "tool_result":
            successful_note_tool = True

        await append_loop_event(
            LoopEvent(
                kind="tool_result",
                message=_tool_result_event_message(
                    row.tool_name or "tool",
                    ToolExecutionResult(
                        tool_name=row.tool_name or "tool",
                        status="completed" if row.type == "tool_result" else "tool_error",
                        in_memory_content=row.content,
                        transcript_content=transcript_content,
                    ),
                ),
                details={
                    "tool_name": row.tool_name,
                    "status": "completed" if row.type == "tool_result" else "tool_error",
                    "generated": True,
                },
            )
        )

    return valid_turns, successful_edit_count, successful_note_tool, tool_failures












def _usage_entry_from_response(
    response: ProviderResponse,
    *,
    index: int,
    stage: str,
) -> UsageLogEntry:
    return UsageLogEntry(
        index=index,
        stage=stage,
        usage=response.usage,
        provider=response.provider_metadata.get("provider"),
        model=response.provider_metadata.get("model"),
        finish_reason=response.finish_reason,
        metadata=dict(response.provider_metadata),
    )


def _provider_response_event(
    response: ProviderResponse,
    *,
    stage: str,
) -> LoopEvent:
    return LoopEvent(
        kind="provider_response",
        message=f"{stage.replace('_', ' ').title()} provider response: {format_usage_brief(response.usage)}.",
        details={
            "stage": stage,
            "usage": {
                "total_tokens": response.usage.total_tokens if response.usage else None,
                "input_tokens": response.usage.input_tokens if response.usage else None,
                "cached_input_tokens": response.usage.cached_input_tokens if response.usage else None,
                "output_tokens": response.usage.output_tokens if response.usage else None,
                "reasoning_output_tokens": (
                    response.usage.reasoning_output_tokens if response.usage else None
                ),
                "model_context_window": (
                    response.usage.model_context_window if response.usage else None
                ),
            },
            "finish_reason": response.finish_reason,
            "provider": response.provider_metadata.get("provider"),
            "model": response.provider_metadata.get("model"),
            "tool_calls": [tc.name for tc in response.tool_calls] if response.tool_calls else [],
            "text_preview": response.text[:300] if response.text else "",
            "provider_metadata": {
                k: v for k, v in response.provider_metadata.items()
                if k in ("effective_strategy", "fallback_occurred", "native_failure")
            },
        },
    )


async def _observe_provider_response(
    response: ProviderResponse,
    *,
    stage: str,
    append_loop_event,
    usage_entries: list[UsageLogEntry],
    index: int,
) -> None:
    usage_entries.append(
        _usage_entry_from_response(
            response,
            index=index,
            stage=stage,
        )
    )
    await append_loop_event(_provider_response_event(response, stage=stage))
