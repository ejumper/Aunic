*note: This is a rough draft. Nothing here is sacred or definite.*

# The Problem
Memory systems exist to solve a handful of real problems. Before designing anything, name them:

- **What the user doesn't know to include.** The model could make better-informed decisions if it knew relevant prior work existed. Example: the user wants to move a directory to another drive, and the model previously helped set up a container with volumes located there. The user may not remember this, but the model should — and should surface that moving the directory will break the container's volume, and offer to update it. Low stakes in this example; easy to imagine higher-stakes versions.
- **Repetition.** If the user has done something one way repeatedly, the model should know that is how they like it done. Example: the user always deploys new services as docker-compose files routed through nginx, with compose files in a specific directory. It's annoying to repeat yourself; it's also a safety concern. If the user has a specific security configuration, the model should remember it across projects even when the user fails to mention it. The more past projects the model can reference, the more likely it is to comply.

These examples point to memory's actual purpose: **building consistency outside the context window**. Widening the model's scope so it treats the device it's working on as a unified system, rather than resolving the same problems and rediscovering the same conventions on every project.

---

# Complying with Aunic's Thesis (aunic-thesis.md)

Aunic emphasizes **transparency**. The LLM should not be a magical black box that "does stuff" — it is a tool for helping users work faster, smarter, and expertly outside their domains of expertise. The LLM should make computers *more* transparent, not less.

Aunic emphasizes **working with the model, not the model working for the user**. The user should have access to the same tools as the model.

A consequence: every other memory system (ChatGPT memory, Claude Projects, mem0, Letta, MemGPT, MemPalace-style knowledge graphs) solves a problem Aunic doesn't have. They all extract "facts" from conversations into a side store because their primary artifact (a chat log) is garbage to re-read. Aunic's primary artifact is a well-written note. You don't need to extract facts from a note — you just re-read it. Extraction is a tax those systems pay that Aunic does not.

The useful reframing: **Aunic doesn't need a memory system. It needs a discovery system.** The real gap isn't "the model needs to remember things"; Aunic notes are already durable, transparent, portable artifacts. The real gap is:

> *"I have N Aunic notes scattered across my filesystem and neither I nor the model knows which are relevant to what I'm doing right now."*

That's a search/discovery problem, and it has a different shape than memory:
- Memory → extraction, summarization, auto-injection, opaque state.
- Discovery → indexing, listing, user-driven or model-pulled selection.

Discovery is thesis-aligned. Memory is thesis-hostile.

---

# Memory Approach

There will be no single "memory system." Aunic should break down the problems memory systems try to solve and attack them individually with a kit of tools. Some are user-facing; some are model-facing. None auto-inject anything into context — the model pulls, the user selects.

## Things that are genuinely unique to Aunic (and should be exploited)

1. **The note *is* the memory.** No extraction step. Summaries can be cheap because the source is always one file-read away.
2. **Transcripts are structured and per-file.** Every Aunic note already has an episodic log (the `# Transcript` table). Grep-able, filterable, dated. Traditional memory systems build this from scratch; Aunic has it for free.
3. **File paths encode user intent.** When the user drops a note into `projects/aunic/notes-and-plans/commands/`, the *path* already tells you what it's about. No topic inference needed.
4. **Edit-commands are the mental model.** The user already thinks "what's in my context right now?" Discovery features should extend that vocabulary, not introduce a parallel one.
5. **Per-file `.aunic/` metadata.** A place to stash derived per-note data (summaries, hashes, flags) without polluting the note itself.

---

# User Tools

The first layer of tools is accessible to the user directly.

## `@rag` — user-driven retrieval from embedded content

`@rag`, `@<configured-embedding-location>`

Aunic will have the option to create RAG systems. The goal is a simple method for users who have never embedded or indexed anything to point Aunic at files on their computer and get retrieval working. Long term, a marketplace of pre-indexed docs (wikipedia clones, python docs, MDN, etc.) would be nice.

