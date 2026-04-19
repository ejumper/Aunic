# Tasks Tool
## User Notes
- this is actually several tools, namely: TaskCreate, TaskGet, TaskList, TaskOutput, TaskUpdate and TaskStop
- I currently have no "sub-agent" implementation, but assume I will in the future, so this tool should have the ability to check if subagents are available and use them if they are and it wants to, but know that currently that won't be possible.
## coding-agent-program-example Implementation
The example project has two systems that share the word "task":

1. A structured work-item list: `TaskCreate`, `TaskGet`, `TaskList`, and `TaskUpdate`.
2. Runtime background task handles: `TaskOutput` and `TaskStop`, used for background shells, agents, remote agents, teammates, workflows, and similar long-running work.

Those two systems are related in the UI and in agent workflows, but they are not the same data model. The task-list tools manage durable work items. The background-task tools manage running processes or agents registered in app state.

Relevant files in the example:

- `src/tools/TaskCreateTool/TaskCreateTool.ts`
- `src/tools/TaskCreateTool/prompt.ts`
- `src/tools/TaskGetTool/TaskGetTool.ts`
- `src/tools/TaskGetTool/prompt.ts`
- `src/tools/TaskListTool/TaskListTool.ts`
- `src/tools/TaskListTool/prompt.ts`
- `src/tools/TaskUpdateTool/TaskUpdateTool.ts`
- `src/tools/TaskUpdateTool/prompt.ts`
- `src/tools/TaskOutputTool/TaskOutputTool.tsx`
- `src/tools/TaskStopTool/TaskStopTool.ts`
- `src/utils/tasks.ts`
- `src/utils/task/framework.ts`
- `src/utils/task/diskOutput.ts`
- `src/Task.ts`
- `src/tasks/types.ts`
- `src/tasks/stopTask.ts`
- `src/hooks/useTaskListWatcher.ts`
- `src/components/TaskListV2.tsx`
- `src/tools/AgentTool/AgentTool.tsx`
- `src/tools/AgentTool/prompt.ts`

### Work-Item Task List
The structured task-list system stores tasks as JSON files on disk. This is the system behind `TaskCreate`, `TaskGet`, `TaskList`, and `TaskUpdate`.

The core data model in `src/utils/tasks.ts` is:

```ts
{
  id: string,
  subject: string,
  description: string,
  activeForm?: string,
  owner?: string,
  status: "pending" | "in_progress" | "completed",
  blocks: string[],
  blockedBy: string[],
  metadata?: Record<string, unknown>
}
```

The task list id is resolved by `getTaskListId()` with this priority:

1. `CLAUDE_CODE_TASK_LIST_ID`
2. in-process teammate team name
3. `CLAUDE_CODE_TEAM_NAME`
4. leader team name
5. session id

Tasks are stored under the Claude config directory:

```text
~/.claude/tasks/<task-list-id>/<task-id>.json
```

The implementation uses a high-water-mark file to avoid reusing ids after deletion or reset:

```text
~/.claude/tasks/<task-list-id>/.highwatermark
```

It also uses file locks around list-level and task-level mutations. That matters because swarms or teammates can create, claim, and update tasks concurrently.

### TaskCreate
`TaskCreate` creates a new work item.

Input:

```json
{
  "subject": "Brief title",
  "description": "What needs to be done",
  "activeForm": "Running tests",
  "metadata": {}
}
```

Behavior:

- creates a new numeric task id,
- writes a JSON task file,
- sets initial status to `pending`,
- leaves owner unset,
- initializes `blocks` and `blockedBy` as empty arrays,
- runs `TaskCreated` hooks,
- deletes the newly-created task and throws if a blocking hook rejects it,
- expands the task-list UI.

The prompt tells the model to use tasks for complex multi-step work, explicit user requests, plan mode, user-provided lists, and new instructions that should be tracked. It tells the model not to use tasks for trivial one-step work or purely conversational answers.

Important design detail: task creation is not just bookkeeping. Hooks can enforce policy, add guardrails, or reject bad task creation.

### TaskGet
`TaskGet` retrieves full details for one task by id.

Input:

```json
{
  "taskId": "1"
}
```

Behavior:

