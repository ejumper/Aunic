from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aunic.domain import ToolSpec
from aunic.plans import PlanService
from aunic.tools.base import ToolDefinition, ToolExecutionResult
from aunic.tools.filesystem import _apply_exact_edit, _build_structured_patch
from aunic.tools.runtime import (
    PermissionRequest,
    RunToolContext,
    failure_from_payload,
    failure_payload,
)


@dataclass(frozen=True)
class EnterPlanModeArgs:
    pass


@dataclass(frozen=True)
class PlanCreateArgs:
    title: str
    content: str | None = None


@dataclass(frozen=True)
class PlanWriteArgs:
    content: str


@dataclass(frozen=True)
class PlanEditArgs:
    old_string: str
    new_string: str
    replace_all: bool = False


@dataclass(frozen=True)
class ExitPlanArgs:
    pass


def build_plan_tool_registry() -> tuple[ToolDefinition[Any], ...]:
    return (
        ToolDefinition(
            spec=ToolSpec(
                name="enter_plan_mode",
                description=(
                    "Enter planning mode before proposing a risky or multi-step change. "
                    "Planning mode allows reading project context and editing the active plan, "
                    "but does not allow mutating the source note or project files."
                ),
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {},
                },
            ),
            parse_arguments=parse_enter_plan_mode_args,
            execute=execute_enter_plan_mode,
            persistence="persistent",
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="plan_create",
                description=(
                    "Create a markdown plan attached to the active source note and make it "
                    "the active mutable planning document."
                ),
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title"],
                    "properties": {
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                    },
                },
            ),
            parse_arguments=parse_plan_create_args,
            execute=execute_plan_create,
            persistence="persistent",
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="plan_write",
                description=(
                    "Replace the entire active plan markdown file. Use this only while "
                    "planning; the source note and project files are not modified."
                ),
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["content"],
                    "properties": {
                        "content": {"type": "string"},
                    },
                },
            ),
            parse_arguments=parse_plan_write_args,
            execute=execute_plan_write,
            persistence="ephemeral",
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="plan_edit",
                description=(
                    "Edit the active plan markdown file using exact old_string/new_string "
                    "replacement semantics. old_string must come from the current plan file."
                ),
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["old_string", "new_string"],
                    "properties": {
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                        "replace_all": {"type": "boolean"},
                    },
                },
            ),
            parse_arguments=parse_plan_edit_args,
            execute=execute_plan_edit,
            persistence="ephemeral",
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="exit_plan",
                description=(
                    "Request user approval for the active plan. This tool reads the plan "
                    "from disk at approval time; do not pass the plan body as input."
                ),
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {},
                },
            ),
            parse_arguments=parse_exit_plan_args,
            execute=execute_exit_plan,
            persistence="persistent",
        ),
    )


def parse_enter_plan_mode_args(payload: dict[str, Any]) -> EnterPlanModeArgs:
    _ensure_no_extra_keys(payload, set())
    return EnterPlanModeArgs()


def parse_plan_create_args(payload: dict[str, Any]) -> PlanCreateArgs:
    _ensure_no_extra_keys(payload, {"title", "content"})
    title = _require_string(payload, "title").strip()
    if not title:
        raise ValueError("`title` must not be empty.")
    content = payload.get("content")
    if content is not None and not isinstance(content, str):
        raise ValueError("`content` must be a string.")
    return PlanCreateArgs(title=title, content=content)


def parse_plan_write_args(payload: dict[str, Any]) -> PlanWriteArgs:
    _ensure_no_extra_keys(payload, {"content"})
    return PlanWriteArgs(content=_require_string(payload, "content"))


def parse_plan_edit_args(payload: dict[str, Any]) -> PlanEditArgs:
    _ensure_no_extra_keys(payload, {"old_string", "new_string", "replace_all"})
    old_string = _require_string(payload, "old_string")
    new_string = _require_string(payload, "new_string")
    replace_all = payload.get("replace_all", False)
    if not isinstance(replace_all, bool):
        raise ValueError("`replace_all` must be a boolean.")
    return PlanEditArgs(old_string=old_string, new_string=new_string, replace_all=replace_all)


def parse_exit_plan_args(payload: dict[str, Any]) -> ExitPlanArgs:
    _ensure_no_extra_keys(payload, set())
    return ExitPlanArgs()


