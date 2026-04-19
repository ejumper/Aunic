# Stop Process Tool

## coding-agent-program-example Implementation
The example project does not expose a broad arbitrary-PID "kill process" tool. Its process stopping behavior is modeled as `TaskStop`: a tool for stopping a known running background task that the application created and registered in app state.

That distinction is important. The model does not get a general-purpose process killer by default. It gets a safe control surface over app-owned runtime work.

Relevant files in the example:

- `src/tools/TaskStopTool/TaskStopTool.ts`
- `src/tools/TaskStopTool/prompt.ts`
- `src/tools/TaskStopTool/UI.tsx`
- `src/tasks/stopTask.ts`
- `src/Task.ts`
- `src/tasks.ts`
- `src/tasks/LocalShellTask/LocalShellTask.tsx`
- `src/tasks/LocalShellTask/killShellTasks.ts`
- `src/tasks/LocalShellTask/guards.ts`
- `src/utils/ShellCommand.ts`
- `src/utils/task/framework.ts`
- `src/utils/task/diskOutput.ts`
- `src/utils/genericProcessUtils.ts`

### Tool Shape
The public tool is named `TaskStop`.

Input:

```json
{
  "task_id": "b123"
}
```

It also accepts a deprecated `shell_id` field for compatibility with the old `KillShell` tool. `KillShell` remains an alias, but the preferred model-facing concept is now "stop a task" rather than "kill a shell."

Output:

```json
{
  "message": "Successfully stopped task: b123 (npm run dev)",
  "task_id": "b123",
  "task_type": "local_bash",
  "command": "npm run dev"
}
```

Important tool metadata:

- `shouldDefer: true`: stopping happens through the host's deferred tool execution path.
- `isConcurrencySafe() true`: it can run without requiring exclusive access to the entire turn.
- `searchHint: "kill a running background task"`: helps tool selection.
- `userFacingName: "Stop Task"`: the UI presents it as a task control, not a raw OS control.
- `maxResultSizeChars: 100_000`: large enough for structured output, though the actual result is small.

The prompt is intentionally small:

- stop a running background task by id,
- use `task_id`,
- return success or failure,
- use it for long-running tasks that need to be terminated.

### Validation
`TaskStopTool.validateInput` performs the core safety checks before execution:

1. Resolve `task_id` or deprecated `shell_id`.
2. Reject if no id was provided.
3. Look up the task in `appState.tasks`.
4. Reject if no task exists with that id.
5. Reject if the task status is not `running`.

This means the tool cannot stop arbitrary processes. The target must be a registered runtime task in the current application state.

### Shared Stop Logic
The tool delegates to `stopTask` in `src/tasks/stopTask.ts`.

`stopTask` is shared by:

- the LLM-invoked `TaskStopTool`,
- SDK stop-task control requests.

Its flow is:

1. Look up the task by id in app state.
2. Require that it exists.
3. Require that `status === "running"`.
4. Resolve the concrete task implementation with `getTaskByType(task.type)`.
5. Call that task implementation's `kill(taskId, setAppState)` method.
6. For local shell tasks, mark the task as notified and emit a direct SDK termination event.
7. Return task id, task type, and the command or description.

Failure is represented with a typed `StopTaskError`:

- `not_found`
- `not_running`
- `unsupported_type`

This is a clean design because the model-facing tool does not need to know how to kill every possible runtime type. It only validates the target and dispatches to the registered runtime task handler.

### Runtime Task Model
Runtime tasks are defined in `src/Task.ts`. These are not the same as the durable work-item tasks managed by `TaskCreate` and `TaskUpdate`.

Runtime task types include:

- `local_bash`
- `local_agent`
- `remote_agent`
- `in_process_teammate`
- `local_workflow`
- `monitor_mcp`
- `dream`

Runtime statuses are:

- `pending`
- `running`
- `completed`
- `failed`
- `killed`

Every task implementation exposes:

```ts
type Task = {
  name: string
  type: TaskType
  kill(taskId: string, setAppState: SetAppState): Promise<void>
}
```

`src/tasks.ts` registers the available task implementations and lets `stopTask` find the correct one by task type.

