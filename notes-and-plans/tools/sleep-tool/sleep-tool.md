# Sleep Tool

## coding-agent-program-example Implementation
The example project treats Sleep as an autonomous-loop pacing tool, not as a shell command and not as user-facing filler text. It exists so the model can deliberately wait without occupying a shell process, while remaining interruptible by the user or by queued system events.

Important caveat: in this backup, the concrete `src/tools/SleepTool/SleepTool.ts` implementation is not present. The repo's README says several feature-gated tool implementations are stripped at compile time, and `SleepTool` is listed among them. The exposed source still shows the tool's prompt, feature gate, settings, proactive loop integration, interrupt behavior, queue semantics, and related Bash guards. That is enough to understand the intended design.

Relevant files in the example:

- `src/tools/SleepTool/prompt.ts`
- `src/tools.ts`
- `src/constants/prompts.ts`
- `src/cli/print.ts`
- `src/query.ts`
- `src/utils/handlePromptSubmit.ts`
- `src/types/textInputTypes.ts`
- `src/screens/REPL.tsx`
- `src/utils/permissions/classifierDecision.ts`
- `src/tools/BashTool/BashTool.tsx`
- `src/tools/BashTool/prompt.ts`
- `src/utils/settings/types.ts`
- `src/utils/sleep.ts`
- `src/services/mcp/channelNotification.ts`
- `README.md`
- `docs/en/05-future-roadmap.md`

### Tool Availability
`SleepTool` is feature-gated.

In `src/tools.ts`, it is conditionally required only when either feature is enabled:

- `PROACTIVE`
- `KAIROS`

It is added to the base tool list only if the feature gate loads the tool module.

There is also a runtime gate around proactive state. `src/main.tsx` includes a comment explaining that proactive mode must be activated before `getTools()` runs so `SleepTool.isEnabled()` passes. Another comment notes that assistant mode can be active while Sleep stays disabled because Sleep's enabled state gates on proactive mode.

This means Sleep is not a general default tool. It is part of autonomous/proactive operation.

### Prompt Contract
`src/tools/SleepTool/prompt.ts` exposes the public tool identity:

```ts
export const SLEEP_TOOL_NAME = 'Sleep'
export const DESCRIPTION = 'Wait for a specified duration'
```

The tool prompt says:

- wait for a specified duration,
- the user can interrupt at any time,
- use it when the user says to sleep/rest,
- use it when the model has nothing to do,
- use it when waiting for something,
- the model may receive `<tick>` prompts as periodic check-ins,
- look for useful work before sleeping,
- Sleep can be called concurrently with other tools,
- prefer Sleep over `Bash(sleep ...)`,
- Sleep does not hold a shell process,
- each wake-up costs an API call,
- prompt cache expires after 5 minutes of inactivity, so choose duration thoughtfully.

The most important design idea is not the timer. It is the contract around idle turns: if there is nothing useful to do, the model should sleep instead of narrating that it is idle.

### Proactive Ticks
The Sleep tool is tied to a proactive loop.

`src/constants/prompts.ts` adds an "Autonomous work" section when proactive mode is active. The model is told:

- it will receive `<tick>` prompts,
- ticks mean "you're awake, what now?",
- tick timestamps are in the user's local time,
- multiple ticks can be batched,
- do not echo tick content,
- use Sleep to control pacing,
- sleep longer when waiting for slow processes,
- sleep shorter when actively iterating,
- if there is nothing useful to do on a tick, it must call Sleep,
- do not send "still waiting" style status messages.

`src/cli/print.ts` schedules proactive ticks. When the command queue is idle and proactive mode is active, it enqueues a meta prompt:

```xml
<tick>current local time</tick>
```

The tick is queued at `later` priority and `run()` is invoked. This keeps the model alive between user messages without pretending that the tick is a normal user prompt.

### Queue And Wake Semantics
The queue has three priorities in `src/types/textInputTypes.ts`:

- `now`: interrupt and send immediately,
- `next`: let the current tool call finish, then send before the next API round trip,
- `later`: process after the current turn finishes.

The comments explicitly mention Sleep:

- `next` wakes an in-progress Sleep call.
- `later` also wakes an in-progress Sleep call.
- Sleep is only available in proactive mode, so this only matters there.

`src/query.ts` has a key detail. After tool calls finish, it checks whether a Sleep tool ran:

```ts
const sleepRan = toolUseBlocks.some(b => b.name === SLEEP_TOOL_NAME)
```

If Sleep ran, queued commands up to `later` priority are drained into the next model input. If Sleep did not run, only `next` priority commands are drained.

That gives Sleep a semantic role beyond waiting. Sleep is an idle boundary. When the model chooses to sleep, lower-priority queued events that arrived during the sleep can be attached to the same continuing turn when it wakes.

### Interrupt Behavior
The tool system supports per-tool interrupt behavior:

```ts
interruptBehavior?(): 'cancel' | 'block'
```

The `Tool.ts` comments define:

- `cancel`: stop the tool and discard its result,
- `block`: keep running and make new input wait.

`src/utils/handlePromptSubmit.ts` includes a comment naming Sleep as the example interruptible tool. If the user submits a prompt while only interruptible tools are in progress, the current turn is aborted with reason `interrupt`.

`src/services/tools/StreamingToolExecutor.ts` then cancels running tools whose `interruptBehavior()` returns `cancel`. It also keeps a UI flag, `hasInterruptibleToolInProgress`, true only when every executing tool is interruptible.

The visible behavior is: user input wakes/cancels Sleep instead of waiting behind it.

### UI Behavior
`src/screens/REPL.tsx` hides the spinner when the only in-progress tool is Sleep.

This is subtle but correct. A normal spinner says "the assistant is working." Sleep says "the assistant is intentionally idle until an event or timer." Those should not look the same.

The UI detects the latest assistant message's in-progress tool uses and checks whether every one is named `Sleep`. If so, it suppresses the normal loading spinner.

### Permission Model
`src/utils/permissions/classifierDecision.ts` lists `SLEEP_TOOL_NAME` in the "misc safe" group.

Sleep does not mutate files, run shell commands, hit external APIs by itself, or spend meaningful local resources beyond time and one future model turn. It should not need a normal permission prompt.

The real cost is API pacing. The prompt and settings handle that by warning about API calls and prompt-cache expiry.

### Settings
`src/utils/settings/types.ts` exposes proactive/KAIROS sleep settings:

- `minSleepDurationMs`
- `maxSleepDurationMs`

`maxSleepDurationMs` can be set to `-1` for indefinite sleep, meaning wait for user input.

These settings are useful in managed or remote environments where an administrator may want to throttle autonomous wake-ups or prevent very long idle loops.

### Bash Sleep Guard
The example strongly prefers the Sleep tool over shell sleeps.

`src/tools/BashTool/prompt.ts` says:

- if waiting for a background task started with `run_in_background`, the model will receive a completion notification and should not poll,
- do not retry failing commands in a sleep loop,
- if the Monitor tool is enabled, leading `sleep N` with `N >= 2` is blocked,
- if Monitor is not enabled, short sleeps are tolerated only when necessary.

`src/tools/BashTool/BashTool.tsx` implements `detectBlockedSleepPattern(command)`. It detects standalone or leading commands like:

```bash
sleep 5
sleep 5 && check
sleep 5; check
```

If the Monitor tool is enabled and the command is foreground bash, `validateInput` rejects those patterns with guidance to:

- run blocking commands in the background,
- rely on completion notifications,
- use Monitor for streaming events or polling APIs,
- keep genuine pacing sleeps under 2 seconds.

This is a good agent-framework pattern. A model should not block a shell process just to wait. Waiting should be a host/runtime capability.

### Shared Sleep Utility
The repo also has `src/utils/sleep.ts`, a general helper used by other code.

It implements:

- abort-responsive `sleep(ms, signal, opts)`,
- optional rejection on abort,
- optional custom abort error,
- optional `unref` so timers do not keep Node alive,
- `withTimeout(promise, ms, message)` to race work against a timeout.

This is not necessarily the SleepTool implementation, but it shows the project's timer style:

- use abort signals,
- clean up timer listeners,
- do not leave dangling timers,
- distinguish "sleep was aborted" from "sleep completed."