async def execute_enter_plan_mode(
    runtime: RunToolContext,
    args: EnterPlanModeArgs,
) -> ToolExecutionResult:
    if runtime.session_state.pre_plan_work_mode is None:
        runtime.session_state.pre_plan_work_mode = runtime.work_mode
    runtime.set_planning_status("drafting")
    return ToolExecutionResult(
        tool_name="enter_plan_mode",
        status="completed",
        in_memory_content={
            "type": "plan_mode_entered",
            "planning_status": runtime.planning_status,
            "active_plan_id": runtime.active_plan_id,
            "active_plan_path": str(runtime.active_plan_path) if runtime.active_plan_path else None,
            "pre_plan_work_mode": runtime.session_state.pre_plan_work_mode,
        },
        transcript_content={
            "type": "plan_lifecycle",
            "event": "entered",
            "planning_status": runtime.planning_status,
            "active_plan_id": runtime.active_plan_id,
            "active_plan_path": str(runtime.active_plan_path) if runtime.active_plan_path else None,
        },
    )


async def execute_plan_create(
    runtime: RunToolContext,
    args: PlanCreateArgs,
) -> ToolExecutionResult:
    if runtime.session_state.pre_plan_work_mode is None:
        runtime.session_state.pre_plan_work_mode = runtime.work_mode
    service = PlanService(runtime.active_file)
    document = service.create_plan(args.title, content=args.content)
    runtime.set_active_plan(
        plan_id=document.entry.id,
        path=document.path,
        planning_status="drafting",
        content=document.markdown,
    )
    await runtime.emit_status(f"Created plan: {document.entry.title}")
    payload = {
        "type": "plan_created",
        "plan_id": document.entry.id,
        "title": document.entry.title,
        "status": document.entry.status,
        "path": str(document.path),
        "source_note": str(runtime.active_file),
        "planning_status": runtime.planning_status,
    }
    return ToolExecutionResult(
        tool_name="plan_create",
        status="completed",
        in_memory_content=payload,
        transcript_content={
            "type": "plan_lifecycle",
            "event": "created",
            "plan_id": document.entry.id,
            "title": document.entry.title,
            "status": document.entry.status,
            "path": str(document.path),
        },
    )


async def execute_plan_write(
    runtime: RunToolContext,
    args: PlanWriteArgs,
) -> ToolExecutionResult:
    active = _require_active_plan(runtime, tool_name="plan_write")
    if isinstance(active, ToolExecutionResult):
        return active
    plan_id = active
    baseline = runtime.working_plan_content
    service = PlanService(runtime.active_file)
    live_path, live_text = await runtime.read_active_plan()
    if baseline and live_text != baseline:
        return _plan_tool_error(
            "plan_write",
            failure_payload(
                category="conflict",
                reason="live_plan_conflict",
                message="The live plan changed after the model read it, so the write could not be applied safely.",
                target_identifier=str(live_path),
            ),
        )
    document = service.save_plan_content(plan_id, args.content)
    runtime.set_active_plan(
        plan_id=document.entry.id,
        path=document.path,
        planning_status=runtime.planning_status if runtime.planning_status != "none" else "drafting",
        content=document.markdown,
    )
    payload = {
        "type": "plan_write",
        "plan_id": document.entry.id,
        "path": str(document.path),
        "status": document.entry.status,
        "content": document.markdown,
        "original_content": live_text,
        "structured_patch": _build_structured_patch(live_text, document.markdown),
    }
    return ToolExecutionResult(
        tool_name="plan_write",
        status="completed",
        in_memory_content=payload,
        transcript_content=None,
    )


async def execute_plan_edit(
    runtime: RunToolContext,
    args: PlanEditArgs,
) -> ToolExecutionResult:
    if not args.old_string:
        return _plan_tool_error(
            "plan_edit",
            failure_payload(
                category="validation_error",
                reason="empty_old_string",
                message="plan_edit requires a non-empty old_string.",
            ),
        )
    if args.old_string == args.new_string:
        return _plan_tool_error(
            "plan_edit",
            failure_payload(
                category="validation_error",
                reason="no_op",
                message="old_string and new_string must differ.",
            ),
        )
    active = _require_active_plan(runtime, tool_name="plan_edit")
    if isinstance(active, ToolExecutionResult):
        return active
    plan_id = active
    baseline = runtime.working_plan_content
    service = PlanService(runtime.active_file)
    live_path, live_text = await runtime.read_active_plan()
    if baseline and live_text != baseline:
        return _plan_tool_error(
            "plan_edit",
            failure_payload(
                category="conflict",
                reason="live_plan_conflict",
                message="The live plan changed after the model read it, so the edit could not be applied safely.",
                target_identifier=str(live_path),
            ),
        )
    source_text = baseline or live_text
    updated, actual_old, error = _apply_exact_edit(
        source_text,
        old_string=args.old_string,
        new_string=args.new_string,
        replace_all=args.replace_all,
    )
    if error is not None:
        return _plan_tool_error("plan_edit", error)
    if updated == source_text:
        return _plan_tool_error(
            "plan_edit",
            failure_payload(
                category="validation_error",
                reason="no_op",
                message="Edit would leave the plan unchanged.",
            ),
        )
    document = service.save_plan_content(plan_id, updated)
    runtime.set_active_plan(
        plan_id=document.entry.id,
        path=document.path,
        planning_status=runtime.planning_status if runtime.planning_status != "none" else "drafting",
        content=document.markdown,
    )
    payload = {
        "type": "plan_edit",
        "plan_id": document.entry.id,
        "path": str(document.path),
        "status": document.entry.status,
        "old_string": args.old_string,
        "new_string": args.new_string,
        "actual_old_string": actual_old,
        "original_content": source_text,
        "structured_patch": _build_structured_patch(source_text, document.markdown),
        "replace_all": args.replace_all,
    }
    return ToolExecutionResult(
        tool_name="plan_edit",
        status="completed",
        in_memory_content=payload,
        transcript_content=None,
    )


