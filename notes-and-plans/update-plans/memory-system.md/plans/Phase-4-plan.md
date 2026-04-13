# Phase 4 — `@rag` + custom `@<scope>` user commands

## Context

Phases 1-3 gave Aunic memory tools for its own notes (search transcripts, grep notes, map). Phase 4 is a separate track: external-corpus retrieval. It lets the user search their own indexed document collections (docs, Wikipedia, RFCs, etc.) from within Aunic using `@rag <query>` or `@docs <query>`, browse results, and insert selected content into the current note.

The user already has a working Qdrant vector DB with 230k+ embedded document chunks and a SQLite text store. Phase 4 adds the Aunic client side — it does NOT ship a server. Aunic defines the server spec (request/response schema) and connects to whatever the user runs.

Core design decision: **Aunic talks to a single RAG server, not directly to a vector DB or embedding server.** The RAG server owns embedding, vector search, text-store lookup, doc slicing, and score merging. Aunic is purely a client.

---

## RAG Server Spec (the contract)

### `POST /search`

**Request:**
```json
{
  "query": "netplan static ip configuration",
  "scope": "docs",
  "limit": 10
}
```
- `query` (string, required): natural-language search query
- `scope` (string, optional): scope name matching a `[[rag.scope]]` entry. Omit for unscoped search.
- `limit` (int, optional, default 10): max results to return

**Response:**
```json
{
  "results": [
    {
      "doc_id": "ubuntu-server:networking:netplan-config",
      "chunk_id": "chunk_a1b2c3",
      "title": "Configuring network interfaces with Netplan",
      "source": "ubuntu-server",
      "snippet": "Netplan uses YAML files to configure network interfaces...",
      "score": 0.8742,
      "heading_path": ["Networking", "Netplan", "Static IP"],
      "url": null
    }
  ]
}
```
- `doc_id` (string): opaque document identifier, passed to `/fetch`
- `chunk_id` (string): opaque chunk identifier
- `title` (string): document or section title
- `source` (string): corpus/collection name
- `snippet` (string): chunk text preview (truncated)
- `score` (float): relevance score (0-1)
- `heading_path` (list[string], optional): document structure breadcrumb
- `url` (string, nullable): canonical URL if the source has one

### `POST /fetch`

**Request:**
```json
{
  "doc_id": "ubuntu-server:networking:netplan-config",
  "section": null
}
```
- `doc_id` (string, required): from search results
- `section` (string, optional): heading path or section ID. Omit for full document or table of contents.

**Response:**
```json
{
  "doc_id": "ubuntu-server:networking:netplan-config",
  "title": "Configuring network interfaces with Netplan",
  "source": "ubuntu-server",
  "url": null,
  "content_type": "markdown",
  "sections": [
    {
      "heading": "Static IP Configuration",
      "heading_path": ["Networking", "Netplan", "Static IP"],
      "text": "To configure a static IP address...",
      "token_estimate": 340
    }
  ],
  "full_text": "# Configuring network interfaces with Netplan\n\n..."
}
```
- `sections` (list): document broken into headed sections for selective insertion
- `full_text` (string): complete document text
- `content_type` (string): always "markdown" for now

---

## Config File

**Path:** `~/.aunic/rag.toml`, parsed with stdlib `tomllib`.

```toml
[rag]
server = "http://localhost:5173"

[[rag.scope]]
name = "docs"
description = "Ubuntu server docs, RFCs, Python stdlib reference."

[[rag.scope]]
name = "wiki"
description = "Wikipedia. Use for general knowledge and concepts."
```

Each `[[rag.scope]]` generates a corresponding `@<name>` command. `@rag` is always available as the unscoped catch-all (no scope filter sent to server). If no `rag.toml` exists or `server` is empty, all `@rag`/`@<scope>` commands show a "RAG not configured" error.

