from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from aunic.context import ContextBuildRequest, ContextEngine, FileManager
from aunic.context.types import FileSnapshot
from aunic.context.types import PromptRun
from aunic.errors import NoteModeError
from aunic.loop import LoopRunRequest, ToolLoop
from aunic.modes.synthesis import SynthesisPassResult, run_synthesis_pass, work_read_tools_were_used
from aunic.modes.types import NoteModePromptResult, NoteModeRunRequest, NoteModeRunResult
from aunic.progress import ProgressEvent, emit_progress, ensure_async_progress_sink
from aunic.transcript.parser import split_note_and_transcript
from aunic.usage import combine_usage_logs, format_usage_brief
from aunic.usage_log import append_usage_record


class NoteModeRunner:
    def __init__(
        self,
        context_engine: ContextEngine | None = None,
        tool_loop: ToolLoop | None = None,
        file_manager: FileManager | None = None,
    ) -> None:
        self._context_engine = context_engine or ContextEngine()
        self._tool_loop = tool_loop or ToolLoop()
        self._file_manager = file_manager or FileManager()

    async def run(self, request: NoteModeRunRequest) -> NoteModeRunResult:
        if not request.user_prompt.strip():
            raise NoteModeError("Direct note mode requires a non-empty prompt.")
        run_metadata = dict(request.metadata)
        run_session_id = str(run_metadata.get("run_session_id") or uuid4().hex)
        run_metadata["run_session_id"] = run_session_id

        try:
            await emit_progress(
                request.progress_sink,
                ProgressEvent(
                    kind="run_started",
                    message="Starting note-mode run.",
                    path=request.active_file,
                    details={"mode": "direct"},
                ),
            )

            context_result = await self._context_engine.build_context(
                ContextBuildRequest(
                    active_file=request.active_file,
                    included_files=request.included_files,
                    user_prompt=request.user_prompt,
                    total_turn_budget=request.total_turn_budget,
                    display_root=request.display_root,
                )
            )
            if not context_result.prompt_runs:
                raise NoteModeError("Note mode requires a prompt run.")

            prompt_results: list[NoteModePromptResult] = []
            completed_prompt_runs = 0
            stop_reason = "finished"
            async_progress_sink = ensure_async_progress_sink(request.progress_sink)
            synthesis_result = SynthesisPassResult(ran=False)

            for prompt_run in context_result.prompt_runs:
                await emit_progress(
                    request.progress_sink,
                    ProgressEvent(
                        kind="prompt_submitted",
                        message=f"Submitting note prompt {prompt_run.index + 1}.",
                        path=request.active_file,
                        details={
                            "prompt_index": prompt_run.index,
                            "prompt_mode": prompt_run.mode,
                        },
                    ),
                )
                loop_result = await self._tool_loop.run(
                    LoopRunRequest(
                        provider=request.provider,
                        prompt_run=prompt_run,
                        context_result=context_result,
                        active_file=request.active_file,
                        included_files=request.included_files,
                        active_plan_id=request.active_plan_id,
                        active_plan_path=request.active_plan_path,
                        planning_status=request.planning_status,
                        model=request.model,
                        reasoning_effort=request.reasoning_effort,
                        display_root=request.display_root,
                        progress_sink=async_progress_sink,
                        metadata=dict(run_metadata),
                        work_mode=request.work_mode,
                        permission_handler=request.permission_handler,
                        persist_message_rows=False,
                    )
                )
                prompt_results.append(
                    NoteModePromptResult(
                        prompt_index=prompt_run.index,
                        prompt_run=prompt_run,
                        loop_result=loop_result,
                    )
                )
                if loop_result.stop_reason != "finished":
                    stop_reason = loop_result.stop_reason
                    break
                completed_prompt_runs += 1

            if (
                stop_reason == "finished"
                and request.work_mode in {"read", "work"}
                and prompt_results
            ):
                last_prompt_result = prompt_results[-1]
                last_loop_result = last_prompt_result.loop_result
                if work_read_tools_were_used(last_loop_result.events):
                    snapshot = await self._file_manager.read_snapshot(request.active_file)
                    note_text, _ = split_note_and_transcript(snapshot.raw_text)
                    await emit_progress(
                        request.progress_sink,
                        ProgressEvent(
                            kind="status",
                            message="Starting synthesis pass to update note-content.",
                            path=request.active_file,
                        ),
                    )
                    try:
                        synthesis_result = await run_synthesis_pass(
                            tool_loop=self._tool_loop,
                            provider=request.provider,
                            context_result=context_result,
                            prompt_run=last_prompt_result.prompt_run,
                            active_file=request.active_file,
                            included_files=request.included_files,
                            model=request.model,
                            reasoning_effort=request.reasoning_effort,
                            progress_sink=async_progress_sink,
                            metadata=dict(run_metadata),
                            note_snapshot_text=note_text,
                            run_log_rows=last_loop_result.run_log[last_loop_result.run_log_new_start :],
                            permission_handler=request.permission_handler,
                        )
                    except Exception as exc:
                        synthesis_result = SynthesisPassResult(
                            ran=True,
                            error_message=f"Synthesis pass failed: {exc}",
                        )
                        await emit_progress(
                            request.progress_sink,
                            ProgressEvent(
                                kind="error",
                                message=f"Synthesis pass failed: {exc}",
                                path=request.active_file,
                            ),
                        )

            completed_all_prompts = completed_prompt_runs == len(context_result.prompt_runs)
            final_snapshots = await self._refresh_snapshots(context_result.file_snapshots)
            usage_logs = [prompt_result.loop_result.usage_log for prompt_result in prompt_results]
            if synthesis_result.ran:
                usage_logs.append(synthesis_result.usage_log)
            usage_log = combine_usage_logs(usage_logs)
            usage_log_path = self._persist_usage_log(
                request=request,
                result_usage_log=usage_log,
                prompt_results=tuple(prompt_results),
                completed_prompt_runs=completed_prompt_runs,
                stop_reason=stop_reason,
                synthesis_result=synthesis_result,
            )
            result = NoteModeRunResult(
                initial_warnings=context_result.warnings,
                prompt_results=tuple(prompt_results),
                completed_prompt_runs=completed_prompt_runs,
                completed_all_prompts=completed_all_prompts,
                final_file_snapshots=final_snapshots,
                stop_reason=stop_reason,
                synthesis_loop_result=synthesis_result.loop_result,
                synthesis_ran=synthesis_result.ran,
                synthesis_error=synthesis_result.error_message,
                usage_log=usage_log,
                usage_log_path=usage_log_path,
            )
            confirmation_text = _latest_note_mode_confirmation(prompt_results)
            finish_parts = [confirmation_text or f"Note-mode run finished: {stop_reason}."]
            if synthesis_result.ran:
                if synthesis_result.error_message:
                    finish_parts.append(f"Synthesis error: {synthesis_result.error_message}")
                else:
                    finish_parts.append("Synthesis complete.")
            finish_parts.append(format_usage_brief(usage_log.total) + ".")
            await emit_progress(
                request.progress_sink,
                ProgressEvent(
                    kind="run_finished",
                    message=" ".join(finish_parts),
                    path=request.active_file,
                    details={
                        "stop_reason": stop_reason,
                        "completed_prompt_runs": completed_prompt_runs,
                        "completed_all_prompts": completed_all_prompts,
                        "confirmation_text": confirmation_text,
                        "synthesis_ran": synthesis_result.ran,
                        "synthesis_error": synthesis_result.error_message,
                        "usage": usage_log.total.__dict__ if usage_log.total else None,
                    },
                ),
            )
            return result
        finally:
            close_run = getattr(request.provider, "close_run", None)
            if callable(close_run):
                await close_run(run_session_id)

    def _persist_usage_log(
        self,
        *,
        request: NoteModeRunRequest,
        result_usage_log,
        prompt_results: tuple[NoteModePromptResult, ...],
        completed_prompt_runs: int,
        stop_reason: str,
        synthesis_result: SynthesisPassResult,
    ) -> str | None:
        try:
            cwd = Path(str(request.metadata.get("cwd", Path.cwd()))).expanduser().resolve()
            path = append_usage_record(
                cwd,
                {
                    "logged_at": datetime.now().astimezone().isoformat(),
                    "mode": "note",
                    "active_file": str(request.active_file.resolve()),
                    "included_files": [str(path.resolve()) for path in request.included_files],
                    "prompt_mode": "direct",
                    "user_prompt": request.user_prompt,
                    "provider": request.provider.name,
                    "model": request.model,
                    "reasoning_effort": request.reasoning_effort,
                    "stop_reason": stop_reason,
                    "completed_prompt_runs": completed_prompt_runs,
                    "synthesis_ran": synthesis_result.ran,
                    "synthesis_error": synthesis_result.error_message,
                    "usage_log": {
                        "entries": [
                            {
                                "prompt_index": prompt_result.prompt_index,
                                "prompt_text": prompt_result.prompt_run.prompt_text,
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
                                        for entry in prompt_result.loop_result.usage_log.entries
                                    ],
                                    "total": (
                                        prompt_result.loop_result.usage_log.total.__dict__
                                        if prompt_result.loop_result.usage_log.total
                                        else None
                                    ),
                                },
                                "events": [
                                    {"kind": e.kind, "message": e.message, "details": e.details}
                                    for e in prompt_result.loop_result.events
                                    if e.kind in ("provider_response", "malformed_turn")
                                ],
                            }
                            for prompt_result in prompt_results
                        ],
                        "total": result_usage_log.total.__dict__ if result_usage_log.total else None,
                    },
                },
            )
            return str(path)
        except Exception:
            return None

    async def _refresh_snapshots(
        self,
        snapshots: Iterable[FileSnapshot],
    ) -> tuple[FileSnapshot, ...]:
        refreshed = [
            await self._file_manager.read_snapshot(snapshot.path)
            for snapshot in snapshots
        ]
        return tuple(refreshed)


def _latest_note_mode_confirmation(
    prompt_results: list[NoteModePromptResult],
) -> str | None:
    for prompt_result in reversed(prompt_results):
        for event in reversed(prompt_result.loop_result.events):
            if event.kind != "stop":
                continue
            assistant_text = event.details.get("assistant_text")
            if isinstance(assistant_text, str) and assistant_text.strip():
                return assistant_text.strip()
            tool_name = event.details.get("tool_name")
            if tool_name in {"note_edit", "note_write"}:
                return "Note updated."
    return None
