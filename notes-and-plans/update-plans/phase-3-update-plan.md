# Implementation Guidance

## Status
This phase is implemented. The plan below describes the transition away from the old `finish` boundary; current Aunic uses natural-stop completion and no longer ships a `finish` tool.

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
    - notes-and-plans/zfuture-features/* that the user wanted to make note of but are not being implemented yet, ignore these.

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


# Phase 3: Run Loop — Implementation Plan

## Context

Phases 1 and 2 built the transcript infrastructure (parser, writer, translation, flattening) and wired it into context assembly. The infrastructure works — `ProviderRequest` has `transcript_messages`, `note_snapshot`, `user_prompt` fields; providers branch on `transcript_messages is not None` to use the structured translation path. But the run loops in both note mode and chat mode still maintain a **redundant dual in-memory list**: a legacy `list[Message]` with synthetic string content alongside the structured `list[TranscriptRow]`. The `finish` tool is the only way to end a note-mode run, which is non-standard and costs tokens. Phase 3 eliminates these issues.

---

## Step 1: Add `persistence` field to `ToolDefinition`

Enables the run loop to distinguish persistent tools (write to both run_log and disk) from ephemeral tools (run_log only) without hardcoding tool names.

**File: `src/aunic/tools/base.py`**
- Add `ToolPersistence = Literal["persistent", "ephemeral"]` type alias
- Add `persistence: ToolPersistence = "persistent"` field to `ToolDefinition` (after `execute` at line 28)
- Default `"persistent"` means all existing tools (web_search, web_fetch) need no changes
- Phase 4 will create note-edit/note-write with `persistence="ephemeral"`

---

## Step 2: Remove the `finish` tool

**File: `src/aunic/tools/note_edit.py`**
- Delete `FinishArgs` dataclass (lines 12-14)
- Delete `parse_finish_args` (lines 44-52)
- Delete `execute_finish` (lines 55-67)
- Keep `_require_string` and `_ensure_no_extra_keys` (Phase 4 note-edit will need them)
- Simplify `build_note_tool_registry()` (lines 17-41) to just `return build_research_tool_registry()`

**File: `src/aunic/tools/__init__.py`**
- Remove `FinishArgs` from import (line 3) and `__all__` (line 9)

**File: `src/aunic/tools/base.py`**
- Remove `finish_summary: str | None = None` and `finish_used_source_ids: tuple[str, ...] = ()` from `ToolExecutionResult` (lines 20-21)

**File: `src/aunic/config.py`**
- Remove `finish_summary_max_chars: int = 200` from `LoopSettings` (line 311)

---

## Step 3: Update loop types to remove finish and support natural stop

**File: `src/aunic/loop/types.py`**

- `LoopStopReason` (line 17): keep `"finished"` — reuse it to mean "assistant returned text with no tool calls" (semantic shift from "finish tool called"). This avoids churn in all downstream `stop_reason == "finished"` checks.
- `LoopMetrics` (line 58): remove `finish_called: bool = False` (line 71)
- `LoopRunResult` (line 92):
  - Remove `transcript: tuple[Message, ...]` (line 95) — the legacy Message list being eliminated
  - Remove `finish_summary: str | None` (line 99)
  - Remove `finish_used_source_ids: tuple[str, ...]` (line 100)
  - All three currently have no downstream consumers beyond the direct construction sites (verified via grep). `finish_summary` is used by TUI controller and CLI — addressed in Step 8.
- `LoopEvent.kind` (line 43): remove `"finish"` from the Literal. Use `"stop"` for all stop events.

---

## Step 4: Implement natural stop + eliminate dual list in `runner.py`

This is the core step. Changes to `src/aunic/loop/runner.py`:

### 4a. Update system prompt (lines 39-47)
```
"You are operating inside Aunic note-edit mode.",
"",
"Use at most one tool call per turn: web_search or web_fetch.",
"When the requested work is complete, reply with a plain text message and no tool call.",
"Do not create new chat-style turns, fake user prompts, transcript separators, or assistant replies unless the user explicitly asks.",
"Treat chat_thread sections as source material, not as the place to continue writing.",
```

### 4b. Delete `transcript: list[Message]` (line 85)
Remove the line entirely. `run_log` at line 86 becomes the sole in-memory history.

### 4c. Remove ALL `transcript.append(...)` calls
These occur at approximately lines: 195, 249, 294, 296, 315-316, 322, 333-334, 360, 470, 481-482. Replace each with the appropriate `run_log.append(TranscriptRow(...))` or `append_run_log_message()` call:

- **Repair prompts** (currently `transcript.append(Message(role="user", content=_repair_prompt(...)))`):
  Replace with `append_run_log_message("user", _repair_prompt(...))`
- **Malformed assistant summaries** (currently `transcript.append(Message(role="assistant", content=_assistant_response_summary(...)))`):
  Replace with `append_run_log_message("assistant", response.text or "(empty response)")`
  Then also `append_run_log_message("user", _repair_prompt(...))`
- **Tool call entries** (line 333-334): already handled by the `run_log.append(TranscriptRow(...))` at lines 343-351 — just delete the `transcript.append(...)` line
- **Tool result entries** (line 481-482): already handled by `run_log.append(...)` at lines 491-499 — just delete the `transcript.append(...)` line
- **Plan reset** (line 195): replace with `run_log = list(request.context_result.transcript_rows or [])`
- **Verification reset** (line 470): same pattern — re-init run_log from transcript rows

### 4d. Write user message row at run start (after line 86)
```python
user_msg_row_number = await runtime.write_transcript_row(
    "user", "message", None, None, current_user_prompt_text,
)
run_log.append(TranscriptRow(
    row_number=user_msg_row_number,
    role="user", type="message", content=current_user_prompt_text,
))
```

### 4e. Always set `transcript_messages` on `ProviderRequest` (lines 215-228)
Change from conditional to unconditional:
```python
provider_request = ProviderRequest(
    messages=[],  # legacy field, unused when transcript_messages is set
    transcript_messages=list(run_log),
    note_snapshot=active_prompt_run.note_snapshot_text or None,
    user_prompt=current_user_prompt_text or None,
    ...
)
```

### 4f. Update `_validate_provider_response()` (lines 819-828)
Allow zero tool calls (natural stop):
```python
def _validate_provider_response(response, tool_map) -> str | None:
    if not response.tool_calls:
        return None  # Natural stop — valid
    if len(response.tool_calls) != 1:
        return "Expected at most one tool call per turn."
    if response.tool_calls[0].name not in tool_map:
        return f"Unknown tool {response.tool_calls[0].name!r}."
    return None
```

### 4g. Add natural stop handling (after validation, before tool dispatch)
After validation passes and `response.tool_calls` is empty:
```python
if not response.tool_calls:
    assistant_text = response.text.strip()
    if not assistant_text:
        malformed_repair_count += 1
        append_run_log_message("assistant", "(empty response)")
        append_run_log_message("user", _repair_prompt("Empty response with no tool call."))
        current_user_prompt_text = _repair_prompt("Empty response with no tool call.")
        if malformed_repair_count >= self._settings.malformed_turn_limit:
            stop_reason = "malformed_turn_limit"
            break
        continue

    row_number = await runtime.write_transcript_row(
        "assistant", "message", None, None, assistant_text,
    )
    run_log.append(TranscriptRow(
        row_number=row_number, role="assistant", type="message",
        content=assistant_text,
    ))
    total_valid_turns += 1
    current_loop_turns += 1
    stop_reason = "finished"
    await append_loop_event(LoopEvent(
        kind="stop", message="Run completed (natural stop).",
        details={"assistant_text_preview": assistant_text[:200]},
    ))
    break
```

### 4h. Move verification logic to trigger on natural stop
The current verification block (lines 378-473) triggers inside `if result.status == "finished"`. Move it to trigger after the natural stop block above (before the `break`). The logic stays the same, just remove references to `finish_summary`/`finish_used_source_ids`.

On verification failure (repair loop): reset run_log from transcript rows on disk:
```python
run_log = list(parse_transcript_rows(
    (await self._file_manager.read_snapshot(active_file)).raw_text
) or [])
```

### 4i. Remove the entire `if result.status == "finished":` block (lines 370-476)
This block no longer applies — the finish tool doesn't exist.

### 4j. Add persistent/ephemeral tool write logic
For tool_call writes (currently lines 336-351):
```python
if definition.persistence == "persistent":
    row_number = await runtime.write_transcript_row(
        "assistant", "tool_call", tool_call.name, tool_call.id, tool_call.arguments,
    )
else:
    row_number = _next_run_log_row_number(run_log)

run_log.append(TranscriptRow(
    row_number=row_number, role="assistant", type="tool_call",
    tool_name=tool_call.name, tool_id=tool_call.id, content=tool_call.arguments,
))
```
Same pattern for tool_result writes (currently lines 484-499).

### 4k. Remove `finish_summary`/`finish_used_source_ids` variables and usage
Delete lines 120-121 and all references in the LoopRunResult construction (lines 558-559).

### 4l. Update LoopRunResult construction (lines 552-565)
Remove `transcript=tuple(transcript)`, `finish_summary=finish_summary`, `finish_used_source_ids=finish_used_source_ids`. Remove `finish_called=finish_summary is not None` from LoopMetrics construction.

### 4m. Remove helper functions
- Delete `_assistant_tool_call_message()` (lines 839-840) — no longer needed
- Delete `_assistant_response_summary()` (lines 843-844) — replaced by direct text
- Delete `validate_finish_source_ids()` from `_LoopRuntime` (lines 739-746)

### 4n. Update `_repair_prompt()` (lines 831-836)
Change `"Reply with exactly one tool call (finish, web_search, web_fetch)."` to:
`"Reply with exactly one tool call (web_search, web_fetch) or a final plain response."`

---

## Step 5: Eliminate dual list in `chat.py`

**File: `src/aunic/modes/chat.py`**

Chat mode has the same dual-list pattern. Changes mirror Step 4 but are simpler (no finish tool, no verification, no plan).

### 5a. Remove `transcript: list[Message]` (line 123)
All `transcript.append(Message(...))` calls → convert to `append_run_log_message()` or direct `run_log.append(TranscriptRow(...))`.

Affected approximate lines: 138, 196-200, 288-293, 295-303, 315, 322, 341-346, 351-356, 403-408, 426-432, 450, 464-469, 471-476, 519-524, 526-531.

### 5b. Always set `transcript_messages` on ProviderRequest (lines 155-159)
Same as Step 4e:
```python
provider_request = ProviderRequest(
    messages=[],
    transcript_messages=list(run_log),
    ...
)
```

### 5c. Remove `_assistant_tool_call_message()` (line 892-893)
No longer needed.

### 5d. No stop signal changes needed
Chat mode already uses natural stop (text response with no tool calls → finished at line 552-595).

---

## Step 6: Update downstream consumers

**File: `src/aunic/tui/controller.py` (lines 425-436)**
Replace finish_summary status line:
```python
self._set_status(
    f"Note-mode run finished: {result.stop_reason}. "
    f"[{format_usage_brief(result.usage_log.total)}]"
)
```

**File: `src/aunic/cli.py` (lines 657-658, 684)**
Remove `finish_summary`, `finish_used_source_ids`, `finish_called` from the usage record dict.

**File: `src/aunic/modes/runner.py`**
No changes needed — `loop_result.stop_reason != "finished"` check at line 103 still works since we kept `"finished"` as the natural stop reason.

---

## Step 7: Update tests

All tests that reference finish-related APIs need updating:

**`tests/test_note_edit_tools.py`** — Tests the finish tool directly. Delete finish-specific tests; add test that `build_note_tool_registry()` returns only research tools.

**`tests/test_integration_smoke.py`** — Lines 362, 432, 448, 471, 493, 593, 615, 640, 669: Replace `finish_summary`/`finish_called`/`finish_used_source_ids` assertions. Update test providers to return natural-stop responses (text with no tool calls).

**`tests/test_note_mode.py`** — Lines 57, 61-62, 74-75: Remove finish fields from `LoopRunResult` construction, remove `finish_called` from `LoopMetrics`.

**`tests/test_note_mode_cli.py`** — Lines 34, 38-39, 133: Same removals.

**`tests/test_tui_controller.py`** — Lines 46-47: Remove from test `LoopRunResult`.

**`tests/test_tui_app.py`** — Lines 49-50: Same.

**`tests/test_progress_bridge.py`** — Lines 74-75: Same.

---

## Step 8: Verify end-to-end

1. Run the full test suite: `pytest tests/`
2. Manual test: open a markdown note, send a prompt in note-mode, verify:
   - User message row appears in transcript table
   - Model calls tools → tool_call + tool_result rows appear
   - Model responds with text (no tools) → assistant message row appears, run ends
   - No `finish` tool in the tool list
3. Manual test: chat-mode send, verify transcript rows written correctly
4. Check providers receive structured `transcript_messages` (not legacy `messages`)

---

## Files Modified (summary)

| File | Changes |
|------|---------|
| `src/aunic/tools/base.py` | Add `persistence` field, remove finish fields from `ToolExecutionResult` |
| `src/aunic/tools/note_edit.py` | Remove finish tool, simplify registry |
| `src/aunic/tools/__init__.py` | Remove `FinishArgs` export |
| `src/aunic/loop/types.py` | Remove finish fields from `LoopRunResult`, `LoopMetrics`, `LoopEvent` |
| `src/aunic/loop/runner.py` | Core refactor: natural stop, single run_log, persistent/ephemeral dispatch, user msg row, system prompt |
| `src/aunic/modes/chat.py` | Remove dual list, always use transcript_messages |
| `src/aunic/tui/controller.py` | Remove finish_summary status display |
| `src/aunic/cli.py` | Remove finish fields from usage record |
| `src/aunic/config.py` | Remove `finish_summary_max_chars` |
| `tests/test_*` | Update 7 test files to remove finish references |

## Implementation Order

1. Steps 1-3 together (tool persistence metadata + remove finish tool + type updates)
2. Step 4 (core runner.py refactor — largest single change)
3. Step 5 (chat.py parallel refactor)
4. Steps 6-7 (downstream consumers + tests)
5. Step 8 (verification)