The idea: users can create custom `@` commands to search specific scopes. Example setup: a wikipedia clone, MDN, docs.python.org clone, and a personal notes directory all indexed separately.
- `@wiki` searches only wikipedia
- `@docs` searches all docs
- `@notes` searches the notes directory
- `@rag` is the default command that searches all embedded content

Works like `@web`:
1. User sends `@rag <query>`
2. Best-matching chunks are displayed in a list for the user to select and append to the note
3. `--synth` (or `-s`) sends selected chunks to the LLM to synthesize into the note instead of appending raw

Fallback: if the user hasn't set up RAG, `@notes`-style commands do fuzzy search over the configured directory without needing an embedding model at all. Users who don't care about embeddings still get something useful.

**Why user-side RAG is fine but model-side RAG is not** (see Model Tools below): the user previews and selects chunks before they enter context. That preserves transparency. A model-side RAG silently injects chunks stripped of their surrounding structure — which is the exact opacity Aunic exists to eliminate.

## `/map` — build and update a map of Aunic notes on the system

`/map <scope>`

Creates or updates a map of Aunic files on the system. Aunic files are detected by the presence of sibling `.aunic/` metadata or by grepping for `---\n# Transcript`. The map contains:
- The path to each file
- A short "snippet" describing the note's contents

**Snippet sources (in priority order):**
1. **User-provided custom summary** — authoritative when set.
2. **Model-generated summary** — authoritative when set (written via explicit command, not automatic).
3. **First N characters of the note-content** — default, auto-refreshed on note save / open / close.

When the user or model explicitly writes a custom summary, a flag is placed in the note's `.aunic/` metadata that stops the first-N-chars update from overwriting it. The automatic snippet update only runs on notes that have no custom summary flag set. This keeps the fast path cheap and lets users/models invest more effort on notes that matter without worrying about their work being overwritten.

**Commands:**
- `/map` — walk the home directory recursively
- `/map ~/example/path/` — start at that directory recursively (can pass multiple paths)
- `/map --set-summary <text>` — set a custom summary for the current file; sets the no-auto-update flag
- `/map --generate-summary` — have the model write the summary for the current file (explicit, not automatic); sets the no-auto-update flag
- `/map --clear-summary` — revert to automatic first-N-chars behavior

**Storage:**
- Stored in `~/.aunic/` as a master map that builds up over time
- Also stored in `<cwd>/.aunic/` for the local directory
- On re-map of an already-mapped directory, skips files unchanged since the last map to avoid repeated work
- Always references the `~/.aunic` version first to avoid redundant work
- Map format is a markdown file tree so users can edit it directly. YAML / JSON / Lua are alternatives if markdown proves awkward, but only if markdown is genuinely insufficient.

**Important: the map is a cache, not a source of truth.** It must always be regenerable by walking the filesystem. If the master map becomes load-bearing in a way that can't be rebuilt from scratch, hidden state has been introduced and the thesis has been violated.

Since `/map` doesn't require the LLM for the default-case snippet, it can auto-update on note save / open / close cheaply. Only custom and model-generated summaries are explicit user actions.

**Future extensions:** `/map --dir` could build a referenceable project-wide table of contents usable by work-mode (aider-ish). Out of scope for the first pass.

---

# Model Tools

This is the harder question. What memory-shaped tools should the model have, given what models are actually trained to do well?

## What models are trained to do well

This is the framing that matters. The right tools are the ones that line up with behaviors already in training distribution.

1. **Read a markdown file with paths and descriptions, then `read_file` what looks relevant.** The dominant agent pattern in training data. CLAUDE.md, AGENTS.md, README.md, `docs/` indices — every coding agent dataset is full of "read the index, pick the thing, open it." A `/map`-shaped tool is directly in this family.
2. **Grep + glob + read loops over a directory.** The single most-used agent tool in training. A grep tool scoped to Aunic notes will outperform any bespoke semantic-memory tool on any frontier model simply because of training distribution.
3. **Follow a link already in context.** If a path appears in the note-content or transcript, the model will naturally open it when relevant. Cross-references between notes are free retrieval, no tool needed.
4. **Mimic structured past examples.** When the model sees "last time I did X, I ran `docker compose up -d` in `/srv/`" in context, it strongly mimics. This is the closest thing to "learning from past work" without fine-tuning — and it explains why *structured* past work (transcript tables) is far more valuable as memory than unstructured chat.

