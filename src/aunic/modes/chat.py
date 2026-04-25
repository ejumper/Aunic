from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from aunic.config import SETTINGS
from aunic.context import FileManager
from aunic.context.markers import analyze_chat_file
from aunic.context.structure import render_parsed_note_text
from aunic.context.types import FileSnapshot, ParseWarning
from aunic.domain import ProviderRequest, ProviderResponse, TranscriptRow, UsageLogEntry
from aunic.loop.dispatch import next_run_log_row_number, process_generated_rows, tool_result_message
from aunic.errors import ChatModeError, ProviderError, StructuredOutputError
from aunic.image_inputs import prepare_image_inputs_from_paths
from aunic.loop.types import LoopEvent, ToolFailure
from aunic.mcp.tools import build_mcp_tool_registry, merge_tool_registries
from aunic.modes.types import ChatModeMetrics, ChatModeRunRequest, ChatModeRunResult
from aunic.progress import ProgressEvent, emit_progress, progress_from_loop_event
from aunic.research import FetchService, ResearchState, SearchService, find_invalid_citation_urls
from aunic.tasks import get_active_task_label
from aunic.tools import RunToolContext, ToolSessionState, build_chat_tool_registry
from aunic.tools.memory_manifest import build_memory_manifest
from aunic.tools.base import ToolExecutionResult
from aunic.transcript.parser import parse_transcript_rows
from aunic.transcript.writer import append_transcript_row
from aunic.usage import build_usage_log, format_usage_brief
from aunic.usage_log import append_usage_record

CHAT_MODE_SYSTEM_PROMPT = "\n".join(
    [
        "You are operating inside Aunic chat mode.",
        "Use at most one tool call per turn.",
        "Prefer dedicated tools over bash whenever a dedicated tool can do the job.",
        "Prefer the sleep tool over bash sleep when the next useful action is simply waiting; do not sleep instead of answering when you already have enough information.",
        "Use stop_process to stop Aunic-owned background commands by background_id; it cannot stop arbitrary system processes.",
        "If you already have enough information, reply with normal markdown and no tool call.",
        "If web-backed information informed the answer, use inline markdown links for citations.",
        "Do not emit note-editing tools, freeform JSON envelopes, or extra workflow assumptions.",
    ]
)

CHAT_MODE_FINAL_RESPONSE_PROMPT = (
    "Research turn budget is exhausted. No tools are available now. "
    "Reply with the best final markdown answer you can from the information already gathered. "
    "If uncertainty remains, say so plainly."
)


@dataclass
class _ChatLoopState:
    """Mutable state accumulated across turns in a chat-mode run."""

    run_log: list[TranscriptRow]
    events: list[LoopEvent]
    tool_failures: list[ToolFailure]
    usage_entries: list[UsageLogEntry]
    assistant_message_patches: list[dict[str, object]]
    counted_turns: int = 0
    malformed_repair_count: int = 0
    citation_repair_count: int = 0
    force_final_response: bool = False
    error_message: str | None = None
    provider_metadata: dict[str, object] = field(default_factory=dict)
    provider_response_index: int = 0

    def append_run_log_message(self, role: str, content: str) -> None:
        self.run_log.append(
            TranscriptRow(
                row_number=next_run_log_row_number(self.run_log),
                role=role,  # type: ignore[arg-type]
                type="message",
                content=content,
            )
        )

    def append_assistant_message_patch(
        self,
        patch: dict[str, object] | None,
        metadata: dict[str, object],
    ) -> None:
        if patch is None:
            return
        self.assistant_message_patches.append(dict(patch))
        limit = metadata.get("reasoning_replay_turns")
        if isinstance(limit, int) and limit > 0:
            del self.assistant_message_patches[:-limit]


@dataclass(frozen=True)
class _ChatContextResult:
    file_snapshots: tuple[FileSnapshot, ...]
    warnings: tuple[ParseWarning, ...]
    parsed_note_text: str
    model_input_text: str
    transcript_rows: list[TranscriptRow] | None = None
    note_snapshot_text: str = ""