The `description` field is carried forward for Phase 5 (model tool descriptions). The `corpora` field from the roadmap is dropped — the server maps scope names to its own collections internally. This keeps Aunic's config minimal and avoids coupling to the server's storage layout.

---

## Architecture

### New files

| File | Purpose |
|------|---------|
| `src/aunic/rag/__init__.py` | Package marker, re-exports public symbols |
| `src/aunic/rag/types.py` | Dataclasses: `RagScope`, `RagConfig`, `RagSearchResult`, `RagFetchSection`, `RagFetchResult` |
| `src/aunic/rag/config.py` | `load_rag_config()` — loads and caches `~/.aunic/rag.toml` |
| `src/aunic/rag/client.py` | `RagClient` — async HTTP client with `search()` and `fetch()` |
| `tests/test_rag_config.py` | Config loading tests |
| `tests/test_rag_client.py` | Client tests with `httpx.MockTransport` |
| `tests/test_rag_dispatch.py` | Controller dispatch + scope registration tests |

### Modified files

| File | What changes |
|------|-------------|
| `src/aunic/tui/rendering.py` | Add `register_rag_scopes()` to make command regex dynamic; add `@rag` to defaults |
| `src/aunic/tui/controller.py` | Add RAG state fields, `@rag`/`@<scope>` dispatch, `_run_rag_search()`, `_run_rag_fetch()`, `_insert_rag_chunks()`, cleanup in `_web_cancel()` |
| `src/aunic/tui/types.py` | No change — reuse existing `WebMode` for RAG flow |

### NOT modified (Phase 5 concerns)

- `src/aunic/tools/memory_tools.py` — model tools are Phase 5
- `src/aunic/tools/memory_manifest.py` — model tool hints are Phase 5
- `src/aunic/modes/chat.py` — system prompt changes are Phase 5

---

## Detailed Design

### 1. `src/aunic/rag/types.py`

```python
@dataclass(frozen=True)
class RagScope:
    name: str               # "docs", "wiki", etc.
    description: str        # for Phase 5 model tool descriptions

@dataclass(frozen=True)
class RagConfig:
    server: str             # "http://localhost:5173"
    scopes: tuple[RagScope, ...]

@dataclass(frozen=True)
class RagSearchResult:
    doc_id: str
    chunk_id: str
    title: str
    source: str
    snippet: str
    score: float
    heading_path: tuple[str, ...] = ()
    url: str | None = None

@dataclass(frozen=True)
class RagFetchSection:
    heading: str
    heading_path: tuple[str, ...]
    text: str
    token_estimate: int = 0

@dataclass(frozen=True)
class RagFetchResult:
    doc_id: str
    title: str
    source: str
    url: str | None
    sections: tuple[RagFetchSection, ...]
    full_text: str = ""
```

### 2. `src/aunic/rag/config.py`

- `RAG_CONFIG_PATH = Path.home() / ".aunic" / "rag.toml"`
- `load_rag_config() -> RagConfig | None` — reads TOML, validates `[rag].server` is non-empty, parses `[[rag.scope]]` entries. Returns `None` if file missing or server empty.
- Module-level cache with mtime-based invalidation (same pattern as `proto_settings.py`).
- `invalidate_rag_config_cache()` for testing.

### 3. `src/aunic/rag/client.py`

```python
class RagClient:
    def __init__(self, server_url: str) -> None:
        self._base_url = server_url.rstrip("/")

    async def search(
        self,
        query: str,
        scope: str | None = None,
        limit: int = 10,
    ) -> tuple[RagSearchResult, ...]:
        """POST /search, returns parsed results."""

    async def fetch(
        self,
        doc_id: str,
        section: str | None = None,
    ) -> RagFetchResult:
        """POST /fetch, returns parsed result."""
```

- Uses `httpx.AsyncClient` with a 30-second timeout.
- Raises `httpx.HTTPStatusError` on server errors (caught by controller).
- Client is instantiated lazily in the controller via `_ensure_rag_client()`.

