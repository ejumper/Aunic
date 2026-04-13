# Phase 3 — `/map` (user command) + `read_map` (model tool)

Derived from [roadmap.md](memory-system.md/roadmap.md#phase-3--map-user-command--read_map-model-tool) and [memory-system.md](memory-system.md/memory-system.md). Reading the "Guiding principles" and "What already exists in Aunic" sections of the roadmap is a prerequisite. Phases 1–2 ([Phase-1-plan.md](memory-system.md/plans/Phase-1-plan.md), [Phase-2-plan.md](memory-system.md/plans/Phase-2-plan.md)) are hard prerequisites: Phase 3 depends on [discovery.py](../../src/aunic/discovery.py) (`walk_aunic_notes`, `is_aunic_note`, the LRU cache), on the aggregator at [tools/memory_tools.py](../../src/aunic/tools/memory_tools.py), on the manifest dict in [tools/memory_manifest.py](../../src/aunic/tools/memory_manifest.py), and on the note-discovery fixture tree at [tests/fixtures/aunic_notes/](../../tests/fixtures/aunic_notes/).

---

## Context

Phases 1–2 gave the model two search surfaces (transcript rows, grep-over-notes) that scale to "a few hundred notes" before their output gets noisy. Phase 3 is the pivot point where the model stops searching and starts *browsing*. `/map` walks every Aunic note on the system, writes a markdown index of path + one-line snippet to `~/.aunic/map.md`, and `read_map` exposes that file to the model as an index-to-read rather than a query-to-run.

The payoff is shape: "read a table of contents, pick a file, read it" is the single most in-distribution memory workflow — it's every codebase-exploration task the model has ever seen. Phases 1–2 already ship the tools for the "search then open" half; Phase 3 adds the "browse then open" half, which is the half users intuitively reach for when they don't yet know the right query.

End state after Phase 3: running `/map` produces a markdown index listing every Aunic note on the user's system with short snippets. The model can call `read_map()` or `read_map(scope="~/work/networking")` to fetch that index (or a subtree of it) and then open individual notes with its existing `read` tool. Snippets refresh automatically on note save unless the user explicitly pinned a summary via `/map --set-summary <text>` or `/map --clear-summary`. The manifest tells the model when to reach for `read_map`. Everything stays visible in the transcript.

---

## User-confirmed scope decisions

*(None yet — surface these before execution if anything below should flip. The "Pinned design decisions" section takes the default for each.)*

1. **`/map --generate-summary` is deferred to a follow-up (call it Phase 3.1).** Reason: it requires an ad-hoc one-shot LLM completion call that doesn't cleanly reuse any existing runtime plumbing (the chat runner, the note loop runner, and the research tools each own their own client paths). Building it cleanly means either (a) plumbing a shared "one-shot completion" helper through `RunToolContext`, or (b) duplicating provider-switching logic inside the map module. Both are larger than the rest of Phase 3 combined. `/map --set-summary <text>` and `/map --clear-summary` ship in Phase 3 — that covers the "user wants to override the auto snippet" case. Model-generated summaries wait.
2. **Dual-file map: global `~/.aunic/map.md` AND local `<cwd>/.aunic/map.md`.** Both are written by `/map`. The global file always contains every Aunic note found under the walk root; the local file is a filtered subset containing only entries whose path is under `cwd`. `read_map` always reads the global map; the local file exists for users who want to commit a project-scoped map to their repo. Authoritative-copy rule: the global map is authoritative; the local map is a cache derived from it on every `/map` run. Never merged in the reverse direction. The "rm -rf ~/.aunic && /map" regenerability invariant still holds because both files are pure functions of the filesystem walk.
3. **Save marks stale; open and close refresh.** Save does NOT rewrite `~/.aunic/map.md` — it writes one byte of metadata (the `auto_snippet_stale` flag in the note's `.meta.json`) and returns. The actual map-entry refresh happens on the next file open or file-switch-away, checking the flag and regenerating the entry only when it fires. This keeps the save path fast (no large-file rewrite), batches rapid edits naturally (saving ten times in a row still only triggers one map update on the next open/close), and still delivers fresh snippets without user intervention.

---

## Pinned design decisions (no further ambiguity)

Decisions below answer the open questions in the roadmap's Phase 3 section. When Phase 3 merges, these answers should be backfilled into the roadmap per its "How to use this roadmap" rule.

### Map file format

Indented markdown with one heading per directory and one bullet per note. A per-entry inline HTML comment carries the mtime used for incremental refresh. This is a TOC shape the model is maximally trained on; it is also trivially grep-able line-by-line for in-place updates.

```markdown
# Aunic note map

Generated: 2026-04-11T17:22:04Z from /home/user (412 notes).

## /home/user/notes/networking/

- [bgp-notes.md](/home/user/notes/networking/bgp-notes.md) — BGP route reflection with ECMP config examples and failover testing notes. <!-- aunic-map mtime=1712345678901234567 -->
- [ospf.md](/home/user/notes/networking/ospf.md) — OSPF area design for the homelab datacenter. <!-- aunic-map mtime=1712345679111111111 -->

## /home/user/notes/homelab/

- [docker-setup.md](/home/user/notes/homelab/docker-setup.md) — Docker compose stack for Traefik + authelia + uptime-kuma. <!-- aunic-map mtime=1712345680000000000 -->
...
```

- **Headings are absolute directory paths** (not trimmed to basename). Model reading the file sees unambiguous paths; passing the full path into `read` works without joining.
- **Bullet label is the note basename as a link to the absolute path**, so clicking the link in a markdown viewer works, and the absolute path is the link target.
- **Snippet is plain text after `— `**, joined on a single line even if the source spans multiple lines. Max 200 chars.
- **`<!-- aunic-map mtime=<ns> -->` is the per-entry sentinel.** Presence marks an entry as auto-generated; absence (or `locked=true` in the sentinel) marks it as user-pinned. Format: `<!-- aunic-map mtime=<nanos> [locked=true] -->`. Parse the sentinel with a single regex; rebuild the entry from scratch when it's missing or stale.
- **Entries are sorted by full path within each heading.** Directories sorted by full path too. Deterministic → `/map` is idempotent.
- **Top matter:** first two lines (`# Aunic note map` + `Generated: ...`) are regenerated on every write. The "(N notes)" count and the walk root are load-bearing for the user ("did my map include the right scope?").

### Snippet extraction

- **Length:** 200 characters, hard cap, ellipsis appended on truncation.
- **Source:** the note-content half of the note (via `split_note_and_transcript`). The transcript half is never used for the auto snippet — the value of the snippet is "what is this note *about*," not "what did I run in it."
- **Frontmatter handling:** Aunic does not currently parse YAML frontmatter anywhere (grepped `src/aunic/` → zero hits). Pin: **strip a leading `---\n...\n---\n` block if present**, using a simple regex, then take the first 200 chars of the remainder. Do not try to parse YAML — this is best-effort cleanup, not real frontmatter support.
- **Whitespace:** collapse runs of whitespace (including newlines) to single spaces before truncating. Empty note → literal `"(empty)"`.
- **Determinism:** the auto snippet is a pure function of `(note_content, 200)`. `rm -rf ~/.aunic && /map` reproduces identical output for every auto-snippetted entry.

### Per-note metadata schema (new, first introduced here)

Lives at `<note_parent>/.aunic/<note_stem>.meta.json`. This is the Phase-3-introduced schema the roadmap's "Cross-cutting / Per-note metadata" section refers to.

```json
{
  "version": 1,
  "summary": "Custom user-written summary text...",
  "summary_locked": true,
  "last_auto_snippet": "BGP route reflection with ECMP config...",
  "last_indexed_mtime_ns": 1712345678901234567
}
```

- `version` — integer, starts at 1. Readers ignore unknown fields.
- `summary` — `str | None`. When non-null and `summary_locked=true`, this string is the entry's snippet in map.md (truncated to 200 chars on write).
- `summary_locked` — `bool`. True iff the summary was set via `/map --set-summary`. Auto-refresh on save never overwrites a locked entry.
- `last_auto_snippet` — `str | None`. The last auto-generated snippet. Used to detect no-op saves (save with unchanged snippet → skip map.md rewrite).
- `last_indexed_mtime_ns` — `int | None`. mtime of the note when the snippet was last computed. Used by `/map` to skip files whose mtime is unchanged since the last walk.

- `auto_snippet_stale` — `bool`. Set to `True` by the save hook to flag that the note content has changed since the last map entry was written. Cleared to `False` when the map entry is refreshed on the next file open or file-switch-away.

Missing file → treat as all-None defaults. Malformed file → log warning once, treat as missing. Never crash on a bad meta file.

### `/map` command surface

Dispatched from `send_prompt` in [controller.py](../../src/aunic/tui/controller.py) following the same pattern as `/include`, `/exclude`, `/isolate`. Remainder text after `/map` is parsed into one of five forms:

| Form | Behavior |
|---|---|
| `/map` (empty remainder) | Walk from `Path.home()`, rewrite `~/.aunic/map.md` (all entries) and `<cwd>/.aunic/map.md` (entries under cwd). Status: `"Mapped N notes."`. |
| `/map <path>` | Walk from `<path>` (resolved against cwd, `~` expanded). Rewrites `~/.aunic/map.md` (subtree entries + preserved out-of-scope entries) and `<path>/.aunic/map.md` (subtree entries only). Status: `"Mapped N notes under <path>."`. |
| `/map --set-summary <text>` | Active note only. Writes `summary=<text>`, `summary_locked=true` to its `.meta.json`. If `~/.aunic/map.md` exists, updates the one entry in-place. Status: `"Summary locked for <basename>."`. |
| `/map --clear-summary` | Active note only. Writes `summary=null`, `summary_locked=false`. Regenerates the entry's auto snippet on the spot. If `~/.aunic/map.md` exists, updates the one entry in-place. Status: `"Summary cleared for <basename>."`. |
| `/map --generate-summary` | **Not implemented in Phase 3.** Returns error: `"--generate-summary is deferred to a follow-up; use --set-summary <text> for now."` The flag is recognized so the user gets a clear error instead of `/map --generate-summary <text>` being parsed as "walk subtree at --generate-summary". |

Parsing is dumb string-split — first token after `/map` decides the subcommand. No argparse.

### Incremental `/map` semantics

On `/map`:

1. Read the existing `~/.aunic/map.md` if present, parse sentinels into `{path: (mtime_ns, snippet, locked)}` dict.
2. `walk_aunic_notes(scope)` → list of note paths.
3. For each note:
   - Load its `.meta.json` (if present).
   - If `meta.summary_locked and meta.summary`: the map entry is the pinned summary.
   - Elif note's current mtime == `prev.mtime_ns` from the old map: reuse `prev.snippet` verbatim (skip reading the file).
   - Else: read the file, compute auto snippet, write `meta.last_auto_snippet` + `meta.last_indexed_mtime_ns` back to `.meta.json`.
4. For `/map <path>` (subtree walk): also keep every entry from the old map whose path is NOT under `<path>`, unchanged. This preserves out-of-scope entries rather than clobbering them.
5. Sort, render, atomic-write via `tmp + os.replace`.

This gives `/map` over a mature tree the fast path: every unchanged note is a dict lookup and a string copy; only touched notes pay the read cost.

### Save-hook: mark stale

On every save of an Aunic note, the save path does exactly one thing for the map: set `meta.auto_snippet_stale = True` and persist the meta file. It does NOT touch `~/.aunic/map.md`.

Splice into `_persist_active_file_text` in [controller.py:2061](../../src/aunic/tui/controller.py#L2061) immediately after the successful `write_text`:

```python
try:
    from aunic.map.builder import mark_map_entry_stale
    await mark_map_entry_stale(self.state.active_file)
except Exception as exc:
    logger.warning("map stale-mark on save failed: %s", exc)
```

`mark_map_entry_stale(note_path)` contract:
- If file is not an Aunic note → return immediately.
- If meta has `summary_locked=True` → return immediately (pinned entries don't go stale).
- Else: load meta (defaults if missing), set `auto_snippet_stale=True`, persist meta.
- Never raises through the save path.

### Open/close refresh

The map entry is rebuilt from the current file content on:
- **File open** — at the end of `_load_active_file` in [controller.py:1148](../../src/aunic/tui/controller.py#L1148), after the file is loaded into editor state.
- **File switch away** — at the start of `_switch_active_file` in [controller.py:1193](../../src/aunic/tui/controller.py#L1193), before switching to the new file (so we use the still-loaded content of the outgoing file).

Both call `refresh_map_entry_if_stale(note_path)` with the same best-effort wrapper:

```python
async def refresh_map_entry_if_stale(note_path: Path) -> None:
    """No-op when:
    - ~/.aunic/map.md does not exist.
    - note is not an Aunic note.
    - meta.auto_snippet_stale is False.
    - summary_locked is True.
    Reads the file, computes new snippet, updates map.md, clears flag.
    Never raises."""
```

Implementation: load meta; if `not meta.auto_snippet_stale` → return. Read file, compute snippet. Parse map.md into in-memory dict, update the one entry, render, atomic-write. Write updated meta (`auto_snippet_stale=False`, `last_auto_snippet=<new>`, `last_indexed_mtime_ns=<now>`).

**Why this shape beats inline-on-save:**
- Rapid saves (autosave, vim-style `:w` loops) all converge to one flag-set; only the final open/close pays the map.md rewrite cost.
- No debounce scheduling, no cancellation on shutdown.
- Batches naturally: save 50 times, open once → one map update.

### `read_map` model tool

Shape mirrors `web_fetch` — small arg surface, returns content verbatim.

```python
@dataclass(frozen=True)
class ReadMapArgs:
    scope: str | None = None   # absolute / ~-prefixed / relative-to-cwd path
```

- `scope=None` → returns the full `~/.aunic/map.md` as a string.
- `scope=<path>` → returns a filtered map containing only entries whose absolute path is under the resolved scope. Filtering is done by walking the parsed map dict, selecting matching keys, and re-rendering. The model sees the same format as the full map — just with fewer sections.
- **Map not present** → returns `tool_error` with `reason="map_not_built"` and a message telling the user to run `/map`. The model should surface this to the user rather than silently falling back — the whole point is that the user controls when the walk happens.
- **Scope not a directory / does not exist** → `tool_error` with `reason="scope_not_found"` or `"scope_not_directory"`, same as the other memory tools.
- **No fallback to walking.** If the user wants live walking, they should use `grep_notes` or `search_transcripts`, both of which walk on every call. `read_map` is specifically the "read the pre-built index" tool — it should fail loudly when the index is missing so the workflow stays visible.

### `search_transcripts` / `grep_notes` integration with the map

Per the roadmap, Phase 3 is supposed to swap `walk_aunic_notes(scope)` for a map read inside both Phase-1/2 tools, with `walk_aunic_notes` as the fallback. Pin: **yes, do this, but keep it behind a tiny internal helper so the call sites change by one line each**.

- New helper in `discovery.py`: `resolve_note_set(scope: Path | None) -> list[Path]`. Behavior:
  1. If `~/.aunic/map.md` exists and is newer than 120 hours (wall-clock): parse it, return the list of paths filtered by scope, skipping any that no longer exist on disk.
  2. Else: **rebuild the map by calling `build_map(scope)`**, then re-read the freshly-written map and return its paths. This ensures the map is always up-to-date before any memory-tool query; no silent stale-set issue.
- `search_transcripts.py` and `grep_notes.py` each change one line (`walk_aunic_notes(scope_path)` → `resolve_note_set(scope_path)`).
- The 120-hour staleness bound is a module-level constant `_MAP_STALENESS_SECONDS = 120 * 3600` at the top of `discovery.py`. Tunable without a schema change.
- **Rebuild on stale:** calling `build_map(scope)` from inside `resolve_note_set` is a side effect (writes to disk) inside a function that looks like a pure read. This is intentional: the map is a cache; a miss repairs the cache rather than silently degrading. The cost is paid once per 120-hour window per scope, then amortized across all subsequent queries.
- **The external behavior of both tools does not change.** Same argument surface, same result shape. Queries are faster when the map is fresh; they self-heal when the map is stale.

### Memory manifest entry

Add one `"read_map"` key to `MEMORY_TOOL_HINTS` in [memory_manifest.py](../../src/aunic/tools/memory_manifest.py). Exact bullet:

```
read_map: read the user's pre-built index of every Aunic note on this system (~/.aunic/map.md). Each entry is a path + short summary. Reach for this when you do not yet know a specific query or phrase to search for, and want to browse the user's notes by topic. Pass scope=<path> to get only the subtree relevant to the current task. If the index is missing, tell the user to run /map.
```

No builder logic changes — `build_memory_manifest` already dynamically includes present keys.

### Registry composition

- `read_map` is strictly read-only. Add **unconditionally** (every work mode, including `off`) via the Phase 2 aggregator. One-line change to `memory_tools.py`: append `*build_read_map_tool_registry()` to the return tuple.
- No changes to `tools/note_edit.py` — the aggregator is already imported there.
- No changes to `tools/__init__.py` beyond re-exports.

### PROMPT_ACTIVE_COMMANDS + prompt-command regex

Both need `"/map"` added. Currently at [rendering.py:17-23](../../src/aunic/tui/rendering.py#L17-L23):

```python
PROMPT_ACTIVE_COMMANDS = frozenset({..., "/map"})
_PROMPT_COMMAND_RE = re.compile(
    r"(@web\b|/context\b|...|/isolate\b|/map\b)"
)
```

One-line additions to both. No new dispatch machinery.

---

## File-level edit list

### New files

| File | LOC | Purpose |
|---|---|---|
| [src/aunic/map/__init__.py](../../src/aunic/map/__init__.py) | ~10 | Re-exports. |
| [src/aunic/map/manifest.py](../../src/aunic/map/manifest.py) | ~120 | `NoteMetadata` dataclass, `load_meta(note_path)`, `save_meta(note_path, meta)`, `meta_path_for(note_path)`. Per-note `.meta.json` read/write with the version-1 schema. |
| [src/aunic/map/snippet.py](../../src/aunic/map/snippet.py) | ~70 | `compute_auto_snippet(note_content: str, max_len: int = 200) -> str`. Strips leading YAML frontmatter, collapses whitespace, truncates. Pure function, no I/O. |
| [src/aunic/map/render.py](../../src/aunic/map/render.py) | ~180 | `MapEntry` dataclass, `parse_map(text: str) -> dict[Path, MapEntry]`, `render_map(entries: dict[Path, MapEntry], *, walk_root: Path, generated_at: datetime) -> str`. The sentinel regex lives here. |
| [src/aunic/map/builder.py](../../src/aunic/map/builder.py) | ~200 | `build_map(scope: Path | None) -> BuildResult`, `refresh_map_entry_on_save(note_path: Path) -> None`, `set_summary(note_path, text)`, `clear_summary(note_path)`. All the I/O orchestration. `~/.aunic/map.md` path constant. |
| [src/aunic/tools/read_map.py](../../src/aunic/tools/read_map.py) | ~130 | `ReadMapArgs`, `parse_read_map_args`, `execute_read_map`, `build_read_map_tool_registry`. Follows the `web_fetch` template. |
| [tests/test_map_snippet.py](../../tests/test_map_snippet.py) | ~80 | Pure unit tests for `compute_auto_snippet`. |
| [tests/test_map_render.py](../../tests/test_map_render.py) | ~150 | Unit tests for `parse_map` / `render_map` round-trips and sentinel handling. |
| [tests/test_map_builder.py](../../tests/test_map_builder.py) | ~300 | Integration tests for `/map` subcommands, incremental refresh, meta file interaction. |
| [tests/test_map_save_hook.py](../../tests/test_map_save_hook.py) | ~120 | Tests for `refresh_map_entry_on_save` — no-op when map missing, no-op when locked, no-op when snippet unchanged, real update when changed. |
| [tests/test_read_map_tool.py](../../tests/test_read_map_tool.py) | ~180 | `ReadMapArgs` parsing, `execute_read_map` behavior, scope filtering, missing-map error, registry presence, manifest inclusion. |

### Modified files

| File | Change |
|---|---|
| [src/aunic/discovery.py](../../src/aunic/discovery.py) | Add `resolve_note_set(scope: Path | None) -> list[Path]` and `_MAP_STALENESS_SECONDS = 120 * 3600`. Stale map triggers `build_map(scope)` before returning paths. ~50 LoC. Existing `walk_aunic_notes` and `is_aunic_note` unchanged. |
| [src/aunic/tools/search_transcripts.py](../../src/aunic/tools/search_transcripts.py) | One-line: swap `walk_aunic_notes(scope_path)` → `resolve_note_set(scope_path)`. Adjust import. |
| [src/aunic/tools/grep_notes.py](../../src/aunic/tools/grep_notes.py) | One-line: swap `walk_aunic_notes(scope_path)` → `resolve_note_set(scope_path)`. Adjust import. |
| [src/aunic/tools/memory_tools.py](../../src/aunic/tools/memory_tools.py) | Add `*build_read_map_tool_registry()` to the returned tuple and import it. ~2 LoC. |
| [src/aunic/tools/memory_manifest.py](../../src/aunic/tools/memory_manifest.py) | Add `"read_map"` key to `MEMORY_TOOL_HINTS` with the exact bullet text pinned above. ~8 LoC. |
| [src/aunic/tools/__init__.py](../../src/aunic/tools/__init__.py) | Re-export `ReadMapArgs`, `build_read_map_tool_registry`. ~4 LoC. |
| [src/aunic/tui/controller.py](../../src/aunic/tui/controller.py) | Add `/map` dispatch branch in `send_prompt` (~40 LoC). Splice `mark_map_entry_stale` into `_persist_active_file_text` after successful write (~5 LoC). Splice `refresh_map_entry_if_stale` into `_load_active_file` (open) and `_switch_active_file` (switch-away) (~6 LoC each). |
| [src/aunic/tui/rendering.py](../../src/aunic/tui/rendering.py) | Add `"/map"` to `PROMPT_ACTIVE_COMMANDS` and `/map\b` to `_PROMPT_COMMAND_RE`. ~2 LoC. |
| [tests/test_search_transcripts_tool.py](../../tests/test_search_transcripts_tool.py) | Add one test asserting `resolve_note_set` is called (or behavior with a pre-existing map). Keep existing tests passing. |
| [tests/test_grep_notes.py](../../tests/test_grep_notes.py) | Same — one test covering the map-fast-path integration. |
| [tests/test_memory_manifest.py](../../tests/test_memory_manifest.py) | Add `test_manifest_includes_read_map_when_present`. |

**Total new code:** ~1,600 lines including tests.

**Not touched:** the transcript parser, the filesystem grep tool, the research tools, the tool runtime, `tools/base.py`, the chat/note-loop splice points (Phase 1 installed them and they remain unchanged).

---

## Exact shapes

### `ReadMapArgs`

```python
@dataclass(frozen=True)
class ReadMapArgs:
    scope: str | None = None   # absolute / ~-prefixed / relative-to-cwd path
```

### `input_schema`

```python
{
    "type": "object",
    "additionalProperties": False,
    "required": [],
    "properties": {
        "scope": {
            "type": "string",
            "description": (
                "Optional absolute path (or ~-prefixed, or relative to cwd) restricting "
                "the map to a subtree. When omitted, returns the full map."
            ),
        },
    },
}
```

### `execute_read_map` signature

```python
async def execute_read_map(
    runtime: RunToolContext,
    args: ReadMapArgs,
) -> ToolExecutionResult: ...
```

### Returned payload on success

```python
{
    "map_path": "/home/user/.aunic/map.md",
    "content": "# Aunic note map\n\nGenerated: 2026-04-11T17:22:04Z from /home/user (412 notes).\n\n## /home/user/notes/networking/\n\n- [bgp-notes.md](...) — BGP route reflection...\n...",
    "entry_count": 412,            # or filtered count when scope is set
    "walk_root": "/home/user",     # from the top-matter line
    "scope_applied": None,         # or the resolved scope path
    "generated_at": "2026-04-11T17:22:04Z",
}
```

### `MapEntry`

```python
@dataclass(frozen=True)
class MapEntry:
    path: Path              # absolute path to the note
    snippet: str            # already truncated to <=200 chars
    mtime_ns: int           # from filesystem at index time; 0 for locked entries
    locked: bool            # True iff this entry came from a pinned summary
```

### `NoteMetadata`

```python
@dataclass
class NoteMetadata:
    version: int = 1
    summary: str | None = None
    summary_locked: bool = False
    auto_snippet_stale: bool = False
    last_auto_snippet: str | None = None
    last_indexed_mtime_ns: int | None = None
```

### `/map` build result

```python
@dataclass(frozen=True)
class BuildResult:
    map_path: Path
    entry_count: int
    walk_root: Path
    entries_added: int
    entries_updated: int
    entries_removed: int
    entries_reused_from_cache: int
    elapsed_seconds: float
```

Status string surfaced via `_set_status`: `f"Mapped {count} notes (+{added} -{removed}, {reused} unchanged) in {elapsed:.1f}s."`

---

## Reused functions (do not reimplement)

- `walk_aunic_notes(root)` + `is_aunic_note(path)` — [discovery.py](../../src/aunic/discovery.py). Phase 1 primitives, unchanged.
- `find_transcript_section(text)` + `split_note_and_transcript(text)` — [transcript/parser.py](../../src/aunic/transcript/parser.py). Used by `compute_auto_snippet` to get the note-content half before truncating.
- `ToolDefinition`, `ToolExecutionResult`, `ToolSpec`, `failure_payload` — [tools/base.py](../../src/aunic/tools/base.py), [tools/runtime.py](../../src/aunic/tools/runtime.py). Tool framework.
- `build_memory_manifest(registry)` — [tools/memory_manifest.py](../../src/aunic/tools/memory_manifest.py). Unchanged; picks up `read_map` via the existing dict lookup.
- `build_memory_tool_registry()` — [tools/memory_tools.py](../../src/aunic/tools/memory_tools.py). The Phase 2 aggregator. One-line extension.
- `_set_status` / `_set_error` — [tui/controller.py:1519](../../src/aunic/tui/controller.py#L1519). Status surface for `/map` output.
- `_persist_active_file_text` — [tui/controller.py:2061](../../src/aunic/tui/controller.py#L2061). Save-hook splice point.
- `_FakeRuntime` test pattern — [tests/test_search_transcripts_tool.py](../../tests/test_search_transcripts_tool.py) + [tests/test_grep_notes.py](../../tests/test_grep_notes.py). Template for `read_map` tests.
- **Fixture tree** — [tests/fixtures/aunic_notes/](../../tests/fixtures/aunic_notes/). Reuse as-is for the builder integration tests. Phase 3 does not add fixtures; per-test synthetic trees via `tmp_path` cover the incremental refresh and save-hook cases.
- **`~/.aunic/` path pattern** — [tui/app.py:1721](../../src/aunic/tui/app.py#L1721) (`tui_prefs.json`). Template for `~/.aunic/map.md` read/write with `parent.mkdir(parents=True, exist_ok=True)`.

---

## Implementation outline

1. **`aunic.map.snippet`:** pure function. Regex to detect leading `---\n...\n---\n` frontmatter. Strip it. Run `split_note_and_transcript` on the remainder. Collapse whitespace with `re.sub(r"\s+", " ", text).strip()`. Truncate at 200, append `"…"` if cut. Empty → `"(empty)"`.

2. **`aunic.map.manifest`:** `meta_path_for(note)` computes `note.parent / ".aunic" / f"{note.stem}.meta.json"`. `load_meta` returns a `NoteMetadata` with defaults on missing/malformed. `save_meta` writes JSON with `indent=2`, `parent.mkdir(parents=True, exist_ok=True)`. Version-1 schema.

3. **`aunic.map.render`:** Sentinel regex: `r"<!-- aunic-map mtime=(\d+)(?: locked=true)? -->"`. `parse_map(text)` reads line-by-line, tracking the current `## <dir>` heading, matching `- [basename](path) — snippet <!-- ... -->` against an entry regex, building `{Path: MapEntry}`. `render_map(entries, walk_root, generated_at)` groups by parent directory, sorts, emits heading+bullets. Round-trip invariant: `parse_map(render_map(e)) == e`.

4. **`aunic.map.builder`:**
   - `MAP_PATH = Path.home() / ".aunic" / "map.md"`.
   - `build_map(scope)`:
     1. Read `MAP_PATH` if present → `prev_entries`.
     2. `walk_aunic_notes(scope or Path.home())`.
     3. For each note, build a `MapEntry`: if meta is locked, use meta.summary; elif prev entry mtime matches current mtime, reuse prev snippet; else read file, compute snippet, persist to meta.
     4. Preserve out-of-scope prev entries when `scope is not None`.
     5. Render + atomic write to `MAP_PATH` (`tmp + os.replace`).
     6. Also write a scoped copy to `local_map_path = (scope or cwd) / ".aunic" / "map.md"`, containing only entries under that directory. Create `.aunic/` if needed. Skip if `local_map_path == MAP_PATH`.
     7. Return `BuildResult`.
   - `mark_map_entry_stale(note_path)`: if not Aunic note or `summary_locked` → return. Load meta, set `auto_snippet_stale=True`, persist. No map.md I/O.
   - `refresh_map_entry_if_stale(note_path)`: load meta; if `not meta.auto_snippet_stale` or `summary_locked` → return. Read file, compute snippet. If snippet == `meta.last_auto_snippet` and mtime unchanged → clear flag, return. Parse MAP_PATH, update entry, render, atomic-write. Persist meta (`stale=False`, updated snippet + mtime).
   - `set_summary(note_path, text)`: update meta with `summary=text[:200], summary_locked=True, auto_snippet_stale=False`. If MAP_PATH exists, update the entry in-place and rewrite.
   - `clear_summary(note_path)`: set `summary=None, summary_locked=False`, recompute auto snippet, persist meta, update MAP_PATH entry.

5. **`aunic.tools.read_map`:** `parse_read_map_args` validates `scope` string (or `None`). `execute_read_map`:
   - If `MAP_PATH` missing → `tool_error` with `reason="map_not_built"`, human-readable message suggesting `/map`.
   - Read map content.
   - If `scope` provided: resolve (`~`, relative-to-cwd), validate exists + is_dir, `parse_map` + filter + `render_map` subset.
   - Return `ToolExecutionResult` with `in_memory_content` = payload dict above, `transcript_content` = compact version.
   - `build_read_map_tool_registry` returns a 1-tuple.

6. **`discovery.resolve_note_set`:**
   ```python
   _MAP_STALENESS_SECONDS = 120 * 3600

   def resolve_note_set(scope: Path | None) -> list[Path]:
       map_path = Path.home() / ".aunic" / "map.md"
       try:
           stat = map_path.stat()
           map_is_fresh = (time.time() - stat.st_mtime) < _MAP_STALENESS_SECONDS
       except OSError:
           map_is_fresh = False

       if not map_is_fresh:
           # Build (or rebuild) the map, then fall through to read it below.
           from aunic.map.builder import build_map
           build_map(scope)  # writes MAP_PATH; returns BuildResult (ignored here)

       try:
           from aunic.map.render import parse_map
           entries = parse_map(map_path.read_text(encoding="utf-8"))
           paths = [p for p in entries if p.exists()]
           if scope is not None:
               scope_resolved = scope.resolve()
               paths = [p for p in paths if _is_under(p, scope_resolved)]
           return paths
       except OSError:
           # Map still not readable (e.g. build_map failed silently). True fallback.
           return walk_aunic_notes(scope)
   ```
   Lazy imports break the module cycle. The `walk_aunic_notes` fallback is the last resort only (e.g. filesystem not writable), not the normal stale path.

7. **`/map` dispatch in `send_prompt`:** parse remainder token-by-token, dispatch to `build_map` / `set_summary` / `clear_summary` / the deferred error. Always surface status or error via `_set_status` / `_set_error`. Wrap every call in try/except and surface exceptions as error-indicator messages, never let them bubble through to the TUI crash path.

8. **Controller splices (three locations):**
   - **Save** (`_persist_active_file_text`, after `write_text`):
     ```python
     try:
         from aunic.map.builder import mark_map_entry_stale
         await mark_map_entry_stale(self.state.active_file)
     except Exception as exc:
         logger.warning("map stale-mark on save failed: %s", exc)
     ```
   - **Open** (`_load_active_file`, after editor state is populated):
     ```python
     try:
         from aunic.map.builder import refresh_map_entry_if_stale
         await refresh_map_entry_if_stale(self.state.active_file)
     except Exception as exc:
         logger.warning("map refresh on open failed: %s", exc)
     ```
   - **Switch away** (`_switch_active_file`, before the new file is loaded):
     ```python
     try:
         from aunic.map.builder import refresh_map_entry_if_stale
         await refresh_map_entry_if_stale(self.state.active_file)
     except Exception as exc:
         logger.warning("map refresh on switch failed: %s", exc)
     ```
   All three use lazy imports to avoid a circular import at controller startup.

---

## Test plan

### `tests/test_map_snippet.py`

- `test_empty_note_returns_empty_marker` — `""` → `"(empty)"`.
- `test_frontmatter_stripped_before_truncation` — `"---\ntitle: X\n---\nHello world"` → `"Hello world"`.
- `test_no_frontmatter_passes_through` — `"Hello world"` → `"Hello world"`.
- `test_malformed_frontmatter_treated_as_content` — `"---\nno closing"` → `"--- no closing"` (collapse whitespace, don't strip).
- `test_transcript_section_excluded_from_snippet` — note with `---\n# Transcript\n| row |` → snippet is the note-content half only.
- `test_whitespace_collapsed` — `"foo\n\n\nbar  baz"` → `"foo bar baz"`.
- `test_truncation_at_200_chars_appends_ellipsis` — 500-char note → exactly 201 chars (200 + `"…"`).
- `test_exactly_200_chars_no_ellipsis` — 200-char note returned verbatim.

### `tests/test_map_render.py`

- `test_render_parse_round_trip_single_entry` — entry → text → parsed entries equals original dict.
- `test_render_groups_by_directory` — three notes in two dirs → two `## <dir>` headings in path order.
- `test_render_sorts_entries_within_directory` — entries rendered sorted by basename.
- `test_sentinel_regex_matches_mtime` — parse `<!-- aunic-map mtime=1234567890123456789 -->` → mtime_ns=1234567890123456789, locked=False.
- `test_sentinel_regex_matches_locked` — parse `<!-- aunic-map mtime=0 locked=true -->` → locked=True.
- `test_parse_skips_lines_without_sentinel` — hand-edited stray line → gracefully skipped.
- `test_parse_handles_multiline_snippet_fallback` — if a snippet got a literal newline (corruption), treat the entry as malformed, skip.
- `test_render_includes_generated_timestamp_and_count` — top matter asserted.

### `tests/test_map_builder.py`

**Fresh build:**
- `test_build_map_writes_file_with_entries` — tmp_path with 3 fake Aunic notes → `MAP_PATH` contains 3 entries with correct paths.
- `test_build_map_skips_plain_markdown` — plain `.md` file without `.aunic/` sibling or transcript header → not in output.
- `test_build_map_honors_scope_argument` — scope=subdir → only subdir notes in result (plus preserved out-of-scope entries when prev map existed).
- `test_build_map_creates_aunic_dir_if_missing` — `~/.aunic/` does not exist → created.
- `test_build_map_atomic_write` — simulate write failure mid-way → no partial file (tmp+replace).

**Incremental build:**
- `test_incremental_reuses_unchanged_entries` — build once, touch nothing, build again → `entries_reused_from_cache == 3`, `entries_updated == 0`. File contents byte-identical except for the `Generated:` timestamp line.
- `test_incremental_regenerates_on_mtime_change` — build once, write new content to one note, build again → that entry's snippet differs, others reused.
- `test_incremental_adds_new_note` — create new note after first build, rebuild → `entries_added == 1`.
- `test_incremental_removes_deleted_note` — delete a note after first build, rebuild → `entries_removed == 1`.

**Locked summaries:**
- `test_set_summary_locks_and_writes_meta` — call `set_summary(note, "pinned text")` → meta file has `summary_locked=True, summary="pinned text"`.
- `test_set_summary_truncates_to_200` — 500-char text → `meta.summary` length ≤ 200.
- `test_locked_entry_survives_incremental_rebuild` — lock, modify note content, rebuild → map entry is still the locked text, not the new auto snippet.
- `test_clear_summary_restores_auto` — lock, then clear, then rebuild → map entry is the auto snippet again.
- `test_set_summary_updates_existing_map_in_place` — build map, set summary, map file is rewritten with the locked entry.

### `tests/test_map_save_hook.py`

Tests cover both the stale-mark (save) path and the refresh-if-stale (open/close) path.

**`mark_map_entry_stale` (save-side):**
- `test_mark_stale_sets_flag_in_meta` — call on an Aunic note → meta file gains `auto_snippet_stale=True`.
- `test_mark_stale_noop_on_plain_markdown` — non-Aunic file → no meta file created.
- `test_mark_stale_noop_when_summary_locked` — locked meta → `auto_snippet_stale` remains False.
- `test_mark_stale_creates_meta_if_missing` — no prior meta file → meta file is created with `stale=True`.
- `test_mark_stale_never_touches_map_md` — map.md exists → its mtime is unchanged after mark_stale.
- `test_mark_stale_never_raises` — patch meta write to raise → helper returns normally; warning logged.

**`refresh_map_entry_if_stale` (open/close-side):**
- `test_refresh_noop_when_stale_flag_false` — `stale=False` in meta → map.md unchanged.
- `test_refresh_noop_when_map_missing` — stale=True but no map.md → returns without raising, no file created.
- `test_refresh_noop_when_summary_locked` — stale=True but locked → map.md unchanged.
- `test_refresh_updates_entry_when_stale` — stale=True, map.md exists with old snippet → after refresh, entry has new snippet and `stale=False`.
- `test_refresh_clears_flag_after_update` — verify meta.auto_snippet_stale is False after successful refresh.
- `test_refresh_noop_when_snippet_unchanged` — stale=True but computed snippet equals last_auto_snippet and mtime unchanged → meta flag cleared, map.md content unchanged.
- `test_refresh_never_raises` — patch map write to raise → helper returns normally; warning logged.

### `tests/test_read_map_tool.py`

- `test_parse_args_defaults` — `{}` → `ReadMapArgs(scope=None)`.
- `test_parse_args_rejects_extra_keys` — `{"foo": 1}` raises.
- `test_parse_args_accepts_scope` — `{"scope": "/tmp/notes"}` → `ReadMapArgs(scope="/tmp/notes")`.
- `test_execute_returns_full_map_when_present` — tmp_path map → `status="completed"`, payload contains the file content, `scope_applied=None`.
- `test_execute_tool_error_when_map_missing` — no map → `status="tool_error"`, `reason="map_not_built"`.
- `test_execute_filters_by_scope` — map with entries in two dirs, scope=one dir → only that dir's entries in content.
- `test_execute_tool_error_on_nonexistent_scope` — scope points at missing path → `tool_error` with `reason="scope_not_found"`.
- `test_execute_tool_error_on_file_scope` — scope points at a file → `tool_error` with `reason="scope_not_directory"`.
- `test_execute_normalizes_tilde_scope` — `scope="~/nowhere"` → fails cleanly with `scope_not_found`.
- `test_build_read_map_tool_registry_returns_one_tool` — smoke.
- `test_read_map_in_chat_registry_all_modes` — parametrized across `work_mode in ["off", "read", "work"]`.
- `test_read_map_in_note_registry_all_modes` — same for note registry.
- `test_memory_manifest_includes_read_map_bullet` — `build_memory_manifest(build_memory_tool_registry())` contains `"read_map:"`.

### Modifications to existing tests

**`tests/test_search_transcripts_tool.py`:**
- Add `test_search_transcripts_uses_map_when_fresh` — build a fake map in tmp_path with two entries (mtime=now), patch `MAP_PATH` and `_MAP_STALENESS_SECONDS`; assert `resolve_note_set` returns the mapped paths without walking the filesystem.
- Add `test_search_transcripts_rebuilds_map_when_stale` — patch map mtime to 121 hours ago; assert `build_map` is called and `resolve_note_set` returns paths from the freshly rebuilt map.

**`tests/test_grep_notes.py`:**
- Add the same two tests, mirrored for `grep_notes`.

**`tests/test_memory_manifest.py`:**
- Add `test_manifest_includes_all_three_when_present` — full memory registry (Phase 1 + 2 + 3) → manifest contains `search_transcripts:`, `grep_notes:`, `read_map:`, in that order.

### Not tested in Phase 3

- `/map --generate-summary` (deferred).
- Model-generated summaries end-to-end (no LLM call).
- Debouncing of the save-hook (not implemented).
- Local `<cwd>/.aunic/map.md` (not written).

---

## Verification

1. **Unit tests pass:** `uv run --no-project pytest tests/test_map_snippet.py tests/test_map_render.py tests/test_map_builder.py tests/test_map_save_hook.py tests/test_read_map_tool.py tests/test_memory_manifest.py -v`
2. **No existing tests broken:** full suite. Baseline from Phase 2 end state: 357 passed, 9 skipped.
3. **Linting passes:** `uv run --no-project ruff check src/aunic/map/ src/aunic/tools/read_map.py src/aunic/discovery.py src/aunic/tools/memory_manifest.py src/aunic/tools/memory_tools.py`
4. **Manual smoke test — fresh map:**
   - Start Aunic in a directory with 5+ real Aunic notes.
   - Type `/map` → status bar shows `"Mapped N notes (+N -0, 0 unchanged) in X.Xs."`.
   - Inspect `~/.aunic/map.md` — verify format matches the pinned schema, one heading per directory, bullet per note, sentinels present.
   - Type `/map` again → status shows `"(0 added, N reused)"`, file content byte-identical except `Generated:` line.
5. **Manual smoke test — summary lock:**
   - With an active note open, type `/map --set-summary This is my pinned summary`.
   - Status: `"Summary locked for <basename>."`.
   - Inspect `<note_parent>/.aunic/<note_stem>.meta.json` → verify `summary_locked=true`.
   - Inspect `~/.aunic/map.md` → verify the entry now shows the pinned text.
   - Edit the note, save. Inspect the map → entry unchanged (lock honored).
   - Type `/map --clear-summary` → meta unlocked, map entry reverts to auto snippet.
6. **Manual smoke test — save hook:**
   - With a mapped note open, edit it, save.
   - Inspect `~/.aunic/map.md` → that entry's snippet reflects the new content; mtime sentinel updated.
   - Other entries untouched.
7. **Manual smoke test — `read_map`:**
   - Ask the model "what notes do I have about networking?" → model should call `read_map(scope="~/notes/networking")` or `read_map()` → the tool call appears in the current note's transcript with the map contents.
   - Delete `~/.aunic/map.md`, ask the model the same question → model sees the `map_not_built` error and surfaces it to the user.
8. **Manifest visibility:** enable system-prompt debug and confirm the `read_map:` bullet appears below `grep_notes:` in both chat-mode and note-mode prompts.
9. **Registry in `off` mode:** start Aunic with `/off`, ask "what tools do you have?" → model lists `search_transcripts`, `grep_notes`, and `read_map`.
10. **`search_transcripts` fast-path:** create a large fake tree of 1000 Aunic notes in a tmp dir, run `/map`, then run a `search_transcripts` call — inspect logs or time the call vs. a no-map run. The map-present path should be at least 2× faster on a cold cache.

---

## Known risks

1. **Sentinel corruption on hand-edit.** A user who hand-edits `~/.aunic/map.md` and mangles a sentinel will lose the fast-path for that one entry on the next `/map` — the entry is rebuilt from scratch, no data loss. Acceptable: the map is a cache. Document this inline in the map's top matter (`# Aunic note map — auto-generated, hand edits are safe but may force entry rebuilds`).
2. **Open/close refresh latency on large maps.** `refresh_map_entry_if_stale` parses and rewrites the full map on file open/switch. For a 10k-note map (~2 MB), parse + render + write is still sub-second on a modern disk. Not debounced — each open pays at most one rewrite (only if the stale flag is set). If users report sluggish open, the follow-up is line-patching. Do not optimize prematurely.
3. **`resolve_note_set` staleness bound.** 120 hours is tunable via `_MAP_STALENESS_SECONDS`. If too short, every memory-tool query triggers a rebuild; if too long, the model searches a stale note set. 120h (5 days) assumes a "run /map once a week" cadence. Adjust to taste.
4. **`/map` walking `~` on first run.** First `/map` on a home directory with many gigabytes of repos will be slow (dominated by the `os.walk`, not by Aunic detection). Phase 1's `DEFAULT_SKIP_DIRS` already skips `.git`, `node_modules`, `.venv`, etc., so walls are mostly hit on large source trees that legitimately contain many real directories. The `/map` status message shows progress only at the end — do NOT add mid-walk progress in Phase 3 (that requires threading through the os.walk iterator). Measure; if this is a problem, a follow-up can add a periodic status update.
5. **Meta files cluttering `.aunic/`.** Every mapped note gets a `.meta.json` sibling. For a 1000-note tree, that's 1000 small files under `.aunic/` directories. Disk-wise this is fine (ext4 handles it; btrfs handles it); `ls` on a crowded `.aunic/` is ugly. Not a Phase 3 blocker — the `.aunic/` dir is internal.
6. **`read_map` with a filter creates a new file?** No — `read_map(scope=...)` parses and re-renders in-memory, never writes to disk. Sanity-check with a test that confirms the file mtime is unchanged after a scoped read.
7. **Race between `/map` and save-hook.** User runs `/map` while a save is in flight. Both are writing to `~/.aunic/map.md`. The `os.replace` at the end of each is atomic, so the last writer wins and neither operation corrupts the file. Worst case: the save-hook's update is lost and the next `/map` rebuilds it. Not a Phase 3 blocker — document inline.
8. **Circular import risk.** `discovery.resolve_note_set` needs `aunic.map.render.parse_map`; `aunic.map.builder` needs `discovery.walk_aunic_notes`. Use a function-local import inside `resolve_note_set` to break the cycle. Tested by `tests/test_map_builder.py` importing both modules at the top.

---

## Amendments to backfill into [roadmap.md](memory-system.md/roadmap.md) after Phase 3 merges

Per the roadmap's self-amendment rule, once Phase 3 lands, add one-sentence notes in the roadmap's Phase 3 section and in "Cross-cutting":

- Phase 3 writes both `~/.aunic/map.md` (global) and `<cwd>/.aunic/map.md` (local, entries under cwd only). `read_map` reads the global map; the local file is for users who want to commit a project-scoped map to their repo.
- Map file format is markdown: `## <dir>` heading per directory, `- [name](path) — snippet <!-- aunic-map mtime=<ns> -->` bullet per note. The sentinel is the per-entry staleness cache.
- Incremental updates: `/map` reads the previous map's sentinels; unchanged mtime → reuse snippet verbatim; changed → re-read and recompute.
- Per-note metadata lives at `<note_parent>/.aunic/<note_stem>.meta.json` with a v1 schema: `summary`, `summary_locked`, `auto_snippet_stale`, `last_auto_snippet`, `last_indexed_mtime_ns`.
- Auto snippet is 200 chars max, taken from the note-content half (via `split_note_and_transcript`), with leading `---\n...\n---\n` frontmatter stripped and whitespace collapsed.
- Save-hook marks `auto_snippet_stale=True` in meta (no map.md I/O). Map entry refreshes on next file open (`_load_active_file`) or file switch-away (`_switch_active_file`).
- `/map --generate-summary` is deferred to a follow-up; `/map --set-summary <text>` + `/map --clear-summary` cover the pin/unpin cases.
- `search_transcripts` and `grep_notes` use `discovery.resolve_note_set(scope)`, which uses a fresh (`<120h`) `~/.aunic/map.md` when available, or calls `build_map(scope)` to rebuild it when stale/missing, then returns paths from the map. `walk_aunic_notes` is the last-resort fallback only (e.g. filesystem not writable).
- `read_map` fails loudly with `reason="map_not_built"` when no map exists — it does not walk or rebuild as a fallback.