### Channel Notifications
`src/services/mcp/channelNotification.ts` notes that inbound MCP channel notifications enqueue messages and that SleepTool polls `hasCommandsInQueue()` and wakes within 1 second.

This gives Sleep event responsiveness even when the sleep duration is long. A sleeping autonomous agent should not miss a Slack/Discord/SMS-style channel message for the full sleep interval.

### What The Example Is Optimizing For
SleepTool exists to solve several problems at once:

- keep autonomous sessions alive without constant user-visible chatter,
- avoid shell-based sleeps,
- let the user interrupt idle waiting,
- let queued notifications wake the model,
- make model wake frequency explicit,
- avoid wasting turns on "still waiting",
- distinguish idle waiting from active work in the UI,
- prevent aggressive polling loops,
- respect API cost and prompt-cache behavior.

The key design lesson is that Sleep is not merely `await setTimeout`. It is an idle-turn protocol between the model, host runtime, user input queue, and notification system.

### Design Lessons For Aunic
The transferable ideas are:

- Sleep should be host-native, not implemented through `bash sleep`.
- Sleep should be interruptible.
- Sleep should not look like active work in the UI.
- Sleep should be available only in contexts where waiting is a meaningful action.
- Sleep should wake early for user input and important runtime events.
- Sleep should have configurable minimum and maximum durations.
- Sleep should be cheap in transcript space.
- Sleep should discourage polling loops.
- Sleep should be paired with background-task notifications, task output, process stop, and future monitor tools.

The parts Aunic should not copy directly:

- The example is autonomous/chat-loop-first. Aunic is note-first, so Sleep should not create a permanent chat loop by default.
- The example uses hidden `<tick>` prompts as a central pacing mechanism. Aunic should use ephemeral runtime events, not persistent transcript rows.
- The example's concrete SleepTool implementation is stripped in this backup. Aunic should design from the visible behavior and its own runtime needs rather than clone an unavailable file.

## Implementing in Aunic
Aunic should implement Sleep as an interruptible, event-aware waiting primitive for agent workflows, not as a general habit of delaying normal answers.

The thesis-level reason is that Aunic's primary workspace is the markdown note. Sleep should help the model coordinate with time, background work, and user input while preserving that note-first shape. It should not turn Aunic into a noisy autonomous chat bot that wakes up just to say it is still waiting.

### Product Purpose
Sleep should be used when waiting is itself part of the workflow.

Good uses:

- The user explicitly says to wait.
- A background command needs time to produce output.
- A server needs a few seconds to start before a health check.
- A rate limit or external system needs a short cooldown.
- A future autonomous/watch mode has nothing useful to do until an event arrives.
- A future monitor or scheduled job system needs pacing between checks.

Bad uses:

- Delaying a normal response for style.
- Replacing a background-task output tool.
- Polling the same failing command repeatedly.
- Blocking the user while a foreground shell command could run with a timeout.
- Hiding uncertainty instead of asking a useful question.
- Writing "still waiting" transcript rows.

The model instruction should be blunt: if there is useful work, do it; if waiting is required, sleep; if waiting is not useful, answer.

### Tool Name
Aunic's existing tool names are snake_case. The Aunic-native tool should be:

```text
sleep
```

If Aunic later wants compatibility with models that know the example project's naming, it can add `Sleep` as an alias. The internal Python module should be `src/aunic/tools/sleep.py`.

### MVP Tool Schema
Keep the first version simple:

```json
{
  "duration_ms": 30000,
  "reason": "Waiting for the dev server to start"
}
```

Fields:

- `duration_ms`: required integer duration in milliseconds.
- `reason`: optional short explanation for the user/UI.

Validation:

- require `duration_ms` to be an integer,
- reject negative values,
- clamp or reject values above `SETTINGS.tools.sleep_max_ms`,
- optionally raise values below `SETTINGS.tools.sleep_min_ms`,
- require a non-empty `reason` for long sleeps, such as over 30 seconds.

Recommended initial settings:

```python
sleep_min_ms: int = 250
sleep_max_ms: int = 300_000
sleep_default_poll_ms: int = 1_000
```

