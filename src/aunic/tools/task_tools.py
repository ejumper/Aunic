from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aunic.domain import ToolSpec
from aunic.tasks import (
    TaskDraft,
    TaskUpdates,
    block_task,
    create_task,
    delete_task,
    get_task,
    list_tasks,
    update_task,
)
from aunic.tools.base import ToolDefinition, ToolExecutionResult
from aunic.tools.runtime import RunToolContext


VALID_UPDATE_STATUSES: frozenset[str] = frozenset(
    {"pending", "in_progress", "completed", "deleted"}
)


TASK_CREATE_PROMPT = """Use this tool to create a structured task list for your current session. This helps you track progress, organize complex tasks, and demonstrate thoroughness to the user.
It also helps the user understand the progress of the task and overall progress of their requests.

## When to Use This Tool

Use this tool proactively in these scenarios:

- Complex multi-step tasks - When a task requires 3 or more distinct steps or actions
- Non-trivial and complex tasks - Tasks that require careful planning or multiple operations
- User explicitly requests todo list - When the user directly asks you to use the todo list
- User provides multiple tasks - When users provide a list of things to be done (numbered or comma-separated)
- After receiving new instructions - Immediately capture user requirements as tasks
- When you start working on a task - Mark it as in_progress BEFORE beginning work
- After completing a task - Mark it as completed and add any new follow-up tasks discovered during implementation

## When NOT to Use This Tool

Skip using this tool when:
- There is only a single, straightforward task
- The task is trivial and tracking it provides no organizational benefit
- The task can be completed in less than 3 trivial steps
- The task is purely conversational or informational

NOTE that you should not use this tool if there is only one trivial task to do. In this case you are better off just doing the task directly.

## Task Fields

- **subject**: A brief, actionable title in imperative form (e.g., "Fix authentication bug in login flow")
- **description**: What needs to be done
- **active_form** (optional): Present continuous form shown in the indicator while the task is in_progress (e.g., "Fixing authentication bug"). If omitted, the indicator shows the subject instead.

All tasks are created with status `pending`.

## Tips

- Create tasks with clear, specific subjects that describe the outcome
- After creating tasks, use task_update to set up dependencies (add_blocks / add_blocked_by) if needed
- Check task_list first to avoid creating duplicate tasks
"""


TASK_GET_PROMPT = """Use this tool to retrieve a task by its ID from the task list.

## When to Use This Tool

- When you need the full description and context before starting work on a task
- To understand task dependencies (what it blocks, what blocks it)

## Output

Returns full task details:
- **subject**: Task title
- **description**: Detailed requirements and context
- **status**: 'pending', 'in_progress', or 'completed'
- **blocks**: Tasks waiting on this one to complete
- **blocked_by**: Tasks that must complete before this one can start

## Tips

- After fetching a task, verify its blocked_by list is empty before beginning work.
- Use task_list to see all tasks in summary form.
"""


TASK_LIST_PROMPT = """Use this tool to list all tasks in the task list.

## When to Use This Tool

- To see what tasks are available to work on (status: 'pending', not blocked)
- To check overall progress on the project
- To find tasks that are blocked and need dependencies resolved
- After completing a task, to check for newly unblocked work
- **Prefer working on tasks in ID order** (lowest ID first) when multiple tasks are available, as earlier tasks often set up context for later ones

## Output

Returns a summary of each task:
- **id**: Task identifier (use with task_get, task_update)
- **subject**: Brief description of the task
- **status**: 'pending', 'in_progress', or 'completed'
- **blocked_by**: List of open task IDs that must be resolved first (tasks with blocked_by cannot be worked on until dependencies resolve)

Use task_get with a specific task ID to view full details including description.
"""


