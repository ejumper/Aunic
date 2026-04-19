# Sleep Tool — Implementation Plan

## Context

Aunic currently has no first-class way to wait. The model can shell out to `bash("sleep 30")`, but that occupies a subprocess slot, blocks the foreground command, and hides intent from the user. Common cases that need it: a dev server taking a few seconds to boot before a health check, a rate-limited API needing a cooldown, the user explicitly saying "wait 30 seconds and check again."

We want a small, boring, reliable wait primitive that:
- runs as host-native `asyncio` (no shell process),
- presents in the TUI as **intentionally idle** (not as "actively thinking"),
- is interruptible by user input — never traps the user behind a timer,
- stays out of the transcript (waiting is not work; the source note should not fill with sleep noise),
- discourages polling loops and shell-based delays.

This matches Aunic's note-first thesis: pacing belongs to the runtime, not to the source note.

## Gold-Standard Reference

The example project's `SleepTool.ts` is feature-gated and stripped, but its surrounding integration is fully visible. The mapping from example → Aunic:

| Example concept | Aunic adaptation |
|---|---|
| `interruptBehavior(): 'cancel' \| 'block'` on every tool | Rely on existing `CancelledError` propagation through `asyncio.sleep`; defer a formal field until a second interruptible tool needs it |
| Hide spinner when only Sleep is in-progress | Add explicit `sleep_status` to [TuiState](src/aunic/tui/types.py) that the renderer treats distinctly from `run_in_progress` |
| `now`/`next`/`later` queue + `sleepRan` drain switch | Skip — Aunic has no prompt queue. Ctrl+C / new prompt aborts the turn (existing behavior) |
| `<tick>` autonomous prompts | Skip — defer to a future `watch` mode; never persist as transcript rows |
| Channel-notification 1s poll | Skip — no MCP channel notifications yet |
| Feature-gated to `PROACTIVE`/`KAIROS` | Available in all work modes unconditionally; the prompt itself disciplines usage |
| `SAFE_YOLO_ALLOWLISTED_TOOLS` (no permission prompt) | No permission prompt — pure-time tool, no side effects |
| `minSleepDurationMs` / `maxSleepDurationMs` settings | Add `SleepSettings` dataclass to [config.py](src/aunic/config.py) |
| `detectBlockedSleepPattern` in BashTool | Phase 2 — out of scope for this PR |

Where Aunic and the example are the same (clamped duration, ephemeral result, `woke_because` field, "do not narrate idle" prompt), follow the example exactly.

## Tool Design

### Name and module
- Tool name: `sleep` (snake_case to match Aunic conventions).
- Module: [src/aunic/tools/sleep.py](src/aunic/tools/sleep.py) (new).

### MVP schema
```json
{
  "duration_ms": 30000,
  "reason": "Waiting for the dev server to start"
}
```

- `duration_ms` — required positive integer.
- `reason` — optional string. **Required** when `duration_ms >= sleep_require_reason_after_ms` (default 30 000). The model should not be allowed to call long sleeps without telling the user why.

### Validation (in `parse_sleep_args`)
Mirror [parse_note_edit_args](src/aunic/tools/note_edit.py) style: raise `ValueError` from a single parsing function. Specifically:

- Reject extra keys.
- `duration_ms` must be `int` (reject `float`, `str`, bool).
- Reject `duration_ms <= 0`.
- If `duration_ms < sleep_min_ms`, **clamp up** and report `status="clamped"` in the result (don't reject — short sleeps are normal CLI pacing).
- If `duration_ms > sleep_max_ms`, **clamp down** and report `status="clamped"`. Do not silently shorten — the model needs to know.
- `reason` must be `str` if present; reject empty after `.strip()`.
- If long-sleep threshold crossed and no `reason`, raise `ValueError` so the model retries with a reason.

### Result shape
```json
{
  "type": "sleep_result",
  "status": "completed",
  "requested_ms": 30000,
  "slept_ms": 30004,
  "woke_because": "timer",
  "reason": "Waiting for the dev server to start"
}
```

`status` ∈ {`completed`, `interrupted`, `clamped`}. `woke_because` ∈ {`timer`, `cancelled`, `max_duration`}. The `event`/`background_process`/`notification`/`task_update`/`file_change` cases are reserved for Phase 3 and not implemented now.

## Settings

Add to [src/aunic/config.py](src/aunic/config.py) alongside `LoopSettings`:

```python
@dataclass(frozen=True)
class SleepSettings:
    sleep_min_ms: int = 250
    sleep_max_ms: int = 300_000           # 5 min — matches Anthropic prompt-cache TTL
    sleep_default_poll_ms: int = 1_000    # reserved for Phase 3 event polling
    sleep_require_reason_after_ms: int = 30_000
```

Wire it onto the existing top-level settings object the same way `LoopSettings` is wired. The sleep tool reads it via the runtime context (or directly from a module-level accessor — match whatever pattern `LoopSettings` already uses).

## Registry & Mode Availability

Sleep is safe in every mode. It needs no FS access and no shell.

- Add `build_sleep_tool_registry()` to [src/aunic/tools/sleep.py](src/aunic/tools/sleep.py).
- Call it unconditionally from [build_note_tool_registry](src/aunic/tools/note_edit.py) (the function that gates by `WorkMode` already — extend it to always include sleep regardless of mode).
- Do **not** add to `_apply_marker_tool_filter` exclusion paths — sleep should never be filtered out.

## Runtime Behavior

### Async timer
Implementation in `execute_sleep`:

1. Record `started_at = time.monotonic()`.
2. After clamping, `requested_clamped_ms = clamped duration`.
3. `await asyncio.sleep(requested_clamped_ms / 1000.0)`.
4. Record `slept_ms = round((time.monotonic() - started_at) * 1000)`.
5. Return `status="completed"` (or `"clamped"` if step 2 changed the duration), `woke_because="timer"`.

### Cancellation
The TUI's [force_stop_run](src/aunic/tui/controller.py) cancels the outer `_run_task`, which propagates `CancelledError` through `await asyncio.sleep`. We do **not** swallow it — re-raise after an emit so the runner's existing abort path runs. The "interrupted" path is exercised when a parent context cancels but the runner chooses to continue (e.g., a future scheduled-prompt mode); not strictly reachable today, but the `interrupted` status is defined so the model already understands the vocabulary.

### Progress events for the UI
During the sleep, emit progress events the controller can render. Use the existing [emit_progress](src/aunic/progress.py) channel:

- On entry: `ProgressEvent(kind="sleep_started", message=reason or "Sleeping", details={"duration_ms": clamped, "deadline_monotonic": deadline})`.
- On exit (in `finally`): `ProgressEvent(kind="sleep_ended", ...)`.

The controller hooks `kind == "sleep_started"` to set `TuiState.sleep_status`, and `kind == "sleep_ended"` (or `CancelledError` propagating) to clear it. The renderer derives the countdown locally from `deadline_monotonic` — no per-tick events needed.

## UI

### TuiState additions
Add to [TuiState](src/aunic/tui/types.py):

```python
sleep_status: SleepStatusState | None = None
```

```python
@dataclass(frozen=True)
class SleepStatusState:
    started_monotonic: float
    deadline_monotonic: float
    reason: str | None
```

### Renderer
Where the existing `run_in_progress` spinner is drawn ([src/aunic/tui/app.py](src/aunic/tui/app.py)), add:

- If `sleep_status is not None` **and** `run_in_progress`, render the sleep banner instead of the active-work spinner.
- Banner format: `Sleeping 0:24 remaining — Waiting for the dev server to start  [Esc to wake]`.
- Refresh ~once per second while `sleep_status` is set (existing TUI tick loop, or a one-shot refresh scheduler).

### Wake affordance
Esc / Ctrl+C while `sleep_status is not None` calls the existing `force_stop_run` — no new key binding needed, just make sure the existing binding works during sleep (it should, since `_run_task` is alive). Submitting a new prompt also cancels via the existing path.

## Transcript & Persistence

Set `persistence="ephemeral"` on the `ToolDefinition` (matches [build_note_only_registry](src/aunic/tools/note_edit.py)).

This is the right default per the research doc: sleep results don't earn a transcript row. The model still receives the structured result for the current turn via the in-memory ephemeral pathway in [runner.py](src/aunic/loop/runner.py).

A compact persistent `sleep_event` row for long user-initiated waits is **future work** — not implemented in this PR. Reason: introducing a new persistent row type means touching the transcript flattener, parser, and renderer; the marginal value isn't there until autonomous/watch mode exists.

## Bash Guard — Future Work

The example's `detectBlockedSleepPattern` (block leading `sleep N` where `N >= 2`) is **deferred**. Reasons:

- It needs a Monitor tool to recommend as the alternative — Aunic doesn't have one yet.
- A nudge without a real alternative becomes an annoyance.
- The new `sleep` tool's existence + a one-line tool-prompt mention ("Prefer the `sleep` tool over `bash sleep`") is enough discouragement for the MVP.

Revisit when the Monitor / TaskOutput tools land.

## Critical Files To Reference

While implementing, keep these open:

- [src/aunic/tools/note_edit.py](src/aunic/tools/note_edit.py) — exact pattern for `parse_*_args`, `execute_*`, `build_*_registry`, ephemeral persistence.
- [src/aunic/tools/runtime.py](src/aunic/tools/runtime.py) — `RunToolContext` for `emit_status`/`emit_progress`, `ToolSessionState` if any session state is needed.
- [src/aunic/loop/runner.py](src/aunic/loop/runner.py) — how tool dispatch works (line ~431), how ephemeral results are handled (lines ~456–465), where to ensure sleep doesn't write a transcript row.
- [src/aunic/tools/base.py](src/aunic/tools/base.py) — `ToolDefinition` shape and the `persistence` field.
- [src/aunic/tui/controller.py](src/aunic/tui/controller.py) — `force_stop_run` (~line 877), `_run_task` cancellation, where progress events are consumed.
- [src/aunic/tui/types.py](src/aunic/tui/types.py) — `TuiState` (line ~76), `run_in_progress`, `indicator_message`.
- [src/aunic/tui/app.py](src/aunic/tui/app.py) — current spinner rendering location.
- [src/aunic/progress.py](src/aunic/progress.py) — `ProgressEvent` shape, `emit_progress` API.
- [src/aunic/config.py](src/aunic/config.py) — `LoopSettings` pattern for `SleepSettings`.
- [src/aunic/domain.py](src/aunic/domain.py) — `WorkMode` literal.

## Milestone 1 — Single PR

Ordered implementation steps:

1. **Settings.** Add `SleepSettings` dataclass to [config.py](src/aunic/config.py) and wire it into the top-level settings object the way `LoopSettings` is wired.
2. **Tool module.** Create [src/aunic/tools/sleep.py](src/aunic/tools/sleep.py) with `SleepArgs`, `parse_sleep_args`, `execute_sleep`, `build_sleep_tool_registry`. Mirror [note_edit.py](src/aunic/tools/note_edit.py).
3. **Tool prompt.** Inline tool description matching the research doc's "Suggested tool prompt" — emphasize "do not sleep instead of answering," "prefer task-aware tools," "user can interrupt."
4. **Registry wiring.** Modify the registry builder in [note_edit.py](src/aunic/tools/note_edit.py) (the function that branches on `WorkMode`) to always include `build_sleep_tool_registry()`.
5. **Progress event types.** Add `"sleep_started"` and `"sleep_ended"` kinds to the `ProgressEvent` literal in [progress.py](src/aunic/progress.py).
6. **TUI state.** Add `SleepStatusState` and `TuiState.sleep_status` field to [tui/types.py](src/aunic/tui/types.py).
7. **Controller wiring.** In [tui/controller.py](src/aunic/tui/controller.py), in the progress-event consumer, set/clear `sleep_status` on the matching event kinds.
8. **Renderer.** In [tui/app.py](src/aunic/tui/app.py), branch the `run_in_progress` indicator: if `sleep_status` is set, render the sleep banner with countdown; otherwise the existing spinner.
9. **Refresh tick.** Confirm (or add) a 1 Hz refresh while `sleep_status` is set so the countdown updates.
10. **Tests.** New file `tests/test_sleep_tool.py` covering the verification matrix below.
11. **Smoke test in TUI.** Open a note, prompt "sleep for 5 seconds and then say done," watch the banner count down, hit Esc partway through, verify the run aborts cleanly.

## Verification

End-to-end scenarios. Items 1–8 are unit tests; 9–11 require running the TUI.

1. `parse_sleep_args({"duration_ms": 100})` returns args; `execute_sleep` returns within ~120ms with `status="completed"`, `woke_because="timer"`, `slept_ms ≈ 100`.
2. `parse_sleep_args({"duration_ms": -5})` raises `ValueError`.
3. `parse_sleep_args({"duration_ms": 1.5})` raises `ValueError` (float rejected).
4. `parse_sleep_args({"duration_ms": "100"})` raises `ValueError` (string rejected).
5. `parse_sleep_args({"duration_ms": 60_000})` (no reason, exceeds threshold) raises `ValueError`.
6. `parse_sleep_args({"duration_ms": 60_000, "reason": "ok"})` succeeds.
7. With `sleep_max_ms=300_000`, `parse_sleep_args({"duration_ms": 999_999_999, "reason": "x"})` succeeds; `execute_sleep` returns `status="clamped"` and `slept_ms ≈ 300_000`.
8. The tool definition's `persistence == "ephemeral"`. Round-tripping a tool result through the runner does **not** write a row to the transcript file (assert via a test that reads the file before and after).
9. **TUI smoke**: start a sleep, the renderer shows `Sleeping 0:04 remaining — <reason>` and counts down, not the active-work spinner.
10. **TUI interrupt**: press Esc partway through a 30 s sleep; `force_stop_run` fires; the run aborts; the banner clears; no orphan task remains (`sleep_status` is `None`, `run_in_progress` is `False`).
11. **TUI new-prompt interrupt**: while sleeping, type and submit a new prompt; the existing cancellation path aborts the turn and the new prompt becomes the next run.

## Future Work

- **Bash sleep guard**: detect leading `sleep N` (N≥2) in foreground bash, nudge toward `sleep` tool — gated on a Monitor tool existing.
- **Event-aware wake-up**: extend schema with `wake_on.background_ids`; integrate with `ShellSessionState.background_tasks` once those processes signal completion via an `asyncio.Event`.
- **Persistent `sleep_event` row**: a compact lifecycle row for user-requested long waits, with flattener support — gated on autonomous/watch mode.
- **Watch / autonomous mode**: ephemeral wake events (not transcript rows), per the thesis. Pairs with `min_sleep_ms`/`max_sleep_ms` becoming meaningful as throttles.
- **Formal `interrupt_behavior` field on `ToolDefinition`**: when a second interruptible tool is added, lift the implicit `CancelledError` contract into a declared field and let the runner inspect it.
- **Notification wake-up**: once Aunic has external notification channels (MCP, webhooks).