What models are **not** trained to do well:
- Reason about opaque chunks from a vector store with no surrounding context.
- Decide *when* to query memory unprompted. They'll do it if reminded; they forget to otherwise.
- Maintain a long-lived structured memory store themselves. They drift, duplicate, and contradict.

This directly prescribes the tool set.

## Tool 1: `search_transcripts` — the Aunic-unique one (build first)

Queries across the `# Transcript` tables of all mapped Aunic notes. Filterable by:
- `tool="bash"` → past bash commands
- `tool="note_edit"`, `tool="note_write"` → past edits
- `query="docker"` → substring match within tool-call args or results
- `since="2026-01-01"` → time-scoped
- `scope=<path>` → limit to a subtree

Returns rows as: *"in `<path>`, on `<date>`, you ran `<tool>` with `<args>` → `<result-snippet>`."*

**Why this is the keystone tool:**
1. The transcript table has columns. Grep can't exploit structure. A structured query can return "all bash commands touching `/srv/` in the last month" in one call.
2. This is where the "model improves off past work" effect actually lives. When the model sees *"last time I set up this kind of container I did exactly these steps and they worked,"* it strongly mimics. Transcript rows are executed, ground-truth records of what succeeded — not chat blather about what someone planned to do.
3. It answers the "user moves a drive, model should remember the docker volume" scenario directly. That's not a RAG query. It's "find past tool calls that touched this path."
4. **No other memory system can build this.** Transcripts only have their structure because Aunic forced chat into a table. This is the one model-memory tool that isn't a reimplementation of something other systems already have.

If only one model-memory tool gets built, build this one.

### Open questions for `search_transcripts`
- **Result overflow.** What should the tool return when 50+ rows match? Options: paginate (cheap, model has to request more), summarize into groups (adds cost + opacity), force a narrower query (annoying but keeps context tidy). Leaning toward pagination + a "this returned N results, N shown, narrow with `query=...` or `since=...` if needed" hint.
- **Per-row snippet size.** Full tool-call args can be huge. Truncate to N chars with a follow-up `read_transcript_row` tool? Or trust the model to ask?

## Tool 2: `grep_notes` — scoped ripgrep (build second)

A ripgrep wrapper that only searches files Aunic recognizes as notes. Optional `section="note-content"` or `section="transcript"` to restrict to one half of the file. Optional `scope=<path>`.

**Why:**
- Grep-over-codebase is the most-used agent tool in training. The model reaches for it naturally and skillfully.
- The `section` filter is the Aunic-specific value-add. Grepping only transcripts answers "have I done X" without note-prose noise. Grepping only note-content answers "have I written notes about X" without transcript chatter.
- Returns `file:line` with surrounding context. Shape the model is trained on. No chunking, no embeddings.

This tool is boring. That's the point. Boring = in-distribution = reliable.

## Tool 3: `read_map` — read the `/map` output as a file (build third)

Just reads `~/.aunic/map.md` (or a scoped subtree). No query, no filtering, no cleverness. The model reads it, picks paths it thinks relevant, and calls its normal `read_file` tool on them.

**Why this is the right shape:**
- Identical to reading a CLAUDE.md or `docs/README.md`. Pure in-distribution.
- Zero ambiguity about scope: the user owns the map; the model owns the decision of what to open.
- The user can audit exactly what the model saw — the map is a file, not a query result.
- Zero opacity: if the model reads three files it didn't need, that's visible in the transcript.

The `scope` argument exists so a large map can be narrowed without blowing up context. **Not** for "intelligent scoping" — just "don't load the whole `~/.aunic` map if I only care about `~/projects/homelab`."

Do not add fuzzy search, do not pre-filter, do not summarize. The model's job is to read the table of contents and pick. It's extremely good at that.