TASK_UPDATE_PROMPT = """Use this tool to update a task in the task list.

## When to Use This Tool

**Mark tasks as resolved:**
- When you have completed the work described in a task
- When a task is no longer needed or has been superseded
- IMPORTANT: Always mark tasks as completed when you finish them
- After resolving, call task_list to find your next task

- ONLY mark a task as completed when you have FULLY accomplished it
- If you encounter errors, blockers, or cannot finish, keep the task as in_progress
- When blocked, create a new task describing what needs to be resolved
- Never mark a task as completed if:
  - Tests are failing
  - Implementation is partial
  - You encountered unresolved errors
  - You couldn't find necessary files or dependencies

**Delete tasks:**
- When a task is no longer relevant or was created in error
- Setting status to `deleted` permanently removes the task

**Update task details:**
- When requirements change or become clearer
- When establishing dependencies between tasks

## Fields You Can Update

- **status**: The task status (see Status Workflow below)
- **subject**: Change the task title (imperative form, e.g., "Run tests")
- **description**: Change the task description
- **active_form**: Present continuous form shown in the indicator when in_progress (e.g., "Running tests")
- **metadata**: Merge metadata keys into the task (set a key to null to delete it)
- **add_blocks**: Mark tasks that cannot start until this one completes
- **add_blocked_by**: Mark tasks that must complete before this one can start

## Status Workflow

Status progresses: `pending` -> `in_progress` -> `completed`

Use `deleted` to permanently remove a task.

## Staleness

Make sure to read a task's latest state using `task_get` before updating it.

## Examples

Mark task as in progress when starting work:
```json
{"task_id": "1", "status": "in_progress"}
```

Mark task as completed after finishing work:
```json
{"task_id": "1", "status": "completed"}
```

Delete a task:
```json
{"task_id": "1", "status": "deleted"}
```

Set up task dependencies:
```json
{"task_id": "2", "add_blocked_by": ["1"]}
```
"""


@dataclass(frozen=True)
class TaskCreateArgs:
    subject: str
    description: str
    active_form: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class TaskGetArgs:
    task_id: str


@dataclass(frozen=True)
class TaskListArgs:
    pass


@dataclass(frozen=True)
class TaskUpdateArgs:
    task_id: str
    subject: str | None = None
    description: str | None = None
    active_form: str | None = None
    status: str | None = None
    add_blocks: tuple[str, ...] = field(default_factory=tuple)
    add_blocked_by: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] | None = None


def build_task_tool_registry() -> tuple[ToolDefinition[Any], ...]:
    return (
        ToolDefinition(
            spec=ToolSpec(
                name="task_create",
                description=TASK_CREATE_PROMPT,
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["subject", "description"],
                    "properties": {
                        "subject": {
                            "type": "string",
                            "description": "Brief, actionable title in imperative form.",
                        },
                        "description": {
                            "type": "string",
                            "description": "What needs to be done.",
                        },
                        "active_form": {
                            "type": "string",
                            "description": (
                                "Present-continuous label shown in the indicator "
                                "while this task is in_progress (e.g. 'Running tests')."
                            ),
                        },
                        "metadata": {
                            "type": "object",
                            "description": "Arbitrary metadata to attach to the task.",
                        },
                    },
                },
            ),
            parse_arguments=parse_task_create_args,
            execute=execute_task_create,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="task_get",
                description=TASK_GET_PROMPT,
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["task_id"],
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "The ID of the task to retrieve.",
                        },
                    },
                },
            ),
            parse_arguments=parse_task_get_args,
            execute=execute_task_get,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="task_list",
                description=TASK_LIST_PROMPT,
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {},
                },
            ),
            parse_arguments=parse_task_list_args,
            execute=execute_task_list,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="task_update",
                description=TASK_UPDATE_PROMPT,
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["task_id"],
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "The ID of the task to update.",
                        },
                        "subject": {"type": "string"},
                        "description": {"type": "string"},
                        "active_form": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed", "deleted"],
                        },
                        "add_blocks": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Task IDs that this task should block.",
                        },
                        "add_blocked_by": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Task IDs that must complete before this one.",
                        },
                        "metadata": {
                            "type": "object",
                            "description": (
                                "Metadata keys to merge into the task. Set a key to "
                                "null to delete it."
                            ),
                        },
                    },
                },
            ),
            parse_arguments=parse_task_update_args,
            execute=execute_task_update,
        ),
    )


def parse_task_create_args(payload: dict[str, Any]) -> TaskCreateArgs:
    _ensure_no_extra_keys(
        payload, {"subject", "description", "active_form", "metadata"}
    )
    subject = _require_nonempty_string(payload, "subject")
    description = _require_string(payload, "description")
    active_form = _optional_nonempty_string(payload, "active_form")
    metadata = _optional_object(payload, "metadata")
    return TaskCreateArgs(
        subject=subject,
        description=description,
        active_form=active_form,
        metadata=metadata,
    )


def parse_task_get_args(payload: dict[str, Any]) -> TaskGetArgs:
    _ensure_no_extra_keys(payload, {"task_id"})
    return TaskGetArgs(task_id=_require_nonempty_string(payload, "task_id"))


def parse_task_list_args(payload: dict[str, Any]) -> TaskListArgs:
    _ensure_no_extra_keys(payload, set())
    return TaskListArgs()


