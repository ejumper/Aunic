# Implementation Guidance

## Status
This phase is implemented. Current Aunic has real work/read/off orchestration and the note-mode synthesis pass described below.

## Files to Reference

### ./notes-and-plans
NOTE: notes-and-plans describes UPDATED behavior. It is meant to reflect the finished state after all updates have been completed. If it conflicts with how Aunic currently behaves, that is a strong signal that portion of Aunic needs to be updated!
- The markdown notes in notes-and-plans/ have detailed information on how each feature should be implemented. They are not exhaustive and may contain bad information. They should be followed with reasonable skepticism. Use them as a starting point, comply with them as much as possible, but do not let them override common sense and best practices. 
    - aunic-thesis.md explains what the program *is*, all changes should be in the spirit of what this file describes Aunic as.
    - notes-and-plans/active-markdown-note/* explains what the active-markdown note aunic works from is and how it should behave.
    - notes-and-plans/building-context/* explains the process of creating the context window that will be sent to the model.
    - notes-and-plans/commands/* explains ways the user can access additional features, or manipulate the programs behavior. 
        - "at" and "slash" commands use a prefix followed by a command in the `prompt-editor`
        - "edit commands" are placed in the text editor and parsed when the user-prompt is sent
    - notes-and-plans/modes/* explains the various "modes" Aunic can be placed in
        - essentially, these are about quickly configuring... 
            - what tools are available
            - how/where the model outputs responses
    - notes-and-plans/tools/* contains detailed descriptions of how every tool works
    - notes-and-plans/UI/* has a general explanation of what the UI looks like
    - notes-and-plans/zfuture-features/* features that the user wanted to make note of but are not being implemented yet, ignore these.

### ~/Desktop/coding-agent-program-example
in ~/Desktop/coding-agent-program-example there is a state of the art Agentic AI program. It functions in the typical chat manner (like OpenCode), but contains useful, known good implementations of many of Aunic's features. Lean on it heavily when deciding how to build/alter features, with some important caveats.
- it is written in typescript, but Aunic is python, so use the logic/architecture, but translate it to python
- do not conflict with Aunic specific features.
    - for instance Aunic stores the message block of the API JSON in a markdown table, not a database.
(note: when referencing it, ~/Desktop/coding-agent-program-example/README.md is a great place to start, it can point you to where you need to go to find exactly what you are looking for)

## How to Implement Changes
Implementing changes should work like this...
1. look for and read the relevant notes-and-plans/ markdown files.
2. look for an equivalent feature in ~/Desktop/coding-agent-program-example/ and if you find one examine it.
3. decide what can be lifted from ~/Desktop/coding-agent-program-example (translated to python) and what needs to be reworked to comply with how Aunic differs from coding-agent-program-example
4. follow the coding-agent-program-example as closely as possible making Aunic specific changes where necessary

# Phase 7: Modes and Orchestration — Implementation Plan

## Context

Phases 1–6 are complete. Phase 7 has two items from `update-guide.md`:

1. **Mode system completion** — note/chat toggle + work/read/off tool gating with UI/state wiring
2. **Note-mode synthesis pass** — after a note-mode run where work/read tools were used, force a final pass to integrate findings into note-content

After thorough exploration, **Item 1 is already functionally complete**. The tool gating backend, UI toggles, system prompts, and state wiring all work correctly. Item 2 (synthesis pass) is entirely new and is the bulk of this plan.

---

## Item 1: Mode System — Assessment

The mode system is already implemented across these files:

| Concern | File | Status |
|---------|------|--------|
| Tool gating | [note_edit.py](src/aunic/tools/note_edit.py) lines 35–54 | `build_note_tool_registry(work_mode)` and `build_chat_tool_registry(work_mode)` correctly gate tools by mode |
| TUI toggle (note/chat) | [controller.py](src/aunic/tui/controller.py) line 372 | `toggle_mode()` works, blocked during runs |
| TUI toggle (work/read/off) | [controller.py](src/aunic/tui/controller.py) line 381 | `toggle_work_mode()` cycles off→read→work→off, blocked during runs |
| UI buttons | [app.py](src/aunic/tui/app.py) lines 126–127, 721–729 | `[ Mode: note ]` and `[ Work: off ]` buttons, disabled during web_mode |
| System prompts | [runner.py](src/aunic/loop/runner.py) line 440, [chat.py](src/aunic/modes/chat.py) line 823 | Both include work mode, available tools, protected paths |
| Request plumbing | [controller.py](src/aunic/tui/controller.py) lines 492, 512 | `work_mode=self.state.work_mode` passed to both run requests |

**No functional changes needed for Item 1.** Optional cosmetic enhancement: color-code the work mode button (green=work, yellow=read, grey=off). This is low priority and can be deferred.

---

## Item 2: Note-Mode Synthesis Pass — Implementation

### Overview

Per [modes.md](notes-and-plans/modes/modes.md) lines 36–44, the synthesis pass:
- Triggers in **note-mode only**, when **work_mode is "read" or "work"**, and the model **successfully used** work/read-mode tools
- After the natural stop signal (assistant message with no tool calls), Aunic forces a final pass
- The model receives the **note-snapshot** + **latest-run-log** (user message through final assistant message)
- It is instructed to add/update/remove information in note-content based on the run results
- Given only **note_edit** and **note_write** tools
- Should complete in a **single pass**

### Step 1: Expose run-log from ToolLoop

The `run_log` (`list[TranscriptRow]`) is local to `ToolLoop.run()` and never returned. The synthesis pass needs the "latest-run-log" (rows added during this run, not pre-existing transcript history).

**File: [loop/types.py](src/aunic/loop/types.py)** — Add to `LoopRunResult`:
```python
run_log: tuple[TranscriptRow, ...] = ()
run_log_new_start: int = 0  # index where rows from this run begin
```

**File: [loop/runner.py](src/aunic/loop/runner.py)** — Two changes:
1. Before the user message is appended (~line 121), capture `run_log_start_index = len(run_log)`
2. In the `LoopRunResult` constructor (~line 428), add:
   ```python
   run_log=tuple(run_log),
   run_log_new_start=run_log_start_index,
   ```

This lets the synthesis pass extract the latest run via `run_log[run_log_new_start:]`.

### Step 2: Add tool name constants and make `build_note_only_registry` public

**File: [tools/note_edit.py](src/aunic/tools/note_edit.py)**
- Add constants near top:
  ```python
  READ_MODE_TOOL_NAMES: frozenset[str] = frozenset({"read", "grep", "glob", "list"})
  WORK_MODE_TOOL_NAMES: frozenset[str] = frozenset({"edit", "write", "bash"})
  OUTSIDE_NOTE_TOOL_NAMES: frozenset[str] = READ_MODE_TOOL_NAMES | WORK_MODE_TOOL_NAMES
  ```
- Rename `_build_note_only_registry` → `build_note_only_registry` (remove underscore)

**File: [tools/__init__.py](src/aunic/tools/__init__.py)** — Add exports:
```python
from aunic.tools.note_edit import build_note_only_registry, OUTSIDE_NOTE_TOOL_NAMES
```

### Step 3: Create the synthesis module

**New file: `src/aunic/modes/synthesis.py`**

Contains all synthesis-pass logic:

#### 3a. Detection function
```python
def _work_read_tools_were_used(events: tuple[LoopEvent, ...]) -> bool:
    """True if at least one work/read-mode tool completed successfully."""
    for event in events:
        if event.kind != "tool_result":
            continue
        tool_name = event.details.get("tool_name")
        status = event.details.get("status")
        if tool_name in OUTSIDE_NOTE_TOOL_NAMES and status == "completed":
            return True
    return False
```

#### 3b. System prompt
```python
SYNTHESIS_SYSTEM_PROMPT = "\n".join([
    "You are operating inside Aunic note-mode synthesis pass.",
    "",
    "A note-mode run has just completed. During the run, work was done outside the note-content",
    "(reading files, editing code, running commands, etc.). Your task is to update the note-content",
    "to reflect what happened during the run.",
    "",
    "You will be given:",
    "- The current note-content (NOTE SNAPSHOT)",
    "- The run log showing what tools were used and what results came back (RUN LOG)",
    "",
    "Your job:",
    "1. Add new information from the run log to the spot in note-content where it fits best.",
    "2. Update information in note-content that has changed based on the run results.",
    "3. Remove information from note-content that has been made irrelevant by the run results.",
    "",
    "Use note_edit for targeted changes or note_write for a full rewrite if many changes are needed.",
    "Complete all updates in a single pass — do not use more than a few tool calls.",
    "When done, reply with a brief summary of what you changed.",
    "Do not create new sections unless necessary. Prefer integrating into existing structure.",
])
```

#### 3c. Run-log formatter
```python
def _format_run_log_for_synthesis(rows: tuple[TranscriptRow, ...]) -> str:
    """Render latest run-log rows as readable text for the synthesis prompt."""
    parts = []
    for row in rows:
        if row.type == "message":
            parts.append(f"[{row.role}] {_content_str(row.content)}")
        elif row.type == "tool_call":
            parts.append(f"[{row.role}] tool_call: {row.tool_name}({_content_str(row.content)})")
        elif row.type == "tool_result":
            parts.append(f"[tool_result: {row.tool_name}] {_content_str(row.content)}")
        elif row.type == "tool_error":
            parts.append(f"[tool_error: {row.tool_name}] {_content_str(row.content)}")
    return "\n".join(parts)
```

#### 3d. Result type
```python
@dataclass(frozen=True)
class SynthesisPassResult:
    ran: bool
    loop_result: LoopRunResult | None = None
    usage_log: UsageLog = field(default_factory=UsageLog)
    error_message: str | None = None
```

#### 3e. Main runner function
```python
async def run_synthesis_pass(
    *,
    tool_loop: ToolLoop,
    provider: LLMProvider,
    context_result: ContextBuildResult,
    active_file: Path,
    included_files: tuple[Path, ...],
    model: str | None,
    reasoning_effort: ReasoningEffort | None,
    progress_sink: Any,
    metadata: dict[str, Any],
    note_snapshot_text: str,
    run_log_rows: tuple[TranscriptRow, ...],
    permission_handler: Any | None,
) -> SynthesisPassResult:
```

This function:
1. Builds the synthesis user prompt: `"NOTE SNAPSHOT\n{note_snapshot}\n\n---\n\nRUN LOG\n{formatted_run_log}"`
2. Gets note-only tools via `build_note_only_registry()` (only note_edit + note_write — no research tools)
3. Creates a synthetic `PromptRun` with `per_prompt_budget=4` (allows a few edits + completion)
4. Creates a `LoopRunRequest` with `system_prompt=SYNTHESIS_SYSTEM_PROMPT`, `work_mode="off"`, and the note-only `tool_registry`
5. Calls `tool_loop.run(request)`
6. Returns `SynthesisPassResult(ran=True, loop_result=result, usage_log=result.usage_log)`

### Step 4: Update `NoteModeRunResult`

**File: [modes/types.py](src/aunic/modes/types.py)** — Add to `NoteModeRunResult`:
```python
synthesis_ran: bool = False
synthesis_error: str | None = None
```

### Step 5: Integrate synthesis into `NoteModeRunner.run()`

**File: [modes/runner.py](src/aunic/modes/runner.py)** — After all prompt runs complete (~line 106) and before final snapshots are gathered (~line 107):

```python
# --- Synthesis pass ---
synthesis_result = SynthesisPassResult(ran=False)
if (
    stop_reason == "finished"
    and request.work_mode in ("read", "work")
    and prompt_results
):
    all_events = tuple(
        event
        for pr in prompt_results
        for event in pr.loop_result.events
    )
    if _work_read_tools_were_used(all_events):
        last_lr = prompt_results[-1].loop_result
        latest_run_log = last_lr.run_log[last_lr.run_log_new_start:]
        
        # Read current note content (post-main-run state)
        snapshot = await self._file_manager.read_snapshot(request.active_file)
        note_text, _ = split_note_and_transcript(snapshot.raw_text)
        
        await emit_progress(request.progress_sink, ProgressEvent(
            kind="status",
            message="Starting synthesis pass to update note-content.",
            path=request.active_file,
        ))
        
        try:
            synthesis_result = await run_synthesis_pass(
                tool_loop=self._tool_loop,
                provider=request.provider,
                context_result=context_result,
                active_file=request.active_file,
                included_files=request.included_files,
                model=request.model,
                reasoning_effort=request.reasoning_effort,
                progress_sink=async_progress_sink,
                metadata=dict(run_metadata),
                note_snapshot_text=note_text,
                run_log_rows=tuple(latest_run_log),
                permission_handler=request.permission_handler,
            )
        except Exception as exc:
            synthesis_result = SynthesisPassResult(
                ran=True, error_message=f"Synthesis pass failed: {exc}"
            )
            await emit_progress(request.progress_sink, ProgressEvent(
                kind="error",
                message=f"Synthesis pass failed: {exc}",
                path=request.active_file,
            ))
```

Update usage log combination:
```python
usage_sources = [pr.loop_result.usage_log for pr in prompt_results]
if synthesis_result.ran and synthesis_result.usage_log:
    usage_sources.append(synthesis_result.usage_log)
usage_log = combine_usage_logs(usage_sources)
```

Thread synthesis fields into `NoteModeRunResult`:
```python
synthesis_ran=synthesis_result.ran,
synthesis_error=synthesis_result.error_message,
```

### Step 6: Update TUI status display

**File: [tui/controller.py](src/aunic/tui/controller.py)** — In `_run_current_mode` (~line 496), update the note-mode status message:

```python
parts = [f"Note-mode run finished: {result.stop_reason}."]
if result.synthesis_ran:
    if result.synthesis_error:
        parts.append(f"Synthesis error: {result.synthesis_error}")
    else:
        parts.append("Synthesis complete.")
parts.append(f"[{format_usage_brief(result.usage_log.total)}]")
self._set_status(" ".join(parts))
```

### Step 7: Update `modes/__init__.py`

**File: [modes/__init__.py](src/aunic/modes/__init__.py)** — Add export:
```python
from aunic.modes.synthesis import SynthesisPassResult
```

---

## File Change Summary

| File | Action | What |
|------|--------|------|
| `src/aunic/loop/types.py` | Modify | Add `run_log` and `run_log_new_start` to `LoopRunResult` |
| `src/aunic/loop/runner.py` | Modify | Record `run_log_start_index`, populate new fields in result |
| `src/aunic/tools/note_edit.py` | Modify | Add `OUTSIDE_NOTE_TOOL_NAMES` constants, rename `_build_note_only_registry` → `build_note_only_registry` |
| `src/aunic/tools/__init__.py` | Modify | Export new symbols |
| `src/aunic/modes/synthesis.py` | **New** | `SynthesisPassResult`, `run_synthesis_pass()`, detection + formatting helpers, system prompt |
| `src/aunic/modes/types.py` | Modify | Add `synthesis_ran`, `synthesis_error` to `NoteModeRunResult` |
| `src/aunic/modes/runner.py` | Modify | Integrate synthesis pass after main loop, update usage combination |
| `src/aunic/modes/__init__.py` | Modify | Export `SynthesisPassResult` |
| `src/aunic/tui/controller.py` | Modify | Update note-mode status message for synthesis |

---

## Implementation Order

1. `loop/types.py` + `loop/runner.py` — expose run-log (prerequisite for everything)
2. `tools/note_edit.py` + `tools/__init__.py` — tool name constants + public registry
3. `modes/synthesis.py` — new file with all synthesis logic
4. `modes/types.py` — add synthesis fields to result
5. `modes/runner.py` + `modes/__init__.py` — integrate synthesis pass
6. `tui/controller.py` — update status display

---

## Design Decisions

- **Synthesis runs via `ToolLoop`**: Reuses the existing tool loop infrastructure (RunToolContext, transcript writing, permission handling) rather than building a parallel execution path
- **note_edit + note_write only**: The synthesis pass uses `build_note_only_registry()` (no research tools) since it should only integrate existing findings, not do new research
- **`work_mode="off"` for synthesis LoopRunRequest**: Prevents the synthesis pass from accidentally getting work/read tools
- **Synthesis errors are non-fatal**: A failed synthesis pass is logged and reported but does not change the main run's stop_reason. The main run already succeeded
- **`per_prompt_budget=4`**: Allows the model a few note_edit calls plus the final text-only completion message. Large enough for reasonable synthesis, small enough to prevent runaway loops
- **Latest run-log only**: Uses `run_log[run_log_new_start:]` to get only rows from the current run, not the full transcript history. This keeps the synthesis prompt focused

---

## Verification

1. **Unit test**: Mock provider + tool loop, verify synthesis pass triggers when work/read tools were used and doesn't trigger when they weren't
2. **Unit test**: Verify `_work_read_tools_were_used` with various event combinations
3. **Unit test**: Verify `_format_run_log_for_synthesis` produces readable output
4. **Integration test**: Run a note-mode prompt with `work_mode="work"`, have the mock provider use a `read` tool, verify synthesis pass runs and note_edit is called
5. **Negative test**: Run note-mode with `work_mode="off"` — synthesis should not trigger
6. **Negative test**: Run note-mode with `work_mode="read"` but model only uses web_search — synthesis should not trigger (web_search is not in `OUTSIDE_NOTE_TOOL_NAMES`)
7. **Error test**: Simulate provider error during synthesis — verify main result is still "finished" and `synthesis_error` is populated
8. **Manual TUI test**: Toggle work mode to "work", send a prompt that triggers file reads, verify synthesis pass runs and status message shows "Synthesis complete"