Build this third — only once there are enough notes that grep is too noisy to be useful on its own.

## Tool 4: `rag_search` / `rag_fetch` — optional power-user retrieval (`web_search`/`web_fetch` shaped)

**Original idea:**
> An optional "power user" feature where if the user has a RAG server, Aunic can integrate with it and offer scoped searches or RAG searches across the entire server. For example: a RAG server with python docs, RFC docs, MDN, openconfig, ubuntu server, a personal notes directory, and wikipedia. The user sets up a RAG server for Aunic to call and declares different scopes — `@python`, `@networking` (openconfig + ubuntu server + RFCs), `@mdn`, `@notes`, `@wiki`, plus a cross-corpus `@rag`. In the RAG tool given to the model, each scope is outlined so the model can enter a query + a scope in its tool calls, retrieve results, then decide whether to fetch a full document to read or just use the snippet and move on.

This is not the chunk-and-inject RAG rejected in "What not to build" below. It's the `web_search` / `web_fetch` shape: the model issues a query, gets titles + snippets + paths back, then decides whether to fetch any full document. Every call is visible in the transcript. The model reads *documents*, not chunks. Structure survives.

**Why this shape dodges the chunk-RAG concerns:**
- It's `web_search` / `web_fetch` by another name. Maximally in-distribution — every frontier model has been trained on this pattern.
- `rag_fetch` returns full documents (or server-pre-chunked sections), not embedding-chunks. Surrounding context survives. The model reads an MDN page or an RFC the same way it reads a source file.
- Every call is auditable in the transcript: search args → results → fetch → content.
- Scope is explicit. `@networking` vs `@mdn` vs `@python` means the model picks the authoritative source by name, not by embedding-luck across a single blob.

**Connection:** plain HTTP REST to a user-configured RAG server. A minimal spec along the lines of `POST /search {scope, query}` → `[{doc_id, title, snippet, score}]` and `POST /fetch {doc_id, section?}` → document content. Users bring any server that speaks this spec — Aunic doesn't ship a RAG backend, doesn't care what the server indexes, doesn't care whether ranking is BM25 or embeddings or hybrid.

**Why HTTP and not MCP here:** MCP is also a good connection option and should be supported separately (see mcp-support doc when written). But the `@rag` user command (see User Tools above) needs an HTTP endpoint anyway — the same server exposing the same REST API can back both the user command and the model tool. One config, one server, two call paths into it. MCP is a parallel option users can choose instead for model tools; HTTP is the baseline because `@rag` requires it.

**Tool surface:**
```
rag_search(query: str, scope: str) -> list[{doc_id, title, snippet, score}]
rag_fetch(doc_id: str, section?: str) -> str
```

A single tool with a `scope` parameter, not one tool per scope. That way the scope choice is explicit in every call (`rag_search(query="BGP route reflection", scope="networking")`) and self-documenting in the transcript. Scopes come from user config — Aunic reads them on startup and splices them into the tool description the model sees, along with each scope's user-written description:

```
Available scopes:
- python: Python stdlib reference. Use for API signatures and stdlib behavior.
- networking: RFCs, openconfig, ubuntu server docs. Use for protocol and server config questions.
- mdn: Web platform docs (HTML/CSS/JS APIs).
- notes: The user's personal notes directory.
- wiki: Offline Wikipedia. General reference.
- rag: Cross-corpus search. Use only when unsure which scope applies.
```

**Scope descriptions are load-bearing.** They're what the model reads to decide which scope to call. Treat them like tool docstrings — the quality of model tool-choice is directly proportional to how clear these descriptions are. The config format should nudge users to write good ones.

**Config shape (rough):**
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

Scopes compose (one scope can bundle multiple server-side corpora). The cross-corpus `@rag` scope is either a server-side "search everything" corpus or a well-known scope name Aunic passes through.