This gives the stop tool polymorphism without making the tool itself complicated. A local shell, a remote agent, and a workflow can all be stopped through one surface as long as each runtime type owns its own `kill` behavior.

### Local Shell Stop Behavior
For background shell commands, the concrete implementation is `LocalShellTask`.

The important path is:

1. `TaskStopTool.call(...)`
2. `stopTask(taskId, context)`
3. `getTaskByType("local_bash")`
4. `LocalShellTask.kill(taskId, setAppState)`
5. `killTask(taskId, setAppState)`
6. `task.shellCommand?.kill()`
7. `task.shellCommand?.cleanup()`

`killTask` in `src/tasks/LocalShellTask/killShellTasks.ts`:

- verifies the task is still running,
- verifies it is a local shell task,
- calls `shellCommand.kill()`,
- calls `shellCommand.cleanup()`,
- unregisters cleanup hooks,
- clears cleanup timers,
- updates task state to `status: "killed"`,
- marks it `notified: true`,
- clears the in-memory `shellCommand`,
- records `endTime`,
- evicts task output.

The actual process kill happens in `src/utils/ShellCommand.ts`.

`ShellCommandImpl.kill()` calls `#doKill()`. `#doKill()`:

- marks the shell command status as `killed`,
- uses `tree-kill` on the child process pid with `SIGKILL`,
- resolves the command result as interrupted.

Using `tree-kill` matters because shell commands often spawn children. Killing only the shell parent can leave grandchildren running. The example chooses a decisive kill for explicit stop requests rather than a graceful `SIGTERM` followed by escalation.

### Background Output And Notifications
The example stores runtime output in task output files rather than keeping all output in the tool result.

For local shell tasks:

- output belongs to a `TaskOutput` instance,
- background commands write to an output file,
- task notifications include the output file path,
- output can be read later by task-output/read-style tools,
- terminal output files can be evicted after consumption.

When `TaskStop` stops a local shell task, it deliberately suppresses the noisy "exit code 137" style notification. The stop itself is already known, so showing an additional killed-process notification would be redundant. To preserve SDK semantics, it emits a task-terminated SDK event directly when it suppresses the XML-style notification.

The UI result is intentionally compact. `TaskStopTool/UI.tsx` renders no visible tool-use message, then renders a result such as:

```text
npm run dev - stopped
```

Long commands are truncated to two lines and about 160 display characters unless verbose mode is enabled.

### Orphan Cleanup
The example also cleans up background shell tasks when their owning agent exits.

`killShellTasksForAgent`:

- scans app state for running local shell tasks owned by an agent id,
- kills each matching shell task,
- removes queued notifications for that now-dead agent.

This prevents long-lived orphaned processes from surviving after the agent that started them is gone. The comment in the code explicitly calls out runaway long-lived fake log processes as the kind of incident this prevents.

### Generic Process Utilities
The example also has utilities for process inspection:

- `isProcessRunning(pid)`
- `getAncestorPidsAsync(pid)`
- `getProcessCommand(pid)`
- `getAncestorCommandsAsync(pid)`
- `getChildPids(pid)`

These utilities are platform-aware and handle Unix/Windows differences. They are not the main stop-tool path. They are supporting infrastructure for lock recovery, process ancestry, and diagnostics.

The stop tool itself still works through registered runtime tasks, not arbitrary pids.

### Design Lessons For Aunic
The transferable ideas are:

- Stop app-owned runtime work by stable task/execution id, not by arbitrary pid.
- Validate that the target exists and is currently running before attempting to stop it.
- Keep the model-facing tool small and delegate to type-specific runtime handlers.
- Treat stopping as a lifecycle event on a registered execution, not as a shell command.
- Kill process trees, not only parent processes.
- Clean up in-memory handles, timers, and output resources after stopping.
- Suppress noisy duplicate notifications after intentional stops.
- Record a compact user-visible stop event rather than dumping raw process details.
- Clean up background tasks when their owner exits.
- Keep process inspection utilities separate from process mutation.

The parts Aunic should not copy directly:

- The example immediately uses `SIGKILL` for explicit shell stops. Aunic should prefer graceful termination first, then escalate.
- The example is chat/session-first. Aunic should tie background executions to the active source note and `.aunic/` metadata where possible.
- The example uses the broad word "task" for durable work items and runtime process handles. Aunic should keep work items, executions, and OS processes distinct in its internal names.