def parse_task_update_args(payload: dict[str, Any]) -> TaskUpdateArgs:
    allowed = {
        "task_id",
        "subject",
        "description",
        "active_form",
        "status",
        "add_blocks",
        "add_blocked_by",
        "metadata",
    }
    _ensure_no_extra_keys(payload, allowed)
    task_id = _require_nonempty_string(payload, "task_id")

    subject = _optional_string(payload, "subject")
    description = _optional_string(payload, "description")
    active_form = _optional_string(payload, "active_form")
    metadata = _optional_object(payload, "metadata")

    status_value = payload.get("status")
    status: str | None = None
    if status_value is not None:
        if not isinstance(status_value, str):
            raise ValueError("`status` must be a string.")
        if status_value not in VALID_UPDATE_STATUSES:
            raise ValueError(
                "`status` must be one of 'pending', 'in_progress', "
                "'completed', or 'deleted'."
            )
        status = status_value

    add_blocks = _optional_string_array(payload, "add_blocks")
    add_blocked_by = _optional_string_array(payload, "add_blocked_by")

    return TaskUpdateArgs(
        task_id=task_id,
        subject=subject,
        description=description,
        active_form=active_form,
        status=status,
        add_blocks=add_blocks,
        add_blocked_by=add_blocked_by,
        metadata=metadata,
    )


async def execute_task_create(
    runtime: RunToolContext, args: TaskCreateArgs
) -> ToolExecutionResult:
    task = create_task(
        runtime.active_file,
        TaskDraft(
            subject=args.subject,
            description=args.description,
            active_form=args.active_form,
            metadata=args.metadata,
        ),
    )
    payload = {
        "type": "task_created",
        "task": {"id": task.id, "subject": task.subject},
    }
    transcript = f"Task #{task.id} created: {task.subject}"
    return ToolExecutionResult(
        tool_name="task_create",
        status="completed",
        in_memory_content=payload,
        transcript_content=transcript,
    )


async def execute_task_get(
    runtime: RunToolContext, args: TaskGetArgs
) -> ToolExecutionResult:
    task = get_task(runtime.active_file, args.task_id)
    if task is None:
        payload = {"type": "task_get", "task": None}
        return ToolExecutionResult(
            tool_name="task_get",
            status="completed",
            in_memory_content=payload,
            transcript_content=f"Task #{args.task_id} not found.",
        )
    payload = {
        "type": "task_get",
        "task": {
            "id": task.id,
            "subject": task.subject,
            "description": task.description,
            "active_form": task.active_form,
            "status": task.status,
            "blocks": list(task.blocks),
            "blocked_by": list(task.blocked_by),
        },
    }
    lines = [
        f"Task #{task.id}: {task.subject}",
        f"Status: {task.status}",
        f"Description: {task.description}",
    ]
    if task.blocked_by:
        lines.append(f"Blocked by: {', '.join(f'#{b}' for b in task.blocked_by)}")
    if task.blocks:
        lines.append(f"Blocks: {', '.join(f'#{b}' for b in task.blocks)}")
    return ToolExecutionResult(
        tool_name="task_get",
        status="completed",
        in_memory_content=payload,
        transcript_content="\n".join(lines),
    )


async def execute_task_list(
    runtime: RunToolContext, args: TaskListArgs
) -> ToolExecutionResult:
    del args
    all_tasks = list_tasks(runtime.active_file)
    visible = [
        task
        for task in all_tasks
        if not (task.metadata and task.metadata.get("_internal") is True)
    ]
    completed_ids = {task.id for task in visible if task.status == "completed"}

    summaries: list[dict[str, Any]] = []
    for task in visible:
        blocked_by_open = [bid for bid in task.blocked_by if bid not in completed_ids]
        summaries.append(
            {
                "id": task.id,
                "subject": task.subject,
                "status": task.status,
                "blocked_by": blocked_by_open,
            }
        )

    payload = {"type": "task_list", "tasks": summaries}
    if not summaries:
        transcript = "No tasks found."
    else:
        transcript_lines = []
        for summary in summaries:
            blocked = (
                f" [blocked by {', '.join(f'#{b}' for b in summary['blocked_by'])}]"
                if summary["blocked_by"]
                else ""
            )
            transcript_lines.append(
                f"#{summary['id']} [{summary['status']}] {summary['subject']}{blocked}"
            )
        transcript = "\n".join(transcript_lines)
    return ToolExecutionResult(
        tool_name="task_list",
        status="completed",
        in_memory_content=payload,
        transcript_content=transcript,
    )