- reads the JSON task file,
- validates the schema,
- returns `null` if not found,
- includes subject, description, status, blocks, and blockedBy.

It is read-only and concurrency-safe.

The prompt encourages using it before starting a task, after being assigned a task, or when dependency information is needed.

### TaskList
`TaskList` returns a compact summary of every non-internal task.

Input:

```json
{}
```

Behavior:

- lists all task JSON files,
- filters out tasks with `metadata._internal`,
- filters `blockedBy` to remove blockers that are already completed,
- returns id, subject, status, owner, and unresolved blockers.

It is read-only and concurrency-safe.

The prompt encourages using it to find available work, check progress, identify blocked tasks, and pick the next task after completion. In teammate mode, it tells agents to prefer pending, unowned, unblocked tasks in id order.

### TaskUpdate
`TaskUpdate` mutates a work item.

Input supports:

```json
{
  "taskId": "1",
  "subject": "New title",
  "description": "New description",
  "activeForm": "Running tests",
  "status": "pending | in_progress | completed | deleted",
  "owner": "agent-name",
  "addBlocks": ["2"],
  "addBlockedBy": ["3"],
  "metadata": {
    "key": "value",
    "delete_me": null
  }
}
```

Behavior:

- reads the current task first,
- returns a non-error result if the task is missing,
- updates only fields that changed,
- merges metadata, with `null` deleting a metadata key,
- treats `status: "deleted"` as a task-file deletion,
- removes dependency references when a task is deleted,
- runs `TaskCompleted` hooks before accepting `completed`,
- blocks completion if a hook returns a blocking error,
- auto-assigns owner in swarm mode when an agent marks an unowned task `in_progress`,
- sends a mailbox assignment message when owner changes in swarm mode,
- supports dependency links through `blocks` and `blockedBy`,
- may add a verification nudge if a main-thread agent closes a 3+ task list without a verification task.

The prompt is strict about completion:

- mark a task `in_progress` before starting,
- mark it `completed` only after it is fully done,
- do not complete if tests are failing,
- do not complete if implementation is partial,
- create a blocker task when blocked,
- read latest state with `TaskGet` before updating.

### TaskOutput
`TaskOutput` is not for work-item descriptions. It reads output from a runtime background task registered in app state.

Input:

```json
{
  "task_id": "a123",
  "block": true,
  "timeout": 30000
}
```

Behavior:

- validates that the runtime task exists,
- supports `block=false` for a non-blocking status check,
- with `block=true`, polls app state every 100ms until the task exits or timeout expires,
- reads output from the task's output file,
- returns `success`, `timeout`, or `not_ready`,
- marks completed tasks as notified after output is consumed,
- formats results with XML-ish tags such as `<retrieval_status>`, `<task_id>`, `<status>`, and `<output>`.

The tool is marked read-only. It also has aliases from older names such as `AgentOutputTool` and `BashOutputTool`.

The example has already deprecated direct `TaskOutput` use in favor of reading the output file path with the normal read tool. Background tasks return an output file path, and completion notifications also include that path.

### TaskStop
`TaskStop` stops a running runtime background task.

Input:

```json
{
  "task_id": "b123"
}
```

It also accepts deprecated `shell_id` for compatibility with older `KillShell` transcripts.

Behavior:

- validates that the task id exists in app state,
- validates that its status is `running`,
- dispatches to the concrete task type's `kill` implementation,
- returns task id, task type, and command/description,
- suppresses noisy shell stop notifications for local shell tasks,
- emits SDK termination events when needed.

It can stop background bash tasks, local agent tasks, remote agent tasks, teammate tasks, workflows, monitors, or other runtime task types if that task type has a registered `kill` implementation.

### Runtime Background Task Framework
Runtime tasks are modeled separately from work-item tasks.

`src/Task.ts` defines task types such as:

- `local_bash`
- `local_agent`
- `remote_agent`
- `in_process_teammate`
- `local_workflow`
- `monitor_mcp`
- `dream`

Runtime task statuses are:

- `pending`
- `running`
- `completed`
- `failed`
- `killed`

Runtime task ids use type prefixes:

- `b...` for local bash
- `a...` for local agents
- `r...` for remote agents
- `t...` for teammates
- `w...` for workflows
- `m...` for monitors
- `d...` for dreams