class ChatModeRunner:
    def __init__(
        self,
        file_manager: FileManager | None = None,
        search_service: SearchService | None = None,
        fetch_service: FetchService | None = None,
    ) -> None:
        self._file_manager = file_manager or FileManager()
        self._search_service = search_service or SearchService()
        self._fetch_service = fetch_service or FetchService()
        self._active_file: Path | None = None
        self._progress_sink = None
        self._session_state = ToolSessionState()

    async def run(self, request: ChatModeRunRequest) -> ChatModeRunResult:
        prompt = request.user_prompt
        if not prompt.strip():
            raise ChatModeError("Chat mode requires a non-empty prompt.")
        if request.total_turn_budget < 0:
            raise ChatModeError("Chat mode requires a non-negative total turn budget.")

        run_metadata = dict(request.metadata)
        run_session_id = str(run_metadata.get("run_session_id") or uuid4().hex)
        run_metadata["run_session_id"] = run_session_id
        mcp_registry = None

        async def _close_mcp_registry_once() -> None:
            nonlocal mcp_registry
            if mcp_registry is None:
                return
            registry_to_close = mcp_registry
            mcp_registry = None
            await registry_to_close.aclose()

        try:
            self._active_file = request.active_file
            self._progress_sink = request.progress_sink
            await emit_progress(
                request.progress_sink,
                ProgressEvent(
                    kind="run_started",
                    message="Starting chat-mode run.",
                    path=request.active_file,
                    details={"mode": "chat"},
                ),
            )
            await self._write_transcript_row("user", "message", None, None, prompt)
            await emit_progress(
                request.progress_sink,
                ProgressEvent(
                    kind="prompt_submitted",
                    message="Appended chat prompt and started run.",
                    path=request.active_file,
                    details={"prompt": prompt},
                ),
            )
            await emit_progress(
                request.progress_sink,
                ProgressEvent(
                    kind="file_written",
                    message="Wrote prompt transcript to the active file.",
                    path=request.active_file,
                    details={"reason": "chat_prompt_append"},
                ),
            )

            context_result = await self._build_context(request)
            persistent_images = await prepare_image_inputs_from_paths(
                request.included_image_files,
                persistent=True,
            )
            research_state = ResearchState()
            runtime = await RunToolContext.create(
                file_manager=self._file_manager,
                context_result=None,
                prompt_run=None,
                active_file=request.active_file,
                session_state=self._session_state,
                search_service=self._search_service,
                fetch_service=self._fetch_service,
                research_state=research_state,
                progress_sink=request.progress_sink,
                work_mode=request.work_mode,
                permission_handler=request.permission_handler,
                metadata=dict(run_metadata),
                active_plan_id=request.active_plan_id,
                active_plan_path=request.active_plan_path,
                planning_status=request.planning_status,
            )
            mcp_registry = await build_mcp_tool_registry(runtime.cwd)
            for error in mcp_registry.errors:
                await emit_progress(
                    request.progress_sink,
                    ProgressEvent(
                        kind="error",
                        message=error.message,
                        path=request.active_file,
                        details={
                            "source": "mcp",
                            "server_name": error.server_name,
                            "path": str(error.path) if error.path else None,
                        },
                    ),
                )
            tool_registry = merge_tool_registries(
                build_chat_tool_registry(work_mode=request.work_mode, project_root=runtime.cwd),
                mcp_registry.tools,
            )
            tool_map = {definition.spec.name: definition for definition in tool_registry}

            state = _ChatLoopState(
                run_log=list(context_result.transcript_rows or []),
                events=[],
                tool_failures=[],
                usage_entries=[],
                assistant_message_patches=[],
                force_final_response=request.total_turn_budget == 0,
            )
            current_user_prompt_text = (
                CHAT_MODE_FINAL_RESPONSE_PROMPT if state.force_final_response else prompt
            )

            while True:
                if state.force_final_response and state.counted_turns > request.total_turn_budget:
                    break

                provider_request = ProviderRequest(
                    messages=[],
                    transcript_messages=list(state.run_log),
                    assistant_message_patches=list(state.assistant_message_patches),
                    note_snapshot=context_result.note_snapshot_text or runtime.note_snapshot_text() or None,
                    user_prompt=current_user_prompt_text or None,
                    persistent_images=list(persistent_images),
                    prompt_images=list(request.prompt_images) if state.counted_turns == 0 else [],
                    tools=[] if state.force_final_response else [definition.spec for definition in tool_registry],
                    system_prompt=_build_chat_system_prompt(
                        work_mode=request.work_mode,
                        registry=tool_registry,
                        protected_paths=runtime.note_scope_paths(),
                    ),
                    model=request.model,
                    reasoning_effort=request.reasoning_effort,
                    metadata={
                        **dict(run_metadata),
                        "active_file": str(runtime.active_file),
                        "mode": "chat",
                        "work_mode": request.work_mode,
                    },
                )
                chat_details: dict[str, object] = {
                    "messages": len(provider_request.messages),
                    "tools": len(provider_request.tools),
                    "final_response_only": state.force_final_response,
                }
                chat_task_label: str | None
                try:
                    chat_task_label = get_active_task_label(runtime.active_file)
                except Exception:
                    chat_task_label = None
                if chat_task_label:
                    chat_details["active_task_label"] = chat_task_label
                state.events.append(
                    loop_event := LoopEvent(
                        kind="provider_request",
                        message="Sent chat-mode turn to provider.",
                        details=chat_details,
                    )
                )
                await emit_progress(
                    request.progress_sink,
                    progress_from_loop_event(loop_event, path=request.active_file),
                )

                try:
                    response = await request.provider.generate(provider_request)
                except StructuredOutputError as exc:
                    state.malformed_repair_count += 1
                    state.error_message = str(exc)
                    state.tool_failures.append(
                        ToolFailure(
                            category="malformed_turn",
                            reason="structured_output",
                            tool_name=None,
                            message=str(exc),
                        )
                    )
                    current_user_prompt_text = _chat_repair_prompt(
                        str(exc),
                        final_only=state.force_final_response,
                    )
                    state.append_run_log_message("user", current_user_prompt_text)
                    if state.malformed_repair_count >= SETTINGS.loop.malformed_turn_limit:
                        await _close_mcp_registry_once()
                        return await self._result_with_error(
                            context_result=context_result,
                            request=request,
                            stop_reason="malformed_turn_limit",
                            state=state,
                            research_state=research_state,
                        )
                    continue
                except ProviderError as exc:
                    state.error_message = str(exc)
                    await _close_mcp_registry_once()
                    return await self._result_with_error(
                        context_result=context_result,
                        request=request,
                        stop_reason="provider_error",
                        state=state,
                        research_state=research_state,
                    )

                state.provider_metadata = dict(response.provider_metadata)
                state.provider_response_index += 1
                state.usage_entries.append(
                    UsageLogEntry(
                        index=state.provider_response_index,
                        stage="chat",
                        usage=response.usage,
                        provider=response.provider_metadata.get("provider"),
                        model=response.provider_metadata.get("model"),
                        finish_reason=response.finish_reason,
                        metadata=dict(response.provider_metadata),
                    )
                )
                state.events.append(
                    loop_event := LoopEvent(
                        kind="provider_response",
                        message=f"Chat provider response: {format_usage_brief(response.usage)}.",
                        details={
                            "stage": "chat",
                            "usage": response.usage.__dict__ if response.usage else None,
                            "finish_reason": response.finish_reason,
                            "provider": response.provider_metadata.get("provider"),
                            "model": response.provider_metadata.get("model"),
                        },
                    )
                )
                await emit_progress(
                    request.progress_sink,
                    progress_from_loop_event(loop_event, path=request.active_file),
                )

                async def _on_tool_event(event: LoopEvent) -> None:
                    state.events.append(event)
                    await emit_progress(
                        request.progress_sink,
                        progress_from_loop_event(event, path=request.active_file),
                    )

                generated_result = await process_generated_rows(
                    generated_rows=response.generated_rows,
                    run_log=state.run_log,
                    write_row=self._write_transcript_row,
                    tool_map=tool_map,
                    on_tool_event=_on_tool_event,
                    write_message_rows=True,
                )
                state.tool_failures.extend(generated_result.tool_failures)
                state.counted_turns += generated_result.valid_turns

                if response.tool_calls:
                    malformed_message = _validate_chat_provider_response(
                        response,
                        tool_map=tool_map,
                        final_only=state.force_final_response,
                    )
                    if malformed_message is not None:
                        state.malformed_repair_count += 1
                        state.error_message = malformed_message
                        state.tool_failures.append(
                            ToolFailure(
                                category="malformed_turn",
                                reason="invalid_provider_response",
                                tool_name=response.tool_calls[0].name if response.tool_calls else None,
                                message=malformed_message,
                            )
                        )
                        state.append_run_log_message("assistant", response.text.strip() or "(empty response)")
                        state.append_assistant_message_patch(response.assistant_message_patch, response.provider_metadata)
                        current_user_prompt_text = _chat_repair_prompt(
                            malformed_message,
                            final_only=state.force_final_response,
                        )
                        state.append_run_log_message("user", current_user_prompt_text)
                        if state.malformed_repair_count >= SETTINGS.loop.malformed_turn_limit:
                            await _close_mcp_registry_once()
                            return await self._result_with_error(
                                context_result=context_result,
                                request=request,
                                stop_reason="malformed_turn_limit",
                                state=state,
                                research_state=research_state,
                            )
                        continue

                    tool_call = response.tool_calls[0]
                    definition = tool_map[tool_call.name]
                    try:
                        parsed_args = definition.parse_arguments(tool_call.arguments)
                    except ValueError as exc:
                        state.malformed_repair_count += 1
                        state.error_message = str(exc)
                        state.tool_failures.append(
                            ToolFailure(
                                category="malformed_turn",
                                reason="invalid_arguments",
                                tool_name=tool_call.name,
                                message=str(exc),
                            )
                        )
                        state.append_run_log_message("assistant", response.text.strip() or "(empty response)")
                        state.append_assistant_message_patch(response.assistant_message_patch, response.provider_metadata)
                        current_user_prompt_text = _chat_repair_prompt(
                            str(exc),
                            final_only=state.force_final_response,
                        )
                        state.append_run_log_message("user", current_user_prompt_text)
                        if state.malformed_repair_count >= SETTINGS.loop.malformed_turn_limit:
                            await _close_mcp_registry_once()
                            return await self._result_with_error(
                                context_result=context_result,
                                request=request,
                                stop_reason="malformed_turn_limit",
                                state=state,
                                research_state=research_state,
                            )
                        continue

                    state.malformed_repair_count = 0
                    result = await definition.execute(runtime, parsed_args)
                    if definition.persistence == "persistent":
                        row_number = await self._write_transcript_row(
                            "assistant",
                            "tool_call",
                            tool_call.name,
                            tool_call.id,
                            tool_call.arguments,
                        )
                    else:
                        row_number = next_run_log_row_number(state.run_log)
                    state.run_log.append(
                        TranscriptRow(
                            row_number=row_number,
                            role="assistant",
                            type="tool_call",
                            tool_name=tool_call.name,
                            tool_id=tool_call.id,
                            content=tool_call.arguments,
                        )
                    )
                    state.append_assistant_message_patch(response.assistant_message_patch, response.provider_metadata)
                    transcript_content = (
                        result.in_memory_content
                        if result.transcript_content is None
                        else result.transcript_content
                    )
                    row_type = "tool_error" if result.status != "completed" else "tool_result"
                    if definition.persistence == "persistent":
                        row_number = await self._write_transcript_row(
                            "tool",
                            row_type,
                            tool_call.name,
                            tool_call.id,
                            transcript_content,
                        )
                    else:
                        row_number = next_run_log_row_number(state.run_log)
                    state.run_log.append(
                        TranscriptRow(
                            row_number=row_number,
                            role="tool",
                            type=row_type,  # type: ignore[arg-type]
                            tool_name=tool_call.name,
                            tool_id=tool_call.id,
                            content=result.in_memory_content,
                        )
                    )
                    state.events.append(
                        loop_event := LoopEvent(
                            kind="tool_result",
                            message=tool_result_message(tool_call.name, result.in_memory_content),
                            details={"tool_name": tool_call.name, "status": result.status},
                        )
                    )
                    await emit_progress(
                        request.progress_sink,
                        progress_from_loop_event(loop_event, path=request.active_file),
                    )
                    if result.tool_failure is not None:
                        state.tool_failures.append(result.tool_failure)
                    state.counted_turns += 1
                    current_user_prompt_text = prompt
                    if state.counted_turns >= request.total_turn_budget and not state.force_final_response:
                        state.force_final_response = True
                        current_user_prompt_text = CHAT_MODE_FINAL_RESPONSE_PROMPT
                    continue

                if not response.text.strip():
                    state.malformed_repair_count += 1
                    state.error_message = "Chat mode received an empty assistant response."
                    state.tool_failures.append(
                        ToolFailure(
                            category="malformed_turn",
                            reason="empty_response",
                            tool_name=None,
                            message=state.error_message,
                        )
                    )
                    state.append_run_log_message("assistant", "(empty response)")
                    state.append_assistant_message_patch(response.assistant_message_patch, response.provider_metadata)
                    current_user_prompt_text = _chat_repair_prompt(
                        state.error_message,
                        final_only=state.force_final_response,
                    )
                    state.append_run_log_message("user", current_user_prompt_text)
                    if state.malformed_repair_count >= SETTINGS.loop.malformed_turn_limit:
                        await _close_mcp_registry_once()
                        return await self._result_with_error(
                            context_result=context_result,
                            request=request,
                            stop_reason="malformed_turn_limit",
                            state=state,
                            research_state=research_state,
                        )
                    continue

                if research_state.known_citation_urls():
                    invalid_urls = find_invalid_citation_urls(
                        response.text,
                        allowed_canonical_urls=research_state.known_citation_urls(),
                    )
                    if invalid_urls:
                        state.malformed_repair_count += 1
                        state.citation_repair_count += 1
                        state.error_message = (
                            "Inline citations must come from the current turn's search or fetch sources. "
                            f"Invalid URLs: {', '.join(invalid_urls)}"
                        )
                        state.tool_failures.append(
                            ToolFailure(
                                category="validation_error",
                                reason="invalid_citation",
                                tool_name=None,
                                message=state.error_message,
                            )
                        )
                        state.append_run_log_message("assistant", response.text.strip() or "(empty response)")
                        state.append_assistant_message_patch(response.assistant_message_patch, response.provider_metadata)
                        current_user_prompt_text = _citation_repair_prompt(invalid_urls)
                        state.append_run_log_message("user", current_user_prompt_text)
                        if state.malformed_repair_count >= SETTINGS.loop.malformed_turn_limit:
                            await _close_mcp_registry_once()
                            return await self._result_with_error(
                                context_result=context_result,
                                request=request,
                                stop_reason="malformed_turn_limit",
                                state=state,
                                research_state=research_state,
                            )
                        continue

                row_number = await self._write_transcript_row("assistant", "message", None, None, response.text)
                if row_number is not None:
                    state.run_log.append(
                        TranscriptRow(
                            row_number=row_number,
                            role="assistant",
                            type="message",
                            content=response.text,
                        )
                    )
                state.append_assistant_message_patch(response.assistant_message_patch, response.provider_metadata)
                final_snapshots = await self._refresh_snapshots(context_result.file_snapshots)
                result = ChatModeRunResult(
                    initial_warnings=context_result.warnings,
                    response_text=response.text,
                    assistant_response_appended=True,
                    final_file_snapshots=final_snapshots,
                    stop_reason="finished",
                    metrics=ChatModeMetrics(
                        valid_turn_count=state.counted_turns,
                        malformed_repair_count=state.malformed_repair_count,
                        citation_repair_count=state.citation_repair_count,
                        stop_reason="finished",
                    ),
                    events=tuple(state.events),
                    tool_failures=tuple(state.tool_failures),
                    research_summary=research_state.summary(),
                    provider_metadata=state.provider_metadata,
                    error_message=None,
                    usage_log=build_usage_log(state.usage_entries),
                    usage_log_path=self._persist_usage_log(
                        request=request,
                        usage_entries=state.usage_entries,
                        stop_reason="finished",
                        response_text=response.text,
                        assistant_response_appended=True,
                    ),
                )
                await emit_progress(
                    request.progress_sink,
                    ProgressEvent(
                        kind="run_finished",
                        message=(
                            "Chat-mode run finished: finished. "
                            f"{format_usage_brief(result.usage_log.total)}."
                        ),
                        path=request.active_file,
                        details={
                            "stop_reason": "finished",
                            "usage": result.usage_log.total.__dict__ if result.usage_log.total else None,
                        },
                    ),
                )
                await _close_mcp_registry_once()
                return result
        finally:
            await _close_mcp_registry_once()
            close_run = getattr(request.provider, "close_run", None)
            if callable(close_run):
                await close_run(run_session_id)

    async def _build_context(self, request: ChatModeRunRequest) -> _ChatContextResult:
        snapshots = await self._file_manager.read_working_set(
            request.active_file,
            request.included_files,
        )
        active_root = request.display_root
        if active_root is None:
            active_root = request.active_file.expanduser().resolve().parent
        display_root = active_root.expanduser().resolve()
        analyses = tuple(
            analyze_chat_file(snapshot, _display_path(snapshot.path, display_root))
            for snapshot in snapshots
        )
        warnings = tuple(
            warning
            for analysis in analyses
            for warning in analysis.parsed_file.warnings
        )
        parsed_note_text = render_parsed_note_text(analyses)
        raw_transcript = analyses[0].parsed_file.transcript_text if analyses else None
        transcript_rows = parse_transcript_rows(raw_transcript) if raw_transcript else None
        model_input_text = _assemble_chat_model_input(
            request.user_prompt,
            parsed_note_text,
        )
        return _ChatContextResult(
            file_snapshots=snapshots,
            warnings=warnings,
            parsed_note_text=parsed_note_text,
            model_input_text=model_input_text,
            transcript_rows=transcript_rows,
            note_snapshot_text=parsed_note_text,
        )

    async def _refresh_snapshots(
        self,
        snapshots: tuple[FileSnapshot, ...],
    ) -> tuple[FileSnapshot, ...]:
        return tuple(
            [await self._file_manager.read_snapshot(snapshot.path) for snapshot in snapshots]
        )

    async def _write_transcript_row(
        self,
        role: str,
        row_type: str,
        tool_name: str | None,
        tool_id: str | None,
        content: object,
    ) -> int | None:
        if self._active_file is None:
            return None
        snapshot = await self._file_manager.read_snapshot(self._active_file)
        updated_text, row_number = append_transcript_row(
            snapshot.raw_text,
            role,  # type: ignore[arg-type]
            row_type,  # type: ignore[arg-type]
            tool_name,
            tool_id,
            content,
        )
        written = await self._file_manager.write_text(
            self._active_file,
            updated_text,
            expected_revision=snapshot.revision_id,
        )
        await emit_progress(
            self._progress_sink,
            ProgressEvent(
                kind="file_written",
                message="Updated the active file transcript.",
                path=self._active_file,
                details={
                    "reason": "transcript_row_append",
                    "revision_id": written.revision_id,
                    "row_number": row_number,
                    "role": role,
                    "type": row_type,
                },
            ),
        )
        return row_number

    async def _result_with_error(
        self,
        *,
        context_result: _ChatContextResult,
        request: ChatModeRunRequest,
        stop_reason: str,
        state: _ChatLoopState,
        research_state: ResearchState,
    ) -> ChatModeRunResult:
        final_snapshots = await self._refresh_snapshots(context_result.file_snapshots)
        result = ChatModeRunResult(
            initial_warnings=context_result.warnings,
            response_text="",
            assistant_response_appended=False,
            final_file_snapshots=final_snapshots,
            stop_reason=stop_reason,
            metrics=ChatModeMetrics(
                valid_turn_count=state.counted_turns,
                malformed_repair_count=state.malformed_repair_count,
                citation_repair_count=state.citation_repair_count,
                stop_reason=stop_reason,
            ),
            events=tuple(state.events),
            tool_failures=tuple(state.tool_failures),
            research_summary=research_state.summary(),
            provider_metadata=state.provider_metadata,
            error_message=state.error_message,
            usage_log=build_usage_log(state.usage_entries),
            usage_log_path=self._persist_usage_log(
                request=request,
                usage_entries=state.usage_entries,
                stop_reason=stop_reason,
                response_text="",
                assistant_response_appended=False,
            ),
        )
        await emit_progress(
            request.progress_sink,
            ProgressEvent(
                kind="run_finished",
                message=f"Chat-mode run finished: {stop_reason}. {format_usage_brief(result.usage_log.total)}.",
                path=request.active_file,
                details={
                    "stop_reason": stop_reason,
                    "error_message": state.error_message,
                    "usage": result.usage_log.total.__dict__ if result.usage_log.total else None,
                },
            ),
        )
        return result

    def _persist_usage_log(
        self,
        *,
        request: ChatModeRunRequest,
        usage_entries: list[UsageLogEntry],
        stop_reason: str,
        response_text: str,
        assistant_response_appended: bool,
    ) -> str | None:
        try:
            cwd = Path(str(request.metadata.get("cwd", Path.cwd()))).expanduser().resolve()
            usage_log = build_usage_log(usage_entries)
            path = append_usage_record(
                cwd,
                {
                    "logged_at": datetime.now().astimezone().isoformat(),
                    "mode": "chat",
                    "active_file": str(request.active_file.resolve()),
                    "included_files": [str(path.resolve()) for path in request.included_files],
                    "user_prompt": request.user_prompt,
                    "provider": request.provider.name,
                    "model": request.model,
                    "reasoning_effort": request.reasoning_effort,
                    "stop_reason": stop_reason,
                    "assistant_response_appended": assistant_response_appended,
                    "response_text": response_text,
                    "usage_log": {
                        "entries": [
                            {
                                "index": entry.index,
                                "stage": entry.stage,
                                "provider": entry.provider,
                                "model": entry.model,
                                "finish_reason": entry.finish_reason,
                                "usage": entry.usage.__dict__ if entry.usage else None,
                                "metadata": dict(entry.metadata),
                            }
                            for entry in usage_log.entries
                        ],
                        "total": usage_log.total.__dict__ if usage_log.total else None,
                    },
                },
            )
            return str(path)
        except Exception:
            return None


