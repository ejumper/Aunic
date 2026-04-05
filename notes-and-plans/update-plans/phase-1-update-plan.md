# Implementation Guidance

## Status
This phase is implemented. The sections below describe the pre-transcript baseline that existed when Phase 1 was planned; current Aunic now uses transcript-table persistence instead of blockquotes or `# Search Results` writes for active flows.

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

# Foundation: Transcript and Provider Message Model

## Context

Aunic's current persistence and provider communication model is string-based. The `Message(role, content, name)` dataclass carries only plain text. Chat mode persists history as `***`-separated blockquotes in the note body. Note mode stores tool interactions as formatted strings in an in-memory list. Search results go into a `# Search Results` heading. This means the model never sees structured tool history -- it gets flattened pseudo-conversations.

The goal is to replace all of this with a single **markdown transcript table** at the bottom of the active note, parsed into structured objects, and translated per-provider (Anthropic content blocks / OpenAI tool_calls) before sending to the API. This is the foundational layer that every subsequent update (run loop, tool updates, rendering, commands) depends on.

---

## Step 1: Define Richer Message Data Types

**File:** `src/aunic/domain.py`

Add new types alongside (not replacing) the existing `Message`:

```python
MessageType = Literal["message", "tool_call", "tool_result", "tool_error"]

@dataclass(frozen=True)
class TranscriptRow:
    """A parsed row from the markdown transcript table."""
    row_number: int
    role: Role                    # "user" | "assistant" | "tool"
    type: MessageType
    tool_name: str | None = None
    tool_id: str | None = None
    content: Any = None           # parsed JSON value (str, dict, list, etc.)
```

Also add content block types needed by the translation pipeline:

```python
@dataclass(frozen=True)
class TextBlock:
    text: str

@dataclass(frozen=True)
class ToolUseBlock:
    tool_name: str
    tool_id: str
    input: dict[str, Any]

@dataclass(frozen=True)
class ToolResultBlock:
    tool_id: str
    content: Any
    is_error: bool = False

ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock
```

Add a bridge method `TranscriptRow.to_legacy_message() -> Message` so existing consumers can gradually migrate.

**Keep the existing `Message` class untouched** -- it continues to work for all current callers.

**Dependencies:** None  
**Verify:** Unit test constructing each type, verifying frozen behavior, round-tripping `to_legacy_message()`.

---

## Step 2: Create Transcript Table Parser

**New file:** `src/aunic/transcript/__init__.py` (empty, makes it a package)  
**New file:** `src/aunic/transcript/parser.py`

### Functions

- **`find_transcript_section(text: str) -> tuple[int, int] | None`**  
  Locate `---\n# Transcript` in file text. Return `(start_offset, end_of_file)` or `None`.

- **`split_note_and_transcript(text: str) -> tuple[str, str | None]`**  
  Return `(note_content, transcript_section_text_or_none)`. The split point is the `---` line immediately before `# Transcript`. Note-content does NOT include the `---` separator.

- **`parse_transcript_rows(transcript_text: str) -> list[TranscriptRow]`**  
  Main parser. For each line in the transcript section:
  1. Skip if it doesn't start with `|` or has fewer than 6 `|` delimiters (header/delimiter lines).
  2. Split on `|` -- take stripped elements at positions 1-5 for `#`, `role`, `type`, `tool_name`, `tool_id`.
  3. **6th-delimiter rule:** everything after the 6th `|` is the raw content cell.
  4. `json.loads()` the content cell. Strings get unquoted, objects/arrays get parsed.
  5. `tool_name`/`tool_id` that are empty or whitespace become `None`.

### Edge cases
- Empty/missing transcript section: return `[]`
- Content cell with `|` inside JSON strings: handled by the 6th-delimiter rule
- Malformed rows (wrong column count): silently skipped

**Dependencies:** Step 1 (`TranscriptRow` type)  
**Verify:** Unit tests covering:
- Well-formed table with user/assistant/tool rows
- 6th-delimiter rule: JSON string containing `|`
- Empty table (header + delimiter only, no data)
- Missing transcript section entirely
- Empty `tool_name`/`tool_id` cells parsed as `None`
- Content cells with JSON arrays, objects, nested structures, and plain strings