Five minutes is a practical max because the example correctly notes prompt-cache expiry around that boundary. Longer waits should become scheduled reminders, cron jobs, monitor subscriptions, or explicit user-driven pause states.

### Event-Aware Schema
After the MVP, Sleep should support wake conditions.

Recommended extended input:

```json
{
  "duration_ms": 60000,
  "reason": "Waiting for tests to finish",
  "wake_on": {
    "user_input": true,
    "background_ids": ["bg-1"],
    "task_ids": ["3"],
    "file_paths": [],
    "notifications": true
  }
}
```

Meaning:

- wake when the timer expires,
- wake earlier if the user sends input,
- wake earlier if a named background execution completes,
- wake earlier if a task execution changes state,
- wake earlier if a watched file changes,
- wake earlier if an external notification arrives.

Do not make this too clever in the first implementation. The best MVP is timer plus user interruption. The second useful step is waking on Aunic-owned background execution completion.

### Output
Return structured data:

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

Possible statuses:

- `completed`: the timer elapsed.
- `interrupted`: user input or host cancellation woke it.
- `event`: a requested event woke it.
- `clamped`: the requested duration exceeded limits and was shortened.

Possible `woke_because` values:

- `timer`
- `user_input`
- `background_process`
- `task_update`
- `file_change`
- `notification`
- `cancelled`
- `max_duration`

The model should know whether it woke because time passed or because something happened.

### Runtime Behavior
Do not use `time.sleep`. Use `asyncio.sleep` and a monotonic clock.

The sleep should be interruptible. A direct implementation can be:

1. Record `started_at = time.monotonic()`.
2. Compute `deadline = started_at + duration_ms / 1000`.
3. Wait in chunks no longer than `sleep_default_poll_ms`.
4. On each wake, check for cancellation and host events.
5. If an event matches `wake_on`, return early.
6. If deadline passes, return completed.

Once Aunic has a proper event bus, replace polling with `asyncio.wait` over:

- timer future,
- user-input event,
- background process event,
- task event,
- file watcher event,
- notification event.

Polling every second is acceptable for the MVP. Event-driven wakeups are better once the surrounding infrastructure exists.

### Cancellation And User Input
Sleep must not trap the user behind a timer.

Current Aunic tool execution does not expose the same per-tool interrupt model as the example repo. A useful Sleep implementation should add that concept:

```python
ToolDefinition(
    ...,
    interrupt_behavior="cancel",
)
```

or an equivalent field in Aunic's tool runtime.

When the user sends a new prompt while Sleep is active:

- cancel the sleep,
- return or discard an `interrupted` result,
- process the user's prompt immediately,
- do not make the prompt wait for the original duration.

If Aunic does not yet have a prompt queue that can interrupt tool execution, implement the first version as a conservative short-duration sleep only. Long sleeps without interruption are bad UX.

### Permissions And Modes
Sleep is safe from a filesystem/security perspective. It should not require permission.

Recommended availability:

- `off`: allowed.
- `read`: allowed.
- `work`: allowed.

But tool exposure should be thoughtful. Sleep should be most visible when at least one of these is true:

- the user explicitly asked to wait,
- a background execution is running,
- a future autonomous/watch mode is active,
- a future monitor/schedule workflow is active.

If Aunic exposes `sleep` in every normal turn, the model may overuse it. The tool prompt should counter that:

```text
Use sleep only when waiting is useful. Do not sleep instead of answering the user. Do not use sleep to poll repeatedly. If a background task has an output/wait tool, prefer that task-aware tool.
```

### Transcript Semantics
Sleep should be ephemeral by default.

The transcript should not fill with:

```text
Sleeping...
Woke up...
Sleeping...
Woke up...
```

Recommended persistence:

- normal sleep tool calls: ephemeral,
- user-requested long wait: compact persistent lifecycle row,
- sleep interrupted by user input: no row unless useful,
- sleep tied to a background process event: the process/task event should be logged, not the sleep itself.

Compact lifecycle row, when needed:

```json
{
  "type": "sleep_event",
  "status": "completed",
  "duration_ms": 60000,
  "reason": "Waiting for CI"
}
```

