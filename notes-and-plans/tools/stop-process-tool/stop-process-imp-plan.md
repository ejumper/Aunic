# `stop_process` Tool — Implementation Plan

## Context

Aunic already lets the model start background commands via `bash(run_in_background=true)` ([src/aunic/tools/bash.py:168](src/aunic/tools/bash.py#L168)), which spawns a subprocess, stores it in [`ShellSessionState.background_tasks`](src/aunic/tools/runtime.py#L69) keyed by `bg-N`, and returns the id and pid. **There is no way to stop these processes from inside Aunic.** They survive until the process itself exits or the host shell closes — a long-running `npm run dev` started by the model becomes invisible runtime state with no controllable surface.

This plan adds a `stop_process` tool that lets the model (and, eventually, the user) terminate Aunic-owned background executions. It deliberately scopes the MVP to **Aunic-owned `background_id`s only** — no raw-pid stopping, no UI panel, no output capture. Those are orthogonal capabilities sketched in [stop-process-tool.md](../stop-process-tool/stop-process-tool.md) and listed under "Future Work" below.

The motivating ethos (from the research doc): the user and model should share a transparent process control plane. The process Aunic started should be the process Aunic can stop, cleanly.

---

## Gold-Standard Reference

The example agent's `TaskStop` tool ([Backups/coding-agent-program-example/src/tools/TaskStopTool/TaskStopTool.ts](../../../../Backups/coding-agent-program-example/src/tools/TaskStopTool/TaskStopTool.ts)) is the gold standard. It is small, synchronous, and idempotent. Mapping each design decision:

| Concern | Gold standard | Aunic plan | Rationale for any deviation |
|---|---|---|---|
| Tool name | `TaskStop` (PascalCase) | `stop_process` (snake_case) | Aunic's existing tool names are snake_case (`bash`, `note_edit`, `web_search`). |
| Target id | `task_id` against `appState.tasks` | `background_id` against `ShellSessionState.background_processes` | "Task" is reserved for the future Tasks tool's durable work items; "background process" is what Aunic actually has today. |
| Lookup behavior | Synchronous flat dict access | Same | No reason to deviate. |
| Validation: missing target | Reject with errorCode 1 | Tool error `not_found` | Same shape, mapped to Aunic's `failure_payload`. |
| Validation: not running | Reject with errorCode 3 | Return `already_exited` (with last known exit code if available) | Idempotency is more useful than rejection — the model and user race naturally. The example was forced to reject because it didn't poll. |
| Kill mechanism | `tree-kill(pid, 'SIGKILL')` immediately | `os.killpg(pgid, SIGTERM)` → wait `grace_ms` → escalate to `SIGKILL` | The research doc argues persuasively for graceful-first. Servers like vite/npm need to flush; Aunic already has this exact pattern in [codex_client.py:74-80](src/aunic/providers/codex_client.py#L74-L80). `force=true` retains the model's SIGKILL escape hatch. |
| Process tree handling | `tree-kill` npm package | `start_new_session=True` + `os.killpg` | Python's stdlib gives us process groups natively. `tree-kill` exists because Node has no portable `killpg`. We don't need it. |
| Output handling after kill | Drains stdout/stderr, evicts output file | Defer — see "Future Work" | Output capture is its own feature (needs log file location, eviction policy, companion read tool). Bundling it here doubles the diff. |
| Permission gating | None (LLM may call directly) | None for known `background_id` | The user already approved the `bash` that started it. The id is not user-supplied. |
| Feature flag | Always available | Always available | Same. |
| Mode gating | N/A | Available in `off`/`read`/`work` | Stopping is a safety action; the model may need to halt a runaway server even in read-only modes. Starting remains `work`-only. |
| Result shape | `{message, task_id, task_type, command}` | `{type, status, background_id, pid, command, description, exit_code, signals_sent, forced, elapsed_ms, reason}` | Aunic transcript rendering is structured-data-first; a richer payload renders the same compact one-liner but supports follow-up reasoning. |
| Persistence | Notification suppression via `notified: true` | `persistence="persistent"` (transcript row) | Aunic doesn't have the example's notification queue; lifecycle events live in the transcript. |
| Orphan cleanup on agent exit | `killShellTasksForAgent()` reaps spawned bash tasks | Defer | Aunic has no subagent system yet. When subagents land, mirror this. |

**Direct adoptions** (no deviation): synchronous lookup, idempotent stops, atomic state transition, compact result, no permission prompt for owned executions, no feature flag.

---

## Tool Design

### Name and identity

```
name: stop_process
description (short): Stop an Aunic-owned background process by id.
```

Long description (model-facing):

```
Stop a background process started by Aunic (e.g. by bash with run_in_background=true).
Provide the background_id returned when the process was started (e.g. "bg-1").
By default sends SIGTERM and waits up to grace_ms before escalating to SIGKILL.
Use force=true to skip the graceful wait.
Idempotent: stopping an already-exited process succeeds with status="already_exited".
This tool only stops processes Aunic started; it cannot stop arbitrary system processes.
```

The trailing sentence is load-bearing — it prevents the model from trying to use this as a general system administration tool when raw-pid stopping is later added.

### MVP schema

```python
@dataclass(frozen=True)
class StopProcessArgs:
    background_id: str
    force: bool = False
    grace_ms: int = 3000
    reason: str | None = None
```

JSON schema for the provider:

```json
{
  "background_id": {"type": "string", "description": "Required. The bg-N id returned by bash."},
  "force": {"type": "boolean", "default": false, "description": "If true, send SIGKILL without waiting."},
  "grace_ms": {"type": "integer", "default": 3000, "minimum": 0, "maximum": 30000,
               "description": "Wait time after SIGTERM before escalating to SIGKILL."},
  "reason": {"type": "string", "description": "Optional human-readable reason for the transcript."}
}
```

### Validation rules (in `parse_arguments`)

1. `background_id` must be a non-empty string. Reject as `validation_error` if missing/blank.
2. `grace_ms` clamped to `[0, 30_000]`. Out-of-range values are clamped (not rejected) and the result reports `clamped_grace_ms` for transparency.
3. `force=True` makes `grace_ms` irrelevant — execution skips the SIGTERM wait entirely.

The `background_id` is **not** validated against the registry at parse time — that's an execution concern (the process may have exited between argument parsing and execution).

---

## Background Process State

Replace the raw `dict[str, asyncio.subprocess.Process]` in [`ShellSessionState`](src/aunic/tools/runtime.py#L64-L70) with a richer state object so `stop_process` (and the future TaskList/output tools) can answer "what is bg-1?" without re-reading proc.

### New dataclass (in [src/aunic/tools/runtime.py](src/aunic/tools/runtime.py))

```python
import time
from datetime import datetime
from typing import Literal

BackgroundProcessStatus = Literal["running", "stopped", "exited", "failed"]

@dataclass
class BackgroundProcessState:
    background_id: str                  # "bg-1"
    process: asyncio.subprocess.Process # raw handle
    command: str                        # the shell command text
    description: str | None             # bash's `description` arg, if provided
    cwd: Path
    pid: int
    pgid: int                           # process group id (== pid for new sessions)
    started_at: datetime                # for elapsed_ms computation
    started_monotonic: float            # time.monotonic() — used for elapsed_ms
    status: BackgroundProcessStatus = "running"
    returncode: int | None = None
    ended_at: datetime | None = None
    signals_sent: tuple[str, ...] = ()
    stop_reason: str | None = None
```

### `ShellSessionState` changes

```python
@dataclass
class ShellSessionState:
    cwd: Path
    base_env: dict[str, str] | None = None
    env_overlays: dict[str, str] = field(default_factory=dict)
    background_processes: dict[str, BackgroundProcessState] = field(default_factory=dict)  # renamed
    next_background_id: int = 1
```

Rename `background_tasks` → `background_processes` to avoid collision with the future Tasks tool's "task" concept. The only existing read-site is the bash tool itself ([src/aunic/tools/bash.py:168](src/aunic/tools/bash.py#L168)) and the type alias in [src/aunic/tools/runtime.py:69](src/aunic/tools/runtime.py#L69), so the rename is contained.

### Helper API on `ShellSessionState`

```python
def register_background_process(self, state: BackgroundProcessState) -> None:
    self.background_processes[state.background_id] = state

def get_background_process(self, background_id: str) -> BackgroundProcessState | None:
    return self.background_processes.get(background_id)

def next_bg_id(self) -> str:
    bid = f"bg-{self.next_background_id}"
    self.next_background_id += 1
    return bid
```

These keep the dict access pattern out of the tools — registry semantics live with the state.

---

## Process Group & Stop Algorithm

### Background bash spawning ([src/aunic/tools/bash.py](src/aunic/tools/bash.py))

When `run_in_background=True`, spawn the subprocess with `start_new_session=True` so it becomes a process group leader. Then registered `pgid` equals the process's `pid`:

```python
process = await asyncio.create_subprocess_exec(
    "bash", "-c", args.command,
    stdout=asyncio.subprocess.DEVNULL,   # unchanged for MVP
    stderr=asyncio.subprocess.DEVNULL,   # unchanged for MVP
    cwd=str(runtime.cwd),
    env=runtime.session_state.shell.base_env,
    start_new_session=True,              # NEW: makes the process a session leader → its own pgid
)
bg_id = runtime.session_state.shell.next_bg_id()
state = BackgroundProcessState(
    background_id=bg_id,
    process=process,
    command=args.command,
    description=args.description,
    cwd=runtime.cwd,
    pid=process.pid,
    pgid=process.pid,                    # session leader: pgid == pid
    started_at=datetime.now(),
    started_monotonic=time.monotonic(),
)
runtime.session_state.shell.register_background_process(state)
```

The `start_new_session=True` is the entire mechanism that makes child reaping work — `os.killpg(pgid, ...)` then signals the leader and every descendant. No `tree-kill` equivalent needed.

### Stop algorithm ([src/aunic/tools/stop_process.py](src/aunic/tools/stop_process.py))

```python
async def execute_stop_process(runtime: RunToolContext, args: StopProcessArgs) -> ToolExecutionResult:
    state = runtime.session_state.shell.get_background_process(args.background_id)
    if state is None:
        return _failure("not_found", f"No background process with id: {args.background_id}",
                        background_id=args.background_id)

    # Idempotency: if the process already exited (naturally or via a prior stop),
    # update state from the OS and return already_exited.
    if state.process.returncode is not None or state.status != "running":
        _refresh_terminal_state(state)  # set returncode/status/ended_at if not already
        return _result("already_exited", state, signals_sent=(), forced=False, reason=args.reason)

    grace_ms = max(0, min(args.grace_ms, 30_000))
    forced = bool(args.force)
    signals: list[str] = []

    started_kill = time.monotonic()

    if forced or grace_ms == 0:
        _signal_pgid(state.pgid, signal.SIGKILL)
        signals.append("SIGKILL")
    else:
        _signal_pgid(state.pgid, signal.SIGTERM)
        signals.append("SIGTERM")
        try:
            await asyncio.wait_for(state.process.wait(), timeout=grace_ms / 1000)
        except asyncio.TimeoutError:
            _signal_pgid(state.pgid, signal.SIGKILL)
            signals.append("SIGKILL")
            forced = True
            await state.process.wait()  # SIGKILL cannot be ignored
        else:
            pass  # exited gracefully

    if state.process.returncode is None:
        await state.process.wait()       # final reap

    state.returncode = state.process.returncode
    state.status = "stopped"
    state.ended_at = datetime.now()
    state.signals_sent = tuple(signals)
    state.stop_reason = args.reason

    elapsed_ms = int((time.monotonic() - started_kill) * 1000)
    return _result("stopped", state, signals_sent=tuple(signals), forced=forced,
                   reason=args.reason, elapsed_ms=elapsed_ms)
```

`_signal_pgid(pgid, sig)` wraps `os.killpg(pgid, sig)` and tolerates `ProcessLookupError` (race: process exited between check and signal).

### Mirror existing graceful-shutdown precedent

[src/aunic/providers/codex_client.py:74-80](src/aunic/providers/codex_client.py#L74-L80) already does `terminate() → wait_for(timeout=2) → kill() → wait()`. Our algorithm is the same shape, just signal-explicit and process-group-aware.

### Linux-only for MVP

`os.killpg` is POSIX. Aunic's primary platform is Linux per the working directory hints. The `start_new_session=True` argument is also POSIX-only in `asyncio.create_subprocess_exec` — Windows already wouldn't take this path correctly. **Document Linux-only in the tool docstring**; cross-platform (Windows `taskkill /t /f`) is future work.

---

## Registry & Mode Availability

### New builder

In [src/aunic/tools/note_edit.py](src/aunic/tools/note_edit.py) (alongside `build_bash_tool_registry`):

```python
def build_stop_process_tool_registry() -> tuple[ToolDefinition[Any], ...]:
    from aunic.tools.stop_process import build_stop_process_tool
    return (build_stop_process_tool(),)
```

### Wiring into the master registries

In `build_note_tool_registry` and `build_chat_tool_registry`, add `stop_process` **unconditionally** — outside any `work_mode` gate:

```python
registry.extend(build_stop_process_tool_registry())  # available in off/read/work
if work_mode in {"read", "work"}:
    registry.extend(build_read_tool_registry())
if work_mode == "work":
    registry.extend(build_mutating_file_tool_registry())
    registry.extend(build_bash_tool_registry())
```

Why unconditional: the model can only stop processes it (or the user's earlier work-mode session) already started. Even in `read` or `off`, if a `bg-1` exists from a prior turn, halting it is strictly safer than leaving it running. Starting remains gated to `work` because the bash tool itself is gated.

---

## Bash Tool Changes ([src/aunic/tools/bash.py](src/aunic/tools/bash.py))

Two changes, both small:

1. **`start_new_session=True`** on the background-mode `create_subprocess_exec` call.
2. **Register `BackgroundProcessState`** instead of the raw `Process` object. Update the model-facing return payload to include `background_id` (preferred) alongside the legacy `task_id` so the model has a clear field to feed into `stop_process`:

```json
{
  "type": "bash_background",
  "task_id": "bg-1",          // kept for compatibility with current model behavior
  "background_id": "bg-1",    // preferred going forward
  "pid": 12345,
  "pgid": 12345,
  "command": "npm run dev",
  "description": "Start dev server"
}
```

The foreground path is untouched.

---

## Result Shape & Transcript Flattening

### Structured result (model-facing)

```json
{
  "type": "process_stop",
  "status": "stopped",
  "background_id": "bg-1",
  "pid": 12345,
  "command": "npm run dev",
  "description": "Start dev server",
  "exit_code": -15,
  "signals_sent": ["SIGTERM"],
  "forced": false,
  "elapsed_ms": 1247,
  "reason": "no longer needed"
}
```

`status` ∈ `{stopped, already_exited, not_found, failed}`. For `not_found` the result is a tool failure (`failure_payload(category="validation_error", reason="not_found", ...)`); the other three statuses are successful tool results — including `already_exited`, which is intentionally not an error.

### Transcript flattening

Add a renderer in [src/aunic/transcript/flattening.py](src/aunic/transcript/flattening.py) (or wherever process_stop will live) that produces a one-liner:

```
Stopped background command bg-1 (npm run dev) — SIGTERM, exit -15 in 1.2s
```

For `already_exited`:

```
Background command bg-1 (npm run dev) had already exited (exit 0)
```

For `forced=true` after escalation:

```
Force-stopped background command bg-1 (npm run dev) — SIGTERM then SIGKILL after 3.0s
```

Keep the renderer mechanical — no decorative formatting. The structured record lives in the row JSON and is reachable for follow-up reasoning.

---

## UI

**Deferred.** The research doc (Phase 3) sketches a process panel listing running background executions with stop buttons. That requires:

- A new TUI surface (panel or status-bar variant)
- Read-side access from the controller into `ShellSessionState.background_processes`
- A user-initiated stop path that bypasses the model

This MVP exposes only the model's tool surface. The user can already cancel a running tool via existing `force_stop_run`. Adding visible background-process state is its own UI iteration.

---

## Critical Files To Reference

Modified:
- [src/aunic/tools/runtime.py](src/aunic/tools/runtime.py) — add `BackgroundProcessState`, rename dict, add helpers
- [src/aunic/tools/bash.py](src/aunic/tools/bash.py) — `start_new_session=True`, register state, return enriched payload
- [src/aunic/tools/note_edit.py](src/aunic/tools/note_edit.py) — wire `build_stop_process_tool_registry()` unconditionally
- [src/aunic/tools/__init__.py](src/aunic/tools/__init__.py) — export new builder & arg dataclass
- [src/aunic/transcript/flattening.py](src/aunic/transcript/flattening.py) — `process_stop` renderer
- [tests/test_note_edit_tools.py](tests/test_note_edit_tools.py) — registry membership assertion

New:
- `src/aunic/tools/stop_process.py` — tool definition, parser, executor
- `tests/test_stop_process.py` — lifecycle tests

Reused (read-only — patterns to mirror):
- [src/aunic/providers/codex_client.py:74-80](src/aunic/providers/codex_client.py#L74-L80) — terminate→wait→kill pattern
- [src/aunic/tools/bash.py:130-150](src/aunic/tools/bash.py#L130-L150) — example of `runtime.resolve_permission` flow (we will **not** call it for MVP, but worth keeping in mind for future raw-pid path)
- [src/aunic/tools/note_edit.py:45-57](src/aunic/tools/note_edit.py#L45-L57) — registry filter pattern

---

## Milestone 1 — Single PR, Ordered Steps

1. **State model.** In [src/aunic/tools/runtime.py](src/aunic/tools/runtime.py), add `BackgroundProcessStatus`, `BackgroundProcessState`. Rename `ShellSessionState.background_tasks` → `background_processes` (typed as `dict[str, BackgroundProcessState]`). Add `register_background_process`, `get_background_process`, `next_bg_id` helpers.
2. **Bash registration.** Update [src/aunic/tools/bash.py:156-176](src/aunic/tools/bash.py#L156-L176) to spawn with `start_new_session=True`, build a `BackgroundProcessState`, register it via the helper, and return the enriched payload (`type`, `task_id`, `background_id`, `pid`, `pgid`, `command`, `description`).
3. **Tool module.** Create `src/aunic/tools/stop_process.py` with: `StopProcessArgs` dataclass, `parse_arguments`, `execute_stop_process` (algorithm above), `_signal_pgid` helper (catches `ProcessLookupError`), `_refresh_terminal_state` (idempotency helper), `build_stop_process_tool()` returning a `ToolDefinition` with `persistence="persistent"`.
4. **Registry wiring.** In [src/aunic/tools/note_edit.py](src/aunic/tools/note_edit.py), add `build_stop_process_tool_registry()` and call it unconditionally from both `build_note_tool_registry` and `build_chat_tool_registry`. Export from [src/aunic/tools/__init__.py](src/aunic/tools/__init__.py).
5. **Transcript flattening.** Add `process_stop` renderer to [src/aunic/transcript/flattening.py](src/aunic/transcript/flattening.py) covering `stopped`, `already_exited`, and `forced` variants.
6. **Tests.** Add `tests/test_stop_process.py` with the verification scenarios below.
7. **Registry membership tests.** Update [tests/test_note_edit_tools.py](tests/test_note_edit_tools.py) to assert `stop_process` is present in `off`/`read`/`work` registries.
8. **Docs.** Update the model system prompt or tool catalogue (wherever new tools are listed for the model) to mention `stop_process`.

---

## Verification

Tests in `tests/test_stop_process.py` (use real subprocesses; this is integration territory and pytest-asyncio is already in the suite):

1. **Round-trip happy path.** `bash(run_in_background=true, command="sleep 60")` → assert `BackgroundProcessState` exists with `pgid==pid`, `command`, `cwd`, `started_at`. Then `stop_process(background_id="bg-1")` → assert status `stopped`, `signals_sent==("SIGTERM",)`, `forced==False`, returncode is negative (Python's signed-signal convention).
2. **Force.** `stop_process(background_id="bg-1", force=True)` → exactly `("SIGKILL",)`, `forced==True`, no SIGTERM sent.
3. **Escalation.** Spawn a process that traps SIGTERM and ignores it (`bash -c 'trap "" TERM; sleep 60'`). `stop_process(background_id="bg-1", grace_ms=200)` → `signals_sent==("SIGTERM","SIGKILL")`, `forced==True`, elapsed_ms ≥ 200.
4. **Idempotency — already exited.** Spawn a quickly-exiting process (`bash -c 'true'`). After it exits naturally, `stop_process(bg-1)` → `status="already_exited"`, `signals_sent==()`, returncode preserved.
5. **Idempotency — double stop.** Stop bg-1 once (status=stopped). Call again → `status="already_exited"`.
6. **Unknown id.** `stop_process(background_id="bg-99")` → tool failure with category `validation_error`, reason `not_found`.
7. **Process group reaping.** `bash -c 'sleep 100 & wait'` (the `sleep` is a child of the bash session leader). After `stop_process(bg-1)`, assert no `sleep` process with that pgid exists (poll `os.getpgid` or `/proc`).
8. **Mode availability.** `build_note_tool_registry(work_mode="off")`, `"read"`, `"work"` all include `stop_process`. `build_bash_tool_registry()` is only present at `work` (existing assertion stays).
9. **Argument validation.** Empty `background_id` → `validation_error`. `grace_ms=99999` → clamped to 30_000 (assert via execution path, not just parser, since clamping is a documented behavior).
10. **Reason persisted.** `stop_process(bg-1, reason="restarting")` → result includes `reason`, transcript renderer mentions it, state's `stop_reason` set.
11. **Transcript shape.** Run a stop, read the transcript row, assert flattening produces the documented one-liner.

Manual TUI smoke (after tests pass):

- Start an interactive session, `bash(run_in_background=true)` a `python -m http.server`, ask the model to `stop_process` it, observe the transcript renderer and confirm the port is freed (`ss -lntp | grep 8000`).

---

## Future Work (explicitly deferred)

These are **not** in Milestone 1. Each is its own iteration with non-trivial design surface:

- **Output capture.** Replace `DEVNULL` with `.aunic/runs/bg-N.log`. Needs a log directory convention, eviction policy, and a companion `bash_output` / `tail_background` tool. Once it exists, `stop_process` adds `output_path` to its result.
- **TUI process panel.** Surface running background processes with elapsed time, command, and a user-initiated stop control.
- **TaskStop wrapper.** When the Tasks tool lands, route `TaskStop` through the same execution manager so stopping a task's execution doesn't auto-complete the task.
- **Raw pid stopping.** Add `pid` argument as an alternative to `background_id`. **Mandatory** permission prompt every call (no "always allow"), refuse pid 0/1, refuse Aunic's own pid, refuse system-critical processes, show command + cwd + child pids in the prompt.
- **Orphan cleanup on subagent exit.** When subagents exist, mirror the example's `killShellTasksForAgent` to reap their background processes when the agent exits.
- **Cross-platform.** Windows `taskkill /T /F` for process trees; abstract behind `_signal_pgid`.
- **Compute completion events.** Background processes currently exit silently. Emitting a `background_ended` `ProgressEvent` when `process.wait()` resolves would let the UI mark them as done without polling. Trivial but orthogonal.
