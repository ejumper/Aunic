# Aunic Codebase Simplification & Efficiency Plan

## Context

The Aunic codebase (~21K LOC, 55 Python source files) is functional but contains significant code duplication across its two main execution paths (note-mode tool loop and chat-mode runner), its two SDK providers (Claude and Codex), and its text boundary detection system. This plan targets ~600 lines of net reduction through consolidation, with zero feature or behavior changes. Every change is independently testable and shippable.

Reference implementations (hermes-agent and coding-agent-program-example) confirmed that well-structured agent frameworks separate "loop mechanics" from "mode-specific behavior" -- exactly the pattern Aunic's duplication violates.

---

## Phase 1: Provider Shared Utilities (~60 lines saved, LOW RISK)

**Problem**: `claude.py` and `codex.py` contain 5 identical functions copy-pasted between them.

**New file**: `src/aunic/providers/shared.py` (~40 lines)

**What moves there**:

| Function | claude.py lines | codex.py lines | Notes |
|----------|----------------|----------------|-------|
| `build_tool_bridge_config()` | 244-261 | 303-320 | Character-identical |
| `render_messages_for_sdk()` | 270-276 | 355-361 | Character-identical |
| `_coerce_int()` | 325-326 | 523-524 | Character-identical |
| `build_turn_input_text()` | 264-267 | 349-352 | Character-identical |
| `normalize_reasoning_effort()` | 329-339 | 527-530 | Merge with `haiku_xhigh_cap` flag |

**Files modified**:
- `src/aunic/providers/shared.py` (new)
- `src/aunic/providers/claude.py` (replace functions with imports)
- `src/aunic/providers/codex.py` (replace functions with imports)

**Verification**: `pytest tests/test_codex_provider.py tests/test_claude_client.py`

---

## Phase 2: Boundary Detection Unification (~70 lines saved, LOW RISK)

**Problem**: `src/aunic/context/structure.py` lines 562-663 contain 10 boundary functions in 5 mirrored pairs (forward/backward search), each repeating the same regex/logic.

**Approach**: Collapse into 3 parameterized functions:

1. `_find_regex_boundary(text, pattern, direction, min_pos=0)` -- unifies blank-line, list-prefix, and sentence boundary pairs (6 functions -> 1)
2. `_find_indent_boundary(text, direction, min_pos=0)` -- unifies indent-change pair (2 functions -> 1)
3. `_find_whitespace_boundary(text, direction, min_pos=0)` -- unifies whitespace pair (2 functions -> 1)

Update `_find_boundary()` and `_find_progressive_boundary()` to call these with `direction="last"` or `direction="first"`.

**File modified**: `src/aunic/context/structure.py`

**Verification**: `pytest tests/test_context_structure.py` plus manual check that `_chunk_text_by_structure` produces identical output for representative inputs.

---

## Phase 3: Chat Loop State Dataclass (~100 lines saved, LOW RISK)

**Problem**: `ChatModeRunner._result_with_error()` takes 13 parameters and is called 6+ times with nearly identical argument lists, adding ~100 lines of repetitive noise.

**Approach**: Bundle mutable loop state into a dataclass:

```python
@dataclass
class _ChatLoopState:
    run_log: list[TranscriptRow]
    events: list[LoopEvent]
    tool_failures: list[ToolFailure]
    usage_entries: list[UsageLogEntry]
    assistant_message_patches: list[dict[str, object]]
    counted_turns: int = 0
    malformed_repair_count: int = 0
    citation_repair_count: int = 0
    force_final_response: bool = False
    error_message: str | None = None
    provider_metadata: dict[str, object] = field(default_factory=dict)
    provider_response_index: int = 0
```

- `_result_with_error` reduces from 13 params to ~5 (`self, context_result, request, response_text, stop_reason, state, research_state`)
- `append_run_log_message` and `append_assistant_message_patch` closures become methods on `_ChatLoopState`
- Each `_result_with_error` call site shrinks from ~14 lines to ~5

**File modified**: `src/aunic/modes/chat.py`

**Verification**: `pytest tests/test_chat_mode.py`

---

## Phase 4: Shared Tool Dispatch Loop (HIGHEST IMPACT, ~350 lines saved, MEDIUM RISK)

**Problem**: `ToolLoop.run()` (runner.py, 815 LOC) and `ChatModeRunner.run()` (chat.py, 972 LOC) share an enormous amount of identical logic: the validate-parse-execute-record cycle, malformed-turn repair, provider error handling, generated-row processing, and event emission.

**Approach**: Extract shared dispatch into `src/aunic/loop/dispatch.py` using a strategy/callback pattern.

### Step 4a: Unify `_append_generated_rows` (do first, independently testable)