## Implementing in Aunic
Aunic should implement this as a safe stop control for Aunic-owned background executions first, with optional explicit external-PID stopping later.

The most important product decision: this tool should not be a generic "let the model kill any process on my machine" capability. The default should be: stop a process that Aunic started, knows about, can describe, and can clean up.

Aunic's ethos points strongly in that direction. The tool should make the computer more transparent and controllable for the user, not give an opaque model a dangerous power with little context.

### Current Aunic State
Aunic already has partial background bash support in `src/aunic/tools/bash.py`.

Current behavior:

- `bash(run_in_background=true)` starts a subprocess.
- It stores the raw `asyncio.subprocess.Process` in `runtime.session_state.shell.background_tasks`.
- It returns a generated id such as `bg-1`.
- It returns the pid.
- It discards stdout and stderr to `DEVNULL`.

Current session state lives in `src/aunic/tools/runtime.py`:

```python
@dataclass
class ShellSessionState:
    cwd: Path
    base_env: dict[str, str] | None = None
    env_overlays: dict[str, str] = field(default_factory=dict)
    background_tasks: dict[str, asyncio.subprocess.Process] = field(default_factory=dict)
    next_background_id: int = 1
```

This is enough to start something and remember its process handle. It is not enough to build a robust stop-process tool.

Problems to fix:

- There is no metadata beyond the raw process handle.
- There is no status tracking.
- There is no start/end time.
- There is no command/cwd record inside the background task state.
- There is no output log.
- There is no process-group handling.
- There is no cleanup path for children/grandchildren.
- There is no idempotent "already exited" state.
- There is no UI surface listing running background commands.

### Product Model
Aunic should use three distinct concepts:

- Work item: a durable task-board item, if the Tasks tool exists.
- Execution: a running or finished Aunic-managed job.
- OS process: the pid/process group that backs an execution.

The Stop Process tool should operate on executions. It may expose pid details for transparency, but pid should not be the preferred target.

Recommended first-class target:

```json
{
  "background_id": "bg-1"
}
```

If the Tasks tool described in `tasks-tool.md` exists, `TaskStop` should stop the execution associated with a work item. `stop_process` should be the lower-level execution control.

Relationship:

- `TaskStop(taskId="3")`: stop task #3's active execution.
- `stop_process(background_id="bg-1")`: stop this raw Aunic background execution.
- Both call the same execution manager internally.

### Tool Name
Aunic's current tool names are snake_case: `bash`, `read`, `note_edit`, `web_search`, and so on. For Aunic-native use, the provider-facing tool should be:

```text
stop_process
```

If Aunic also implements the compatibility-oriented task suite, expose:

```text
TaskStop
```

as the task-level tool. Do not make `TaskStop` the only process stop surface, because Aunic already has `bash(run_in_background=true)` returning `bg-*` ids that are not necessarily tied to durable work items.

### Recommended Tool Schema
Initial safe version:

```json
{
  "background_id": "bg-1",
  "reason": "No longer needed",
  "force": false,
  "grace_ms": 3000
}
```

Fields:

- `background_id`: required for the MVP. Must refer to an Aunic-owned background execution.
- `reason`: optional user/model-readable reason, useful for transcript rows.
- `force`: if true, skip or shorten graceful shutdown and escalate to kill.
- `grace_ms`: how long to wait after graceful termination before escalation. Clamp to a safe range.

Later external-PID extension:

```json
{
  "pid": 12345,
  "include_children": true,
  "signal": "TERM",
  "reason": "Runaway process",
  "force": false,
  "grace_ms": 3000
}
```

Rules for the later extension:

- Require exactly one of `background_id` or `pid`.
- Prefer `background_id`.
- Treat raw `pid` as an advanced operation requiring explicit user permission every time.
- Never allow an "always allow" permission for arbitrary pids.

### Recommended Output
Return structured data that is compact enough for transcript flattening but complete enough for the model to reason about:

```json
{
  "type": "process_stop",
  "status": "stopped",
  "target": {
    "kind": "aunic_background",
    "background_id": "bg-1",
    "pid": 12345,
    "pgid": 12345,
    "command": "npm run dev",
    "cwd": "/path/to/project"
  },
  "signals": ["SIGTERM"],
  "forced": false,
  "exit_code": -15,
  "message": "Stopped background process bg-1."
}
```

Possible statuses:

- `stopped`
- `already_exited`
- `not_found`
- `permission_denied`
- `failed`

For `already_exited`, include the known return code if available. Idempotent stopping is useful because the model or user may click stop shortly after a process exits naturally.

### Execution State
Replace `background_tasks: dict[str, Process]` with a richer state object.

Recommended model:

```python
@dataclass
class BackgroundProcessState:
    id: str
    process: asyncio.subprocess.Process
    command: str
    description: str | None
    cwd: Path
    pid: int | None
    pgid: int | None
    started_at: datetime
    ended_at: datetime | None = None
    status: Literal["running", "completed", "failed", "stopped"] = "running"
    returncode: int | None = None
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    owner: str | None = None
```

If Aunic adds the execution manager from `tasks-tool.md`, this should become an `ExecutionState` instead of a shell-specific class:

```python
@dataclass
class ExecutionState:
    id: str
    kind: Literal["bash", "subagent"]
    status: Literal["running", "completed", "failed", "stopped", "stale"]
    command: str | None
    description: str | None
    cwd: Path | None
    pid: int | None
    pgid: int | None
    output_path: Path | None
    started_at: datetime
    ended_at: datetime | None
    exit_code: int | None
    source_note: Path | None
    work_item_id: str | None
```

Even if the first implementation only supports background bash, use the broader execution shape internally. It will make future `TaskStop`, subagent stop, and output reading much cleaner.

### Process Groups
Aunic should start background subprocesses in their own process group/session.

On Unix, use one of:

- `start_new_session=True` if available through the Python subprocess path being used.
- `preexec_fn=os.setsid` where appropriate.

Then stop the process group rather than only the parent pid:

```python
os.killpg(pgid, signal.SIGTERM)
```

After a timeout:

```python
os.killpg(pgid, signal.SIGKILL)
```

This prevents the common failure mode where Aunic kills `/bin/bash` but leaves `npm`, `vite`, `pytest`, `sleep`, or a server process running underneath it.

For Windows, use platform-specific process tree termination later. The first implementation can document Unix-first behavior if Aunic's immediate environment is Linux, but the abstraction should not bake in Unix-only assumptions.

### Stop Algorithm
Recommended behavior for Aunic-owned background executions:

1. Parse and validate arguments.
2. Resolve `background_id` to an Aunic-owned execution.
3. If no execution exists, return `not_found` as a tool error or structured failure.
4. If the process already exited, update state and return `already_exited`.
5. Mark the execution as `stopping` if that state exists.
6. Send `SIGTERM` to the process group by default.
7. Wait up to `grace_ms`, default 3000ms.
8. If the process exits, mark `stopped`, capture return code, and clean up handles.
9. If still running and `force` is true, send `SIGKILL`.
10. If still running and `force` is false, either escalate by policy or return a `failed` result saying the process did not stop before the grace timeout.
11. Persist a compact lifecycle event in the transcript.
12. Refresh any UI background process indicator.

I recommend escalating to `SIGKILL` after the grace period by default for Aunic-owned background processes. The tool's purpose is to stop something the application started. A stuck child process is usually worse than a decisive stop.

`force=true` should mean "skip graceful waiting and kill immediately", not "eventually guarantee stop." The default should already eventually guarantee stop for Aunic-owned executions.

### External PID Handling
External PID stopping should be a later feature, not the MVP.

If added, it should be heavily guarded.

Required checks:

- pid must be greater than 1,
- pid must not be Aunic's own process,
- pid must be owned by the current OS user unless explicitly elevated outside Aunic,
- command line and cwd should be inspected when possible,
- parent/child tree should be shown in the permission prompt,
- system-critical processes should be blocked,
- repeated identical requests should hit doom-loop protection,
- no persistent "always allow" grant should be available for raw pid stopping.

Permission request should say something concrete:

```text
stop_process wants to terminate PID 12345
Command: node /path/to/vite
CWD: /path/to/project
Children: 12346, 12347
Reason: No longer needed
```

