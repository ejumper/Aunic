# Implementation Guidance

## Status
This phase is implemented. Current Aunic ships the simplified `web_fetch(url)` path, filesystem-backed fetch cache, work/read/off tool matrix, and structured `tool_error` handling described in this plan.

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

# Phase 4: Tool Runtime and Tool Updates
## Summary

Implement phase 4 as the backend-ready tool/runtime layer that phases 5 through 7 build on.
Follow notes-and-plans first, but mirror the example app as closely as possible for registry composition, read-state, exact-string edit/write behavior, ripgrep-backed discovery tools, fresh-shell bash, and permission evaluation.
Rework only the parts where Aunic is intentionally different: markdown-table transcript persistence, ephemeral note tools, split note-content vs transcript, protected active-note scope, and note working-copy conflict handling.
Deliver end-to-end execution now, including a minimal permission prompt for ask decisions, but leave the polished work/read/off mode UX and note-mode synthesis pass to phase 7.
Interface and Runtime Changes
Add work_mode: Literal["off","read","work"] to NoteModeRunRequest, ChatModeRunRequest, and LoopRunRequest. Current product flows can still default this to "off" until phase 7 exposes the full selector.
Introduce app-session ToolSessionState for permission grants, read-state, shell cwd/env snapshot, background tasks, doom-loop tracking, and shared tool policy.
Introduce per-run RunToolContext for active_markdown_note, progress sink, current working note copy, baseline note snapshot, research state, and transcript/file helpers.
Replace the current string-only ToolExecutionResult with a structured contract containing status, in_memory_content, transcript_content, tool_failure, and metadata. transcript_content=None should mean “persist the same payload the model saw.”
Expand tool failure/error payloads so every tool can emit stable category, reason, and message values. The same structured source object should drive transcript tool_error rows, run metrics, and UI messaging.
Add an active_markdown_note runtime object per active-markdown-note.md, including normalized absolute note path and note_scope_paths, and use it everywhere write-capable work tools need to reject note-content mutations.
Compose tool registries by the full mode matrix: note+off = note tools + research; note+read = note tools + research + read tools; note+work = note tools + research + read tools + mutating work tools; chat+off = research; chat+read = research + read tools; chat+work = research + read tools + mutating work tools.
Update note/chat system prompts to steer strongly toward dedicated tools over bash, and inject protected note path(s) into prompts for work-mode safety.
Add config settings for fetch cache size and truncation, read byte/token/PDF limits, max editable file size, bash timeout default/max, and permission behavior. Remove research depth/chunk-scoring settings that phase 4 makes obsolete.
Add Python-side dependencies needed to mirror the example app behavior: pypdf, Pillow, nbformat, and bashlex or an equivalent Bash AST parser. Remove spacy and the embedding/reranking path if nothing else needs them after the research rewrite.
Implementation Changes
Build a lightweight permission broker now, not later. It should evaluate allow/ask/deny, support session-scoped “once” and “always” decisions, and expose a minimal controller/TUI prompt path for ask without waiting for the full phase 7 mode UX.
Mirror the example app’s file-tool architecture nearly directly for read, edit, write, grep, glob, list, and bash, then adapt persistence and note-scope behavior for Aunic.
Rewrite web_search to keep queries but enforce max-items 1, remove dead purpose/depth behavior, remove embedding reranking, rank by engine count then best rank then date then stable title, emit indicator-area progress through the existing progress sink, and persist compact [{"url","title","snippet"}] transcript payloads.
Rewrite web_fetch to accept only url, remove the chunk-and-score model-fetch path, fetch with httpx, convert via trafilatura or plain text fallback, and return split persistence: full markdown to the in-memory run log and compact {"url","title","snippet"} JSON to the transcript.
Replace the in-memory fetch cache dict with XDG filesystem cache at ~/.cache/aunic/fetch/<note-path-hash>/, storing .md, .meta.json, and manifest.json, tracking redirect aliases, and enforcing 3 MB per-note LRU eviction. A same-run hot entry in memory is fine, but disk becomes the source of truth.
Implement note-edit as the note-mode sibling of the example app’s file edit flow: exact-string old_string/new_string/replace_all, no empty old_string, no-op rejection, multiple-match validation, quote-style normalization when helpful, newline-aware deletion behavior, working-copy-first application, structured patch result, and structured tool_error on unresolved live-note divergence.
Implement note-write as whole-note replacement of note-content, using working copy first, allowing same-content writes, generating structured patch results, and returning structured tool_error on unresolved live-note divergence.
Keep both note tools ephemeral. Their tool_call and tool_result/tool_error rows belong only in the in-memory run log, never the persisted transcript.
Implement read with structured result types for text, notebook, PDF page extraction, image metadata, and file_unchanged dedup. Update session read-state on every successful read so later edit and write can enforce read-before-mutate and stale-read rules.
Make read support text files, notebooks, images, and PDFs per the notes. Text and notebook reads should persist bounded structured content; PDF reads should support page ranges and fail helpfully when unsupported or too large; image reads should persist replay-safe metadata and let provider translation use a richer in-run payload only when the active provider path supports it.
Implement edit(file_path, old_string, new_string, replace_all=false) using the example app’s exact-string replacement model, including read-before-edit, stale-read detection, multiple-match validation, missing-file create when old_string == "", structured patch results, and hard note-scope rejection.
Implement write(file_path, content) using the example app’s full-file write model, including create vs overwrite detection, read-before-overwrite on existing files, second stale-read check before write, same-content writes allowed, structured patch results, and hard note-scope rejection.
Implement grep, glob, and list as persistent work/read tools with the note-specified ordering, truncation, ignore behavior, and output shapes. Use rg first for grep and glob, filesystem walk for list, and documented fallback behavior when rg is unavailable.
Implement bash as fresh-shell-per-call execution with reconstructed session state, AST-aware validation, read-only fast path, sandbox-aware execution, background task bookkeeping, protected note-scope mutation rejection, and bounded stdout/stderr transcript payloads.
Update runners so persistent tools write tool_call plus tool_result or tool_error rows to transcript, while note tools only affect the in-memory run log. The runner must use transcript_content for persistence and in_memory_content for the active provider conversation.
Extend transcript flattening and provider translation so all new structured tool payloads remain JSON in storage but become concise provider-friendly text or richer in-run blocks at send time. tool_error should be handled everywhere as a first-class path, never as a special-case string fallback.
Keep legacy @web note-body/search-history behavior intact for now. Phase 4 should only make the new research services reusable by the future phase 6 migration.
Test Plan
Verify the full tool matrix across note/chat x off/read/work, including protected-note prompt injection and correct registry exposure.
Verify permission outcomes for allow, once, always, reject, external_directory, and doom_loop, including minimal controller/TUI prompt handling for ask.
Verify web_search deterministic merge/sort, progress events, compact transcript payloads, and total removal of embedding/reranking dependencies from the search path.
Verify web_fetch cache miss, cache hit, redirect aliasing, per-note isolation, manifest updates, 3 MB LRU eviction, split persistence, and fetch/conversion failures producing tool_error.
Verify note-edit and note-write success paths, no-op rejection, replace-all behavior, deletion behavior, quote normalization, same-content note-write acceptance, live-note divergence handling, and ephemeral-only persistence.
Verify read text ranges, byte/token caps, file_unchanged dedup, notebook reads, image reads, PDF page reads, device-file rejection, directory rejection, and helpful not-found errors.
Verify edit and write read-before-mutate, stale-read detection, missing-file creation rules, empty-old_string rules, note-scope rejection, and structured patch results.
Verify grep, glob, list, and bash ordering, truncation, fallback behavior, cwd/env persistence, background tasks, timeout handling, and execution failures.
Verify parser/writer/translation round-trips for tool_result and tool_error, including split-persistence tools that store compact transcript content while the model sees richer in-run content.
Verify regression behavior for current note/chat runs with work_mode="off" and current @web flows until phase 6 replaces their persistence path.
Assumptions and Defaults
Phase 4 includes backend-complete tooling and a minimal permission prompt, but not the polished work/read/off selector UX.
Work/read tools are backend-available in both note mode and chat mode because modes.md defines two independent mode axes.
Note-mode synthesis of outside-note work remains phase 7 work and is not pulled forward into phase 4.
note-write is the whole-note replacement tool from note-write-tool.md; stale prose elsewhere about append/prepend is treated as outdated.
@web transcript migration, transcript rendering changes, and row-deletion UI remain phase 5 and phase 6 work.
When the example app and Aunic notes differ, prefer the example app’s execution and validation model, but always preserve Aunic’s note/transcript architecture, note-scope protection, and ephemeral note-tool behavior.