The two generated-row processors (`_append_provider_generated_rows` in runner.py:637-735 and `_append_generated_rows` in chat.py:863-949) differ only in:
- How they write transcript rows (callable signature)
- Whether they track edit counts

Create a single unified function in `dispatch.py`:
```python
@dataclass
class GeneratedRowsResult:
    valid_turns: int
    successful_edit_count: int
    successful_note_tool: bool
    tool_failures: list[ToolFailure]

async def process_generated_rows(..., write_row: Callable, track_edits: bool = False) -> GeneratedRowsResult:
```

### Step 4b: Extract the main dispatch loop

Create `DispatchConfig` to hold mode-specific callbacks:

```python
@dataclass
class DispatchConfig:
    mode_label: str                    # "note" or "chat"
    validate_response: Callable        # mode-specific validation rules
    build_repair_prompt: Callable      # _repair_prompt vs _chat_repair_prompt  
    on_no_tool_call: Callable          # note: redirect to note_edit; chat: treat as final answer
    should_stop_after_tool: Callable   # note: stop on note_edit success; chat: check budget
    write_transcript_row: Callable     # how to persist transcript rows
    malformed_turn_limit: int
```

The shared loop handles: the `while True` structure, `StructuredOutputError`/`ProviderError` catches, usage recording, generated-row processing, tool validation/parsing/execution, malformed-turn repair cycle, and event emission.

### Files created/modified:
- `src/aunic/loop/dispatch.py` (new, ~200 lines)
- `src/aunic/loop/runner.py` (reduce from 815 to ~350 lines)
- `src/aunic/modes/chat.py` (reduce from 972 to ~500 lines)

**Verification**: Full test suite -- `test_loop_runner_structured.py`, `test_chat_mode.py`, `test_note_mode.py`, `test_synthesis.py`. Manual smoke test of both note and chat modes.

**Risk mitigation**: Do step 4a first (unify generated rows), test, commit. Then extract the main dispatch loop. Test, commit.

---

## Phase 5: Dead Code & Minor Cleanup (~30 lines saved, LOW RISK)

1. **Remove `_observe_provider_response()`** at runner.py:800-815 -- never called; the inline logic at lines 230-237 does the same thing
2. **Remove trailing blank lines** at runner.py:737-747 (10 empty lines)
3. **Unify `_tool_result_event_message`** (runner.py:617-634) and **`_tool_result_message`** (chat.py:845-860) -- nearly identical; move the richer version into the shared dispatch module
4. **Narrow `except Exception:`** in healthcheck methods (claude.py:74, codex.py:86) to catch specific connection/HTTP errors

**Files modified**: `runner.py`, `chat.py`, `claude.py`, `codex.py`

**Verification**: `pytest` full suite

---

## Execution Order

```
Phase 1 (Provider shared)  ─┐
                             ├── Independent, do in any order or parallel
Phase 2 (Boundary detect)  ─┘
                             
Phase 3 (Chat loop state)  ──── Prepares chat.py for Phase 4

Phase 4 (Shared dispatch)  ──── Depends on Phase 3

Phase 5 (Dead code cleanup) ─── Depends on Phase 4
```

## Impact Summary

| Phase | Lines Saved | Risk | Files Touched |
|-------|------------|------|---------------|
| 1. Provider shared | ~60 | Low | 3 (1 new, 2 modified) |
| 2. Boundary detection | ~70 | Low | 1 |
| 3. Chat loop state | ~100 | Low | 1 |
| 4. Shared dispatch | ~350 | Medium | 4 (1 new, 3 modified) |
| 5. Dead code | ~30 | Low | 2-4 |
| **Total** | **~610** | | **~8 files** |

## What This Plan Does NOT Change (and why)

- **TUI layer**: `except Exception: pass` blocks in TUI code are low-priority; TUI error handling is inherently looser and touching it risks UI regressions with no meaningful code quality gain
- **System prompts**: Compressing them risks subtle LLM behavior regressions that are nearly impossible to test automatically
- **`LLMProvider` base class**: Claude and Codex transport dataclasses differ enough (`system_prompt` vs `thread_id`/`reasoning_effort`) that a generic base adds complexity without simplification
- **`_line_starts()` caching**: Performance gain is marginal; adds statefulness to pure functions
- **ToolSessionState/RunToolContext merge**: They manage different lifecycles (session vs single-run) and merging would obscure that distinction

## Verification Plan

After all phases complete:
1. `pytest` -- full test suite passes
2. Manual smoke test: run a note-mode prompt (tool loop executes, note_edit writes)
3. Manual smoke test: run a chat-mode prompt (tool calls, final markdown response)
4. Verify no behavior change in transcript output format
5. Verify usage logging still works (check `.aunic/usage/` output)