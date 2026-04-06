from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from typing import Sequence

from aunic.context import ContextBuildRequest, ContextEngine, FileManager
from aunic.context.markers import warning_to_dict
from aunic.errors import ChatModeError, NoteModeError
from aunic.domain import HealthCheck, Message, ProviderRequest
from aunic.modes import ChatModeRunRequest, ChatModeRunner, NoteModeRunRequest, NoteModeRunner
from aunic.providers import (
    ClaudeProvider,
    CodexProvider,
    LlamaCppProvider,
    OllamaEmbeddingProvider,
    OpenAICompatibleProvider,
)
from aunic.tui import run_tui
from aunic.usage import usage_log_to_dict, usage_to_dict
from aunic.usage_log import append_usage_record


def main(argv: Sequence[str] | None = None) -> int:
    processed_argv, bare_tui_launch = _coerce_default_tui_argv(
        list(sys.argv[1:] if argv is None else argv)
    )
    parser = _build_parser()
    args = parser.parse_args(processed_argv)
    args.bare_tui_launch = bare_tui_launch
    return asyncio.run(_dispatch(args))


async def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "prompt":
        return await _run_prompt(args)
    if args.command == "doctor":
        return await _run_doctor(args)
    if args.command == "context":
        return await _run_context(args)
    if args.command == "note":
        return await _run_note(args)
    if args.command == "chat":
        return await _run_chat(args)
    if args.command == "tui":
        return await _run_tui(args)
    raise SystemExit(f"Unsupported command: {args.command}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aunic")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prompt_parser = subparsers.add_parser("prompt", help="Send a prompt to a provider.")
    prompt_parser.add_argument("prompt", help="Prompt text to send.")
    prompt_parser.add_argument(
        "--provider",
        choices=("codex", "openai_compatible", "llama", "claude"),
        default="codex",
        help="Provider to use for the prompt.",
    )
    prompt_parser.add_argument(
        "--system-prompt",
        default=None,
        help="Optional additional system guidance.",
    )
    prompt_parser.add_argument("--model", default=None, help="Override the model identifier.")
    prompt_parser.add_argument(
        "--reasoning-effort",
        choices=("none", "minimal", "low", "medium", "high", "xhigh"),
        default=None,
        help="Override the reasoning effort.",
    )
    prompt_parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=None,
        help="Maximum output tokens to request.",
    )
    prompt_parser.add_argument(
        "--cwd",
        default=os.getcwd(),
        help="Working directory metadata forwarded to providers.",
    )

    doctor_parser = subparsers.add_parser("doctor", help="Check provider availability.")
    doctor_parser.add_argument(
        "--provider",
        choices=("all", "codex", "openai_compatible", "llama", "claude", "embedding"),
        default="all",
        help="Limit doctor checks to a single provider.",
    )

    note_parser = subparsers.add_parser(
        "note",
        help="Run the Phase 4 backend note-mode flow.",
    )
    note_subparsers = note_parser.add_subparsers(dest="note_command", required=True)
    note_run_parser = note_subparsers.add_parser(
        "run",
        help="Build note context and execute the note-mode loop.",
    )
    note_run_parser.add_argument("active_file", help="Primary markdown file.")
    note_run_parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="Additional files to include in the working set.",
    )
    note_run_parser.add_argument(
        "--provider",
        choices=("codex", "openai_compatible", "llama", "claude"),
        default="codex",
        help="Provider to use for note mode.",
    )
    note_run_parser.add_argument(
        "--prompt",
        required=True,
        help="Direct note-mode prompt.",
    )
    note_run_parser.add_argument("--model", default=None, help="Override the model identifier.")
    note_run_parser.add_argument(
        "--reasoning-effort",
        choices=("none", "minimal", "low", "medium", "high", "xhigh"),
        default=None,
        help="Override the reasoning effort.",
    )
    note_run_parser.add_argument(
        "--cwd",
        default=os.getcwd(),
        help="Working directory metadata forwarded to the provider loop.",
    )
    note_run_parser.add_argument(
        "--display-root",
        default=None,
        help="Optional root used for relative file labels.",
    )
    note_run_parser.add_argument(
        "--total-turn-budget",
        type=int,
        default=8,
        help="Total turn budget used for direct note mode.",
    )

    chat_parser = subparsers.add_parser(
        "chat",
        help="Run the Phase 5 backend chat-mode flow.",
    )
    chat_subparsers = chat_parser.add_subparsers(dest="chat_command", required=True)
    chat_run_parser = chat_subparsers.add_parser(
        "run",
        help="Append a chat prompt to the note and append the assistant reply.",
    )
    chat_run_parser.add_argument("active_file", help="Primary markdown file.")
    chat_run_parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="Additional files to include in the working set.",
    )
    chat_run_parser.add_argument(
        "--provider",
        choices=("codex", "openai_compatible", "llama", "claude"),
        default="codex",
        help="Provider to use for chat mode.",
    )
    chat_run_parser.add_argument(
        "--prompt",
        required=True,
        help="Chat prompt to append and send.",
    )
    chat_run_parser.add_argument("--model", default=None, help="Override the model identifier.")
    chat_run_parser.add_argument(
        "--reasoning-effort",
        choices=("none", "minimal", "low", "medium", "high", "xhigh"),
        default=None,
        help="Override the reasoning effort.",
    )
    chat_run_parser.add_argument(
        "--cwd",
        default=os.getcwd(),
        help="Working directory metadata forwarded to the provider request.",
    )
    chat_run_parser.add_argument(
        "--display-root",
        default=None,
        help="Optional root used for relative file labels.",
    )
    chat_run_parser.add_argument(
        "--total-turn-budget",
        type=int,
        default=8,
        help="Maximum number of research tool turns before chat mode must answer.",
    )

    context_parser = subparsers.add_parser(
        "context",
        help="Inspect or watch the Phase 2 file and context engine.",
    )
    context_subparsers = context_parser.add_subparsers(
        dest="context_command",
        required=True,
    )

    inspect_parser = context_subparsers.add_parser(
        "inspect",
        help="Build and print the parsed note text and structural view.",
    )
    inspect_parser.add_argument("active_file", help="Primary markdown file.")
    inspect_parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="Additional files to include in the working set.",
    )
    inspect_parser.add_argument(
        "--user-prompt",
        default="",
        help="Prompt text for direct mode.",
    )
    inspect_parser.add_argument(
        "--total-turn-budget",
        type=int,
        default=8,
        help="Total turn budget used while building the direct prompt context.",
    )
    inspect_parser.add_argument(
        "--display-root",
        default=None,
        help="Optional root used for relative file labels.",
    )

    watch_parser = context_subparsers.add_parser(
        "watch",
        help="Watch the working set and print file-change batches.",
    )
    watch_parser.add_argument("active_file", help="Primary markdown file.")
    watch_parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="Additional files to include in the watch set.",
    )
    watch_parser.add_argument(
        "--max-events",
        type=int,
        default=0,
        help="Stop after this many change batches. Zero means no limit.",
    )
    watch_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="Optional timeout while waiting for the next change batch.",
    )

    tui_parser = subparsers.add_parser(
        "tui",
        help="Launch the prompt_toolkit terminal UI.",
    )
    tui_parser.add_argument("active_file", help="Primary markdown file.")
    tui_parser.add_argument(
        "-p",
        "--parents",
        action="store_true",
        help="Create missing parent directories on first save for a new file.",
    )
    tui_parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="Additional files to keep in the working set.",
    )
    tui_parser.add_argument(
        "--provider",
        choices=("codex", "openai_compatible", "llama", "claude"),
        default="codex",
        help="Initial provider to use.",
    )
    tui_parser.add_argument("--model", default=None, help="Override the initial model identifier.")
    tui_parser.add_argument(
        "--reasoning-effort",
        choices=("none", "minimal", "low", "medium", "high", "xhigh"),
        default=None,
        help="Override the reasoning effort.",
    )
    tui_parser.add_argument(
        "--display-root",
        default=None,
        help="Optional root used for relative file labels.",
    )
    tui_parser.add_argument(
        "--mode",
        choices=("note", "chat"),
        default="note",
        help="Initial output mode.",
    )
    tui_parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory metadata forwarded to providers.",
    )

    return parser


