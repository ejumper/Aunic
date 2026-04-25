# Plan: Add Task (Todo) Tools to Aunic

## Context

Aunic has no structured task/todo system for the model to track multi-step work. The reference implementation at `/home/ejumps/HalfaCloud/Backups/coding-agent-program-example/` has `TaskCreate` / `TaskGet` / `TaskList` / `TaskUpdate` backed by per-task JSON files, with status (`pending` → `in_progress` → `completed`), dependencies (`blocks` / `blockedBy`), and an `activeForm` field that drives the UI spinner label.

Goal: port that system to Aunic as closely as practical, minus all subagent/swarm features.

**Requirements**
1. Per-note storage in `<note_parent>/.aunic/tasks/` (mirrors existing `.aunic/plans/` pattern from [src/aunic/plans/service.py:30-33](src/aunic/plans/service.py#L30-L33)).
2. Tools available only when `work_mode in {"read", "work"}` (not `"off"`), in both note mode and chat mode.
3. Indicator line shows the in-progress task's `active_form` (or `subject`) in place of `"Pontificating..."` on `provider_request`. Tool-call verbs (`Reading...`, `Editing...`) continue to appear during tool execution.
4. Faithful translation of the TypeScript original. Drop subagent-specific features: `owner`, hooks, lockfiles, team resolution, status migrations.

## Files to Create

| Path | Purpose |
|---|---|
| `src/aunic/tasks.py` | Storage API: `Task` dataclass, `create_task`, `get_task`, `list_tasks`, `update_task`, `delete_task`, `block_task`, `get_active_task_label`, path helpers, high-water-mark read/write. |
| `src/aunic/tools/task_tools.py` | Four `ToolDefinition`s + `build_task_tool_registry()` + verbatim-ish prompt text. |
| `tests/test_tasks.py` | Storage unit tests. |
| `tests/test_task_tools.py` | Tool input validation + execute roundtrip + mode-gating assertion. |

## Files to Modify

| Path | Change |
|---|---|
| [src/aunic/tools/__init__.py](src/aunic/tools/__init__.py) | Re-export `build_task_tool_registry`. |
| [src/aunic/tools/note_edit.py](src/aunic/tools/note_edit.py#L49-L85) | In `build_note_tool_registry`, add task tools when `work_mode in {"read", "work"}`. |
| [src/aunic/modes/chat.py](src/aunic/modes/chat.py) | In `build_chat_tool_registry`, same gating. |
| [src/aunic/loop/runner.py](src/aunic/loop/runner.py#L258-L264) | In the `provider_request` emit, include `active_task_label` in `details` (from `get_active_task_label(active_file)`). |
| [src/aunic/modes/chat.py](src/aunic/modes/chat.py#L242-L250) | Same field added to the chat-mode `provider_request` emit. |
| [web/src/state/session.ts](web/src/state/session.ts#L105-L143) | In `indicatorFromLoopEvent`, use `event.details.active_task_label` as the prefix for `provider_request`; add task tool verbs to `TOOL_VERBS`. |
| [web/src/state/session.test.ts](web/src/state/session.test.ts) | Cover the new `provider_request` branch (with and without label). |
| [src/aunic/tui/controller.py](src/aunic/tui/controller.py#L1104-L1115) | Replace hard-coded `"Pontificating"` with `event.details.get("active_task_label")` fallback; add task verbs to `_TOOL_VERBS` ([lines 2839-2852](src/aunic/tui/controller.py#L2839-L2852)). |

## Storage Layer (`src/aunic/tasks.py`)

Mirrors `utils/tasks.ts` from the example, translated to Python.

**Layout**
```
<note_parent>/.aunic/tasks/
    .highwatermark        # int, max ID ever assigned
    1.json
    2.json
    ...
```

**Schema (Python dataclass, serialised to JSON)**
```python
@dataclass
class Task:
    id: str
    subject: str
    description: str
    active_form: str | None = None               # present-continuous label
    status: Literal["pending","in_progress","completed"] = "pending"
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    metadata: dict[str, Any] | None = None
```
`owner`, `TaskCreated/TaskCompleted` hooks, and the 'ant' status migration are dropped.

**JSON format**: 2-space indent, UTF-8, `id` stored as string (e.g. `"42"`). Optional fields omitted when `None`; arrays always present.

**Atomicity**: write to tempfile, `os.replace` into place. No `lockfile` library — Aunic is single-process per session. `create_task` reads hwm → increments → writes task → writes hwm (the one edge case is `create_task` racing with itself, which can't happen in single-process).

**Cascade on delete**: iterate all sibling task files; rewrite any whose `blocks` / `blocked_by` include the deleted id.

**`get_active_task_label(note_path) -> str | None`**: load tasks, find the first with `status == "in_progress"`, return its `active_form or subject`. None if no in-progress task.

## Tools (`src/aunic/tools/task_tools.py`)

Four `ToolDefinition`s, following the pattern of [src/aunic/tools/sleep.py:21-54](src/aunic/tools/sleep.py#L21-L54). All `persistence="persistent"`.

| Tool name | Input | Behaviour |
|---|---|---|
| `task_create` | `subject: str`, `description: str`, `active_form?: str`, `metadata?: dict` | `create_task(status="pending")`. Return `{"task": {"id", "subject"}}`. Transcript: `Task #<id> created: <subject>`. |
| `task_get` | `task_id: str` | `get_task`. Return full record (minus metadata internals) or `{"task": null}`. Transcript: multi-line summary with `Status:`, `Description:`, `Blocked by:`, `Blocks:`. |
| `task_list` | `{}` | `list_tasks`, drop `metadata._internal`, strip `blocked_by` entries that point to completed tasks. Transcript: `#<id> [<status>] <subject>` per line (or `"No tasks found"`). |
| `task_update` | `task_id: str`, optional `subject`, `description`, `active_form`, `status` (incl. special `"deleted"`), `add_blocks`, `add_blocked_by`, `metadata` | Special-case `status == "deleted"` → `delete_task` + cascade. Otherwise partial update + `block_task` calls for `add_blocks` / `add_blocked_by`. Returns `{success, taskId, updatedFields[], statusChange?, error?}`. `metadata` merge: `null` values delete keys. |

**Prompt text**: port the four `prompt.ts` bodies verbatim into each tool's `ToolSpec.description`. Remove paragraphs about teammates/claim/swarms/verification-agent, but keep:
- "When to Use / When Not to Use"
- Task field explanations (especially the `active_form` paragraph — that's what drives the indicator)
- Status workflow and the JSON examples in `task_update`
- `TaskList`'s "prefer working on tasks in ID order"

Each tool's description is passed to the provider via `ProviderRequest.tools` (Aunic's existing mechanism).

**Registration**
```python
# in src/aunic/tools/note_edit.py build_note_tool_registry
if work_mode in {"read", "work"}:
    registry.extend(build_task_tool_registry())

# in src/aunic/modes/chat.py build_chat_tool_registry — same
```

## Indicator: replacing "Pontificating..."

**Backend — emit `active_task_label`** ([src/aunic/loop/runner.py:258-264](src/aunic/loop/runner.py#L258-L264))
```python
details = {"messages": len(...), "tools": len(...)}
label = get_active_task_label(request.active_file)
if label:
    details["active_task_label"] = label
await append_loop_event(LoopEvent(kind="provider_request", ..., details=details))
```
Identical edit in [src/aunic/modes/chat.py:242-250](src/aunic/modes/chat.py#L242-L250). `LoopEvent.details` is already a generic `dict[str, Any]` ([src/aunic/loop/types.py:45-55](src/aunic/loop/types.py#L45-L55)) — no type change needed. The field rides through `progress_from_loop_event` → serialisation untouched.

**Browser** ([web/src/state/session.ts:105-143](web/src/state/session.ts#L105-L143))
```ts
if (loopKind === "provider_request") {
  const label = typeof event.details.active_task_label === "string"
    ? event.details.active_task_label
    : null;
  return { text: label ? `${label}...` : "Pontificating...", kind: "status" };
}
```
Plus add to `TOOL_VERBS` ([lines 74-87](web/src/state/session.ts#L74-L87)):
```ts
task_create: "Creating task",
task_get:    "Reading task",
task_list:   "Listing tasks",
task_update: "Updating task",
```

**TUI** ([src/aunic/tui/controller.py:1104-1115](src/aunic/tui/controller.py#L1104-L1115))
```python
label = event.details.get("active_task_label") if isinstance(event.details, dict) else None
base = label if isinstance(label, str) and label else "Pontificating"
msg = f"{base}... ({self._run_turn_count})"
```
Plus the same four verbs added to `_TOOL_VERBS` at [lines 2839-2852](src/aunic/tui/controller.py#L2839-L2852).

**Semantics**: the indicator stays on the task label between turns and during `provider_request`; switches to tool verbs during tool execution; reverts to the label on the next `provider_request`. When no task is `in_progress`, the old "Pontificating..." behaviour is preserved.

## Reused Utilities

- Path resolution pattern: copy the shape of `plans_dir` in [src/aunic/plans/service.py:30-33](src/aunic/plans/service.py#L30-L33) and `meta_path_for` in [src/aunic/map/manifest.py:21-23](src/aunic/map/manifest.py#L21-L23).
- `active_file` is already on `RunToolContext` ([src/aunic/tools/runtime.py:148-171](src/aunic/tools/runtime.py#L148-L171)) and `LoopRunRequest` ([src/aunic/loop/runner.py:172-180](src/aunic/loop/runner.py#L172-L180)).
- Existing `ToolDefinition` / `ToolSpec` / `ToolExecutionResult` from [src/aunic/tools/base.py](src/aunic/tools/base.py) — no changes.
- Indicator plumbing (both ends) already accepts arbitrary `details` keys — no envelope/schema changes.

## Verification

1. **Unit tests** — `pytest tests/test_tasks.py tests/test_task_tools.py`
   - `create_task` writes file, bumps `.highwatermark`.
   - `delete_task` cascades — no dangling `blocks` / `blocked_by` references.
   - `get_task` returns `None` on missing id; `list_tasks` returns `[]` on empty dir.
   - `update_task` with `status="deleted"` deletes the file.
   - ID reuse prevented after delete (create → delete → create → new ID > old).
   - Mode-gating: `build_note_tool_registry(work_mode="off")` does not contain any task tool; `work_mode in {"read","work"}` contains all four.
2. **Web tests** — `npm test` in `web/`. Extend `web/src/state/session.test.ts` to cover:
   - `provider_request` with `active_task_label` → `"<label>..."`.
   - `provider_request` without the field → `"Pontificating..."` (regression).
   - `provider_response` with `tool_calls: ["task_update"]` → `"Updating task..."`.
3. **End-to-end smoke**
   - Open a note in the browser; start an Agent: Work session; ask the model to create a 3-step task list.
   - Watch the indicator: turn N shows `Pontificating...`; after `task_update(status="in_progress")` fires, turn N+1 shows the task's `active_form`; during `task_update` the indicator reads `Updating task...`.
   - Quit browser, open same note in the TUI → `ls <note>/.aunic/tasks/` shows the persisted files; TUI indicator shows the same label.
   - `pytest` and `npm test` pass with no regressions.