def _assemble_chat_model_input(prompt_text: str, parsed_note_text: str) -> str:
    return (
        "CURRENT USER PROMPT\n"
        f"{prompt_text}\n\n"
        "PARSED NOTE TEXT\n"
        f"{parsed_note_text}"
    ).strip()


def _display_path(path: Path, display_root: Path) -> str:
    try:
        return str(path.relative_to(display_root))
    except ValueError:
        return str(path)


def _validate_chat_provider_response(
    response: ProviderResponse,
    *,
    tool_map: dict[str, object],
    final_only: bool,
) -> str | None:
    if final_only:
        return "No tools are available now. Provide a final markdown response."
    if len(response.tool_calls) != 1:
        return "Return exactly one tool call or a final plain response."
    tool_call = response.tool_calls[0]
    if tool_call.name not in tool_map:
        return f"Unknown tool {tool_call.name!r}. Use one of the available tools."
    if response.text.strip():
        return "Do not mix a tool call with a final plain response in the same turn."
    return None


def _chat_repair_prompt(message: str, *, final_only: bool) -> str:
    if final_only:
        return (
            "The previous response was invalid for Aunic chat mode.\n"
            f"Problem: {message}\n"
            "Reply again with normal markdown only and no tool call."
        )
    return (
        "The previous response was invalid for Aunic chat mode.\n"
        f"Problem: {message}\n"
        "Reply again with either exactly one valid tool call or a final plain markdown response."
    )


def _citation_repair_prompt(invalid_urls: tuple[str, ...]) -> str:
    return (
        "The previous response used inline citations that were not returned by this turn's "
        "search or fetch results.\n"
        f"Invalid URLs: {', '.join(invalid_urls)}\n"
        "Reply again with corrected inline citations or with no unsupported citations."
    )


def _build_chat_system_prompt(
    *,
    work_mode: str,
    registry: tuple[object, ...],
    protected_paths: tuple[Path, ...],
) -> str:
    tool_names = ", ".join(definition.spec.name for definition in registry)
    parts = [
        CHAT_MODE_SYSTEM_PROMPT,
        f"Current work mode: {work_mode}.",
        f"Available tools: {tool_names or 'none'}.",
    ]
    manifest = build_memory_manifest(registry)
    if manifest:
        parts.append(manifest)
    if work_mode != "work":
        parts.append("Do not try to mutate files in this work mode.")
    if protected_paths:
        joined = "\n".join(f"- {path}" for path in protected_paths)
        parts.append(
            "Protected note-content path(s). Do not use work-mode edit/write/bash to mutate them.\n"
            f"{joined}"
        )
    return "\n\n".join(part for part in parts if part.strip())