Aunic should make the user approve that with full context. This fits the thesis: the user and model share transparent control, and the user remains in charge of dangerous machine actions.

### Permissions And Modes
Stopping Aunic-owned background work is a safety action. It should be available in all modes:

- `off`: may stop Aunic-owned background executions.
- `read`: may stop Aunic-owned background executions.
- `work`: may stop Aunic-owned background executions.

This is different from starting a process. Starting a process can mutate the environment and should remain work-mode gated. Stopping a process that Aunic already started is how the user/model prevents runaway behavior.

External PID stopping should require explicit permission regardless of mode.

Recommended policy:

- Aunic-owned `background_id`: `allow` by default, with compact logging.
- Unknown `background_id`: fail safely.
- Raw `pid`: `ask` every time.
- Raw `pid` for protected/system process: deny.

### Transcript Semantics
Stop events should be persistent but compact.

Stopping a background process is meaningful session history. It explains why a server disappeared, why a test run did not finish, or why a long-running monitor stopped. But the transcript should not contain a giant process dump.

Recommended transcript content:

```json
{
  "type": "process_stop",
  "status": "stopped",
  "background_id": "bg-1",
  "pid": 12345,
  "command": "npm run dev",
  "reason": "No longer needed"
}
```

`src/aunic/transcript/flattening.py` can render this as:

```text
Stopped background command bg-1: npm run dev
```

This matches Aunic's note-first design. The note remains the primary context. The transcript records the lifecycle event without becoming the workspace.

### UI
Aunic needs a visible background process surface if it adds a stop tool.

At minimum, the indicator area should show running background executions:

```text
Processes: bg-1 npm run dev
```

A better shape:

```text
Processes: 2 running
  bg-1 npm run dev              [stop]
  bg-2 pytest -q                [tail] [stop]
```

Recommended UI behavior:

- show id, short command/description, elapsed time, and status,
- let the user stop a process without prompting the model,
- let the model call `stop_process` when appropriate,
- keep user and model controls pointed at the same execution manager,
- show completed/stopped status briefly, then collapse or archive,
- expose output path or tail once output capture exists.

This matters philosophically for Aunic. The user should have the same operational visibility the model has.

### Integration With Bash
Update `bash(run_in_background=true)` so it registers a real execution.

Recommended background bash return:

```json
{
  "type": "bash_background",
  "background_id": "bg-1",
  "pid": 12345,
  "pgid": 12345,
  "command": "npm run dev",
  "description": "Start dev server",
  "cwd": "/path/to/project",
  "output_path": "/path/to/project/.aunic/runs/bg-1.log"
}
```

Keep `task_id` temporarily for compatibility if existing model prompts already expect it:

```json
{
  "task_id": "bg-1",
  "background_id": "bg-1"
}
```

But internally, prefer `background_id` or `execution_id`. The word `task_id` is confusing once Aunic has real work-item tasks.

Also stop discarding stdout/stderr. Even before the full Tasks tool exists, background processes should write output to an Aunic-owned log file. Without output, the model can start a server but cannot inspect why it failed.

### Integration With Tasks
If the Tasks tool is implemented, `TaskStop` should be a thin wrapper over the same execution manager.

Example:

```text
TaskStop(taskId="3")
  -> resolve task #3
  -> resolve active execution run-8f2a
  -> ProcessManager.stop("run-8f2a")
  -> mark execution stopped
  -> leave task #3 intact
```

Stopping an execution must not automatically complete, delete, or cancel the work item.

Useful statuses:

- Work item remains `in_progress` if the stop was just to rerun a command.
- Work item can move back to `pending` if the model explicitly pauses it.
- Work item can move to `cancelled` only if the caller explicitly cancels the work.

This avoids the common agent mistake of treating "the process stopped" as "the task is done."

### File-Level Implementation
Likely implementation areas:

- `src/aunic/processes/model.py`
  - `ExecutionState` / `BackgroundProcessState`,
  - status literals,
  - signal/result dataclasses.

- `src/aunic/processes/manager.py`
  - register background executions,
  - stop executions,
  - poll/update return codes,
  - cleanup handles,
  - process group helpers,
  - optional external pid inspection.