async def execute_exit_plan(
    runtime: RunToolContext,
    args: ExitPlanArgs,
) -> ToolExecutionResult:
    active = _require_active_plan(runtime, tool_name="exit_plan")
    if isinstance(active, ToolExecutionResult):
        return active
    plan_id = active
    if runtime.planning_status not in {"drafting", "awaiting_approval", "approved"}:
        return _plan_tool_error(
            "exit_plan",
            failure_payload(
                category="validation_error",
                reason="not_planning",
                message="exit_plan can only be used while a plan is being drafted or approved.",
            ),
        )

    service = PlanService(runtime.active_file)
    awaiting_doc = service.set_status(plan_id, "awaiting_approval")
    runtime.set_active_plan(
        plan_id=awaiting_doc.entry.id,
        path=awaiting_doc.path,
        planning_status="awaiting_approval",
        content=awaiting_doc.markdown,
    )
    decision = await runtime.resolve_permission(
        PermissionRequest(
            tool_name="exit_plan",
            action="approve_plan",
            target=str(awaiting_doc.path),
            message=f"Approve plan for implementation: {awaiting_doc.entry.title}",
            policy="ask",
            details={
                "kind": "plan_approval",
                "plan_id": awaiting_doc.entry.id,
                "plan_title": awaiting_doc.entry.title,
                "plan_path": str(awaiting_doc.path),
                "plan_markdown": awaiting_doc.markdown,
            },
        )
    )
    if not decision.allowed:
        draft_doc = service.set_status(plan_id, "draft")
        runtime.set_active_plan(
            plan_id=draft_doc.entry.id,
            path=draft_doc.path,
            planning_status="drafting",
            content=draft_doc.markdown,
        )
        return _plan_tool_error(
            "exit_plan",
            failure_payload(
                category="user_cancel",
                reason="approval_dismissed",
                message="The user kept the plan in drafting mode.",
                target_identifier=str(draft_doc.path),
            ),
        )

    approved_doc = service.set_status(plan_id, "approved")
    runtime.set_active_plan(
        plan_id=approved_doc.entry.id,
        path=approved_doc.path,
        planning_status="approved",
        content=approved_doc.markdown,
    )
    runtime.work_mode = "work"
    payload = {
        "type": "plan_approved",
        "plan_id": approved_doc.entry.id,
        "title": approved_doc.entry.title,
        "status": approved_doc.entry.status,
        "path": str(approved_doc.path),
        "source_note": str(runtime.active_file),
        "plan_markdown": approved_doc.markdown,
        "next_action": "Implement this approved plan now.",
    }
    return ToolExecutionResult(
        tool_name="exit_plan",
        status="completed",
        in_memory_content=payload,
        transcript_content={
            "type": "plan_lifecycle",
            "event": "approved",
            "plan_id": approved_doc.entry.id,
            "title": approved_doc.entry.title,
            "status": approved_doc.entry.status,
            "path": str(approved_doc.path),
            "source_note": str(runtime.active_file),
        },
    )


def _require_active_plan(runtime: RunToolContext, *, tool_name: str) -> str | ToolExecutionResult:
    if runtime.active_plan_id and runtime.active_plan_path:
        return runtime.active_plan_id
    return _plan_tool_error(
        tool_name,
        failure_payload(
            category="validation_error",
            reason="no_active_plan",
            message="No active plan is selected. Call plan_create first.",
        ),
    )


def _plan_tool_error(tool_name: str, payload: dict[str, Any]) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=tool_name,
        status="tool_error",
        in_memory_content=payload,
        transcript_content=None,
        tool_failure=failure_from_payload(payload, tool_name=tool_name),
    )


def _require_string(payload: dict[str, Any], key: str) -> str:
    if key not in payload:
        raise ValueError(f"Missing required field `{key}`.")
    value = payload[key]
    if not isinstance(value, str):
        raise ValueError(f"`{key}` must be a string.")
    return value


def _ensure_no_extra_keys(payload: dict[str, Any], expected: set[str]) -> None:
    extras = sorted(set(payload) - expected)
    if extras:
        raise ValueError(f"Unexpected fields: {', '.join(extras)}.")