Flattened transcript text:

```text
Waited 60s: Waiting for CI
```

Most model-facing result detail should stay in memory for the current turn, not become historical note content.

### UI
Sleep should have a distinct idle indicator.

Recommended indicator:

```text
Sleeping 0:24 remaining - Waiting for tests to finish [wake]
```

Behavior:

- do not show the same spinner used for active model work,
- show remaining time,
- show reason if provided,
- offer a wake/cancel action,
- if sleeping on a background process, show the process id or task id,
- if user types a prompt, wake automatically.

This mirrors the example's "hide spinner when only Sleep is active" behavior while fitting Aunic's TUI. The UI should communicate that Aunic is intentionally waiting, not hung.

### Context And Tick Design
Aunic should avoid making `<tick>` prompts persistent transcript content.

If Aunic adds a future autonomous/watch mode, implement ticks as runtime events:

```text
AUNIC WAKE EVENT
time: 2026-04-16T12:34:56-05:00
reason: timer
```

That event can be included in the immediate model context, but it should not be written into the source note transcript unless it caused meaningful work.

This preserves Aunic's thesis:

- the note remains primary context,
- the transcript remains a useful log,
- idle pacing does not become project memory.

### Relationship To Other Tools
Sleep should not replace more specific wait tools.

Use the more specific tool when possible:

- Waiting for a background bash process: use future `TaskOutput(block=true)` or execution output wait.
- Waiting for a process to stop: use `stop_process` or execution state.
- Waiting for repeated external condition checks: use a future `monitor` tool.
- Waiting for scheduled future work: use a future schedule/cron/reminder tool.
- Waiting because there is no useful work in autonomous mode: use `sleep`.

Sleep is the lowest-level idle primitive. Higher-level tools should own domain-specific waiting.

### Integration With Bash
Aunic should discourage `bash(command="sleep 30")` for the same reason as the example project: it occupies a shell process and hides intent.

Recommended Bash validation:

- Detect foreground commands that start with `sleep N` where `N >= 2`.
- If the command is just a delay, tell the model to use `sleep`.
- If the command is `sleep N && check`, tell the model to use a monitor/task-output pattern instead.
- Allow tiny sleeps under 2 seconds for CLI pacing, rate limits, and flaky interactive tools.
- Allow sleeps inside scripts or complex shell commands when static detection is uncertain.

This should be a validation nudge, not a brittle shell parser project. Use Aunic's existing conservative command classification style.

### Integration With Background Processes
Once `stop_process` and the execution manager exist, Sleep should wake on process lifecycle events.

Example:

```json
{
  "duration_ms": 30000,
  "reason": "Waiting for server startup",
  "wake_on": {
    "background_ids": ["bg-1"]
  }
}
```

If `bg-1` exits after 4 seconds, Sleep returns:

```json
{
  "type": "sleep_result",
  "status": "event",
  "requested_ms": 30000,
  "slept_ms": 4021,
  "woke_because": "background_process",
  "background_id": "bg-1"
}
```

This is better than making the model sleep for the full 30 seconds or poll with Bash.

### Integration With Tasks
If the Tasks tool is implemented, Sleep can be task-aware but should remain separate from `TaskOutput`.

Good task-aware Sleep behavior:

- wake when a task execution completes,
- wake when a task is assigned to the main agent,
- wake when a blocker is resolved,
- wake when a subagent posts output.

But if the model wants task output, it should call `TaskOutput`. Sleep should only wake the model; it should not become an output retrieval API.

### Model Instructions
Suggested tool prompt:

```text
Wait without running a shell command. Use this only when waiting is useful: the user asked you to wait, a background execution needs time, a rate limit needs a cooldown, or autonomous/watch mode has no useful work to do. The user can interrupt sleep at any time. Prefer task/output/monitor tools when waiting for a specific execution or external condition. Do not use sleep to avoid answering the user, and do not narrate idle "still waiting" messages.
```

Suggested autonomous/watch-mode addendum:

```text
If a wake event arrives and there is useful work, do it. If there is no useful work and no user message to answer, call sleep. Choose shorter sleeps while actively iterating and longer sleeps when waiting for slow external systems. Avoid wake intervals over five minutes unless the user explicitly asked for a long wait.
```

