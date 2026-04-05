# Implementation Guidance

## Status
This phase is implemented. References below to `prompt_from_note` are historical planning context; current Aunic note mode is direct-only and keeps `note-content` separate from the transcript during context building.

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

# Phase 2: Context Assembly + API Translation

## Context

Phase 1 built all the individual components — transcript parser, writer, translation module, richer domain types, and conditional provider branches — but none are connected end-to-end. The run loop still creates a single legacy `Message(role="user", content=model_input_text)` and builds `ProviderRequest` with only the `messages` field. The `transcript_messages`, `note_snapshot`, and `user_prompt` fields are always `None`, so every provider takes the fallback path.

Phase 2 wires these components together: parse transcript rows from the context engine, separate note-snapshot from user-prompt, populate the new `ProviderRequest` fields, and activate the provider translation branches that already exist.

## Files to modify

| File | Purpose |
|------|---------|
| `src/aunic/context/types.py` | Add `transcript_rows` to `ContextBuildResult`, add `note_snapshot_text` + `user_prompt_text` to `PromptRun` |
| `src/aunic/context/engine.py` | Parse transcript rows in `build_context()`, add `_build_note_snapshot()`, populate new `PromptRun` fields |
| `src/aunic/loop/runner.py` | Initialize `run_log`, extend it after writes, populate `ProviderRequest` new fields, update `_prompt_run_with_model_input()` |
| `src/aunic/modes/chat.py` | Add `transcript_rows` to `_ChatContextResult`, parse in `_build_context()`, populate `ProviderRequest` new fields |
| `src/aunic/transcript/flattening.py` | **New file.** Tool-specific content flattening for provider-facing results |
| `src/aunic/transcript/translation.py` | Integrate flattening layer for tool result rows |
| `src/aunic/plan/service.py` | Update `_prompt_run_with_model_input()` callers to carry new fields after plan execution |

## Files to reference

- `notes-and-plans/building-context/transcript-to-api.md` — the 3-step pipeline spec
- `notes-and-plans/building-context/building-context.md` — how note-snapshot + user-prompt + transcript combine
- `notes-and-plans/tools/tools.md` — in-memory vs transcript distinction, content cell formats
- `notes-and-plans/active-markdown-note/active-markdown-note.md` — transcript table format and encoding rules
- `~/Desktop/coding-agent-program-example/src/utils/messages.ts` — reference for message normalization patterns

---

## Step 1: Add `transcript_rows` field to `ContextBuildResult`

**File:** `src/aunic/context/types.py`

Add a pre-parsed field alongside the existing `transcript_text`:

```python
from aunic.domain import TranscriptRow

@dataclass(frozen=True)
class ContextBuildResult:
    # ... existing fields ...
    transcript_text: str | None = None
    transcript_rows: list[TranscriptRow] | None = None  # NEW
```

Pure additive — no existing code breaks.

## Step 2: Populate `transcript_rows` in `ContextEngine.build_context()`

**File:** `src/aunic/context/engine.py`

After line 89 where `transcript_text` is already computed:

```python
from aunic.transcript.parser import parse_transcript_rows

raw_transcript = analyses[0].parsed_file.transcript_text if analyses else None
parsed_rows = parse_transcript_rows(raw_transcript) if raw_transcript else None
```

Pass `transcript_rows=parsed_rows` to the `ContextBuildResult` constructor.

## Step 3: Add `note_snapshot_text` and `user_prompt_text` to `PromptRun`

**File:** `src/aunic/context/types.py`

```python
@dataclass(frozen=True)
class PromptRun:
    # ... existing fields ...
    note_snapshot_text: str = ""    # NEW: rendered note content with maps
    user_prompt_text: str = ""      # NEW: raw user prompt, separate from note
    # model_input_text stays for backward compat
```

**File:** `src/aunic/context/engine.py`

Add a `_build_note_snapshot()` helper and populate both old and new fields in `_build_prompt_runs()`:

```python
def _build_note_snapshot(
    parsed_note_text: str,
    target_map_text: str,
    read_only_map_text: str = "",
) -> str:
    parts = [f"NOTE SNAPSHOT\n{parsed_note_text}", f"TARGET MAP\n{target_map_text}"]
    if read_only_map_text:
        parts.append(f"READ-ONLY MAP\n{read_only_map_text}")
    return "\n\n".join(parts)
```