**Fetch size is a real problem.** An RFC or a long wikipedia article can blow out context. Two mitigations:
- Server pre-chunks documents by heading. `rag_fetch(doc_id=X)` with no section returns a TOC of section headings. `rag_fetch(doc_id=X, section="9.2.1")` returns just that section.
- Or: return first N chars + a "more available" marker and let the model ask for the next range.

The server owns document structure, so it can slice along semantically meaningful boundaries — which is the thing `web_fetch` can't do well because the web has no standard structure.

**Graceful degradation.** If the configured server is unreachable, `rag_search` returns a clear error (not a crash), the manifest can note the tool is configured but offline, and the user sees an indicator somewhere in the status area. Don't let a broken RAG server brick the session.

**Status: optional, power-user.** This is not a default feature. Most users won't run a RAG server. The default Aunic experience stays simple; the users who'd benefit most (people who already curate indexed corpora) get the power without the rest of the userbase paying complexity tax. The build order reflects this — it comes after the memory-specific tools, not before.

## What not to build

**Model-side chunk-RAG with auto-injection.** Distinct from Tool 4 above, which is *user-configured, search-then-fetch, never injected*. What's rejected here is the pattern where the model gets opaque chunks stripped of structure silently prepended to context:
- RAG-as-auto-injection returns chunks stripped of structure. Aunic's entire thesis is that structure is what makes context efficient. Feeding the model context-free chunks regresses to chat-era opacity.
- The model can't judge chunk relevance well when chunks arrive unbidden. It either trusts them (and gets confused by bad matches) or distrusts them (and wastes tokens re-reading the source).
- Tool 4 dodges this because the model *pulls* full documents on demand, visibly, and scope is explicit.

**A model-maintained memory store.** Models can't reliably maintain long-lived structured memory. The store drifts, gets duplicates, gets contradictions. The note *is* the store. Any "memory update" should be a regular `note_edit` or `note_write`, visible in the transcript like everything else.

**A model-maintained memory store.** Models can't reliably maintain long-lived structured memory. The store drifts, gets duplicates, gets contradictions. The note *is* the store. Any "memory update" should be a regular `note_edit` or `note_write`, visible in the transcript like everything else.

**Subagents to summarize notes.** Not worth it. The first N characters of a well-written note is already the summary. Trust the thesis.

**Auto-injection of anything.** Not a "potentially useful" section, not a "relevant past work" sidebar, nothing. The moment something shows up in model-context the user didn't ask for or the model didn't pull for itself, Aunic's opacity contract is broken. If it's worth including, the user should `/include` it or the model should pull it with a visible tool call.

---

# How does the model know to use these tools?

Models do not spontaneously check memory. They do what's in front of them. A memory tool nobody calls is no memory system at all.

**Solution: a memory manifest embedded in Aunic's system-generated prefix.**

The prefix already exists and is a tested surface — the model reliably distinguishes it from the note content. That makes it the right place to put a short, always-on block listing:

- What memory tools exist
- When the model should reach for each

Rough shape:
```
# Memory tools
- search_transcripts: call before proposing commands that modify the
  system — the user may have done something similar before, and the
  prior approach likely worked.
- grep_notes: call to find notes mentioning specific terms or files.
- read_map: call when you're uncertain whether relevant past work
  exists anywhere in the user's notes.
```

**The memory stays pull-based. Only the manifest is always-on.** That distinction is what keeps Aunic out of the extraction-and-injection trap every other memory system falls into. The user can audit the manifest (it's a file), edit it, disable it. It's transparent by construction because it lives in a surface that's already transparent.

---

# Build order

1. **`search_transcripts`** — keystone, uniquely Aunic, justifies the whole memory story.
2. **`grep_notes`** — boring, reliable, high leverage.
3. **`/map` (user command) + `read_map` (model tool)** — once there are enough notes to need an index.
4. **`@rag` and custom `@<scope>` commands** — user-facing retrieval, independent track.
5. **Memory manifest in the system prefix** — added alongside tool 1 so the model actually reaches for it.

Everything else — model-side RAG, auto-injection, subagent summarization, model-maintained memory stores — explicitly deferred or rejected.