Each runtime task has a base state:

```ts
{
  id: string,
  type: TaskType,
  status: TaskStatus,
  description: string,
  toolUseId?: string,
  startTime: number,
  endTime?: number,
  outputFile: string,
  outputOffset: number,
  notified: boolean
}
```

`src/utils/task/framework.ts` provides the app-state lifecycle:

- `registerTask`: add a runtime task to app state and emit task-started SDK events.
- `updateTaskState`: update one task in app state.
- `getRunningTasks`: list currently running runtime tasks.
- `generateTaskAttachments`: read output deltas and create task-status attachments.
- `applyTaskOffsetsAndEvictions`: update output offsets and evict terminal tasks after they are consumed.
- `pollTasks`: periodic polling and notification generation.

Completed task notifications are rendered as XML-ish user-role messages:

```xml
<task-notification>
<task-id>...</task-id>
<task-type>...</task-type>
<output-file>...</output-file>
<status>completed</status>
<summary>Task "..." completed successfully</summary>
</task-notification>
```

The important idea is that long-running work can finish outside the model turn. The next model turn receives a structured notification instead of needing to poll.

### Runtime Output Files
`src/utils/task/diskOutput.ts` stores runtime output in per-session files under a project temp directory.

Important details:

- task output files are session-scoped so concurrent sessions do not clobber each other,
- writes are queued and drained to disk to avoid memory bloat,
- output files use `O_NOFOLLOW` where available to reduce symlink attacks,
- output is capped at 5GB,
- tools can read deltas by offset,
- old output files can be symlinked or recovered for background agents.

This output-file design is better than returning huge command output directly through the tool result.

### Task UI
`TaskListV2` renders a compact task list:

- completed, in-progress, and pending counts,
- icons for each status,
- owner labels when teammate mode is active,
- blocked-task indicators,
- activity summaries for running teammates,
- truncation for small terminals,
- recent completions kept visible briefly.

The prompt footer has a task toggle shortcut. `TaskCreate` and `TaskUpdate` auto-expand the task list so progress becomes visible when the model starts using tasks.

### Task Reminders
The example injects task reminders as attachments when the model has gone several assistant turns without using `TaskCreate` or `TaskUpdate`.

The reminder is skipped when:

- task v2 is disabled,
- the build/user type should not receive it,
- `TaskUpdate` is not available,
- another communication tool would make the reminder noisy.

This is a useful pattern: task tracking is encouraged, but not by permanently bloating every system prompt.

### Task List Watcher
`useTaskListWatcher` implements a "tasks mode."

Behavior:

- watches a task-list directory,
- waits until the main session is idle,
- finds the first pending, unowned, unblocked task,
- claims it with a file lock,
- formats the task as a prompt,
- submits it to the main model,
- releases the claim if submission fails.

This lets tasks be created externally and picked up automatically by an agent process.

### Subagent Relationship
The task-list tools do not directly spawn subagents.

Subagents are launched by `AgentTool`, not by `TaskCreate`. The task list integrates with agents through:

- `owner`: tasks can be assigned to agents or teammates,
- `claimTask`: agents can atomically claim unowned tasks,
- `TaskList`: agents can find available work,
- `TaskUpdate`: agents mark work in progress or completed,
- mailbox messages: assignment changes can notify teammates,
- task completed hooks: completion can be validated or blocked,
- verification nudges: large task lists can require an independent verification agent.

The distinction is important. The task list is coordination state. Subagents are workers. Runtime background tasks are execution handles. The example connects all three, but it does not collapse them into one tool.

### Design Lessons For Aunic
The transferable ideas are:

- Keep work items separate from running execution handles.
- Store task state outside the transcript.
- Make task ids stable, short, and user-visible.
- Use status, owner, dependencies, and metadata rather than unstructured todo text.
- Make task tools cheap enough to use often.
- Do not spam the transcript with every task update.
- Expose a compact UI task panel so the user can track progress without reading tool JSON.
- Treat completion as a serious state transition, not a decorative checkbox.
- If future subagents exist, use tasks as coordination state, not as a substitute for agent execution.
- For background output, prefer output files plus notifications over huge tool results.

The parts Aunic should not copy directly:

- The example stores tasks globally under `~/.claude/tasks`. Aunic should attach tasks to the source Aunic note.
- The example is chat/session-first. Aunic should make tasks a sidecar to the note workspace.
- The example mixes user-facing "task list" and runtime "background task" under the same broad name. Aunic should name its internals clearly as work items and executions.

## Implementing in Aunic
Aunic should implement the Tasks tool as a source-note-associated task board with optional runtime executions.

The key design point: tasks should not become the primary context. The active markdown note remains the primary context. Tasks are coordination state around that note. They should help the user and model see progress, decompose work, track blockers, and manage background executions without turning the transcript into a todo database.

### Product Model
Aunic should use three separate concepts:

- Task list: the collection of work items associated with a source Aunic note.
- Work item: a structured task such as "Update provider bridge tests."
- Execution: a running process or future subagent working on a task.

This separation avoids a common agent-framework mistake: treating a todo item, an OS process, and a subagent as if they were the same thing. They have different lifetimes and failure modes.

Example:

- Work item `#3`: "Run regression tests for note editing."
- Execution `run-8f2a`: a background `pytest` process for task `#3`.
- Future execution `agent-a17`: a subagent assigned to task `#3`.

`TaskGet` should return the work item and any active execution. `TaskOutput` should read the active execution's output. `TaskStop` should stop the active execution, not delete the work item.

### Storage
Tasks should be stored under the source note's sibling `.aunic/` directory.

For:

```text
/path/to/project/task.md
```

Use:

```text
/path/to/project/.aunic/tasks/
```

Recommended structure:

```text
.aunic/tasks/
  index.json
  lists/
    task-md-7d1b2c/
      tasks.json
      highwatermark
      runs/
        run-8f2a.log
        run-8f2a.json
```

`index.json` maps source notes to task lists:

```json
{
  "task_lists": [
    {
      "id": "task-md-7d1b2c",
      "source_note": "../task.md",
      "created_at": "2026-04-16T10:30:00-05:00",
      "updated_at": "2026-04-16T10:44:00-05:00",
      "archived": false
    }
  ]
}
```

`tasks.json` is the authoritative task-list state:

```json
{
  "schema_version": 1,
  "source_note": "../../task.md",
  "highwatermark": 4,
  "tasks": [
    {
      "id": "1",
      "subject": "Inspect current note-mode task flow",
      "description": "Read the current note-mode runner and transcript write path.",
      "active_form": "Inspecting note-mode flow",
      "status": "completed",
      "owner": "main",
      "blocked_by": [],
      "blocks": ["2"],
      "created_at": "2026-04-16T10:30:00-05:00",
      "updated_at": "2026-04-16T10:36:00-05:00",
      "completed_at": "2026-04-16T10:36:00-05:00",
      "evidence": "Read src/aunic/loop/runner.py and src/aunic/tools/runtime.py.",
      "metadata": {}
    }
  ]
}
```

Why one JSON file instead of one JSON file per task:

- Aunic is currently a single TUI process.
- The file is easier to render and snapshot.
- Atomic rewrite is simple.
- A future subagent implementation can add file locking around the list file.

If future process-level concurrency becomes common, Aunic can move to one file per task with a list-level index. The public tool API does not need to change.

The task list should be treated as Aunic metadata, not source note content. The user sees it in the UI and can export it later, but it should not be silently inserted into the markdown note.

### Task Data Model
Recommended work-item fields:

```json
{
  "id": "1",
  "subject": "Short imperative title",
  "description": "Full task details",
  "active_form": "Working on short title",
  "status": "pending",
  "owner": null,
  "blocked_by": [],
  "blocks": [],
  "created_at": "...",
  "updated_at": "...",
  "started_at": null,
  "completed_at": null,
  "cancelled_at": null,
  "evidence": null,
  "metadata": {},
  "execution": null
}
```

Recommended statuses:

- `pending`: ready if blockers are resolved.
- `in_progress`: someone is actively working on it.
- `completed`: work is finished and evidence is recorded.
- `cancelled`: no longer needed, but kept for history.

Do not add a separate `blocked` status. Like the example project, derive blocked state from unresolved `blocked_by` dependencies. This avoids status contradictions such as `status=blocked` with no blockers.