**Where target_map and read_only_map go:** Embedded in `note_snapshot_text`. They describe the note's structure and belong with it. The combined final user message becomes `"{note_snapshot_with_maps}\n\n---\n\n{user_prompt}"`, matching the spec format.

When building `PromptRun` instances (both `direct` and `prompt_from_note` modes), set:
- `note_snapshot_text = _build_note_snapshot(parsed_note_text, target_map_text, read_only_map_text)`
- `user_prompt_text = request.user_prompt` (or `source["prompt_text"]` for prompt-from-note)
- `model_input_text = _assemble_model_input(...)` (unchanged, for backward compat)

## Step 4: Wire up `ProviderRequest` in the run loop

**File:** `src/aunic/loop/runner.py`

### 4a. Initialize `run_log`

After line 85, alongside the existing `transcript: list[Message]`:

```python
run_log: list[TranscriptRow] = list(request.context_result.transcript_rows or [])
```

### 4b. Extend `run_log` after each transcript write

After each `write_transcript_row()` call (for tool_call ~line 298, tool_result ~line 420, and messages), also append a `TranscriptRow` to `run_log`:

```python
run_log.append(TranscriptRow(
    row_number=row_number,
    role=role,
    type=row_type,
    tool_name=tool_name,
    tool_id=tool_id,
    content=content,
))
```

### 4c. Populate `ProviderRequest` with new fields

Replace the `ProviderRequest` construction at line 192:

```python
provider_request = ProviderRequest(
    messages=list(transcript),
    transcript_messages=list(run_log) if request.context_result.transcript_rows is not None else None,
    note_snapshot=active_prompt_run.note_snapshot_text or None,
    user_prompt=active_prompt_run.user_prompt_text or active_prompt_run.prompt_text or None,
    tools=[definition.spec for definition in registry],
    system_prompt=_build_system_prompt(request.system_prompt),
    model=request.model,
    reasoning_effort=request.reasoning_effort,
    metadata=dict(request.metadata),
)
```

**Gating:** When `transcript_rows` is `None` (file has no transcript section), `transcript_messages` stays `None` and the provider takes the legacy fallback path. When `transcript_rows` is `[]` (empty transcript), the provider takes the new path and `translate_for_anthropic(group_assistant_rows([]), note_snapshot, user_prompt)` correctly returns just the final combined user message.

### 4d. Update `_prompt_run_with_model_input()`

Add forwarding of new fields so plan/verification re-wrapping preserves them:

```python
def _prompt_run_with_model_input(
    prompt_run: PromptRun,
    model_input_text: str,
    *,
    per_prompt_budget: int,
    note_snapshot_text: str = "",
    user_prompt_text: str = "",
) -> PromptRun:
    return PromptRun(
        # ... existing fields ...
        note_snapshot_text=note_snapshot_text or prompt_run.note_snapshot_text,
        user_prompt_text=user_prompt_text or prompt_run.user_prompt_text,
    )
```

Update the call site after plan execution (~line 160) to pass rebuilt note-snapshot and user-prompt texts.

## Step 5: Wire up `ProviderRequest` in chat mode

**File:** `src/aunic/modes/chat.py`

### 5a. Add fields to `_ChatContextResult`

```python
@dataclass(frozen=True)
class _ChatContextResult:
    file_snapshots: tuple[FileSnapshot, ...]
    warnings: tuple[ParseWarning, ...]
    parsed_note_text: str
    model_input_text: str
    transcript_rows: list[TranscriptRow] | None = None  # NEW
    note_snapshot_text: str = ""                          # NEW
```

### 5b. Parse transcript in `_build_context()`

In `_build_context()`, after calling `split_note_and_transcript()`:

```python
from aunic.transcript.parser import parse_transcript_rows

raw_transcript = analyses[0].parsed_file.transcript_text if analyses else None
transcript_rows = parse_transcript_rows(raw_transcript) if raw_transcript else None
```

Pass to `_ChatContextResult`.

### 5c. Populate `ProviderRequest` in chat loop