---

## Step 3: Create Transcript Table Writer

**New file:** `src/aunic/transcript/writer.py`

### Functions

- **`ensure_transcript_section(text: str) -> str`**  
  If no transcript section exists, append:
  ```
  \n\n---\n# Transcript\n| # | role      | type        | tool_name  | tool_id  | content\n|---|-----------|-------------|------------|----------|-------------------------------\n
  ```
  If it already exists, return unchanged.

- **`append_transcript_row(text: str, role: str, type: str, tool_name: str | None, tool_id: str | None, content: Any) -> tuple[str, int]`**  
  1. Call `ensure_transcript_section(text)`
  2. Find last data row's `#` value to determine next row number
  3. Format the new row with `json.dumps(content, ensure_ascii=False, separators=(",", ":"))`
  4. Append after the last row (or after the delimiter line if no rows yet)
  5. Return `(updated_text, assigned_row_number)`

- **`format_transcript_row(row_number, role, type, tool_name, tool_id, content) -> str`**  
  Format a single `| # | role | type | tool_name | tool_id | content` line. Pad columns for alignment. Content is always JSON-encoded.

- **`repair_transcript_section(text: str) -> str`**  
  Scan bottom-up for lines matching the transcript row pattern (`| <digits> | <role> | ...`). If found without a proper header above them, reconstruct the header + delimiter immediately above the first matched row.

### Internal helpers
- `_find_last_row_number(transcript_text: str) -> int` -- extract `#` from last data row, return 0 if none
- `_is_transcript_data_row(line: str) -> bool` -- regex check for `| \d+ | ...` pattern

**Dependencies:** Step 2 (`find_transcript_section`, `split_note_and_transcript`)  
**Verify:** Unit tests:
- Append to file with no transcript section (initialization)
- Append to file with existing transcript (auto-increment)
- Append multiple rows sequentially
- Content with special characters (newlines in JSON, quotes, pipes)
- Repair: damaged header with surviving rows
- Round-trip: write rows then parse them back, verify equality

---

## Step 4: Implement Transcript Row Deletion

**File:** `src/aunic/transcript/writer.py` (add functions)

### Functions

- **`delete_rows_by_tool_id(text: str, tool_id: str) -> str`**  
  Parse all rows, remove every row where `tool_id` matches. Cascading: if a `tool_result`/`tool_error` is targeted, also remove matching `tool_call` rows (same `tool_id`). And vice versa. Renumber remaining rows.

- **`delete_row_by_number(text: str, row_number: int) -> str`**  
  Remove the row with the given `#`. If it has a `tool_id`, cascade-delete all rows with the same `tool_id`. Renumber remaining rows.

- **`_renumber_rows(lines: list[str]) -> list[str]`**  
  Rewrite the `#` column in all data rows sequentially starting from 1.

**Dependencies:** Steps 2, 3  
**Verify:** Unit tests:
- Delete user message by row number, verify renumbering
- Delete `tool_result` cascades to delete matching `tool_call`
- Delete `tool_call` cascades to delete matching `tool_result`/`tool_error`
- Delete by `tool_id` removes all matching rows
- Delete from single-row transcript

---

## Step 5: Build the Transcript-to-API Translation Pipeline

**New file:** `src/aunic/transcript/translation.py`

This is the 3-step process from `transcript-to-api.md`.

### Functions

- **`group_assistant_rows(rows: list[TranscriptRow]) -> list[TranscriptRow | list[TranscriptRow]]`**  
  Consecutive rows with `role="assistant"` merge into a group (list). All other rows stay as individual items.

- **`translate_for_anthropic(groups, note_snapshot: str, user_prompt: str) -> list[dict]`**  
  - User `message` rows: `{"role": "user", "content": "<text>"}`
  - Assistant groups: single message, `content` array of `{"type": "text", "text": "..."}` and `{"type": "tool_use", "id": "...", "name": "...", "input": {...}}` blocks. If group is text-only, use shorthand string `content`.
  - Consecutive tool rows: merge into single `{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "...", "content": "..."}]}`. Add `"is_error": true` for `tool_error` type.
  - Final message: `{"role": "user", "content": "<note_snapshot>\n\n---\n\n<user_prompt>"}`