Use `cancelled` rather than deleting by default. Deletion can still exist as a special action, but cancellation is more honest project memory.

Recommended execution fields:

```json
{
  "id": "run-8f2a",
  "kind": "bash | subagent",
  "status": "pending | running | completed | failed | stopped | stale",
  "description": "Running regression tests",
  "output_path": ".aunic/tasks/lists/task-md-7d1b2c/runs/run-8f2a.log",
  "pid": 12345,
  "subagent_id": null,
  "started_at": "...",
  "ended_at": null,
  "exit_code": null
}
```

This lets a task exist before, during, and after a run.

### Tool Design
Implement the user-requested suite:

- `TaskCreate`
- `TaskGet`
- `TaskList`
- `TaskOutput`
- `TaskUpdate`
- `TaskStop`

Keep the public names exactly as written above if Aunic wants compatibility with the model priors learned from other coding agents. Internally, Python modules can use `task_create`, `task_get`, etc., but provider-facing tool names should match the known names.

#### `TaskCreate`

Creates a work item in the current source note's task list.

Recommended input:

```json
{
  "subject": "Brief title",
  "description": "What needs to be done",
  "activeForm": "Running tests",
  "blockedBy": ["1"],
  "owner": "main",
  "metadata": {},
  "runIfPossible": false,
  "executionKind": "subagent_if_available"
}
```

Only `subject` and `description` should be required.

Behavior:

- creates the task list if it does not exist,
- assigns the next numeric id,
- validates blockers exist,
- starts as `pending`,
- sets owner only if provided,
- records metadata,
- optionally starts execution if `runIfPossible` is true and the requested execution kind is available.

For the current Aunic implementation, subagents are not available. If `executionKind` is `subagent_if_available`, the tool should create the task and return:

```json
{
  "task": {"id": "1", "subject": "..."},
  "execution": null,
  "capabilities": {"subagents_available": false}
}
```

Do not fail just because subagents are unavailable unless the model explicitly asks for `executionKind: "subagent_required"`.

#### `TaskGet`

Returns the full task record.

Recommended input:

```json
{
  "taskId": "1",
  "includeOutputTail": false
}
```

Behavior:

- returns full task fields,
- returns derived `blocked` boolean,
- returns unresolved blockers,
- includes active execution metadata,
- optionally includes the tail of the execution output.

This should be read-only.

#### `TaskList`

Returns a compact summary of the current source note's tasks.

Recommended input:

```json
{
  "status": "all",
  "includeCompleted": true,
  "includeArchived": false
}
```

Behavior:

- lists tasks for the current source note only,
- sorts by numeric id,
- returns counts,
- returns each task's id, subject, status, owner, blocked state, and active execution status,
- includes a `capabilities` object:

```json
{
  "capabilities": {
    "subagents_available": false,
    "background_executions_available": false,
    "max_parallel_executions": 0
  }
}
```

This is how the model can check whether subagents exist without hallucinating that they do.

#### `TaskUpdate`

Updates a work item.

Recommended input:

```json
{
  "taskId": "1",
  "subject": "New title",
  "description": "New description",
  "activeForm": "Running tests",
  "status": "in_progress",
  "owner": "main",
  "addBlockedBy": ["2"],
  "removeBlockedBy": ["3"],
  "addBlocks": ["4"],
  "removeBlocks": ["5"],
  "evidence": "Tests passed: uv run pytest tests/test_note_edit_tools.py",
  "metadata": {}
}
```

Behavior:

- refuses unknown task ids,
- validates dependency ids,
- prevents cycles in the dependency graph,
- updates `started_at` when moving to `in_progress`,
- requires `evidence` when moving to `completed`,
- sets `completed_at` when moving to `completed`,
- clears or stops active execution only if explicitly requested,
- merges metadata,
- allows `metadata` keys set to `null` to delete keys.

Completion should be stricter than the example project. Requiring an `evidence` string is useful because it makes the model state why the task is complete. Evidence can be "changed X and manually inspected Y" for tasks that cannot run tests, but it should not be empty.

Recommended completion guard:

- If status changes to `completed` and `evidence` is missing, return a tool error.
- If unresolved blockers remain, reject completion unless `force` is true and the evidence explains why.
- If active execution is still running, reject completion unless the model stops it or marks the task as "waiting for background result."

