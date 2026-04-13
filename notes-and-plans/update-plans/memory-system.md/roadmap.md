# Memory System Roadmap

A high-level build plan covering every feature outlined in [memory-system.md](memory-system.md). This is a **plan for building plans** — each section below should contain enough context that a future "write a detailed implementation plan for feature X" prompt can be answered from that section alone, without re-deriving the thesis, the tool shape, or the prerequisites.

Implementation-level details (exact file edits, function signatures, test cases) live in per-feature plans written *from* this roadmap, not in this roadmap itself.

---

## Guiding principles (carry into every per-feature plan)

These come from [memory-system.md](memory-system.md) and [aunic-thesis.md](../../aunic-thesis.md). Every feature below must satisfy them.

1. **Pull, don't push.** Nothing auto-injects into model-context. The model calls a tool; the user invokes a command. The only always-on surface is the memory manifest, and the manifest describes tools — it does not contain memory content.
2. **The note is the memory.** No extraction step. Anything that summarizes, indexes, or mirrors note content must remain a regenerable *cache* — never a source of truth.
3. **In-distribution tool shapes.** Prefer shapes models are already trained on: read-a-markdown-index, grep-and-read-loops, search-then-fetch. Avoid opaque chunk retrieval.
4. **Transparency over cleverness.** Every memory read is visible in the transcript. Every piece of context the model sees is auditable by the user.

---

## What already exists in Aunic (don't reimplement)

Anchor points the memory system will build on. A per-feature plan should start by reading these.

