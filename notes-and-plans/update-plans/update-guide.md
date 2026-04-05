# Implementation Guidance

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

## Note on terminology
you will notice many words in notes-and-plans wrapped in backticks. These are "key words" that usually refer to something Aunic specific. If you don't know what they mean, they will be defined clearly in a markdown note somewhere in notes-and-plans

---

# Updates

## Current baseline in Aunic
These are already present in the codebase today and should be preserved or consciously replaced while doing the updates below.
- **Context engine baseline**: Aunic builds `parsed_note_text`, target/read-only maps, marker-aware structural nodes, transcript rows, and an explicit split between `note-content` and `transcript`.
- **Current note-mode orchestration**: `NoteModeRunner` + `ToolLoop` run direct-only note prompts with transcript-first history, natural-stop completion, work/read/off tool gating, and a synthesis pass after successful outside-note work.
- **Current chat-mode flow**: chat-mode stores prompt/response history in transcript rows, and `@web` search/fetch writes synthetic transcript tool rows instead of note-body history sections.
- **Current TUI baseline**: the TUI has note/chat toggle, work/read/off toggle, transcript rendering, indicator updates, file watching, and an `@web` search/fetch flow.

## Features to update

### Phase 1: Foundation - transcript and provider message model
These must come first; everything else depends on them.
- [x] **Richer message data types**: replace the simple `Message(role, content, name)` dataclass with types that carry `type`, `tool_name`, `tool_id`, and structured content blocks. This needs to cover at least text, tool_call, tool_result, tool_error, and any in-memory-only thinking/tool-use metadata needed for provider round-tripping.
- [x] **Transcript table parser**: parse the markdown table from the active note into row objects (Step 1 of `transcript-to-api.md`). Handle JSON-encoded content cells, the 6th-delimiter parsing rule, and missing/empty tables.
- [x] **Transcript table writer**: append rows to the markdown table on disk. Read file → find last table row → append → write back. Auto-increment `#` column. Handle transcript initialization (`---\n# Transcript` + header row on first write) and transcript repair (damaged delimiter/header).
- [x] **Transcript row deletion**: delete rows from the markdown table by `tool_id` (cascading: tool_result/tool_error deletion also removes matching tool_call) or by row number (for chat messages).
- [x] **Provider envelope/builders overhaul**: update `ProviderRequest`, provider envelope builders, and provider adapters so Aunic can send translated transcript messages instead of flattening everything to string-only pseudo-conversations. This is required for Anthropic/OpenAI-compatible tool history to work correctly.
- [x] **Replace current managed-section persistence**: remove the current `***` chat transcript + `# Search Results` / inline fetch insertion persistence model once transcript rows are in place. There should be one durable history system, not two.

### Phase 2: Context assembly + API translation
Depends on the transcript parser and richer provider message model.
- [x] **Transcript-to-API translation pipeline**: implement the 3-step process from `transcript-to-api.md` — parse rows → group consecutive assistant rows → translate per-provider (Anthropic content blocks, OpenAI tool_calls array).
- [x] **Combined note-snapshot + user-prompt**: build the final `role: "user"` message combining note-snapshot and user-prompt with a delimiter, appended after the translated transcript messages.
- [x] **Split `note-content` from `transcript` in context building**: the context engine currently treats the active note as one parsed document plus special managed sections. It needs to understand `note-content` vs `transcript` explicitly so target maps, read-only maps, and `/prompt-from-note` operate on note-content only.
- [x] **Provider-facing result flattening rules**: transcript rows may persist structured JSON locally, but provider-facing tool results sometimes need flatter text. Add an explicit translation layer for that rather than baking provider compromises into stored transcript content.

### Phase 3: Run loop
Depends on parser, writer, and translation.
- [x] **In-memory message list (run-log)**: rework the run loop to start each run by translating the transcript + building the combined user message, then grow the in-memory list turn-by-turn. Persistent tool rows written to both in-memory list and transcript table; ephemeral tools (note-edit, note-write) and thinking blocks in-memory only.
- [x] **Row write timing**: write `tool_call` rows to the transcript immediately when the API response arrives (before execution). Write `tool_result` rows when execution completes. Write user `message` rows on send, assistant `message` rows on API response.
- [x] **Run completion via natural stop signal**: remove the `finish` tool. The run ends when the model returns an assistant message with no tool calls. Handle tool call limit by stopping the loop and displaying a notification.
- [x] **Replace chat blockquote format**: the current `chat.py` appends messages as markdown blockquotes. Replace with transcript table rows.
- [x] **Replace note-loop pseudo-transcript strings**: the current note loop feeds the model synthetic assistant/tool text summaries. Replace that with real translated transcript/run-log messages built from the richer message model.