#### `TaskOutput`

Reads the output of a task's active execution.

Recommended input:

```json
{
  "taskId": "1",
  "executionId": "run-8f2a",
  "block": true,
  "timeoutMs": 30000,
  "tailBytes": 12000
}
```

Behavior:

- resolves `executionId` from `taskId` if omitted and the task has one active execution,
- returns `not_running` if the task has no execution,
- with `block=true`, waits for completion or timeout,
- returns output tail and output path,
- never returns huge output by default,
- marks execution output as observed in metadata.

For Aunic, this should not be deprecated initially. Aunic's `read` tool can read output files, but the model benefits from one task-aware way to ask "what happened with task #3?"

#### `TaskStop`

Stops a task's active execution.

Recommended input:

```json
{
  "taskId": "1",
  "executionId": "run-8f2a",
  "reason": "No longer needed"
}
```

Behavior:

- resolves the active execution,
- stops background bash process if it is still alive,
- later stops subagent execution when subagents exist,
- marks execution `stopped`,
- does not delete the work item,
- optionally moves task status back to `pending` or `cancelled` depending on caller input.

Stopping a process and cancelling a task should stay separate. A stopped test run does not mean the task no longer matters.

### Subagent Capability Design
Aunic currently has no subagent implementation. The task suite should still be future-proof.

Recommended capability surface:

- `TaskList` returns `capabilities.subagents_available`.
- `TaskCreate` and `TaskUpdate` accept optional execution intent, but gracefully degrade when unavailable.
- The system prompt says whether subagents are available.
- The task service exposes a Python capability object:

```python
@dataclass(frozen=True)
class TaskRuntimeCapabilities:
    subagents_available: bool
    subagent_types: tuple[str, ...]
    background_executions_available: bool
    max_parallel_executions: int
```

Current value:

```python
TaskRuntimeCapabilities(
    subagents_available=False,
    subagent_types=(),
    background_executions_available=False,
    max_parallel_executions=0,
)
```

After the Phase 2 execution manager exists, `background_executions_available` can become `True`. Aunic has partial background bash support today, but because it discards stdout/stderr and has no task-aware output/stop layer, it should not be advertised as task-managed execution yet.

Future behavior when subagents exist:

- `TaskCreate(runIfPossible=true, executionKind="subagent_if_available")` can create the task and launch a subagent if the task is independent and safe.
- `TaskUpdate(owner="agent:<id>")` can assign ownership.
- A subagent should claim a task before working on it.
- The task list should enforce leases so two agents do not work the same task.
- Subagent output should land in the task execution log.
- The main model should review subagent results before presenting completion to the user if files were modified.

Do not let subagents silently mutate the source note's task board without locks. Task ownership and status updates must be atomic.

### Permissions And Modes
Task metadata operations should be available in every work mode:

- `off`: create/list/update task metadata only.
- `read`: create/list/update task metadata and inspect output.
- `work`: create/list/update task metadata, inspect output, and start/stop executions.

Starting a background bash execution or future subagent is different from editing task metadata. It should respect work mode and permission policy.

Recommended rules:

- `TaskCreate`, `TaskGet`, `TaskList`, and metadata-only `TaskUpdate` are allowed in `off`, `read`, and `work`.
- `TaskOutput` is allowed in all modes if it only reads Aunic-owned output logs.
- `TaskStop` is allowed in all modes for Aunic-owned executions, because stopping runaway work is a safety action.
- Starting a new execution requires `work` if it may run arbitrary shell commands or mutate project files.
- Starting a read-only subagent may be allowed in `read` when that exists.

### Model Instructions
The task prompt should be direct and enforce good habits.

Suggested system/tool guidance:

```text
Use TaskCreate/TaskUpdate for complex work with three or more meaningful steps, explicit user task lists, plan execution, or any work where progress tracking helps the user. Do not create tasks for one trivial action.

Before starting a task, mark it in_progress. When it is fully complete, mark it completed with evidence. Do not mark a task completed if tests failed, implementation is partial, blockers remain, or output has not been checked.

Use TaskList before creating tasks if a task list may already exist. Use dependencies instead of duplicating blocked work. Keep at most one task in_progress unless independent parallel execution is actually available.

Subagents available: false. Do not claim subagent execution happened. If a task would be good for a subagent, create it as pending and note that subagents are unavailable.
```