- **`translate_for_openai(groups, note_snapshot: str, user_prompt: str) -> list[dict]`**  
  - User `message` rows: `{"role": "user", "content": "<text>"}`
  - Assistant groups: text joined into `content` (or `null`), tool calls into `tool_calls` array with `{"id": "...", "type": "function", "function": {"name": "...", "arguments": "<json-string>"}}`.
  - Tool rows: each becomes own `{"role": "tool", "tool_call_id": "...", "content": "..."}`
  - Final user message same as Anthropic.

- **`translate_transcript(rows, provider: str, note_snapshot: str, user_prompt: str) -> list[dict]`**  
  Dispatch: `"claude"`/`"codex"` -> Anthropic format, `"llama_cpp"` -> OpenAI format.

**Dependencies:** Step 1 (uses `TranscriptRow`)  
**Verify:** Tests with the full walkthrough example from `transcript-to-api.md`:
- Anthropic output matches expected (alternating roles, content blocks)
- OpenAI output matches expected (separate tool messages, null content)
- Mixed assistant turns (text + tool_call in same group)
- Tool errors: `is_error: true` for Anthropic, plain content for OpenAI
- Empty transcript produces only the final combined user message
- Consecutive tool results merge (Anthropic) vs stay separate (OpenAI)

---

## Step 6: Widen ProviderRequest for Structured Transcript

**File:** `src/aunic/domain.py`

Add an optional field to `ProviderRequest` for the migration period:

```python
@dataclass(frozen=True)
class ProviderRequest:
    messages: list[Message]
    transcript_messages: list[TranscriptRow] | None = None
    note_snapshot: str | None = None
    user_prompt: str | None = None
    # ... existing fields unchanged
```

The `transcript_messages` + `note_snapshot` + `user_prompt` fields are the new path. When present, providers use them via the translation pipeline. When absent, providers fall back to the old `messages` path. This means **zero existing code breaks**.

**Dependencies:** Step 1  
**Verify:** Existing tests pass unchanged. New test constructs a `ProviderRequest` with `transcript_messages` set and verifies the fields are accessible.

---

## Step 7: Update Envelope Builders for Structured Messages

**File:** `src/aunic/providers/envelope.py`

Add new builder functions alongside existing ones (do NOT modify existing builders):

- **`build_llama_structured_messages(translated_messages: list[dict], system_prompt: str | None, tools: list[ToolSpec]) -> list[dict]`**  
  Prepend the system message (same content as `build_llama_native_messages` uses), then append all translated messages (already in OpenAI format). Add tool definitions to the payload if tools are provided.

- **`build_claude_structured_input(translated_messages: list[dict], system_prompt: str | None, tools: list[ToolSpec]) -> str`**  
  For the Claude SDK path (which takes a single string), render the Anthropic-format structured messages as a well-formatted conversation string. This is a serialization bridge until the Claude SDK supports multi-turn message arrays.

Existing `render_conversation()`, `build_llama_native_messages()`, etc. remain unchanged.

**Dependencies:** Step 5 (the builders accept output from the translation pipeline)  
**Verify:** Unit tests building structured messages, verifying output format. Existing builder tests pass unchanged.

---

## Step 8: Update LlamaCppProvider for Structured Transcript

**File:** `src/aunic/providers/llama_cpp.py`

In `_generate_native()` and `_generate_text_tool_blocks()`, add a conditional branch:

```python
if request.transcript_messages is not None:
    translated = translate_for_openai(
        group_assistant_rows(request.transcript_messages),
        request.note_snapshot or "",
        request.user_prompt or "",
    )
    messages = build_llama_structured_messages(translated, ...)
else:
    messages = build_llama_native_messages(request)  # existing path
```

For native tool calling: the translated messages already contain `tool_calls` arrays that match the OpenAI chat completion format, so the HTTP payload assembly is straightforward.