async def _run_prompt(args: argparse.Namespace) -> int:
    provider = _build_llm_provider(args.provider, cwd=args.cwd)
    request = ProviderRequest(
        messages=[Message(role="user", content=args.prompt)],
        system_prompt=args.system_prompt,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        max_output_tokens=args.max_output_tokens,
        metadata={"cwd": args.cwd},
    )
    response = await provider.generate(request)
    usage_log_path = _persist_prompt_usage_log(args, response)
    payload = {
        "provider": provider.name,
        "text": response.text,
        "tool_calls": [
            {"name": tool_call.name, "arguments": tool_call.arguments}
            for tool_call in response.tool_calls
        ],
        "finish_reason": response.finish_reason,
        "usage": usage_to_dict(response.usage),
        "usage_log_path": usage_log_path,
        "provider_metadata": response.provider_metadata,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


async def _run_doctor(args: argparse.Namespace) -> int:
    checks: list[HealthCheck] = []
    if args.provider in ("all", "codex"):
        checks.append(await CodexProvider().healthcheck())
    if args.provider in ("all", "openai_compatible", "llama"):
        if args.provider == "llama":
            checks.append(await LlamaCppProvider().healthcheck())
        else:
            checks.append(await OpenAICompatibleProvider(project_root=Path.cwd()).healthcheck())
    if args.provider in ("all", "claude"):
        checks.append(await ClaudeProvider().healthcheck())
    if args.provider in ("all", "embedding"):
        checks.append(await OllamaEmbeddingProvider().healthcheck())

    overall_ok = True
    for check in checks:
        status = "OK" if check.ok else "FAIL"
        print(f"[{status}] {check.provider}: {check.message}")
        if check.details:
            print(json.dumps(check.details, indent=2, sort_keys=True))
        overall_ok = overall_ok and check.ok

    return 0 if overall_ok else 1


async def _run_note(args: argparse.Namespace) -> int:
    if args.note_command == "run":
        return await _run_note_run(args)
    raise SystemExit(f"Unsupported note command: {args.note_command}")


async def _run_note_run(args: argparse.Namespace) -> int:
    provider = _build_llm_provider(args.provider, cwd=args.cwd)
    runner = NoteModeRunner()
    try:
        result = await runner.run(
            NoteModeRunRequest(
                active_file=Path(args.active_file),
                included_files=tuple(Path(item) for item in args.include),
                provider=provider,
                user_prompt=args.prompt,
                total_turn_budget=args.total_turn_budget,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                display_root=Path(args.display_root) if args.display_root else None,
                metadata={"cwd": args.cwd},
            )
        )
    except NoteModeError as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 1

    payload = {
        "active_file": str(Path(args.active_file).resolve()),
        "prompt_mode": "direct",
        "completed_prompt_runs": result.completed_prompt_runs,
        "completed_all_prompts": result.completed_all_prompts,
        "stop_reason": result.stop_reason,
        "synthesis_ran": result.synthesis_ran,
        "synthesis_error": result.synthesis_error,
        "usage_log": usage_log_to_dict(result.usage_log),
        "usage_log_path": result.usage_log_path,
        "initial_warnings": [warning_to_dict(item) for item in result.initial_warnings],
        "prompt_results": [_note_prompt_result_to_dict(item) for item in result.prompt_results],
        "final_file_snapshots": [_snapshot_to_dict(item) for item in result.final_file_snapshots],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


async def _run_chat(args: argparse.Namespace) -> int:
    if args.chat_command == "run":
        return await _run_chat_run(args)
    raise SystemExit(f"Unsupported chat command: {args.chat_command}")


async def _run_chat_run(args: argparse.Namespace) -> int:
    provider = _build_llm_provider(args.provider, cwd=args.cwd)
    runner = ChatModeRunner()
    try:
        result = await runner.run(
            ChatModeRunRequest(
                active_file=Path(args.active_file),
                included_files=tuple(Path(item) for item in args.include),
                provider=provider,
                user_prompt=args.prompt,
                total_turn_budget=args.total_turn_budget,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                display_root=Path(args.display_root) if args.display_root else None,
                metadata={"cwd": args.cwd},
            )
        )
    except ChatModeError as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 1

    payload = {
        "active_file": str(Path(args.active_file).resolve()),
        "response_text": result.response_text,
        "assistant_response_appended": result.assistant_response_appended,
        "stop_reason": result.stop_reason,
        "metrics": _chat_metrics_to_dict(result.metrics),
        "events": [_loop_event_to_dict(item) for item in result.events],
        "tool_failures": [_tool_failure_to_dict(item) for item in result.tool_failures],
        "research_summary": _research_summary_to_dict(result.research_summary),
        "usage_log": usage_log_to_dict(result.usage_log),
        "usage_log_path": result.usage_log_path,
        "provider_metadata": result.provider_metadata,
        "error_message": result.error_message,
        "initial_warnings": [warning_to_dict(item) for item in result.initial_warnings],
        "final_file_snapshots": [_snapshot_to_dict(item) for item in result.final_file_snapshots],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if result.stop_reason == "finished" else 1


async def _run_context(args: argparse.Namespace) -> int:
    if args.context_command == "inspect":
        return await _run_context_inspect(args)
    if args.context_command == "watch":
        return await _run_context_watch(args)
    raise SystemExit(f"Unsupported context command: {args.context_command}")


async def _run_tui(args: argparse.Namespace) -> int:
    try:
        active_file, allow_missing_active_file = _resolve_tui_active_file(
            args.active_file,
            create_missing_parents=args.parents,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    display_root = Path(args.display_root).expanduser().resolve() if args.display_root else None
    if display_root is None and getattr(args, "bare_tui_launch", False):
        display_root = active_file.parent

    cwd = Path(args.cwd).expanduser().resolve() if args.cwd else None
    if cwd is None:
        cwd = active_file.parent if getattr(args, "bare_tui_launch", False) else Path.cwd()

    return await run_tui(
        active_file=active_file,
        included_files=tuple(Path(item) for item in args.include),
        initial_provider=args.provider,
        initial_model=args.model,
        initial_profile_id="llama_addie" if args.provider == "llama" else None,
        reasoning_effort=args.reasoning_effort,
        display_root=display_root,
        initial_mode=args.mode,
        cwd=cwd,
        allow_missing_active_file=allow_missing_active_file,
        create_missing_parents_on_save=args.parents,
    )


async def _run_context_inspect(args: argparse.Namespace) -> int:
    engine = ContextEngine()
    request = ContextBuildRequest(
        active_file=Path(args.active_file),
        included_files=tuple(Path(item) for item in args.include),
        user_prompt=args.user_prompt,
        total_turn_budget=args.total_turn_budget,
        display_root=Path(args.display_root) if args.display_root else None,
    )
    result = await engine.build_context(request)
    payload = {
        "file_snapshots": [_snapshot_to_dict(item) for item in result.file_snapshots],
        "warnings": [warning_to_dict(item) for item in result.warnings],
        "prompt_runs": [_prompt_run_to_dict(item) for item in result.prompt_runs],
        "parsed_note_text": result.parsed_note_text,
        "target_map_text": result.target_map_text,
        "read_only_map_text": result.read_only_map_text,
        "model_input_text": result.model_input_text,
        "structural_nodes": [_structural_node_to_dict(item) for item in result.structural_nodes],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


async def _run_context_watch(args: argparse.Namespace) -> int:
    file_manager = FileManager()
    tracked = [Path(args.active_file), *(Path(item) for item in args.include)]
    snapshots = await file_manager.read_working_set(tracked[0], tracked[1:])
    current_revisions = {
        str(snapshot.path): snapshot.revision_id
        for snapshot in snapshots
    }
    print(
        json.dumps(
            {
                "type": "initial",
                "current_revisions": current_revisions,
            },
            sort_keys=True,
        )
    )

    event_count = 0
    iterator = file_manager.watch(tracked)
    while args.max_events <= 0 or event_count < args.max_events:
        try:
            if args.timeout_seconds is not None:
                batch = await asyncio.wait_for(
                    anext(iterator),
                    timeout=args.timeout_seconds,
                )
            else:
                batch = await anext(iterator)
        except TimeoutError:
            return 0
        except StopAsyncIteration:
            break

        for change in batch:
            current_revisions[str(change.path)] = change.revision_id
        event_count += 1
        payload = {
            "type": "changes",
            "batch_index": event_count,
            "changes": [
                {
                    "path": str(change.path),
                    "change": change.change,
                    "exists": change.exists,
                    "revision_id": change.revision_id,
                    "captured_at": change.captured_at.isoformat(),
                }
                for change in batch
            ],
            "current_revisions": current_revisions,
        }
        print(json.dumps(payload, sort_keys=True))


    return 0


def _build_llm_provider(name: str, *, cwd: str | os.PathLike[str] | None = None):
    if name == "codex":
        return CodexProvider()
    if name == "openai_compatible":
        return OpenAICompatibleProvider(project_root=Path(cwd or os.getcwd()))
    if name == "llama":
        return LlamaCppProvider()
    if name == "claude":
        return ClaudeProvider()
    raise ValueError(f"Unknown provider: {name}")


def _coerce_default_tui_argv(argv: Sequence[str]) -> tuple[list[str], bool]:
    if not argv:
        return [], False
    commands = {"prompt", "doctor", "context", "note", "chat", "tui"}
    first_non_option = next((token for token in argv if not token.startswith("-")), None)
    if first_non_option is None or first_non_option in commands:
        return list(argv), False
    return ["tui", *argv], True


def _resolve_tui_active_file(
    raw_path: str,
    *,
    create_missing_parents: bool,
) -> tuple[Path, bool]:
    path = Path(raw_path).expanduser().resolve()
    if path.exists():
        if path.is_dir():
            raise ValueError(f"Target is a directory: {path}")
        return path, False
    if path.parent.exists():
        if not path.parent.is_dir():
            raise ValueError(f"Parent path is not a directory: {path.parent}")
        return path, True
    if not create_missing_parents:
        raise ValueError(
            f"Parent directory does not exist: {path.parent}. "
            "Re-run with -p/--parents to create it on first save."
        )
    return path, True


def _snapshot_to_dict(snapshot) -> dict[str, str | int]:
    return {
        "path": str(snapshot.path),
        "revision_id": snapshot.revision_id,
        "content_hash": snapshot.content_hash,
        "mtime_ns": snapshot.mtime_ns,
        "size_bytes": snapshot.size_bytes,
        "captured_at": snapshot.captured_at.isoformat(),
    }


def _prompt_run_to_dict(prompt_run) -> dict[str, object]:
    return {
        "index": prompt_run.index,
        "prompt_text": prompt_run.prompt_text,
        "mode": prompt_run.mode,
        "per_prompt_budget": prompt_run.per_prompt_budget,
        "source_path": str(prompt_run.source_path) if prompt_run.source_path else None,
        "source_target_id": prompt_run.source_target_id,
        "source_raw_span": _span_to_dict(prompt_run.source_raw_span),
        "source_parsed_span": _span_to_dict(prompt_run.source_parsed_span),
        "target_map_text": prompt_run.target_map_text,
        "model_input_text": prompt_run.model_input_text,
    }


def _structural_node_to_dict(node) -> dict[str, object]:
    return {
        "target_id": node.target_id,
        "file_path": str(node.file_path),
        "file_label": node.file_label,
        "kind": node.kind,
        "label": node.label,
        "heading_path": list(node.heading_path),
        "line_start": node.line_start,
        "line_end": node.line_end,
        "raw_span": _span_to_dict(node.raw_span),
        "parsed_span": _span_to_dict(node.parsed_span),
        "preview": node.preview,
        "heading_id": node.heading_id,
        "anchor_id": node.anchor_id,
        "is_focus_area": node.is_focus_area,
    }


def _span_to_dict(span) -> dict[str, int] | None:
    if span is None:
        return None
    return {"start": span.start, "end": span.end}
def _persist_prompt_usage_log(args: argparse.Namespace, response) -> str | None:
    try:
        cwd = Path(args.cwd).expanduser().resolve()
        path = append_usage_record(
            cwd,
            {
                "logged_at": datetime.now().astimezone().isoformat(),
                "mode": "prompt",
                "provider": response.provider_metadata.get("provider") or args.provider,
                "model": args.model,
                "reasoning_effort": args.reasoning_effort,
                "prompt": args.prompt,
                "finish_reason": response.finish_reason,
                "usage": usage_to_dict(response.usage),
                "provider_metadata": response.provider_metadata,
            },
        )
        return str(path)
    except Exception:
        return None


def _note_prompt_result_to_dict(prompt_result) -> dict[str, object]:
    loop_result = prompt_result.loop_result
    return {
        "prompt_index": prompt_result.prompt_index,
        "prompt_run": _prompt_run_to_dict(prompt_result.prompt_run),
        "stop_reason": loop_result.stop_reason,
        "metrics": _loop_metrics_to_dict(loop_result.metrics),
        "events": [_loop_event_to_dict(item) for item in loop_result.events],
        "tool_failures": [_tool_failure_to_dict(item) for item in loop_result.tool_failures],
        "research_summary": _research_summary_to_dict(loop_result.research_summary),
        "usage_log": usage_log_to_dict(loop_result.usage_log),
        "final_file_snapshots": [_snapshot_to_dict(item) for item in loop_result.final_file_snapshots],
    }


def _loop_metrics_to_dict(metrics) -> dict[str, object]:
    return {
        "valid_turn_count": metrics.valid_turn_count,
        "malformed_repair_count": metrics.malformed_repair_count,
        "protected_rejection_count": metrics.protected_rejection_count,
        "conflict_rejection_count": metrics.conflict_rejection_count,
        "successful_edit_count": metrics.successful_edit_count,
        "main_turn_cap": metrics.main_turn_cap,
        "stop_reason": metrics.stop_reason,
    }


def _loop_event_to_dict(event) -> dict[str, object]:
    return {
        "kind": event.kind,
        "message": event.message,
        "details": event.details,
    }


def _tool_failure_to_dict(failure) -> dict[str, object]:
    return {
        "category": failure.category,
        "reason": failure.reason,
        "tool_name": failure.tool_name,
        "message": failure.message,
        "target_identifier": failure.target_identifier,
        "details": failure.details,
    }


def _chat_metrics_to_dict(metrics) -> dict[str, object]:
    return {
        "valid_turn_count": metrics.valid_turn_count,
        "malformed_repair_count": metrics.malformed_repair_count,
        "citation_repair_count": metrics.citation_repair_count,
        "stop_reason": metrics.stop_reason,
    }


def _research_summary_to_dict(summary) -> dict[str, object]:
    return {
        "search_batches": [_search_batch_to_dict(item) for item in summary.search_batches],
        "fetch_packets": [_fetch_packet_to_dict(item) for item in summary.fetch_packets],
        "fetch_failures": [_fetch_failure_to_dict(item) for item in summary.fetch_failures],
    }


def _search_batch_to_dict(batch) -> dict[str, object]:
    return {
        "queries": list(batch.queries),
        "depth": batch.depth,
        "freshness": batch.freshness,
        "purpose": batch.purpose,
        "results": [_search_result_to_dict(item) for item in batch.results],
        "failures": [_search_query_failure_to_dict(item) for item in batch.failures],
    }


def _search_result_to_dict(result) -> dict[str, object]:
    return {
        "source_id": result.source_id,
        "title": result.title,
        "url": result.url,
        "canonical_url": result.canonical_url,
        "snippet": result.snippet,
        "rank": result.rank,
        "refined_score": result.refined_score,
        "query_labels": list(result.query_labels),
        "category_labels": list(result.category_labels),
        "date": result.date,
    }


def _search_query_failure_to_dict(failure) -> dict[str, object]:
    return {
        "query": failure.query,
        "attempted_engines": list(failure.attempted_engines),
        "message": failure.message,
    }


def _fetch_packet_to_dict(packet) -> dict[str, object]:
    return {
        "source_id": packet.source_id,
        "title": packet.title,
        "url": packet.url,
        "canonical_url": packet.canonical_url,
        "desired_info": packet.desired_info,
        "chunks": [_fetched_chunk_to_dict(item) for item in packet.chunks],
    }


def _fetched_chunk_to_dict(chunk) -> dict[str, object]:
    return {
        "source_id": chunk.source_id,
        "title": chunk.title,
        "url": chunk.url,
        "canonical_url": chunk.canonical_url,
        "text": chunk.text,
        "score": chunk.score,
        "heading_path": list(chunk.heading_path),
    }


def _fetch_failure_to_dict(failure) -> dict[str, object]:
    return {
        "source_id": failure.source_id,
        "url": failure.url,
        "message": failure.message,
    }


if __name__ == "__main__":
    raise SystemExit(main())