When subagents become available, the final line can change dynamically.

### UI
Tasks should be visible without opening the transcript.

Recommended UI surfaces:

- A top-bar indicator: `Tasks: 2/5`.
- A toggleable task panel, probably with `/tasks` and a keyboard shortcut.
- A compact task list near the indicator area during runs.
- Task rows with status, id, subject, owner, blockers, and execution status.
- A detail view for one task with description, evidence, metadata, and output path.

The task panel should be associated with the source note. If Aunic later implements the `display_file` vs `context_file` split for plans, task lists should attach to `context_file`, not whichever sidecar file is currently displayed.

Suggested row rendering:

```text
[x] #1 Inspect current runner
[>] #2 Add task service tests
[ ] #3 Implement TaskCreate [blocked by #2]
[!] #4 Run regression tests [run failed]
```

The UI should make completion evidence visible on selection, not cram it into every row.

### Context Behavior
The full task list should not be injected into every model turn.

Recommended behavior:

- If no tasks exist, only the tool descriptions mention task tools.
- If tasks exist, add a tiny task summary attachment:

```text
TASK SUMMARY
Source note: /path/to/task.md
5 tasks: 2 completed, 1 in progress, 2 pending.
In progress: #2 Add task service tests.
Use TaskList for details.
```

- Full descriptions stay behind `TaskGet`.
- Output stays behind `TaskOutput` or `read`.

This keeps the source note primary while still preventing the model from forgetting that a task board exists.

### Transcript Semantics
Task tools should not spam the transcript in note mode.

Recommended persistence:

- `TaskCreate`: ephemeral tool result, durable task-list file.
- `TaskGet`: ephemeral.
- `TaskList`: ephemeral.
- `TaskUpdate`: ephemeral for routine status changes, with optional compact lifecycle row.
- `TaskOutput`: persistent only when output materially informs the note or chat answer.
- `TaskStop`: compact persistent lifecycle row, because stopping a process is important session history.

Compact lifecycle rows could look like:

```json
{
  "type": "task_event",
  "event": "completed",
  "task_id": "2",
  "subject": "Add task service tests",
  "evidence": "uv run pytest tests/test_tasks_tool.py"
}
```

Do not write the entire task list into the transcript after every update. The task file is the durable record.

In chat mode, Aunic currently records all tool calls. That is acceptable, but transcript content should be compact. For example, `TaskList` transcript content should be a brief summary, while `in_memory_content` can include the full structured list for the immediate model turn.

### Runtime Execution Manager
Aunic already has partial background bash support in `src/aunic/tools/bash.py`:

- `bash(run_in_background=true)` starts a subprocess,
- stores the `asyncio.subprocess.Process` in `runtime.session_state.shell.background_tasks`,
- returns a `task_id`,
- discards stdout/stderr to `DEVNULL`.

That is not enough for `TaskOutput`.

Recommended execution manager:

- `src/aunic/tasks/executions.py`
- capture stdout/stderr to an Aunic-owned log file,
- store pid, status, command, cwd, start/end times,
- cap output size,
- support tail reads,
- support blocking wait with timeout,
- stop process groups, not just parent pids,
- mark orphaned executions `stale` on startup if the process is gone.

Then update `bash(run_in_background=true)` to register an execution:

```json
{
  "type": "bash_background",
  "task_id": "1",
  "execution_id": "run-8f2a",
  "pid": 12345,
  "output_path": ".aunic/tasks/lists/task-md-7d1b2c/runs/run-8f2a.log"
}
```

For compatibility, `TaskOutput` should accept both a work-item `taskId` and a raw background execution id.

### File-Level Implementation
Likely implementation areas:

- `src/aunic/tasks/model.py`
  - dataclasses or pydantic-style typed records for task lists, tasks, executions, statuses, and capabilities.

- `src/aunic/tasks/service.py`
  - resolve task list for source note,
  - create/list/get/update tasks,
  - dependency validation,
  - high-water-mark handling,
  - compact summaries for context/UI.

- `src/aunic/tasks/storage.py`
  - `.aunic/tasks/` paths,
  - atomic JSON read/write,
  - lock file handling,
  - recovery from malformed or missing files.

