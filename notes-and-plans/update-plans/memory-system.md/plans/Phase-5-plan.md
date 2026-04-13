# Phase 5 — `rag_search` / `rag_fetch` Model Tools

## Context

Phase 4 added user-facing `@rag`/`@<scope>` commands that let the user manually search and fetch from the RAG server at `http://0.0.0.0:5173`. Phase 5 exposes the same RAG server to the **model** via standard tool calls so it can autonomously search and fetch RAG content during chat sessions. The RAG client (`src/aunic/rag/client.py`) and types (`src/aunic/rag/types.py`) already exist from Phase 4.

User requirements:
- Config lives in `proto-settings.json`, not `rag.toml`
- Fetches work like `web_fetch` — recorded in transcript, but old content compacted out before model sees it
- Each scope (docs, notes, python, networking, wiki, rag) gets a `@` command (already done in Phase 4)
- DO NOT test against the live RAG server

---

## Implementation Plan

### 1. Add RAG config reader to `proto_settings.py`

**File:** [proto_settings.py](src/aunic/proto_settings.py)

Add `get_rag_config(project_root: Path) -> RagConfig | None`:
- Calls existing `_load_proto_payload(project_root)` (gets mtime caching for free)
- Reads `payload["rag"]` dict → `RagConfig(server=..., scopes=...)`
- Returns `None` if missing/malformed

Expected proto-settings.json shape:
```json
{
  "rag": {
    "server": "http://0.0.0.0:5173",
    "scopes": [
      {"name": "docs", "description": "Official documentation"},
      {"name": "notes", "description": "Personal Aunic notes"},
      {"name": "python", "description": "Python stdlib and ecosystem"},
      {"name": "networking", "description": "Networking protocols and tools"},
      {"name": "wiki", "description": "Wiki knowledge base"},
      {"name": "rag", "description": "RAG system internals"}
    ]
  }
}
```

### 2. Update `rag/config.py` to check proto-settings first

**File:** [rag/config.py](src/aunic/rag/config.py)

At the top of `load_rag_config()`, try `get_rag_config(Path.home())` first. If it returns a `RagConfig`, use it. Fall back to `rag.toml` only if proto-settings has no `"rag"` key. This keeps Phase 4 `@rag` commands working with the new config location.

### 3. Create the RAG tool module

**New file:** `src/aunic/tools/rag_tools.py`

Follow the pattern from [research.py](src/aunic/tools/research.py):

**Args dataclasses:**
- `RagSearchArgs(query: str, scope: str | None, limit: int)` — frozen
- `RagFetchArgs(doc_id: str, section: str | None)` — frozen

**Builder:**
- `build_rag_tool_registry(project_root: Path) -> tuple[ToolDefinition, ...]`
- Calls `get_rag_config(project_root)` — returns `()` if no config
- Builds dynamic `rag_search` description listing available scopes from config
- Uses `enum` constraint on scope parameter so model only picks valid scopes
- Returns two `ToolDefinition` instances

**`rag_search` tool:**
- Description dynamically includes scope names + descriptions from config
- Input schema: `query` (required string), `scope` (optional, enum of scope names), `limit` (optional int, default 10, max 20)
- Execute: constructs `RagClient` ad-hoc (same pattern as `search_transcripts` which constructs its service inline), calls `client.search()`, returns results as list of dicts
- Both `in_memory_content` and `transcript_content` are the same results list (search results are small)

**`rag_fetch` tool:**
- Description: "Fetch content of a RAG document by doc_id. Use after rag_search."
- Input schema: `doc_id` (required string), `section` (optional string)
- Execute: constructs `RagClient`, calls `client.fetch()`
- Split content pattern (matching `web_fetch`):
  - `in_memory_content`: full dict with `type: "rag_fetch"`, `doc_id`, `title`, `source`, `full_text`, section count
  - `transcript_content`: lightweight dict with just `doc_id`, `title`, `source` (NOT full text)

**Config injection via closure** — the builder captures `RagConfig` and creates a lambda `client_factory = lambda: RagClient(config.server)` used by both execute functions. No changes needed to `RunToolContext`.