**Dependencies:** Steps 5, 6, 7  
**Verify:** Existing llama_cpp tests pass (they don't set `transcript_messages`). New test with `transcript_messages` verifies the HTTP payload contains proper OpenAI-format tool_calls and tool messages.

---

## Step 9: Update ClaudeProvider for Structured Transcript

**File:** `src/aunic/providers/claude.py`  
**File:** `src/aunic/providers/claude_client.py`

The Claude provider currently sends everything through `ClaudeSession.query(prompt_text)` -- a single string. The Claude Agent SDK's `ClaudeSDKClient` accepts a string prompt, not a message array.

**Approach for this step:** When `transcript_messages` is present, use `translate_for_anthropic()` then serialize the structured messages via `build_claude_structured_input()` into the prompt string. This gives the model proper conversation structure even through the string interface.

In `_generate_native()` and `_generate_text_tool_blocks()`:
```python
if request.transcript_messages is not None:
    translated = translate_for_anthropic(
        group_assistant_rows(request.transcript_messages),
        request.note_snapshot or "",
        request.user_prompt or "",
    )
    prompt_text = build_claude_structured_input(translated, ...)
else:
    prompt_text = build_codex_input_text(request)  # existing path
```

**Note:** A future step (outside this foundation work) should explore whether the Claude Agent SDK can accept message arrays directly, which would give true multi-turn tool history. For now, the serialized representation is a significant improvement over the current flat text dump.

**Dependencies:** Steps 5, 6, 7  
**Verify:** Existing Claude tests pass. New test with `transcript_messages` verifies the prompt string contains properly formatted conversation with tool_use/tool_result blocks.

---

## Step 10: Update CodexProvider for Structured Transcript

**File:** `src/aunic/providers/codex.py`

Same pattern as Steps 8/9. When `transcript_messages` is present, translate for Anthropic format (Codex uses Anthropic internally), serialize via `build_codex_structured_input_text()`.

**Dependencies:** Steps 5, 6, 7  
**Verify:** Existing tests pass. New test with structured transcript.

---

## Step 11: Teach Context Engine to Strip Transcript

**Files:**
- `src/aunic/context/markers.py`
- `src/aunic/context/structure.py`
- `src/aunic/context/engine.py`

The context engine currently treats the entire file as note-content. It needs to strip the transcript section before marker/structure analysis so that `parsed_note_text`, target maps, and edit commands operate only on `note-content`.

### Changes in `markers.py`

In `analyze_note_file()` and `analyze_chat_file()`:
1. Before scanning markers, call `split_note_and_transcript(snapshot.raw_text)`.
2. Use only the `note_content` portion for all marker scanning and visibility computation.
3. Store the full `raw_text` in the snapshot (unchanged), but all char-indexed arrays (`labels_by_char`, `visible_by_char`, `wrapper_by_char`) are computed against `note_content` only.
4. The `ParsedNoteFile` keeps track of the transcript split point so downstream consumers know where note-content ends.

### Changes in `structure.py`

- `build_structural_nodes()` operates on the note-content text only, not the full file.
- No structural nodes are created for the transcript section (it's handled separately by the transcript parser/renderer).

### Changes in `engine.py`

- `build_context()` works as before from the consumer perspective. The `parsed_note_text` returned now excludes the transcript table. Target maps don't include transcript rows.
- The `ContextBuildResult` can optionally carry a `transcript_text: str | None` field for callers that need it.

### Important backward compatibility note
Files without a transcript section work exactly as before -- `split_note_and_transcript` returns the whole text as note-content and `None` for transcript.

**Dependencies:** Step 2 (`split_note_and_transcript`)  
**Verify:**
- Existing context engine tests pass (no transcript section in test files)
- New test: file with `---\n# Transcript\n| ...` -- transcript excluded from `parsed_note_text` and structural nodes
- Target maps don't reference transcript content
- Markers within note-content still work correctly
- Chat file analysis also strips transcript

---

## Step 12: Integrate Transcript Writer into Tool Loop

**File:** `src/aunic/loop/runner.py`

Add a transcript writing helper on `_LoopRuntime`:

```python
async def write_transcript_row(self, role, type, tool_name, tool_id, content):
    snapshot = await self.file_manager.read_snapshot(self.active_file)
    updated_text, row_num = append_transcript_row(
        snapshot.raw_text, role, type, tool_name, tool_id, content
    )
    await self.file_manager.write_text(
        self.active_file, updated_text, expected_revision=snapshot.revision_id
    )
```

Insert calls at the right points in the `ToolLoop.run()` method:

1. **After tool call parsed successfully** (before execution, around line 295):
   ```python
   await runtime.write_transcript_row("assistant", "tool_call", tool_call.name, tool_call.id, tool_call.arguments)
   ```

2. **After tool execution completes** (around line 410):
   ```python
   await runtime.write_transcript_row("tool", "tool_result", tool_call.name, tool_call.id, result_content)
   ```

3. **On tool error** (in error handling paths):
   ```python
   await runtime.write_transcript_row("tool", "tool_error", tool_call.name, tool_call.id, error_message)
   ```

The existing in-memory `transcript: list[Message]` continues to be maintained in parallel. The run loop uses it for provider requests. The file gets transcript rows as a durable log. Later (in the "Run loop" update group), the in-memory list will be replaced with `list[TranscriptRow]` and the provider request will use `transcript_messages`.

Also replace the `_LoopRuntime._append_search_history()` call (which writes to `# Search Results`) with transcript row writes for search tool_call/tool_result.

**Dependencies:** Steps 3, 11  
**Verify:**
- Run a note-mode loop with mock provider returning tool_calls; verify transcript rows appear in file
- Note-content portion unchanged after transcript writes
- Row numbers auto-increment correctly
- Search results appear as transcript rows, not in `# Search Results`

---

## Step 13: Integrate Transcript Writer into Chat Mode

**File:** `src/aunic/modes/chat.py`

Replace the blockquote persistence:

1. **User prompt** (around line 98): Replace `append_chat_prompt_transcript()` with:
   ```python
   updated_text, _ = append_transcript_row(snapshot.raw_text, "user", "message", None, None, prompt)
   ```

2. **Assistant response** (around line 475): Replace `append_chat_response_transcript()` with:
   ```python
   updated_text, _ = append_transcript_row(snapshot.raw_text, "assistant", "message", None, None, response.text)
   ```

3. **Research tool calls/results** (around line 351-363): Write transcript rows instead of the in-memory-only `Message(role="tool", ...)`:
   ```python
   await write_transcript_row("assistant", "tool_call", tool_call.name, tool_call.id, tool_call.arguments)
   await write_transcript_row("tool", "tool_result", tool_call.name, tool_call.id, result_content)
   ```

4. **Search history** (around line 584/664): Replace `_append_search_history()` / `append_search_history_batch()` with transcript rows for both tool_call and tool_result.

Remove calls to `format_chat_prompt_blockquote()`, `append_chat_prompt_transcript()`, `append_chat_response_transcript()`, `_append_chat_entry()`, `insert_before_managed_sections()` for chat entries. Keep the functions temporarily marked as deprecated.

**Dependencies:** Steps 3, 12  
**Verify:**
- Chat mode test: send prompt, verify transcript rows appear (no blockquotes, no `***`)
- Search results appear as transcript rows, not `# Search Results` section
- File without transcript section: transcript initialized on first message
- Context engine still works after chat messages are in transcript format

---

## Step 14: Remove Managed-Section Persistence

**Files:**
- `src/aunic/research/history.py`
- `src/aunic/modes/chat.py`
- `src/aunic/context/structure.py`

### In `history.py`
- Mark or remove `append_search_history_batch()`, `insert_before_managed_sections()`, `_managed_suffix_start()`, `_append_transcript_to_body()`. These should no longer be called from active code paths after Steps 12-13.
- Keep `find_top_level_section()` as a utility -- it may still be useful.

### In `chat.py`
- Remove `format_chat_prompt_blockquote()`, `append_chat_prompt_transcript()`, `append_chat_response_transcript()`, `_append_chat_entry()`.
- Remove the `insert_before_managed_sections` import if no longer used.

### In `structure.py`
- Update `_merge_chat_threads()` to recognize that the old `***` + blockquote pattern is legacy. New files will have transcript tables instead of chat threads. Old files may still have them (read-only backward compat).

**Dependencies:** Steps 12, 13  
**Verify:**
- Full integration: note-mode and chat-mode produce only transcript-based persistence
- No `***` separators in new output files
- No `# Search Results` section created in new runs
- Legacy files with `# Search Results` or `***` blockquotes still parse without error

---

## Dependency Graph

```
Step 1 (domain types)
  |
  +---> Step 2 (parser) ---------> Step 11 (context engine strip)
  |       |
  |       +---> Step 3 (writer) --> Step 4 (deletion)
  |       |       |
  |       |       +---> Step 12 (tool loop integration)
  |       |       |       |
  |       |       +---> Step 13 (chat integration)
  |       |               |
  |       |               +---> Step 14 (cleanup)
  |       |
  +---> Step 5 (translation pipeline)
  |       |
  |       +---> Step 7 (envelope builders)
  |               |
  |               +---> Step 8  (llama_cpp)
  |               +---> Step 9  (claude)
  |               +---> Step 10 (codex)
  |
  +---> Step 6 (ProviderRequest widening)
          |
          +---> Steps 8, 9, 10 (providers)
```

**Parallelism opportunities:**
- After Step 1: Steps 2 and 6 can proceed in parallel
- After Step 2: Steps 3 and 5 can proceed in parallel
- After Step 3: Step 4 is independent from Steps 5-10
- Steps 8, 9, 10 are independent of each other
- Step 11 can proceed as soon as Step 2 is done (parallel with everything else)
- Steps 12, 13 require both tracks (parser/writer + providers) but for the initial file-writing integration, they only need Steps 3 and 11

---

## Key Files to Modify/Create

| File | Action |
|------|--------|
| `src/aunic/domain.py` | Add `TranscriptRow`, content blocks, `MessageType`, widen `ProviderRequest` |
| `src/aunic/transcript/__init__.py` | New package |
| `src/aunic/transcript/parser.py` | New: parse markdown table |
| `src/aunic/transcript/writer.py` | New: append/delete/repair rows |
| `src/aunic/transcript/translation.py` | New: 3-step parse/group/translate pipeline |
| `src/aunic/providers/envelope.py` | Add structured message builders |
| `src/aunic/providers/llama_cpp.py` | Dual-path: old or structured |
| `src/aunic/providers/claude.py` | Dual-path: old or structured |
| `src/aunic/providers/claude_client.py` | May need structured input support |
| `src/aunic/providers/codex.py` | Dual-path: old or structured |
| `src/aunic/context/markers.py` | Strip transcript before analysis |
| `src/aunic/context/structure.py` | Exclude transcript from structural nodes |
| `src/aunic/context/engine.py` | Optional `transcript_text` in result |
| `src/aunic/loop/runner.py` | Write transcript rows during tool loop |
| `src/aunic/modes/chat.py` | Replace blockquote persistence with transcript rows |
| `src/aunic/research/history.py` | Deprecate/remove managed-section persistence |

---

## Testing Strategy

### Unit tests (per step)
- **Steps 1-4:** `tests/test_transcript_parser.py`, `tests/test_transcript_writer.py` -- pure function tests, no I/O mocking needed
- **Step 5:** `tests/test_transcript_translation.py` -- validate against the full walkthrough from `transcript-to-api.md`
- **Steps 6-7:** `tests/test_envelope.py` (extend existing) -- verify structured builders produce correct format
- **Steps 8-10:** Extend existing `tests/test_llama_provider.py` etc. -- verify dual-path behavior

### Integration tests
- **Step 11:** `tests/test_context_markers.py` (extend) -- file with transcript section
- **Steps 12-14:** Integration test with mock provider: run chat and note modes, verify transcript rows in output file, no legacy artifacts

### Round-trip test
Parse a table, write additional rows, parse again, verify consistency. This catches serialization/deserialization mismatches.

---

## Architectural Decisions

1. **Dual-path migration via optional `transcript_messages`** -- zero breakage for existing callers
2. **Transcript as append-only file I/O** -- uses existing `FileManager.write_text` with optimistic locking
3. **Translation functions are pure and stateless** -- no I/O, trivially testable
4. **Context engine strips transcript upfront** -- simpler than teaching every parser about table syntax
5. **Row renumbering on deletion** -- `tool_id` is the stable identifier, not row number
6. **Content is always JSON** -- parser always `json.loads()`, writer always `json.dumps()`
7. **In-memory transcript parallels file transcript during migration** -- both are maintained in Steps 12-13; the in-memory list switches to `TranscriptRow` in the later "Run loop" update group