Same pattern as the note-mode loop: initialize `run_log` from `context_result.transcript_rows`, extend after writes, populate `ProviderRequest` with `transcript_messages`, `note_snapshot`, `user_prompt`.

For chat mode, `note_snapshot` is simply `parsed_note_text` (no target/read-only maps).

## Step 6: Create provider-facing result flattening

**New file:** `src/aunic/transcript/flattening.py`

```python
def flatten_tool_result_for_provider(row: TranscriptRow) -> str:
    """Convert a tool_result/tool_error row's content to provider-facing text."""
    if isinstance(row.content, str):
        return row.content
    if row.tool_name == "web_search" and isinstance(row.content, list):
        return _flatten_search_results(row.content)
    if row.tool_name == "web_fetch" and isinstance(row.content, dict):
        return _flatten_fetch_summary(row.content)
    return json.dumps(row.content, ensure_ascii=False, separators=(",", ":"))
```

Tool-specific flatteners:
- `_flatten_search_results(results: list) -> str` — renders each result as `title | url | snippet` on its own line
- `_flatten_fetch_summary(result: dict) -> str` — renders as `Title: ...\nURL: ...\nSnippet: ...`

### Integration in `translation.py`

Modify `_translate_tool_row_for_anthropic()` and the OpenAI tool result path to use `flatten_tool_result_for_provider(row)` instead of `_content_as_text(row.content)` for `tool_result` and `tool_error` type rows. Since these functions already receive the full `TranscriptRow`, this is a one-line change per call site.

Keep `_content_as_text()` for non-tool-result content (user messages, assistant text, tool_call arguments).

## Step 7: Handle plan service path

**File:** `src/aunic/plan/service.py`

The plan service builds its own single-turn `ProviderRequest` with `messages` only. This is correct — planning is not a transcript replay. Leave the plan service's internal `ProviderRequest` construction unchanged.

After plan execution, when `_prompt_run_with_model_input()` is called in `runner.py` (~line 160), also rebuild note-snapshot and user-prompt texts:

```python
rebuilt_note_snapshot = _build_note_snapshot(
    parsed_note_text=request.context_result.parsed_note_text,
    target_map_text=request.context_result.target_map_text,
    read_only_map_text=request.context_result.read_only_map_text,
)
# Optionally prepend objectives/search context to note_snapshot
active_prompt_run = _prompt_run_with_model_input(
    active_prompt_run,
    model_input_text=build_augmented_note_input(...),
    per_prompt_budget=current_turn_cap,
    note_snapshot_text=rebuilt_note_snapshot,
    user_prompt_text=active_prompt_run.prompt_text,
)
```

---

## Implementation order

```
Step 1 → Step 2 → Step 3 (additive data model + engine changes, zero risk)
    ↓
Step 4 (activate in note-mode run loop — this is the critical wiring)
    ↓
Step 7 (update plan service callers)

Step 5 (activate in chat-mode — parallel with step 4)

Step 6 (flattening module — independent, can be done anytime)
```

Steps 1-3 are purely additive and cannot break existing behavior. Step 4 is the activation point where providers start receiving `transcript_messages` and switching from the fallback to the new translation path. Step 5 mirrors Step 4 for chat mode.

## Backward compatibility

The dual-path design from Phase 1 provides a natural safety net:
- **`transcript_messages is None`**: provider takes legacy path (current working state)
- **`transcript_messages is not None`**: provider takes new translation path

Files without a `---\n# Transcript` section produce `transcript_rows = None` → legacy path. Files with a transcript section (even empty) produce `transcript_rows = []` → new path with only the combined user message.

## Verification

1. **Unit tests:** Extend `tests/test_transcript.py` with round-trip test: write rows → build context → verify `transcript_rows` matches. Add `tests/test_transcript_flattening.py` for the new module.
2. **Integration test:** Create a note file with a populated transcript table, run through context engine + translation, verify the output matches the Anthropic/OpenAI format examples from `transcript-to-api.md`.
3. **End-to-end:** Run a note-mode session with a note that has existing transcript rows. Verify the provider receives properly formatted messages (add logging or breakpoint in `_generate_native()`). Confirm both the transcript history and the combined final user message appear correctly.
4. **Regression:** Run existing test suite to confirm legacy path still works for files without transcript sections.