async def execute_task_update(
    runtime: RunToolContext, args: TaskUpdateArgs
) -> ToolExecutionResult:
    existing = get_task(runtime.active_file, args.task_id)
    if existing is None:
        payload = {
            "type": "task_update",
            "success": False,
            "task_id": args.task_id,
            "updated_fields": [],
            "error": "Task not found.",
        }
        return ToolExecutionResult(
            tool_name="task_update",
            status="completed",
            in_memory_content=payload,
            transcript_content=f"Task #{args.task_id} not found.",
        )

    # Special case: delete
    if args.status == "deleted":
        ok = delete_task(runtime.active_file, args.task_id)
        payload = {
            "type": "task_update",
            "success": ok,
            "task_id": args.task_id,
            "updated_fields": ["deleted"] if ok else [],
            "status_change": {"from": existing.status, "to": "deleted"} if ok else None,
        }
        transcript = (
            f"Task #{args.task_id} deleted."
            if ok
            else f"Task #{args.task_id} could not be deleted."
        )
        return ToolExecutionResult(
            tool_name="task_update",
            status="completed",
            in_memory_content=payload,
            transcript_content=transcript,
        )

    updated_fields: list[str] = []
    updates = TaskUpdates(
        subject=args.subject if args.subject is not None and args.subject != existing.subject else None,
        description=(
            args.description
            if args.description is not None and args.description != existing.description
            else None
        ),
        active_form=(
            args.active_form
            if args.active_form is not None and args.active_form != existing.active_form
            else None
        ),
        status=(
            args.status  # type: ignore[arg-type]
            if args.status is not None
            and args.status != "deleted"
            and args.status != existing.status
            else None
        ),
        metadata=args.metadata,
    )
    if updates.subject is not None:
        updated_fields.append("subject")
    if updates.description is not None:
        updated_fields.append("description")
    if updates.active_form is not None:
        updated_fields.append("active_form")
    if updates.status is not None:
        updated_fields.append("status")
    if updates.metadata is not None:
        updated_fields.append("metadata")

    if updates.has_changes():
        update_task(runtime.active_file, args.task_id, updates)

    # Apply dependency edges.
    new_blocks: list[str] = []
    for blocked_id in args.add_blocks:
        if blocked_id == args.task_id:
            continue
        if block_task(runtime.active_file, args.task_id, blocked_id) and blocked_id not in new_blocks:
            new_blocks.append(blocked_id)
    new_blocked_by: list[str] = []
    for blocker_id in args.add_blocked_by:
        if blocker_id == args.task_id:
            continue
        if block_task(runtime.active_file, blocker_id, args.task_id) and blocker_id not in new_blocked_by:
            new_blocked_by.append(blocker_id)
    if new_blocks:
        updated_fields.append("blocks")
    if new_blocked_by:
        updated_fields.append("blocked_by")

    status_change = None
    if updates.status is not None:
        status_change = {"from": existing.status, "to": updates.status}

    payload = {
        "type": "task_update",
        "success": True,
        "task_id": args.task_id,
        "updated_fields": updated_fields,
        "status_change": status_change,
    }
    if updated_fields:
        transcript = f"Updated task #{args.task_id}: {', '.join(updated_fields)}"
    else:
        transcript = f"Task #{args.task_id} unchanged."
    return ToolExecutionResult(
        tool_name="task_update",
        status="completed",
        in_memory_content=payload,
        transcript_content=transcript,
    )


def _ensure_no_extra_keys(payload: dict[str, Any], expected: set[str]) -> None:
    extras = sorted(set(payload) - expected)
    if extras:
        raise ValueError(f"Unexpected fields: {', '.join(extras)}.")


def _require_string(payload: dict[str, Any], key: str) -> str:
    if key not in payload:
        raise ValueError(f"Missing required field `{key}`.")
    value = payload[key]
    if not isinstance(value, str):
        raise ValueError(f"`{key}` must be a string.")
    return value


def _require_nonempty_string(payload: dict[str, Any], key: str) -> str:
    value = _require_string(payload, key)
    if not value.strip():
        raise ValueError(f"`{key}` must not be empty.")
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    if key not in payload:
        return None
    value = payload[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"`{key}` must be a string.")
    return value


def _optional_nonempty_string(payload: dict[str, Any], key: str) -> str | None:
    value = _optional_string(payload, key)
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return value


def _optional_object(payload: dict[str, Any], key: str) -> dict[str, Any] | None:
    if key not in payload:
        return None
    value = payload[key]
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"`{key}` must be an object.")
    return dict(value)


def _optional_string_array(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    if key not in payload:
        return ()
    value = payload[key]
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"`{key}` must be an array of strings.")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ValueError(f"`{key}` must be an array of non-empty strings.")
        result.append(item)
    return tuple(result)