### Phase 4: Tool updates
Can proceed in parallel with run loop work.
- [x] **Simplify `web_fetch`**: replace the `desired_info`/`source_ids`/`urls` interface with a single `url` parameter. Return full page markdown to the in-memory list, compact summary `{"url","title","snippet"}` to the transcript. Remove the chunk-and-score pipeline from the model fetch path.
- [x] **Filesystem page cache**: implement XDG cache at `~/.cache/aunic/fetch/<note-path-hash>/` with per-entry `.md` + `.meta.json` files, `manifest.json` index, 3MB per-note cap, and LRU eviction. Replace the in-memory `_page_cache` dict.
- [x] **Update `web_search`**: adopt simpler deterministic ranking (engine count → rank → date → title) per updated `search-tool.md`. Remove embedding-based reranking. Add progress reporting to indicator-area.
- [x] **`note-write` tool**: implement per `note-write-tool.md` — whole-note replacement of `note-content`, working copy model, conflict handling against the live note, ephemeral (in-memory only).
- [x] **`edit` tool (work-mode)**: implement per `edit-tool.md` — `file_path`/`old_string`/`new_string`/`replace_all` parameters, note-scope rejection, persistent transcript recording.
- [x] **`note-edit` tool**: update per `note-edit-tool.md` — `old_string`/`new_string`/`replace_all` parameters, working copy model, ephemeral (in-memory only), conflict resolution against live note.
- [x] **Remaining work-mode tools**: implement `write`, `bash`, `read`, `grep`, `glob`, and `list` per their notes. The current outline was missing these entirely, but work/read/off mode cannot be considered implemented without them.
- [x] **Runtime note-scope object**: add the `active_markdown_note` / protected note-scope runtime object described in `active-markdown-note.md` so work-mode tools can reject edits/writes that target note-content.
- [x] **Structured `tool_error` handling**: transcript parsing, translation, rendering, and tool execution all need an explicit `tool_error` path, not just success-only `tool_result`s.


### Phase 5: Transcript rendering
Depends on parser and writer being functional.
- [x] **Chat message rendering**: user messages right-aligned, assistant messages left-aligned, 67%/33% column split per `active-markdown-note.md`.
- [x] **Agentic tool result rendering**: 2-column rows (tool_name + result content), tool_call rows hidden, only tool_result shown.
- [x] **Bash tool rendering**: collapsed/expanded toggle — command in collapsed row, output on expand, capped at ~20-30 lines, red for errors.
- [x] **Search result rendering**: collapsible dropdown — query + result count collapsed, individual results with title/snippet/link on expand.
- [x] **Fetch result rendering**: single row with page title, snippet, and link. Blue underlined title when cached page available.
- [x] **Transcript filters and ordering**: `[ Chat ]`, `[ Tools ]`, `[ Search ]` filter buttons and `[ Descending ]`/`[ Ascending ]` toggle.
- [x] **Row deletion UI**: "X" button on each rendered row, cascading delete for tool entries.
- [x] **Separate transcript UI from raw markdown editor**: the current TUI still shows the raw file body with folds. The new transcript rendering needs its own human-readable view instead of exposing the markdown table directly in the editor.

### Phase 6: Commands and integration
Depends on transcript writer and rendering.
- [x] **@web synthetic transcript rows**: when user completes a search/fetch via `@web`, write tool_call + tool_result row pairs with synthetic `user_` prefixed tool IDs.
- [x] **@web chunking (user flow only)**: chunk-and-score pipeline now exclusive to the `@web` user flow, not the model's `web_fetch` tool. Chunks sorted by score for user selection.
- [x] **Migrate `@web` persistence away from note-body insertions/search-history sections**: the current controller writes search history to `# Search Results` and inserts fetched chunks straight into note-content. After the transcript rewrite, search/fetch history must live in transcript rows, with note insertion only for explicitly selected user content.
- [x] **Remove `/prompt-from-note` and `/plan`**: these should both be removed.

### Phase 7: Modes and orchestration
Depends on run loop, tools, and rendering all working.
- [x] **Mode system**: complete the existing partial mode system. Note/chat toggle already exists; work/read/off is still a placeholder and needs real backend tool gating plus UI/state wiring per `modes.md`.
- [x] **Note-mode synthesis pass**: after a run in note-mode where work/read-mode tools were used, force a final pass where the model is given the `note-snapshot` + `latest-run-log` and asked to update the note-content with `note-edit`/`note-write`.

### Phase 8: Tests + docs cleanup
These are part of the work, not afterthoughts.
- [x] **Rewrite stale tests**: current tests still assume old blockquote chat persistence, `finish`, old `web_fetch` arguments, and note-edit parser helpers that no longer exist in code. Update the test suite to match the new transcript architecture.
- [x] **Clean up stale note references**: several notes still mention `finish` as the completion boundary, while the updated design removes it. Align `modes.md`, tool docs, and any prompts/examples with the natural-stop design.
- [x] **Resolve notes-and-plans naming drift**: there are small but real doc inconsistencies such as the `grob-tool/` directory holding the `glob` tool notes. Fix these while touching the plan so the docs are trustworthy during implementation.
- [x] **Clean up dead code**: take a pass verifying that no deprecated code remains.