- `src/aunic/tools/stop_process.py`
  - tool schema,
  - argument parsing,
  - permission requests,
  - execution manager call,
  - compact result shaping.

- `src/aunic/tools/bash.py`
  - create process groups for background commands,
  - register execution metadata,
  - capture output logs,
  - return `background_id` and output path.

- `src/aunic/tools/runtime.py`
  - replace raw `background_tasks` with execution state,
  - expose execution manager or state through `ToolSessionState`.

- `src/aunic/tools/note_edit.py`
  - include `stop_process` in registries even outside work mode,
  - keep process start gated to work mode through `bash`.

- `src/aunic/transcript/flattening.py`
  - render compact `process_stop` rows.

- `src/aunic/tui/controller.py`
  - expose user stop action,
  - refresh background process state,
  - handle process list commands.

- `src/aunic/tui/app.py`
  - render background process indicator/panel,
  - render stop buttons/actions.

If the task execution manager from `tasks-tool.md` is built first, put most of this under `src/aunic/tasks/executions.py` instead of `src/aunic/processes/`. The important thing is to avoid two separate process registries.

### Model Instructions
The tool prompt should make the safe boundary explicit.

Suggested prompt:

```text
Use stop_process to stop a running Aunic-owned background execution when it is no longer needed, appears stuck, is blocking progress, or should be restarted with different arguments. Prefer background_id/execution_id returned by bash or task tools. Do not use this tool to stop arbitrary system processes unless the user explicitly asks and the runtime requests permission. Stopping an execution does not mean the associated work item is completed.
```

If external pid stopping is not implemented, say so directly in the tool description:

```text
This tool only stops background executions started by Aunic.
```

That helps prevent the model from trying to use it as a general system administration tool.

### Tests
Important tests:

- `bash(run_in_background=true)` registers a background execution with command, cwd, pid, and status.
- background executions start in a separate process group.
- `stop_process(background_id="bg-1")` sends graceful termination and returns `stopped`.
- a process that ignores `SIGTERM` is escalated to `SIGKILL`.
- child processes are stopped with the parent process group.
- stopping an unknown id returns a safe failure.
- stopping an already exited process returns `already_exited`.
- repeated stop calls are idempotent.
- stopped execution state records `ended_at` and return code.
- stop result transcript flattening is compact.
- `stop_process` is available in `off`, `read`, and `work` for Aunic-owned executions.
- starting background bash remains gated to `work`.
- external pid stop, if implemented, asks permission every time.
- external pid stop refuses pid `0`, pid `1`, Aunic's own pid, and protected/system processes.
- UI stop action and model tool call use the same execution manager.
- output log handles are closed after stop.
- no orphaned child process survives a stop in the normal Unix path.

### Recommended Rollout
Phase 1: Aunic-owned background stop

- Add execution metadata to background bash.
- Start background bash in a process group.
- Add `stop_process(background_id=...)`.
- Stop process groups with graceful termination plus escalation.
- Add compact transcript flattening.
- Add tests for stop/idempotency/children.

Phase 2: Output-aware background executions

- Capture stdout/stderr to `.aunic/runs/` or the future task execution log.
- Return output path from background bash.
- Add UI tail/read affordances.
- Mark execution completion/stopped status automatically.

Phase 3: UI process panel

- Show running background executions in the indicator area.
- Add user stop controls.
- Keep recently stopped/completed executions visible briefly.

Phase 4: Task integration

- Route `TaskStop` through the same execution manager.
- Attach executions to task work items.
- Ensure stopping execution does not imply completing the work item.

Phase 5: External PID stop, optional

- Add process inspection.
- Add strict permission prompts.
- Add platform-aware process tree handling.
- Keep raw pid stopping separate from the default Aunic-owned path.

### Best Shape For Aunic
The best Aunic version is a transparent process control plane, not a kill command wrapper.

The user and model should be able to see:

- what Aunic started,
- why it was started,
- where it is running,
- where its output is,
- whether it is still alive,
- how to stop it safely.

The model should stop only the execution it means to stop. The user should be able to do the same from the UI. The transcript should record that lifecycle event compactly. The active note should remain the primary context.

That shape fits Aunic better than copying a chat-agent stop tool directly: background processes become visible shared state around the note, not hidden model side effects.
