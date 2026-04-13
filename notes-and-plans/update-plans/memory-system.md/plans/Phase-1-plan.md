# Phase 1 — `search_transcripts` + memory manifest

Derived from [roadmap.md](memory-system.md/roadmap.md#phase-1--search_transcripts--memory-manifest) and [memory-system.md](memory-system.md/memory-system.md). Reading the "Guiding principles" and "What already exists in Aunic" sections of the roadmap is a prerequisite for executing this plan.

---

## Context

The memory-system roadmap identifies `search_transcripts` as its keystone: the one model-memory tool no non-Aunic system can replicate, because it exploits Aunic's structured per-file transcript tables. If exactly one thing gets built from the roadmap, it is this. The memory manifest ships in the same phase because a tool the model never reaches for is no memory system at all — the manifest is a short always-on block in the system prompt telling the model when to call each memory tool.

End state after Phase 1: the model can query every Aunic note's transcript by tool name, substring, and path subtree, get back tool_call/tool_result pairs with absolute paths + row numbers, and the manifest in both chat-mode and note-mode system prompts reminds it to do so before proposing destructive work.

---

## User-confirmed scope decisions

1. **Manifest ships in BOTH chat mode and note mode.** Note mode is where destructive bash and edits happen, so the reminder belongs there too. Both surfaces use the same `build_memory_manifest(registry)` helper.
2. **`since=`/`until=` filters are dropped from Phase 1.** `TranscriptRow` has no timestamp field and adding one would require a writer/parser migration that invalidates every existing note. Time filtering returns in a later phase alongside a real timestamp column. **No `timestamp` field on returned hits.** This is a correction to the roadmap's Phase 1 signature; the roadmap should be updated in one sentence once Phase 1 lands.

---

## Pinned design decisions (no further ambiguity)

Decisions below answer the open questions in the roadmap's Phase 1 section. When Phase 1 merges, these answers should be backfilled into the roadmap per its "How to use this roadmap" rule.

### Note discovery
- **Module:** new [src/aunic/discovery.py](../../src/aunic/discovery.py) (top of the package, not folded into `context/`). Phase 2 (`grep_notes`) will depend on it.
- **`is_aunic_note(path)` rules:** (1) sibling `.aunic/` directory exists → True (single `exists()` call); else (2) read up to 64 KiB from file head and run the existing `_TRANSCRIPT_SECTION_RE` pattern from [transcript/parser.py](../../src/aunic/transcript/parser.py). Do NOT invent a looser regex — consistency with the parser is load-bearing.
- **Caching:** module-level OrderedDict LRU (max 4096 entries) keyed on `(resolved_path_str, mtime_ns) -> bool`. Bounded, process-local, cheap to rebuild. Phase 2 reuses this cache.
- **`walk_aunic_notes(root)`:** uses `os.walk(followlinks=False)`. Default root is `Path.home()`. Skips `DEFAULT_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", "target", ".next", ".cache", ".aunic"}` plus any directory whose name begins with `.` except when needed for Rule 1 (we never *descend* into `.aunic/`, we detect it from its parent's siblings). Skips symlinks.

### Result shape
- **Call + result pairs grouped by `tool_id` within a file.** Iterate parsed rows in `row_number` order; for each `type="tool_call"`, look forward for the next row with the same `tool_id` and `type in {"tool_result", "tool_error"}`. Orphaned calls → pair with `result_status="missing"`. Orphaned results → pair with `args_snippet=None`. Plain `type="message"` rows are emitted as pairs only when the query substring matches, with `tool_name=None`, `args_snippet=None`, `result_snippet=<message text>`, `result_status="message"`.
- **Why pairs:** the model needs to see "you ran X → got Y" as one unit. Forcing the model to join rows in context wastes tokens and is error-prone.

### Snippet truncation
- **600 chars per snippet field** (both `args_snippet` and `result_snippet`). JSON-encode with compact separators, truncate, append `"...[truncated]"` when cut. Each hit includes `path` (absolute) and `row_number` so the model opens the full file with its existing `read` tool. **No `read_transcript_row` tool** — trust the model to ask, per memory-system.md.

### Pagination
- **`limit` (default 20, hard-capped at 100) + `offset` (default 0) + `narrow_hint`.** When `total_matches > limit + offset`, set `truncated=True` and populate `narrow_hint` with `"NN matches, showing A–B. Narrow with query=, tool=, scope= or raise limit (max 100)."` Explicit offset beats opaque cursors because the transcript stays human-readable.

### Service instantiation
- **`TranscriptSearchService` is constructed ad hoc inside `execute_search_transcripts`.** It has no network I/O, no credentials, no shared mutable state — just a stateless wrapper around `parse_transcript_rows`. Attaching it to `RunToolContext` would plumb it through `RunToolContext.create`, `ChatModeRunner.__init__`, `ToolLoop.__init__`, and every test fixture for zero behavioral gain. The module-level LRU in `discovery.py` already handles the one thing that would benefit from persistence.
- **Contrast:** `SearchService` (the web one) is runtime-attached because it owns an `httpx.AsyncClient`, a searxng scheduler, and mutable state. `TranscriptSearchService` owns none of that.

### Memory manifest shape and location
- **Literal string built by `build_memory_manifest(registry)` in [src/aunic/tools/memory_manifest.py](../../src/aunic/tools/memory_manifest.py).** Roadmap says "default to literal string, revisit if users complain." Takes the registry so tool names can't drift and the manifest automatically shrinks when tools are absent.
- **Returns `None` when no memory tool is in the registry.** Callers only append the part when non-None.
- **Splice points — identical shape in both:**
  - Chat mode: [modes/chat.py:816](../../src/aunic/modes/chat.py#L816) — `_build_chat_system_prompt`. New part after `f"Available tools: {tool_names or 'none'}."` and before the work-mode clause.
  - Note mode: [loop/runner.py:557](../../src/aunic/loop/runner.py#L557) — `_build_system_prompt` (confirmed identical `parts` list pattern, same `"\n\n".join(...)` assembly). New part in the same relative position.
- **Phase 1 manifest string (exact):**
  ```
  Memory tools. Before proposing destructive bash commands, significant edits, or research that may already have been done in this or another note, check prior sessions:
  - search_transcripts: query past tool calls and results across every Aunic note on this system. Filter by tool= (e.g. "bash", "web_search"), query= (substring over args and results JSON), scope=<path subtree>. Reach for this when the user mentions "last time", "before", or anything time-referential, and before any action that might repeat or contradict past work. Returns absolute path + row_number for each hit so you can open the full file with the read tool.
  ```
- Phase 2 adds a `- grep_notes:` bullet; Phase 3 adds `- read_map:`; Phase 5 adds `- rag_search:`. The preamble stays stable.

### Registry composition
- `search_transcripts` is strictly read-only. Gets added **unconditionally** (every work mode, including `off`) to both registry builders in [tools/note_edit.py](../../src/aunic/tools/note_edit.py):
  - `build_note_tool_registry` at line 41
  - `build_chat_tool_registry` at line 53
- Unconditional placement means the manifest shows up in `off` mode too, which is the mode where the user most wants the model to look things up rather than do things. Load-bearing.

---

## File-level edit list

### New files

| File | LOC | Purpose |
|---|---|---|
| [src/aunic/discovery.py](../../src/aunic/discovery.py) | ~140 | `is_aunic_note`, `walk_aunic_notes`, LRU cache, `DEFAULT_SKIP_DIRS` |
| [src/aunic/transcript/search.py](../../src/aunic/transcript/search.py) | ~220 | `TranscriptSearchService`, `TranscriptSearchHit`, `TranscriptSearchResult`, pairing helpers |
| [src/aunic/tools/search_transcripts.py](../../src/aunic/tools/search_transcripts.py) | ~180 | `SearchTranscriptsArgs`, `parse_search_transcripts_args`, `execute_search_transcripts`, `build_memory_tool_registry` |
| [src/aunic/tools/memory_manifest.py](../../src/aunic/tools/memory_manifest.py) | ~70 | `MEMORY_TOOL_HINTS` dict, `build_memory_manifest(registry)` |
| [tests/fixtures/aunic_notes/](../../tests/fixtures/aunic_notes/) | — | Git-tracked fixture: 3 small fake Aunic notes with transcript rows. Reused by Phase 2. |
| [tests/test_discovery.py](../../tests/test_discovery.py) | ~100 | |
| [tests/test_transcript_search.py](../../tests/test_transcript_search.py) | ~220 | |
| [tests/test_search_transcripts_tool.py](../../tests/test_search_transcripts_tool.py) | ~160 | |
| [tests/test_memory_manifest.py](../../tests/test_memory_manifest.py) | ~90 | Includes both `build_memory_manifest` unit tests and chat/note prompt splice tests. |

### Modified files

| File | Change |
|---|---|
| [src/aunic/tools/__init__.py](../../src/aunic/tools/__init__.py) | Re-export `SearchTranscriptsArgs`, `build_memory_tool_registry`, `build_memory_manifest` alongside existing exports. |
| [src/aunic/tools/note_edit.py:41-60](../../src/aunic/tools/note_edit.py#L41-L60) | Import `build_memory_tool_registry` from `aunic.tools.search_transcripts`. Add `registry.extend(build_memory_tool_registry())` unconditionally near the top of both `build_note_tool_registry` and `build_chat_tool_registry`, before the work-mode branches. |
| [src/aunic/modes/chat.py:816-836](../../src/aunic/modes/chat.py#L816-L836) | Import `build_memory_manifest` from `aunic.tools.memory_manifest`. In `_build_chat_system_prompt`, compute `manifest = build_memory_manifest(registry)` and append `manifest` to `parts` when non-None, positioned after `"Available tools: ..."`. ~4 lines. |
| [src/aunic/loop/runner.py:557-581](../../src/aunic/loop/runner.py#L557-L581) | Symmetric change in `_build_system_prompt`. Same import, same `build_memory_manifest(registry)` call, same positional append. ~4 lines. |

**Total new code:** ~1,200 lines including tests and fixtures. No changes to `tools/runtime.py`, `tools/base.py`, the transcript writer, or the parser.

**Not touched:** the `tools/runtime.py` "register the tool" step listed in the roadmap's Phase 1 "Touches" is incorrect — tools register via `build_*_tool_registry()` builders in `note_edit.py`, not through `runtime.py`. Correct the roadmap when backfilling.

---

## Exact shapes

### `SearchTranscriptsArgs`

```python
@dataclass(frozen=True)
class SearchTranscriptsArgs:
    query: str | None = None
    tool: str | None = None
    scope: str | None = None   # absolute or ~-prefixed path; walker root override
    limit: int = 20            # hard-capped at 100
    offset: int = 0
```

### `input_schema` (literal JSON schema dict, same style as `tools/research.py`)

```python
{
    "type": "object",
    "additionalProperties": False,
    "required": [],
    "properties": {
        "query": {
            "type": "string",
            "description": "Substring to match against tool-call args and results (compact JSON text).",
        },
        "tool": {
            "type": "string",
            "description": "Exact tool name to filter by (e.g. 'bash', 'web_search', 'note_edit').",
        },
        "scope": {
            "type": "string",
            "description": "Absolute path to restrict the walk to a subtree. Defaults to the user home directory.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "description": "Max hits to return. Default 20.",
        },
        "offset": {
            "type": "integer",
            "minimum": 0,
            "description": "Number of hits to skip for pagination. Default 0.",
        },
    },
}
```

### `execute_search_transcripts` signature

```python
async def execute_search_transcripts(
    runtime: RunToolContext,
    args: SearchTranscriptsArgs,
) -> ToolExecutionResult: ...
```

### Returned payload (for both `in_memory_content` and `transcript_content`)

```python
{
    "hits": [
        {
            "path": "/home/user/notes/networking.md",
            "row_number": 42,
            "tool": "bash",
            "tool_id": "call_017",
            "args_snippet": "{\"command\":\"docker compose down && docker volume prune -f\"}",
            "result_snippet": "Volumes removed: 3...[truncated]",
            "result_status": "ok",   # "ok" | "error" | "missing" | "message"
        },
        ...
    ],
    "total_matches": 57,
    "returned": 20,
    "offset": 0,
    "limit": 20,
    "truncated": True,
    "scanned_files": 412,
    "narrow_hint": "57 matches, showing 0-20. Narrow with query=, tool=, scope= or raise limit (max 100).",
}
```

---

## Reused functions (do not reimplement)

- `find_transcript_section(text)` — [transcript/parser.py](../../src/aunic/transcript/parser.py). Used inside `is_aunic_note` Rule 2 fallback.
- `split_note_and_transcript(text)` — [transcript/parser.py](../../src/aunic/transcript/parser.py). Used by `TranscriptSearchService` to isolate the transcript half of each file before parsing.
- `parse_transcript_rows(transcript_text) -> list[TranscriptRow]` — [transcript/parser.py](../../src/aunic/transcript/parser.py). Core parser, reused as-is.
- `TranscriptRow` dataclass — [domain.py:20-30](../../src/aunic/domain.py#L20-L30). Source of row fields.
- `ToolDefinition`, `ToolExecutionResult`, `ToolSpec`, `failure_payload` — [tools/base.py](../../src/aunic/tools/base.py), [tools/runtime.py](../../src/aunic/tools/runtime.py). Tool framework.
- `build_research_tool_registry` pattern — [tools/research.py](../../src/aunic/tools/research.py). The closest existing template; mirror its service-layer split (`tools/research.py` → `research/search.py`) as `tools/search_transcripts.py` → `transcript/search.py`.
- `_FakeRuntime` test pattern — [tests/test_note_edit_tools.py](../../tests/test_note_edit_tools.py). Template for the new tool tests.

---

## Test plan

### `tests/test_discovery.py`
- `test_is_aunic_note_via_sibling_aunic_dir` — note + `.aunic/` sibling → True.
- `test_is_aunic_note_via_transcript_header_in_content` — note with `---\n# Transcript` and no sibling → True.
- `test_is_aunic_note_returns_false_for_plain_markdown` — markdown with neither → False.
- `test_is_aunic_note_caches_on_mtime` — call twice, patch `Path.read_text`, assert reads == 1.
- `test_is_aunic_note_cache_invalidates_on_mtime_change` — touch, call again, assert re-read.
- `test_walk_aunic_notes_skips_default_noise_dirs` — `.git/`, `node_modules/`, `.venv/` each with fake notes → not yielded.
- `test_walk_aunic_notes_respects_scope` — walk subtree, only descendants yield.
- `test_walk_aunic_notes_skips_symlinks` — symlink loop terminates.
- `test_walk_aunic_notes_uses_home_by_default` — monkeypatch `Path.home()`, assert walked.

### `tests/test_transcript_search.py`
- `test_pair_rows_groups_call_and_result` — 2 calls + 2 results → 2 pairs.
- `test_pair_rows_handles_orphaned_call` — call without result → `result_status="missing"`.
- `test_pair_rows_handles_orphaned_result` — result without call → `args_snippet=None`.
- `test_search_filters_by_tool_name` — multi-file fixture, `tool="bash"` → only bash hits.
- `test_search_filters_by_query_in_args` — query matches args JSON.
- `test_search_filters_by_query_in_result` — query matches result JSON.
- `test_search_filters_by_scope_subtree` — files outside `scope` not walked.
- `test_search_truncates_snippets_at_600_chars` — huge JSON payload → snippet ends with `...[truncated]`.
- `test_search_paginates_with_limit_and_offset` — 25 matches, `limit=10 offset=10` → returned 10, `total_matches=25`.
- `test_search_emits_narrow_hint_when_truncated` — `narrow_hint` non-None when truncated.
- `test_search_no_matches_returns_empty_hits_no_hint` — clean empty case.
- `test_search_includes_message_rows_when_query_matches` — plain message row with matching query → `result_status="message"`.
- `test_search_uses_fixture_tree` — point at `tests/fixtures/aunic_notes/`, assert at least one hit per fixture file with a permissive query.

### `tests/test_search_transcripts_tool.py`
- `test_parse_args_rejects_extra_keys` — `{"foo": 1}` raises.
- `test_parse_args_defaults` — empty payload → `limit=20 offset=0`, others `None`.
- `test_parse_args_rejects_limit_over_100` — `limit=500` raises.
- `test_execute_returns_completed_on_success` — `_FakeRuntime`, tmp_path with Aunic notes, assert `status="completed"` and `in_memory_content["hits"]` populated.
- `test_execute_respects_scope_override` — scope in args overrides default walk root.
- `test_execute_returns_tool_error_for_nonexistent_scope` — unknown scope path → `status="tool_error"`.
- `test_build_memory_tool_registry_contains_search_transcripts` — registry smoke.
- `test_search_transcripts_in_chat_registry_off_mode` — `build_chat_tool_registry(work_mode="off")` contains it.
- `test_search_transcripts_in_note_registry_off_mode` — `build_note_tool_registry(work_mode="off")` contains it.
- `test_search_transcripts_in_chat_registry_work_mode` — also present in `work` mode.
- `test_search_transcripts_in_note_registry_work_mode` — also present in `work` mode.

### `tests/test_memory_manifest.py`
- `test_manifest_returns_none_for_empty_registry` — no memory tools → `None`.
- `test_manifest_returns_string_when_search_transcripts_present` — contains `"search_transcripts"` and the literal preamble.
- `test_manifest_only_mentions_present_tools` — registry with only `search_transcripts` does not mention `grep_notes` or `read_map` (forward-compat).
- `test_chat_system_prompt_splices_manifest` — `_build_chat_system_prompt` with a memory-tool registry → output contains `"Memory tools."`.
- `test_chat_system_prompt_omits_manifest_without_memory_tools` — research-only registry → `"Memory tools."` absent.
- `test_note_loop_system_prompt_splices_manifest` — `_build_system_prompt` in runner.py, symmetric.
- `test_note_loop_system_prompt_omits_manifest_without_memory_tools` — symmetric.

### Fixture layout

```
tests/fixtures/aunic_notes/
├── networking/
│   └── bgp-notes.md          # note-content + transcript with bash + web_search rows
├── projects/
│   └── homelab/
│       └── docker-setup.md   # transcript with docker-related bash rows
└── plain-markdown.md         # no transcript; control for negative discovery
```

This fixture tree is git-tracked and reused by Phase 2 (`grep_notes`) tests. Keep each note under 2 KiB so test setup stays fast.

---

## Verification

End-to-end, from a clean checkout:

1. **Unit tests pass:** `uv run pytest tests/test_discovery.py tests/test_transcript_search.py tests/test_search_transcripts_tool.py tests/test_memory_manifest.py -v`
2. **No existing tests broken:** `uv run pytest` full suite.
3. **Linting passes:** `uv run ruff check src/aunic/discovery.py src/aunic/transcript/search.py src/aunic/tools/search_transcripts.py src/aunic/tools/memory_manifest.py`
4. **Manual smoke test:** open Aunic in a directory with 2–3 existing Aunic notes that have non-trivial transcripts. In chat mode, ask the model "what was the last time I ran docker commands?" or similar. Confirm:
   - The model issues a `search_transcripts` tool call (visible in the transcript table).
   - The returned rows include real past tool calls.
   - The tool call and result are both written to the current note's transcript.
5. **Manifest visibility:** enable any debug logging path that captures the system prompt and confirm the `Memory tools.` block is present in both chat and note mode prompts.
6. **Registry presence in `off` mode:** start Aunic with `/off`, type `what tools do you have?` in chat mode, confirm the model lists `search_transcripts` among available tools.

---

## Known risks

1. **`parse_transcript_rows` silently drops malformed rows** ([parser.py:50-63](../../src/aunic/transcript/parser.py#L50-L63)). A user who has hand-edited a transcript and broken a row will see that row absent from search results. Not introduced by Phase 1; Phase 1 only exposes it. Do not fix in Phase 1 — note the limitation in the tool description if desired.
2. **Large transcripts.** 1000 notes × 500 rows × substring scan is well under a second on SSD, but Phase 1 does not cache parsed rows — only `is_aunic_note` results. If this becomes slow in practice, add a second `(path, mtime_ns) -> parsed_rows` cache — but do NOT do it in Phase 1. Measure first.
3. **`scope` validation.** Reject scopes that don't exist or aren't directories. Resolve `~` and relative paths via `Path.expanduser().resolve()`. If a relative scope arrives, resolve it against `runtime.cwd`.
4. **`search_transcripts` finds itself.** Every `search_transcripts` call writes a transcript row that becomes a future match. Model filtering with `tool="search_transcripts"` will return its own history. This is fine and honest — the transcript is the memory — but document it in the tool description so the model learns to narrow with `tool=<other>` when it wants to exclude search history.
5. **Absolute paths in returned hits expose the user's home layout.** This matches every other Aunic tool (`read`, `grep`, `list` all return absolute paths). Don't break from convention.
6. **The transcript header regex is strict.** `_TRANSCRIPT_SECTION_RE = r"(?m)^---\n# Transcript(?:\n|$)"`. Rule 2 of `is_aunic_note` reuses exactly this pattern — do not invent a looser one. Consistency with the parser is more important than catching hand-edited edge cases.

---

## Amendments to backfill into [roadmap.md](memory-system.md/roadmap.md) after Phase 1 merges

Per the roadmap's self-amendment rule, once Phase 1 lands, add one-sentence notes in the roadmap's Phase 1 section:

- Note discovery module lives at `src/aunic/discovery.py` (not `src/aunic/aunic/discovery.py`).
- `is_aunic_note` caches on `(path, mtime_ns)` via a module-level LRU.
- `search_transcripts` omits `since`/`until` and a `timestamp` field in Phase 1; time filtering waits for a real transcript timestamp column.
- The roadmap's "Touches" line `"Edit: tools/runtime.py — register the tool"` is incorrect. Registration happens in `tools/note_edit.py` via the registry builders.
- `search_transcripts` is available in every work mode (including `off`), added unconditionally to both `build_note_tool_registry` and `build_chat_tool_registry`.
- The memory manifest is spliced into BOTH `_build_chat_system_prompt` (modes/chat.py) AND `_build_system_prompt` (loop/runner.py).