**Error handling:** catch `httpx.HTTPStatusError`, `httpx.ConnectError`, generic `Exception` → return `_tool_error_result()` with `failure_payload()`, matching `research.py` pattern.

### 4. Register RAG tools in memory tool registry

**File:** [memory_tools.py](src/aunic/tools/memory_tools.py)

Update `build_memory_tool_registry()` to accept `project_root: Path | None = None`:
```python
def build_memory_tool_registry(*, project_root: Path | None = None) -> tuple[ToolDefinition[Any], ...]:
    base = (
        *build_search_transcripts_tool_registry(),
        *build_grep_notes_tool_registry(),
        *build_read_map_tool_registry(),
    )
    if project_root is not None:
        from aunic.tools.rag_tools import build_rag_tool_registry
        base = (*base, *build_rag_tool_registry(project_root))
    return base
```

### 5. Thread `project_root` through registry builders and callers

**File:** [note_edit.py](src/aunic/tools/note_edit.py)

Add `project_root: Path | None = None` parameter to both:
- `build_note_tool_registry(*, work_mode, project_root=None)` → forward to `build_memory_tool_registry(project_root=project_root)`
- `build_chat_tool_registry(*, work_mode, project_root=None)` → same

**Callers to update:**

| File | Line | Change |
|------|------|--------|
| [chat.py:194](src/aunic/modes/chat.py#L194) | `build_chat_tool_registry(work_mode=...)` | Add `project_root=runtime.cwd` |
| [runner.py:70](src/aunic/loop/runner.py#L70) | `build_note_tool_registry(work_mode=...)` | Add `project_root=request.active_file.parent` |
| [sdk_tools.py:92](src/aunic/providers/sdk_tools.py#L92) | `build_chat_tool_registry(work_mode=...)` | Add `project_root=self.cwd` |
| [sdk_tools.py:90](src/aunic/providers/sdk_tools.py#L90) | `build_note_tool_registry(work_mode=...)` | Add `project_root=self.cwd` |

### 6. Add compaction support

**File:** [compaction.py](src/aunic/transcript/compaction.py)

Add `"rag_search"` and `"rag_fetch"` to `MODEL_COMPACTION_TOOLS` frozenset. This ensures old RAG results get cleared after 5 more recent calls — the exact same behavior as `web_fetch`.

### 7. Add flattening support

**File:** [flattening.py](src/aunic/transcript/flattening.py)

Add two new branches in `flatten_tool_result_for_provider()`:

```python
if row.tool_name == "rag_search" and isinstance(row.content, list):
    return _flatten_rag_search_results(row.content)
if row.tool_name == "rag_fetch" and isinstance(row.content, dict):
    return _flatten_rag_fetch_result(row.content)
```

- `_flatten_rag_search_results`: format as `title | [source] doc_id | snippet` per line (mirrors `_flatten_search_results`)
- `_flatten_rag_fetch_result`: if `full_text` present → title + full text; otherwise compact metadata (mirrors `_flatten_fetch_summary`)

### 8. Add memory manifest hint

**File:** [memory_manifest.py](src/aunic/tools/memory_manifest.py)

Add to `MEMORY_TOOL_HINTS`:
```python
"rag_search": (
    "rag_search: search the local RAG knowledge base across indexed scopes. "
    "Use scope= to narrow to a specific collection. Returns doc_id, title, snippet, "
    "and score for each hit. Follow up with rag_fetch to retrieve document content. "
    "Reach for this when the user asks about topics likely covered in their indexed "
    "documentation, notes, or reference material."
),
```

No `rag_fetch` entry needed — the `rag_search` hint already directs to it, and `rag_fetch` has its own tool description.

### 9. Update `__init__.py` exports

**File:** [tools/__init__.py](src/aunic/tools/__init__.py)

Add `RagSearchArgs`, `RagFetchArgs`, `build_rag_tool_registry` to imports and `__all__`.

### 10. Update controller error message

**File:** [controller.py](src/aunic/tui/controller.py#L767)

Change the error message at line 767-768 from:
> "RAG not configured. Create ~/.aunic/rag.toml with a [rag] server = ... entry."

to:
> "RAG not configured. Add a \"rag\" section to proto-settings.json with a server URL."

---

## Implementation Order

1. `proto_settings.py` — add `get_rag_config()` (no deps)
2. `rag/config.py` — check proto-settings first (depends on 1)
3. `tools/rag_tools.py` — new file (depends on 1)
4. `transcript/compaction.py` — add to frozenset (independent)
5. `transcript/flattening.py` — add flatten cases (independent)
6. `tools/memory_manifest.py` — add hint (independent)
7. `tools/memory_tools.py` — register RAG tools (depends on 3)
8. `tools/note_edit.py` — thread `project_root` (depends on 7)
9. `chat.py`, `runner.py`, `sdk_tools.py` — pass `project_root` (depends on 8)
10. `tools/__init__.py` — exports (depends on 3)
11. `controller.py` — update error message (independent)

Steps 4–6 and 11 are independent and can be done in any order.

---

## Tests

**New file:** `tests/test_rag_tools.py`

| Test | What it verifies |
|------|-----------------|
| `test_build_rag_tool_registry_no_config` | Returns `()` when no RAG config exists |
| `test_build_rag_tool_registry_with_config` | Returns 2 tools (`rag_search`, `rag_fetch`) |
| `test_rag_search_description_includes_scopes` | Dynamic description lists scope names |
| `test_rag_search_scope_enum_constraint` | Schema `enum` matches config scopes |
| `test_parse_rag_search_args_valid` | Parses `{query, scope, limit}` correctly |
| `test_parse_rag_search_args_missing_query` | Raises `ValueError` |
| `test_parse_rag_fetch_args_valid` | Parses `{doc_id, section}` correctly |
| `test_parse_rag_fetch_args_missing_doc_id` | Raises `ValueError` |
| `test_execute_rag_search_success` | Mock httpx → returns results list |
| `test_execute_rag_search_server_error` | Mock 500 → returns `tool_error` |
| `test_execute_rag_search_connection_error` | Mock connect fail → returns `tool_error` |
| `test_execute_rag_fetch_success` | Mock httpx → `in_memory_content` has `full_text`, `transcript_content` does not |
| `test_execute_rag_fetch_with_section` | Passes section parameter through |
| `test_rag_tools_in_chat_registry` | `build_chat_tool_registry(project_root=...)` includes `rag_search` |
| `test_rag_tools_absent_without_project_root` | `build_chat_tool_registry()` does NOT include `rag_search` |
| `test_memory_manifest_includes_rag_search` | Manifest has `rag_search` bullet when tool is registered |

**Existing tests to update:**

| File | Change |
|------|--------|
| `test_note_edit_tools.py` | Calls to `build_chat_tool_registry` / `build_note_tool_registry` gain `project_root=None` (no behavior change for existing tests) |
| `test_search_transcripts_tool.py` | Same — `project_root=None` on existing calls |
| `test_grep_notes.py` | Same |
| `test_rag_config.py` | Add test for proto-settings fallback path |

**Config test:** `test_get_rag_config_from_proto_settings` — write proto-settings.json with `"rag"` section, verify `get_rag_config()` returns correct `RagConfig`.

---

## Verification

1. Run `pytest` — all existing + new tests pass
2. Add `"rag"` section to `.aunic/proto-settings.json` with the 6 scopes
3. Open Aunic, start a chat session, verify:
   - `rag_search` and `rag_fetch` appear in the model's tool list
   - Memory manifest includes the `rag_search` hint
   - Model can call `rag_search(query="...", scope="docs")` and get results
   - Model can call `rag_fetch(doc_id="...")` and get document content
   - Old `rag_fetch` results get compacted after 5 newer calls
   - `@docs <query>` still works (backward compat via updated `load_rag_config`)
4. Do NOT hit the live server during automated tests — all tests use mocked httpx