- `src/aunic/tasks/executions.py`
  - background process registry,
  - output log capture,
  - stop/wait/read output functions.

- `src/aunic/tools/tasks.py`
  - `TaskCreate`,
  - `TaskGet`,
  - `TaskList`,
  - `TaskOutput`,
  - `TaskUpdate`,
  - `TaskStop`.

- `src/aunic/tools/note_edit.py`
  - include task tools in both note and chat registries,
  - keep them available independent of work mode where safe.

- `src/aunic/tools/runtime.py`
  - add task service/capabilities to `RunToolContext`,
  - expose source note path,
  - expose execution manager through session state.

- `src/aunic/tools/bash.py`
  - integrate background command execution with the task execution manager,
  - stop discarding background output.

- `src/aunic/context/engine.py`
  - optionally attach compact task summary, not full task list.

- `src/aunic/tui/types.py`
  - add task panel state,
  - add selected task id,
  - add task summary cache.

- `src/aunic/tui/controller.py`
  - add `/tasks`,
  - refresh task summaries after task tool calls,
  - handle task detail view,
  - route stop/output actions from UI.

- `src/aunic/tui/app.py`
  - render task indicator,
  - render task panel,
  - render task detail dialog.

- `src/aunic/transcript/flattening.py`
  - add compact flatteners for task tool results.

### Tests
Important tests:

- Task list path for `/tmp/project/task.md` resolves under `/tmp/project/.aunic/tasks/`.
- Creating the first task returns id `1`.
- Deleted/cancelled tasks do not cause id reuse.
- `TaskCreate` validates required fields and dependency ids.
- `TaskList` returns only tasks for the current source note.
- `TaskList` derives blocked state from unresolved blockers.
- `TaskGet` returns active execution metadata.
- `TaskUpdate(status="completed")` requires evidence.
- `TaskUpdate` rejects dependency cycles.
- `TaskUpdate` can add and remove blockers.
- `TaskUpdate` metadata merge deletes keys set to null.
- `TaskOutput` returns `not_running` for tasks without execution.
- `TaskOutput(block=false)` returns current output without waiting.
- `TaskOutput(block=true)` waits until completion or timeout.
- `TaskStop` stops an active background process and leaves the work item intact.
- Background bash output is written to the execution log.
- Orphaned execution recovery marks dead pids stale.
- Task tool results are ephemeral in note mode except compact lifecycle rows.
- Chat-mode transcript content is compact.
- The compact task summary attachment appears when tasks exist and does not include full descriptions.
- Subagent capability reports `false` today.
- `executionKind="subagent_if_available"` degrades to pending task creation when subagents are unavailable.

### Recommended Rollout
Phase 1: task board without executions

- Add storage and task service.
- Add `TaskCreate`, `TaskGet`, `TaskList`, `TaskUpdate`.
- Add compact task UI.
- Add `/tasks`.
- Add task summary context attachment.
- No background execution or subagents yet.

This phase is immediately useful and low risk.

Phase 2: background execution integration

- Add execution manager.
- Capture background bash output.
- Add `TaskOutput`.
- Add `TaskStop`.
- Add UI output/stop controls.

Phase 3: plan integration

- Let an approved plan create an initial task list.
- Add `plan_id` metadata to tasks created from plans.
- Show plan-linked tasks in the plan approval/implementation flow.

Phase 4: future subagents

- Add subagent capabilities.
- Add task claiming and leases.
- Let subagents own and update tasks.
- Store subagent output under the task execution log.
- Require main-agent review before declaring subagent-written work complete.

### Best Shape For Aunic
The best Aunic version is not a hidden todo list for the model. It is a visible, source-note-bound task board.

The user should be able to glance at Aunic and see:

- what the model thinks the work is,
- what is in progress,
- what is blocked,
- what finished and why,
- what background execution is still running,
- whether any future subagent has been assigned work.

The model should be able to use tasks without turning the transcript into noise. The durable record belongs in `.aunic/tasks/`; the transcript should only record meaningful lifecycle moments. This keeps Aunic's thesis intact: the note remains the primary context, the transcript remains a log, and tasks become shared coordination state rather than assistant-only scratchwork.