### 4. Dynamic Prompt Highlighting (`src/aunic/tui/rendering.py`)

The `PROMPT_ACTIVE_COMMANDS` frozenset and `_PROMPT_COMMAND_RE` regex are currently static. Phase 4 needs dynamic scopes from config.

Approach: add a `register_rag_scopes()` function that **replaces** the module-level regex and frozenset. Called once during controller `__init__`.

```python
# Module level: mutable set + pattern
_prompt_active_commands: set[str] = {
    "/context", "/note", "/chat", "/work", "/read", "/off",
    "/model", "/find", "@web", "/include", "/exclude", "/isolate", "/map",
}

# Keep the frozenset for backward compat (read-only view)
PROMPT_ACTIVE_COMMANDS: frozenset[str]  # rebuilt by _rebuild_regex()

_PROMPT_COMMAND_RE: re.Pattern[str]  # rebuilt by _rebuild_regex()

def _rebuild_regex() -> None:
    global PROMPT_ACTIVE_COMMANDS, _PROMPT_COMMAND_RE
    PROMPT_ACTIVE_COMMANDS = frozenset(_prompt_active_commands)
    # Build pattern: sort by length descending so longer commands match first
    escaped = sorted(
        (re.escape(cmd) + r"\b" for cmd in _prompt_active_commands),
        key=len, reverse=True,
    )
    # Also keep /clear-history which is in the regex but not in the active set
    escaped.append(r"/clear-history\b")
    _PROMPT_COMMAND_RE = re.compile("(" + "|".join(escaped) + ")")

def register_rag_scopes(scope_names: tuple[str, ...]) -> None:
    """Add @rag and @<scope> commands dynamically from config."""
    _prompt_active_commands.add("@rag")
    for name in scope_names:
        _prompt_active_commands.add(f"@{name}")
    _rebuild_regex()

# Initialize defaults
_rebuild_regex()
```

The `PromptLexer` class already uses `_PROMPT_COMMAND_RE` by reference (not by value), so it picks up changes automatically. The special `@web`-only-at-start logic at line 141 should be generalized to apply to all `@`-prefixed commands.

### 5. Controller Integration (`src/aunic/tui/controller.py`)

#### Init changes

In `__init__`, after state creation (~line 139):
```python
# Register RAG scopes for prompt highlighting
try:
    from aunic.rag.config import load_rag_config
    rag_cfg = load_rag_config()
    if rag_cfg is not None:
        from aunic.tui.rendering import register_rag_scopes
        register_rag_scopes(tuple(s.name for s in rag_cfg.scopes))
except Exception:
    pass  # RAG config is optional
```

#### New state fields (after line 182)

```python
# @rag ephemeral navigation state
self._rag_active: bool = False          # True when current web_mode flow is RAG
self._rag_scope: str | None = None      # scope name used for current search
self._rag_results: tuple[RagSearchResult, ...] = ()
self._rag_client: RagClient | None = None
```

#### Dispatch in `send_prompt()` (after @web block, ~line 742)

```python
if cmd == "@rag" or (cmd.startswith("@") and cmd[1:] in self._rag_scope_names()):
    scope = None if cmd == "@rag" else cmd[1:]
    query = remaining
    if not query:
        self._set_error(f"Usage: {cmd} <search query>")
        self._invalidate()
        return
    if self.state.editor_dirty and not await self.save_active_file():
        return
    self._sync_prompt_text("")
    self._run_task = asyncio.create_task(self._run_rag_search(query, scope))
    return
```

#### `_rag_scope_names()` helper

```python
def _rag_scope_names(self) -> frozenset[str]:
    try:
        from aunic.rag.config import load_rag_config
        cfg = load_rag_config()
        return frozenset(s.name for s in cfg.scopes) if cfg else frozenset()
    except Exception:
        return frozenset()
```

#### `_ensure_rag_client()` helper