- **Per-file `.aunic/` metadata directory.** Already used for conflict backups ([app.py:1534](../../../src/aunic/tui/app.py#L1534)). The same pattern extends naturally to per-note summaries, flags, and indexes.
- **Global `~/.aunic/` directory.** Already used for `tui_prefs.json` ([app.py:1721](../../../src/aunic/tui/app.py#L1721)). This is the right place for the global map and RAG config.
- **Transcript parsing.** [transcript/parser.py](../../../src/aunic/transcript/parser.py) already turns `# Transcript` tables into structured rows. `search_transcripts` consumes this directly — no new parser needed.
- **Tool registry.** [tools/base.py](../../../src/aunic/tools/base.py), [tools/runtime.py](../../../src/aunic/tools/runtime.py). New model tools plug in here; the existing tools ([bash.py](../../../src/aunic/tools/bash.py), [note_edit.py](../../../src/aunic/tools/note_edit.py), [research.py](../../../src/aunic/tools/research.py)) are templates for every model-facing tool on this roadmap.
- **System prompt builder.** [modes/chat.py:797](../../../src/aunic/modes/chat.py#L797) — `_build_chat_system_prompt` is the surface where the memory manifest gets spliced in. The "system-generated prefix" from memory-system.md is this function plus the structured note prefix built by [context/engine.py](../../../src/aunic/context/engine.py).
- **Slash command dispatch.** `send_prompt()` in [tui/controller.py](../../../src/aunic/tui/controller.py) — where `/map`, `/include`, `/exclude`, `/isolate`, `@rag`, `@<scope>` are all parsed and dispatched.
- **Prompt highlighting.** [tui/rendering.py](../../../src/aunic/tui/rendering.py#L17-L23) — `PROMPT_ACTIVE_COMMANDS` and `_PROMPT_COMMAND_RE` need every new slash/@ command registered here.
- **Research tool pattern.** [tools/research.py](../../../src/aunic/tools/research.py) + [research/search.py](../../../src/aunic/research/search.py) + [research/fetch.py](../../../src/aunic/research/fetch.py) — this is the closest existing analog to `rag_search`/`rag_fetch` and to the `@rag` user command. Copy the shape.
- **File detection.** Aunic notes are already detected implicitly by the TUI via the `.aunic/` sibling directory. `/map` and `grep_notes` will formalize a `is_aunic_note(path)` helper — see "Cross-cutting" below.

A per-feature plan should never propose new transcript parsing, new tool-registry plumbing, or new `.aunic/`-dir creation logic. All four exist.

---

## Phases at a glance

| Phase | Feature | Why this order |
|---|---|---|
| 1 | `search_transcripts` + memory manifest | Keystone, Aunic-unique, justifies the whole story. Manifest ships with it so the model actually reaches for it. |
| 2 | `grep_notes` | Boring, reliable, fills the "find notes mentioning X" gap. Cheap to add once `is_aunic_note` exists. |
| 3 | `/map` (user command) + `read_map` (model tool) | Only pays off once there are enough notes that grep is noisy. Deferred accordingly. |
| 4 | `@rag` + custom `@<scope>` user commands | Independent track. Can start any time after Phase 1; blocks Phase 5. |
| 5 | `rag_search` / `rag_fetch` model tool | Reuses Phase 4's server + config. Power-user, optional. |
| ⊕ | MCP client support | Parallel track, lands alongside Phase 5. Separate doc when written. |

Phases 1–3 are sequential because each makes the next more useful. Phases 4 and 5 are a separate track — Phase 4 can ship before Phase 3 if user demand goes that way. Phase 5 cannot ship before Phase 4.

---

## Phase 1 — `search_transcripts` + memory manifest

**Goal:** give the model a tool that exploits Aunic's one structural advantage (transcript tables with columns) and make sure it actually reaches for it.

**Why first:** this is the only tool on the list that no non-Aunic memory system could replicate. If only one thing gets built, this is it. Everything else on this roadmap is either a reimplementation of grep or a reimplementation of web search; only `search_transcripts` is unique.

**What it does:** structured query across the parsed `# Transcript` tables of every Aunic note on the system. Filterable by `tool=`, `query=` (substring over args/results), `since=` / `until=`, `scope=<path subtree>`. Returns rows as *"in `<path>`, on `<date>`, you ran `<tool>` with `<args>` → `<result-snippet>`."*

**Input/output shape:**
```
search_transcripts(
    query: str | None = None,
    tool: str | None = None,
    since: str | None = None,
    until: str | None = None,
    scope: str | None = None,
    limit: int = 20,
) -> list[{path, timestamp, tool, args_snippet, result_snippet}]
```

**Key design questions a plan must resolve:**
- **Where does the set of notes to search come from?** Two options: (a) walk the filesystem each call (simple, slow on large trees); (b) use the `/map` index (fast, but creates a Phase 3 prerequisite). **Recommendation:** start with a cached filesystem walk scoped to `~` by default, skipping dotdirs and common noise dirs. Swap to reading `/map` once Phase 3 lands. The tool's external shape doesn't change.
- **Result overflow.** 50+ matching rows is plausible. Plan should pick between pagination, forced query narrowing, and grouped summarization. memory-system.md leans toward pagination + a "narrow with `query=` / `since=`" hint. Avoid grouping — it's extraction, which is the thing we're not doing.
- **Per-row snippet size.** Tool-call args can be huge. Plan should pick a truncation length and decide whether to add a follow-up `read_transcript_row` tool or let the model open the full file with its existing `read_file` tool. memory-system.md leans toward the latter (fewer tools, in-distribution).
- **Aunic-note detection.** See "Cross-cutting" — this feature forces the `is_aunic_note` helper to be built. Put it in [context/file_manager.py](../../../src/aunic/context/file_manager.py) or a new `aunic/discovery.py` module.

**Memory manifest (ships in the same phase):**
- A short always-on block in the system-generated prefix built by [modes/chat.py:797](../../../src/aunic/modes/chat.py#L797).
- Lists each memory tool with a one-line "when to reach for this" hint.
- The manifest itself is the only always-on part; memory stays pull-based.
- Manifest content is a plain string (or a small builder that splices in tool names + descriptions dynamically as tools are added/removed).
- The user must be able to audit and edit it. Options: literal string in source, or a template file under `~/.aunic/`. Plan should pick; memory-system.md doesn't mandate either.

**Prerequisites:** `is_aunic_note` helper. Everything else already exists.

**Touches:**
- New: `src/aunic/tools/search_transcripts.py`, `src/aunic/aunic/discovery.py` (or similar) for `is_aunic_note` + filesystem walk.
- Edit: [tools/runtime.py](../../../src/aunic/tools/runtime.py) — register the tool.
- Edit: [modes/chat.py:797](../../../src/aunic/modes/chat.py#L797) — splice in the memory manifest.
- Tests: new `tests/test_search_transcripts.py` covering filter combinations, pagination, snippet truncation, and the "no matches" path.

**Definition of done:** in a fresh session with a handful of real Aunic notes, the model spontaneously calls `search_transcripts` before proposing a destructive bash command (because the manifest told it to), and the result contains rows from prior sessions. The user sees the tool call in the transcript.

---

## Phase 2 — `grep_notes`

**Goal:** give the model a scoped ripgrep that only searches Aunic notes, with an optional `section=` filter for searching only `note-content` or only `transcript`.

**Why second:** grep-over-codebase is the most-used agent tool in training — the model reaches for it naturally. The `section` filter is the Aunic-specific value-add that grep-over-everything can't offer (grep every file in `~` and you'll drown in source code; restrict to notes and split by half-of-file and it becomes precise).

**What it does:** wrap ripgrep. Limit the file set to `is_aunic_note(path)`. Optionally split each note into its note-content half and its transcript half and grep only the requested half. Return `file:line` + surrounding context — exactly the shape the model expects from grep.

**Input/output shape:**
```
grep_notes(
    pattern: str,
    section: "note-content" | "transcript" | "all" = "all",
    scope: str | None = None,
    case_sensitive: bool = False,
    context: int = 2,
) -> list[{path, line, match, context_before, context_after}]
```

**Key design questions a plan must resolve:**
- **Splitting note-content from transcript for the `section` filter.** The parser in [context/markers.py](../../../src/aunic/context/markers.py) / [transcript/parser.py](../../../src/aunic/transcript/parser.py) already knows where the split lives. The plan should decide whether to (a) pre-split each file to a temp buffer and grep that, or (b) grep the whole file then filter matches by byte offset against the known split point. (b) is faster and avoids writing temp files.
- **Ripgrep dependency.** Does Aunic already ship with `rg` or shell out to the user's `rg`? Check, and fall back to pure-python `re` if `rg` isn't guaranteed. The plan should pick.
- **Result shape when a match spans a `section` boundary.** Should not happen in practice, but define the tie-break anyway.

**Prerequisites:** `is_aunic_note` helper (built in Phase 1).

**Touches:**
- New: `src/aunic/tools/grep_notes.py`.
- Edit: tool registry, memory manifest (add a line for `grep_notes`).
- Tests: new `tests/test_grep_notes.py` covering section filtering, scope filtering, case sensitivity, and the "no matches" path.

**Definition of done:** `grep_notes(pattern="docker compose", section="transcript")` returns only executed-command hits from past runs, with zero noise from note prose.

---

## Phase 3 — `/map` (user command) + `read_map` (model tool)

**Goal:** build an always-up-to-date index of Aunic notes with short snippets, and expose it to the model as a file-to-read rather than a query-to-run.

**Why third:** only pays off once there are enough notes that grep is too noisy to be the first move. Before that, `search_transcripts` + `grep_notes` cover the need. Building this too early wastes effort on a structure that won't be load-bearing.

**What it does:**

- **`/map <scope>`** — user-facing command. Walks the filesystem (scope defaults to `~`), finds Aunic notes via `is_aunic_note`, writes a markdown file tree to `~/.aunic/map.md` + a local `<cwd>/.aunic/map.md`. Each entry has the path + a short snippet.
- **Snippet source priority:** user-provided custom > model-generated > first N characters of note-content. Custom and model-generated summaries set a no-auto-update flag in the note's `.aunic/` metadata so the automatic first-N-chars refresh never overwrites them.
- **Commands:** `/map` (walk home), `/map <path>` (walk subtree), `/map --set-summary <text>`, `/map --generate-summary` (explicit LLM call on current file), `/map --clear-summary` (clears the flag, reverts to auto).
- **Automatic refresh.** First-N-chars snippets update on note save/open/close, cheaply, for any note without the no-auto-update flag.
- **`read_map(scope=None)`** — model tool. Reads `~/.aunic/map.md` (or a scoped subtree) and returns it as-is. No query, no filtering, no cleverness. The model picks paths it wants and calls its existing `read_file` tool. Scope argument exists only to avoid loading a huge map when the model only cares about a subtree — **not** for "intelligent scoping."

**Key design questions a plan must resolve:**
- **Map file format.** memory-system.md leans markdown file-tree. Plan should confirm and pin a concrete format (indented list? heading-per-directory? table?). The format needs to be (a) readable by the model in-distribution, (b) parseable enough to update individual entries cheaply.
- **Cheap incremental update.** On re-map, skip files whose mtime/hash is unchanged since the last map entry. Store the hash/mtime alongside the snippet in map.md itself (inline HTML comment) or in a parallel `.aunic/map-index.json`. Plan should pick — the constraint is that `rm -rf ~/.aunic && /map` must reproduce the same map content.
- **Where the no-auto-update flag lives.** Per-note `.aunic/meta.json` or similar. Plan should define the exact per-note metadata schema (this is the first feature that needs one; see "Cross-cutting").
- **Snippet length N.** Pick a number. 200 chars? First heading + first paragraph? Plan should pick.
- **What `/map --generate-summary` actually does.** An extra LLM call on the current file, with a fixed prompt, writing the result to the per-note metadata. Decide which provider/model to use — probably the current session's model — and whether it counts toward the session's usage log.
- **`read_map` scoping syntax.** Pass an absolute path? A glob? A symbolic scope name? Plan should pick the simplest one that works.

**Prerequisites:** `is_aunic_note` (Phase 1). Per-note metadata schema (first introduced here).

**Touches:**
- New: `src/aunic/tools/read_map.py`, `src/aunic/map/` (or similar) for the `/map` command implementation.
- New: per-note metadata schema — define once, reuse everywhere.
- Edit: controller slash dispatch, rendering prompt-command regex, memory manifest.
- Edit: note save/open/close hooks to trigger the cheap auto-update.
- Edit: `search_transcripts` (`src/aunic/tools/search_transcripts.py`) and `grep_notes` (`src/aunic/tools/grep_notes.py`) — replace the `walk_aunic_notes` call at query time with a map read (loading the pre-built index instead of scanning the filesystem). Both tools should fall back to `walk_aunic_notes` when no map exists. The `walk_aunic_notes` function in `src/aunic/discovery.py` is retained as the backing primitive for the map builder and as the fallback path. `discovery.py` and the `is_aunic_note` cache should be revised or optimized as needed to make this integration work well (e.g. if the map-building pass benefits from a different walk strategy or the cache keying needs adjusting).
- Tests: map generation, map update-in-place, snippet priority, the no-auto-update flag, `read_map` output, `search_transcripts` + `grep_notes` behavior with and without a pre-built map.

**Definition of done:** `/map` produces a markdown file listing every Aunic note on the system; `read_map()` returns that file; a session's first model action after loading an unfamiliar note is often "read_map → pick a related file → read_file."

---

## Phase 4 — `@rag` + custom `@<scope>` user commands

**Goal:** give the user a `web_search`-style command for their own indexed corpora. Independent track from Phases 1–3 — can ship any time.

**Why this is a separate track:** Phases 1–3 are about Aunic-note memory. Phase 4 is about external-corpus retrieval. They share nothing structurally. Phase 4 is more similar to the existing `@web` / research tool ([research/search.py](../../../src/aunic/research/search.py)) than to anything in Phases 1–3.

**What it does:**

- User sends `@rag <query>` or `@<scope> <query>` (e.g. `@wiki`, `@docs`, `@python`).
- Aunic POSTs to the user-configured RAG server: `POST /search {scope, query}`.
- Results render in the web-search-view surface ([tui/web_search_view.py](../../../src/aunic/tui/web_search_view.py)) — reuse it, don't build a parallel UI.
- User selects chunks/documents to append to the note. `--synth` / `-s` sends the selected chunks through the LLM to synthesize into prose before appending, instead of raw.
- **Fallback when no RAG server is configured:** `@notes`-style commands fall back to fuzzy filesystem search over the configured directory, no embeddings needed. Users who don't care about embeddings still get something.

**Config shape (shared with Phase 5 — define here):**
```toml
[rag]
server = "http://localhost:5173"

[[rag.scope]]
name = "python"
description = "Python stdlib reference. Use for API signatures and stdlib behavior."
corpora = ["python_stdlib_3.12"]

[[rag.scope]]
name = "networking"
description = "RFCs, openconfig, ubuntu server docs."
corpora = ["rfcs", "openconfig", "ubuntu_server"]
```

Stored in `~/.aunic/rag.toml` (or merged into the existing `tui_prefs.json` — plan should pick). Scope descriptions are load-bearing for Phase 5; write them for the model even though Phase 4 only surfaces them to the user.

**Key design questions a plan must resolve:**
- **Server spec.** Define the exact request/response schema for `POST /search` and `POST /fetch`. Aunic does not ship a server — it ships a client + a spec. The spec is the contract that also governs Phase 5.
- **Where the `@<scope>` commands get registered.** Dynamic registration from config, or static list? The `PROMPT_ACTIVE_COMMANDS` frozenset in [rendering.py:17](../../../src/aunic/tui/rendering.py#L17) currently expects a static set. Plan should either (a) make it dynamic, or (b) add a generic `@` prefix highlighter that activates for any configured scope.
- **What `--synth` does.** One extra LLM call synthesizing selected chunks into note prose. Decide prompt shape and provider.
- **Fallback behavior.** Fuzzy-search over a configured directory when no server is set — pick a library (`fzf`-like, `rapidfuzz`, or pure-python), decide the UI when the user hasn't configured anything (error? silent noop? prompt to configure?).
- **Graceful degradation.** Server unreachable → show a clear error in the indicator area, don't crash. Indicator-area already exists ([tui/app.py](../../../src/aunic/tui/app.py)).

**Prerequisites:** none structural. Reuses the web search view and the research module's patterns.

**Touches:**
- New: `src/aunic/rag/` (or similar) — client, config loader, scope registry.
- New: `~/.aunic/rag.toml` config file.
- Edit: slash-command dispatch in the controller to handle `@rag` and dynamic `@<scope>`.
- Edit: prompt-highlighter in [rendering.py](../../../src/aunic/tui/rendering.py) to recognize dynamic scopes.
- Edit: reuse [web_search_view.py](../../../src/aunic/tui/web_search_view.py).
- Tests: config parsing, scope dispatch, fallback behavior, synth flag.

**Definition of done:** `@wiki BGP route reflection` returns a list of wikipedia-server results, user selects three, they get appended to the note with clear provenance.

---

## Phase 5 — `rag_search` / `rag_fetch` (model tool)

**Goal:** expose the same RAG server to the model via a `web_search` / `web_fetch`-shaped tool.

**Why after Phase 4:** the server, config, and spec all come from Phase 4. Phase 5 is a thin wrapper that registers a model tool pointing at the same endpoints. Building Phase 5 first would duplicate config work.

**What it does:**

```
rag_search(query: str, scope: str) -> list[{doc_id, title, snippet, score}]
rag_fetch(doc_id: str, section: str | None = None) -> str
```

Single tool with a `scope` parameter (not one tool per scope), so scope choice is explicit in every call and self-documenting in the transcript. Scopes come from the same config as Phase 4. Aunic splices each scope's user-written description into the tool description the model sees — that's what the model reads to decide which scope to call.

**Key design questions a plan must resolve:**
- **Fetch size.** Long docs (RFCs, long wikipedia articles) can blow context. Two mitigations from memory-system.md: (a) server pre-chunks by heading; `rag_fetch` with no section returns a TOC; `rag_fetch(section="9.2.1")` returns one section; (b) return first-N-chars + "more available" marker and let the model ask for the next range. Plan should pick, or do both. (a) is more in-distribution and respects document structure.
- **Tool description generation.** The tool description the model sees is generated from config at startup by splicing in `[[rag.scope]]` descriptions. Decide where this lives — probably alongside `_build_chat_system_prompt` in [modes/chat.py](../../../src/aunic/modes/chat.py) or in a `tools/rag.py` that builds its own spec.
- **Offline behavior.** If the server is unreachable, `rag_search` returns a clear tool error (not a crash) and the memory manifest notes "RAG tool configured but offline." Manifest needs a hook for runtime status; plan should decide how.
- **Per-session usage metering.** `rag_fetch` calls can return large documents. Decide whether these count against the context-size displays already in [context-size.md](../../context-size.md) / existing token accounting.

**Prerequisites:** Phase 4 (config, server spec, client library). MCP support is optional — Phase 5 doesn't require it — but the MCP track lands here.

**Touches:**
- New: `src/aunic/tools/rag.py` — registers `rag_search` / `rag_fetch` against the Phase 4 client.
- Edit: tool registry, memory manifest.
- Edit: chat system prompt builder to splice scope descriptions into the tool description.
- Tests: tool dispatch, scope routing, section fetch, offline error handling.

**Definition of done:** `rag_search(query="BGP route reflection", scope="networking")` returns snippets from the user's RFC corpus, `rag_fetch(doc_id=X)` returns a TOC, `rag_fetch(doc_id=X, section="9.2.1")` returns one section — all visible in the transcript.

---

## Parallel track — MCP client support

*note this should already be implemented, I just kept it here for my own reference, you can ignore it*

**Goal:** let Aunic connect to any MCP server and expose that server's tools to the model. Not strictly a memory-system feature, but lands alongside Phase 5 because the RAG server can optionally speak MCP.

**Why parallel, not blocking:** MCP is a transport/protocol layer, not a memory feature. It unlocks the broader ecosystem (filesystem servers, database servers, tool marketplaces) — RAG is just one use case.

**What it does:** Aunic acts as an MCP client. User configures MCP servers in config (probably `~/.aunic/mcp.toml`). On startup, Aunic connects, lists each server's tools, and registers them in the tool runtime so the model can call them exactly like native tools.

**Key design questions a plan must resolve (out of scope for this roadmap — see future `mcp-support.md`):**
- Which MCP features to support first (tools? resources? prompts?).
- How namespacing works when two servers expose tools with the same name.
- How tool descriptions from MCP servers are displayed in the memory manifest / tool list.
- Whether Aunic also acts as an MCP *server* (exposing its own tools to other clients). Probably out of scope.

**Relationship to Phase 5:** the user can run a RAG server that speaks HTTP (for `@rag`) and MCP (for `rag_search` / `rag_fetch`) from the same process. Aunic doesn't care which. HTTP is the baseline because `@rag` requires it; MCP is an additional path users can choose.

**Definition of done:** a user-configured MCP server's tools show up in `_build_chat_system_prompt`'s tool list and are callable by the model.

---

## Cross-cutting concerns

Things that span multiple phases. A per-feature plan should reference these rather than redefining them.

### Aunic-note detection (`is_aunic_note(path)`)

First needed in Phase 1. Should live in a shared module (e.g. `src/aunic/aunic/discovery.py` — name TBD). Detection rules from memory-system.md:
1. Sibling `.aunic/` directory exists for the file, OR
2. File contains `---\n# Transcript` (grep heuristic for the frontmatter-then-transcript-heading shape).

Rule 1 is cheap (single `exists()` call). Rule 2 requires reading file head. Plan should decide whether Rule 2 is always-on or only a fallback. Cache results by path+mtime — Phase 2 (`grep_notes`) will call this on thousands of files.

### Per-note metadata schema

First needed in Phase 3 (`/map`). Lives in `<note_parent>/.aunic/<note_stem>.json` (or similar — plan should pin the exact layout). Fields introduced so far:
- `summary: str | None` — user or model-written summary.
- `summary_locked: bool` — the no-auto-update flag. True iff `summary` was set explicitly.
- `last_indexed_hash: str` — for cheap `/map` incremental updates.

Schema should be open to extension without breaking old clients (use a `version` field, ignore unknown keys). Aunic already uses `.aunic/` for conflict backups — this coexists.

### Storage layout summary

```
~/.aunic/
├── tui_prefs.json          # existing
├── map.md                  # Phase 3 — global map
├── map-index.json          # Phase 3 — mtime/hash cache (or inline in map.md)
├── rag.toml                # Phase 4 — RAG server + scopes
└── mcp.toml                # Parallel track — MCP servers

<cwd>/.aunic/
└── map.md                  # Phase 3 — local-subtree map

<note_parent>/.aunic/
├── <note_stem>.json        # Phase 3 — per-note metadata (summary, flags, hash)
└── conflicts/              # existing
```

Everything under `~/.aunic/` except `tui_prefs.json` is a cache. Deleting it and rerunning the relevant command must reproduce identical output (modulo model-generated summaries, which are explicitly user-initiated).

### Memory manifest

Single always-on string spliced into `_build_chat_system_prompt` in [modes/chat.py:797](../../../src/aunic/modes/chat.py#L797). Content evolves as tools land:

- Phase 1 adds lines for `search_transcripts`.
- Phase 2 adds `grep_notes`.
- Phase 3 adds `read_map`.
- Phase 5 adds `rag_search` / `rag_fetch` (with scope list spliced from config).

Manifest builder should take the tool registry as input so tool names don't drift. Plan should decide whether the manifest lives as a literal string in source (simple, code-reviewable) or as a user-editable template in `~/.aunic/manifest.md` (user-auditable, but now there's another file to maintain). memory-system.md doesn't mandate either — default to the literal string, revisit if users complain.

### Tests

Every phase needs:
- Unit tests for the tool/command itself.
- An integration test that exercises it via the existing [tui/controller.py](../../../src/aunic/tui/controller.py) `send_prompt` path (for user commands) or via a tool-dispatch test (for model tools).
- No test should require a real RAG server or a real MCP server. Mock the HTTP boundary; ship a fake server in `tests/fixtures/`.

---

## Explicitly out of scope / rejected

From memory-system.md's "What not to build." A per-feature plan should **not** propose any of these. If a plan finds itself wanting one, the feature probably isn't fitting the thesis and should be rethought.

- **Model-side chunk-RAG with auto-injection.** Distinct from Phase 5 (which is search-then-fetch, never injected).
- **A model-maintained memory store.** Models drift and duplicate. The note is the store; memory updates are regular `note_edit`/`note_write` calls.
- **Subagents to summarize notes.** First N chars is already the summary for the auto path; explicit `--generate-summary` handles the rest.
- **Auto-injection of anything into model context.** No "potentially useful" sidebar, no "relevant past work" panel. Everything the model sees, either the user or the model put there with a visible action.

---

## How to use this roadmap

When it's time to build feature X:

1. Open the relevant phase section above.
2. Read its "Goal," "What it does," and "Key design questions" subsections.
3. Note which prerequisites are already built (earlier phases or pre-existing).
4. Read the referenced source files listed in "Touches" and "What already exists."
5. Write a per-feature implementation plan answering the design questions, proposing a file-level edit list, and listing test cases.
6. The per-feature plan is what actually gets executed.

This roadmap should not grow implementation details. When a design question gets answered by a per-feature plan, note the answer *back* in this roadmap in one sentence so the next plan starts with the answer already in hand.