### File-Level Implementation
Likely implementation areas:

- `src/aunic/tools/sleep.py`
  - `SleepArgs`,
  - schema,
  - parsing/validation,
  - async timer,
  - structured result.

- `src/aunic/config.py`
  - `sleep_min_ms`,
  - `sleep_max_ms`,
  - `sleep_default_poll_ms`,
  - optional `sleep_require_reason_after_ms`.

- `src/aunic/tools/base.py`
  - optional `interrupt_behavior` field on `ToolDefinition`.

- `src/aunic/tools/runtime.py`
  - session-level sleep cancellation/event state,
  - helper to wait on runtime events,
  - maybe `runtime.sleep(...)`.

- `src/aunic/tools/note_edit.py`
  - include `sleep` in note and chat registries where appropriate.

- `src/aunic/loop/runner.py`
  - allow user prompt submission to cancel interruptible tool execution.

- `src/aunic/modes/chat.py`
  - ensure sleep results do not create noisy transcript rows.

- `src/aunic/transcript/flattening.py`
  - compact flattener for rare persistent `sleep_event` rows.

- `src/aunic/tui/controller.py`
  - wake/cancel action,
  - sleep status updates,
  - user input interrupts active sleep.

- `src/aunic/tui/app.py`
  - render idle sleep indicator rather than active-work spinner.

- `src/aunic/tools/bash.py`
  - optional validation against long foreground `sleep N` patterns.

If Aunic builds a central event bus for background executions and file watchers, Sleep should use that bus rather than maintaining its own parallel mechanism.

### Tests
Important tests:

- `sleep(duration_ms=100)` returns after roughly the requested duration.
- negative duration is rejected.
- non-integer duration is rejected.
- duration above max is rejected or clamped according to settings.
- short sleep is available in `off`, `read`, and `work`.
- sleep does not invoke bash or create a subprocess.
- sleep result reports requested and actual slept milliseconds.
- user interruption cancels sleep before the timer expires.
- sleep can wake on a background execution completion event.
- sleep can wake on a queued notification event once notifications exist.
- sleep progress/status appears in the TUI as idle waiting, not active work.
- sleep tool results are ephemeral by default.
- persistent long-wait transcript rows flatten compactly.
- `bash("sleep 5")` validation nudges toward `sleep`.
- `bash("sleep 0.5")` remains allowed.
- repeated Sleep calls do not create a doom-loop of transcript noise.
- future autonomous ticks are not persisted as source note transcript rows.

### Recommended Rollout
Phase 1: simple interruptible timer

- Add `sleep(duration_ms, reason)`.
- Clamp/validate duration.
- Make it ephemeral.
- Add TUI idle indicator.
- Allow user prompt to wake/cancel it.

Phase 2: Bash anti-pattern guard

- Detect leading `sleep N` in foreground bash.
- Nudge toward `sleep` or task-aware waiting.
- Keep short pacing sleeps allowed.

Phase 3: event-aware wakeups

- Wake on background process completion.
- Wake on task execution changes.
- Wake on external notifications.
- Return `woke_because` details.

Phase 4: watch/autonomous mode

- Add explicit user-controlled watch/autonomous mode.
- Use ephemeral wake events instead of persistent `<tick>` transcript rows.
- Require Sleep when the model has no useful action.
- Add settings for min/max autonomous sleep duration.

Phase 5: monitor/schedule integration

- Move repeated condition checks into a real Monitor tool.
- Move long future waits into schedule/reminder tools.
- Keep Sleep as the short-term idle primitive.

### Best Shape For Aunic
The best Aunic version is a small, boring, reliable wait primitive that makes time explicit without making the transcript noisy.

The user should be able to see when Aunic is sleeping and wake it immediately. The model should use Sleep instead of shell sleeps or idle narration. Background executions and monitors should wake the model with events rather than force polling. Long-running autonomous behavior should be opt-in and should leave the source note clean.

Sleep is useful precisely because it is not work. It gives Aunic a disciplined way to do nothing until doing something is useful again.