```python
def _ensure_rag_client(self) -> RagClient | None:
    if self._rag_client is not None:
        return self._rag_client
    from aunic.rag.config import load_rag_config
    cfg = load_rag_config()
    if cfg is None:
        return None
    from aunic.rag.client import RagClient
    self._rag_client = RagClient(cfg.server)
    return self._rag_client
```

#### `_run_rag_search()` (new async method)

Follows `_run_web_search()` pattern exactly:

1. Set `self._rag_active = True`, `self._rag_scope = scope`
2. Set `run_in_progress = True`, status "Searching..."
3. Call `client.search(query, scope, limit=10)`
4. Map results to `SearchResult` for WebSearchView reuse:
   ```python
   self._web_results = tuple(
       SearchResult(
           source_id=f"r{i}",
           title=r.title,
           url=r.url or f"[{r.source}] {r.doc_id}",
           canonical_url=r.doc_id,
           snippet=r.snippet,
           rank=i,
           refined_score=r.score,
       )
       for i, r in enumerate(results)
   )
   ```
5. Store `self._rag_results = results` (for doc_id lookup during fetch)
6. Append transcript: tool=`"rag_search"`, call=`{"query": query, "scope": scope}`, response=result list
7. Set `web_mode = "results"`, status "Found N results. Space=select  Ctrl+R=fetch  Esc=cancel"
8. Error path: display error in indicator, cancel flow
9. `finally`: `run_in_progress = False`

#### `_run_rag_fetch()` (new async method)

Follows `_run_web_fetch()` pattern:

1. Get selected `RagSearchResult` from `self._rag_results[self._web_selected_result]`
2. Call `client.fetch(doc_id=result.doc_id)`
3. Map sections to `FetchedChunk` for WebSearchView reuse:
   ```python
   chunks = tuple(
       FetchedChunk(
           source_id=f"r{i}",
           title=section.heading,
           url=result.url or result.doc_id,
           canonical_url=result.doc_id,
           text=section.text,
           score=0.0,
           heading_path=section.heading_path,
       )
       for i, section in enumerate(fetch_result.sections)
   )
   packet = FetchPacket(
       source_id="r0",
       title=fetch_result.title,
       url=result.url or result.doc_id,
       canonical_url=result.doc_id,
       desired_info=self._web_query,
       chunks=chunks,
       full_markdown=fetch_result.full_text,
   )
   ```
4. Append transcript: tool=`"rag_fetch"`, call=`{"doc_id": ..., "source": ...}`, response=`{"title": ..., "sections": N}`
5. Set `web_mode = "chunks"`, show chunk count status

#### `_handle_web_send()` modification

Add RAG branch at the top:
```python
def _handle_web_send(self) -> None:
    if self.state.run_in_progress:
        self._set_error("Search/fetch in progress.")
        self._invalidate()
        return
    if self._rag_active:
        # RAG flow: same structure as web flow
        if self.state.web_mode == "results":
            if self._web_selected_result is None:
                self._set_error("Select a result with [Space] first.")
                self._invalidate()
                return
            self._run_task = asyncio.create_task(self._run_rag_fetch())
        elif self.state.web_mode == "chunks":
            if self._web_chunk_cursor == -1 or self._web_chunk_selected:
                self._run_task = asyncio.create_task(self._insert_web_chunks())
            else:
                self._set_error("Select chunks with [Space] or navigate to 'Fetch full page'.")
                self._invalidate()
        return
    # ... existing web flow below
```

Note: `_insert_web_chunks()` is reused as-is — it already works with `FetchPacket` objects, which RAG results are mapped into.

#### `_web_cancel()` cleanup

Add RAG state reset:
```python
self._rag_active = False
self._rag_scope = None
self._rag_results = ()
```

### 6. `--synth` Flag

Deferred to a follow-up within Phase 4. The core `@rag` search/fetch/insert flow is complete without it. When added:

- Syntax: `@rag -s <query>` or `@docs --synth <query>`
- Parse the flag from `remaining` before extracting the query
- After chunk selection, before insertion: pass selected chunks + original query through a single LLM call using the current session's provider
- Synthesis prompt: "Synthesize the following reference material into concise prose suitable for inclusion in a working note. Preserve technical accuracy. Do not add information not present in the sources.\n\n{chunks}"
- On synthesis failure: fall back to raw chunk insertion with a warning

### 7. Error Handling

- **No config file / empty server:** `_ensure_rag_client()` returns `None`. Dispatch shows: "RAG not configured. Create ~/.aunic/rag.toml with a [rag] server = ... entry."
- **Server unreachable:** `httpx.ConnectError` caught in `_run_rag_search()`, displayed in indicator: "RAG server unreachable at http://..."
- **Server returns error:** `httpx.HTTPStatusError` caught, displayed: "RAG server error: {status_code} {reason}"
- **No results:** Show "No results for '{query}'" in indicator, cancel flow (same as web search empty results)
- **Invalid config:** `load_rag_config()` returns `None`, logs warning

---

## Test Plan

### `tests/test_rag_config.py`

```
test_load_valid_config                  — full TOML with server + scopes
test_load_missing_file                  — returns None
test_load_empty_server                  — returns None
test_load_no_scopes                     — valid config with empty scopes tuple
test_load_malformed_toml                — returns None, logs warning
test_cache_invalidation                 — load twice, second uses cache; invalidate, third re-reads
```

### `tests/test_rag_client.py`

```
test_search_success                     — mock transport returns results, assert mapping
test_search_empty_results               — server returns {"results": []}
test_search_with_scope                  — assert scope passed in request body
test_search_server_error                — 500 response raises HTTPStatusError
test_search_connection_error            — unreachable server raises ConnectError
test_fetch_success                      — mock transport returns sections
test_fetch_no_sections                  — empty sections list
test_fetch_with_section_param           — assert section passed in request body
```

### `tests/test_rag_dispatch.py`

```
test_register_rag_scopes_updates_regex  — @docs, @wiki match after registration
test_register_rag_scopes_includes_rag   — @rag always matches
test_unregistered_scope_no_match        — @unknown does not match
test_rag_dispatch_no_config             — shows "RAG not configured" error
test_rag_dispatch_empty_query           — shows usage error
test_rag_result_to_search_result_map    — unit test for mapping logic
test_rag_fetch_to_fetch_packet_map      — unit test for mapping logic
```

---

## Verification

1. **Unit tests:** `pytest tests/test_rag_config.py tests/test_rag_client.py tests/test_rag_dispatch.py -v`
2. **Full suite:** `pytest` — all existing tests must still pass (especially `test_note_edit_tools.py` and `test_tui_controller.py`)
3. **Manual smoke test (requires a running RAG server):**
   - Create `~/.aunic/rag.toml` with server URL
   - Open Aunic, type `@docs netplan static ip`
   - Verify results appear in the search view
   - Select a result with Space, press Ctrl+R to fetch
   - Select chunks with Space, press Ctrl+R to insert
   - Verify content appears in note
   - Verify transcript records both `rag_search` and `rag_fetch` entries
   - Type `@rag` with no query — verify usage error
   - Stop RAG server — type `@docs test` — verify graceful error message
   - Delete `rag.toml` — type `@rag test` — verify "not configured" message

---

## Implementation Order

1. `src/aunic/rag/types.py` + `src/aunic/rag/__init__.py` — data structures
2. `src/aunic/rag/config.py` + `tests/test_rag_config.py` — config loader
3. `src/aunic/rag/client.py` + `tests/test_rag_client.py` — HTTP client
4. `src/aunic/tui/rendering.py` — dynamic scope registration
5. `src/aunic/tui/controller.py` — dispatch + RAG flow methods
6. `tests/test_rag_dispatch.py` — dispatch and mapping tests
7. Full test suite pass
