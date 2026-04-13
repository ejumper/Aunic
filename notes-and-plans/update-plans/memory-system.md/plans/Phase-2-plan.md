# Phase 2 — `grep_notes`

Derived from [roadmap.md](../roadmap.md#phase-2--grep_notes) and [memory-system.md](../memory-system.md). Reading the "Guiding principles" and "What already exists in Aunic" sections of the roadmap is a prerequisite for executing this plan. Phase 1 ([Phase-1-plan.md](Phase-1-plan.md)) is a hard prerequisite: `grep_notes` depends on [discovery.py](../../../../src/aunic/discovery.py) (`is_aunic_note`, `walk_aunic_notes`, the LRU cache), on the memory-manifest splice points in [chat.py](../../../../src/aunic/modes/chat.py) and [runner.py](../../../../src/aunic/loop/runner.py), and on the fixture tree at [tests/fixtures/aunic_notes/](../../../../tests/fixtures/aunic_notes/).

---

## Context

Phase 1 gave the model a structured query over transcript tables — the one Aunic-unique memory tool. Phase 2 adds the boring, reliable complement: a ripgrep-shaped tool the model already knows how to use, constrained to Aunic notes only, with an optional half-of-file filter (`section="note-content" | "transcript" | "all"`). The section filter is the Aunic-specific value-add: plain grep over `~` drowns in source code, and even plain grep over notes mixes prose and transcript rows. Splitting by half of the file makes "find where I wrote about BGP" and "find where I actually ran a BGP command" two different queries.

End state after Phase 2: the model can call `grep_notes(pattern="docker compose", section="transcript")` to recover every past execution of a matching shell command, or `grep_notes(pattern="ECMP", section="note-content")` to recover every note-body mention. The manifest in both chat-mode and note-mode tells the model when to reach for it. Everything is visible in the transcript.

---

## User-confirmed scope decisions

*(None yet — these should be surfaced and confirmed before execution. The "Pinned design decisions" section below takes the default for each; mark any you want changed.)*

---

## Pinned design decisions (no further ambiguity)

Decisions below answer the open questions in the roadmap's Phase 2 section. When Phase 2 merges, these answers should be backfilled into the roadmap per its "How to use this roadmap" rule.

### Section splitting strategy

- **Option (a) from the roadmap: split first, then grep only the requested half.** Use `split_note_and_transcript(text)` (already in [transcript/parser.py](../../../../src/aunic/transcript/parser.py#L20)) to isolate each half in-memory, then run the matcher only against the requested half.
- **Line numbers reference the original file.** When section is `"transcript"`, compute `transcript_base_line = note_content.count("\n") + 1 + 1` (the `+1 +1` accounts for the `\n` terminator after note-content and the `---` line that begins the transcript section) and add it to the line-within-transcript of every match. The `split_note_and_transcript` contract returns the note-content with trailing `\n` stripped; derive the base from the original text, not the returned halves, to avoid off-by-one bugs.
- **`section="all"`** greps the full file content and emits `section="note-content"` or `section="transcript"` on each hit by comparing the hit line to the transcript's starting line (from `find_transcript_section`). If the file has no transcript section, every hit is `"note-content"`.
- **No temp files.** Everything happens on strings already in memory.

### Ripgrep vs pure-python

- **Pure-python `re` only.** Mirror the `_grep_fallback` branch of the existing grep tool ([filesystem.py:1023](../../../../src/aunic/tools/filesystem.py#L1023)), not the `rg` branch. Reasons:
  1. The file set is not "everything under root" — it is exactly the output of `walk_aunic_notes(scope)`, which requires a per-file `is_aunic_note` check. Passing that filtered list to `rg` is possible (`--files-from`), but the section filter still requires in-process splitting, which means reading each file twice (once for rg, once for the split). Pure-python reads once.
  2. Aunic ships the `rg` fallback for correctness in environments without ripgrep. Using `rg` here would make `grep_notes` silently slower in the fallback case rather than a uniform speed.
  3. Phase 2's performance target is "reliable under a thousand notes" — well within pure-python's range. Benchmark only if a user reports it slow.
- **If `rg` becomes justified later** (e.g. Phase 3 `/map` scales the note count up), revisit. The external tool shape does not change.

### Match context

- **`context` parameter: default 2, min 0, max 10.** Return `context_before` and `context_after` as lists of raw line strings (no line numbers embedded — those are on each hit object, not on context lines). When a hit is near a file boundary, the lists are shorter accordingly.
- **Context clipped to section bounds when `section != "all"`.** A hit two lines from the top of the transcript half gets one `context_before` line, not `context` lines that spill into note-content. This keeps the "section filter" promise honest.

### Pattern handling

- **`pattern` is a Python regex by default; `literal_text: bool = False` flips it to `re.escape`**. Same shape as the existing `grep` tool.
- **Case sensitivity: default off** (`case_sensitive: bool = False`). When off, compile with `re.IGNORECASE`.
- **Invalid regex → `tool_error` with `category="validation_error", reason="invalid_regex"`.** Do not fall back to literal — that would mask user intent.

### Pagination + truncation

- **`limit` (default 20, hard-capped at 100) + `offset` (default 0)**, same shape as `search_transcripts`. Uniform pagination across memory tools means one mental model for the user.
- **`truncated=True` + `narrow_hint`** when `total_matches > offset + limit`. Hint wording: `"NN matches, showing A–B. Narrow with pattern=, scope=, section= or raise limit (max 100)."`
- **Hard cap on scanned matches to prevent pathological patterns.** If a pattern like `.` matches millions of lines, cap total collection at `10 × limit` (i.e. max 1000 hits collected) and set `narrow_hint` to include `"collection capped at 1000; refine pattern"`. Do not OOM.

### Scope resolution

- **Identical to `search_transcripts`:** `scope=` is an absolute path, a `~`-prefixed path, or a relative path resolved against `runtime.session_state.cwd`. Must exist and be a directory; otherwise `tool_error` with `reason="scope_not_found"` or `"scope_not_directory"`. Default is `walk_aunic_notes(None)` which uses `Path.home()`.
- **Reuse the `walk_aunic_notes` + `is_aunic_note` cache from Phase 1.** No new caching; per-file reads are a single pass.

### Memory manifest entry

- **Add one bullet to [memory_manifest.py](../../../../src/aunic/tools/memory_manifest.py).** Phase 1 already structured `MEMORY_TOOL_HINTS` as a dict keyed on tool name, and `build_memory_manifest` only includes bullets for tools actually present in the registry. Phase 2 adds one key — no builder logic changes.
- **Phase 2 bullet (exact):**
  ```
  grep_notes: ripgrep-shaped content search scoped to Aunic notes only, with an optional section= filter ("note-content", "transcript", or "all"). Use section="transcript" to find past executed commands and tool calls without prose noise; use section="note-content" to find prose mentions without transcript noise. Returns absolute path, line number, and surrounding context. Reach for this when you know a literal phrase or pattern and want to find every note that contains it, or to distinguish "where did I write about X" from "where did I actually do X".
  ```

### Registry composition

- **`grep_notes` is strictly read-only.** Gets added **unconditionally** (every work mode, including `off`) to both `build_note_tool_registry` and `build_chat_tool_registry`. Same placement logic as `search_transcripts`.
- **Aggregator refactor.** Phase 1 put `build_memory_tool_registry()` inside [search_transcripts.py:24](../../../../src/aunic/tools/search_transcripts.py#L24), where it currently returns a 1-tuple containing only the `search_transcripts` tool. That name in that file is a misnomer as of Phase 2 — the aggregator's job is "return every memory tool," not "return the search_transcripts tool." Two reasonable paths:
  - **(A) Move the aggregator to a new [tools/memory_tools.py](../../../../src/aunic/tools/memory_tools.py).** Rename the in-file builder in `search_transcripts.py` to `build_search_transcripts_tool_registry` (1-tuple). Do the same for `grep_notes.py`. `memory_tools.py` imports both and concatenates. Update `tools/note_edit.py` and `tools/__init__.py` imports.
  - **(B) Leave the aggregator in `search_transcripts.py` and extend it.** Import `build_grep_notes_tool_registry` from `grep_notes.py` and concat inside `build_memory_tool_registry`. Minimal churn, but the name-in-file smell gets worse with every phase (Phase 3 adds `read_map`, Phase 5 adds `rag_search`/`rag_fetch`).
- **Pinned: (A).** Do the refactor once, in Phase 2, so Phase 3 and Phase 5 are one-line additions to `memory_tools.py`. The refactor is ~15 moved lines and ~3 import updates; the alternative compounds.

### Result shape

- **`section` on each hit,** set from the splitter (or derived for `section="all"`). This is load-bearing: when the model greps with `section="all"` and sees a mix of hits, it needs per-hit section attribution to decide what to open next.

---

## File-level edit list

### New files

| File | LOC | Purpose |
|---|---|---|
| [src/aunic/tools/grep_notes.py](../../../../src/aunic/tools/grep_notes.py) | ~260 | `GrepNotesArgs`, `parse_grep_notes_args`, `execute_grep_notes`, `build_grep_notes_tool_registry`, internal matcher + section splitter helpers. |
| [src/aunic/tools/memory_tools.py](../../../../src/aunic/tools/memory_tools.py) | ~25 | Aggregator: `build_memory_tool_registry()` concatenates `build_search_transcripts_tool_registry()` and `build_grep_notes_tool_registry()`. |
| [tests/test_grep_notes.py](../../../../tests/test_grep_notes.py) | ~320 | Tool unit + integration tests (see Test plan). |

### Modified files

| File | Change |
|---|---|
| [src/aunic/tools/search_transcripts.py](../../../../src/aunic/tools/search_transcripts.py) | Rename local `build_memory_tool_registry` → `build_search_transcripts_tool_registry`. Nothing else changes. |
| [src/aunic/tools/memory_manifest.py](../../../../src/aunic/tools/memory_manifest.py) | Add `"grep_notes"` key to `MEMORY_TOOL_HINTS` with the exact bullet text pinned above. No builder logic changes. |
| [src/aunic/tools/note_edit.py](../../../../src/aunic/tools/note_edit.py) | Change `from aunic.tools.search_transcripts import build_memory_tool_registry` to `from aunic.tools.memory_tools import build_memory_tool_registry`. The two `build_*_tool_registry` functions already call `build_memory_tool_registry()` unconditionally — no call-site changes. |
| [src/aunic/tools/__init__.py](../../../../src/aunic/tools/__init__.py) | Re-export `GrepNotesArgs`, `build_grep_notes_tool_registry`. Update `build_memory_tool_registry` re-export to point at `memory_tools`. |
| [tests/test_note_edit_tools.py](../../../../tests/test_note_edit_tools.py) | Update `test_build_note_tool_registry_defaults_to_note_and_research_tools` to include `"grep_notes"` in the expected set. Update `test_tool_registries_expand_with_work_mode` similarly if it asserts on memory-tool presence. |
| [tests/test_memory_manifest.py](../../../../tests/test_memory_manifest.py) | Add `test_manifest_includes_grep_notes_when_present` and `test_manifest_only_mentions_present_tools` variants that cover the two-memory-tool case. |

**Total new code:** ~600 lines including tests.

**Not touched:** the transcript parser, the filesystem-grep tool, the research tools, the runtime, the splice points in `chat.py`/`runner.py` (Phase 1 already installed them).

---

## Exact shapes

### `GrepNotesArgs`

```python
@dataclass(frozen=True)
class GrepNotesArgs:
    pattern: str
    section: str = "all"              # "note-content" | "transcript" | "all"
    scope: str | None = None           # absolute / ~-prefixed / relative-to-cwd path
    case_sensitive: bool = False
    literal_text: bool = False
    context: int = 2                   # 0..10
    limit: int = 20                    # hard-capped at 100
    offset: int = 0
```

### `input_schema`

```python
{
    "type": "object",
    "additionalProperties": False,
    "required": ["pattern"],
    "properties": {
        "pattern": {
            "type": "string",
            "description": (
                "Python regex pattern (or literal string if literal_text=true) "
                "to search for inside Aunic notes."
            ),
        },
        "section": {
            "type": "string",
            "enum": ["note-content", "transcript", "all"],
            "description": (
                "Which half of each note to search. 'note-content' skips transcript rows; "
                "'transcript' skips prose. Defaults to 'all'."
            ),
        },
        "scope": {
            "type": "string",
            "description": (
                "Absolute path (or ~-prefixed, or relative to cwd) restricting the walk "
                "to a subtree. Defaults to the user home directory."
            ),
        },
        "case_sensitive": {
            "type": "boolean",
            "description": "Case-sensitive match. Default false.",
        },
        "literal_text": {
            "type": "boolean",
            "description": "Treat pattern as literal text rather than a regex. Default false.",
        },
        "context": {
            "type": "integer",
            "minimum": 0,
            "maximum": 10,
            "description": "Lines of context before and after each match. Default 2.",
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

### `execute_grep_notes` signature

```python
async def execute_grep_notes(
    runtime: RunToolContext,
    args: GrepNotesArgs,
) -> ToolExecutionResult: ...
```

### Returned payload (for both `in_memory_content` and `transcript_content`)

```python
{
    "hits": [
        {
            "path": "/home/user/notes/networking/bgp-notes.md",
            "line": 42,
            "section": "transcript",          # "note-content" | "transcript"
            "match": "| 17 | assistant | tool_call | bash | call_12 | {\"command\":\"docker compose up -d\"} |",
            "context_before": ["| 16 | user | message |  |  | \"run it\" |"],
            "context_after": ["| 18 | tool | tool_result | bash | call_12 | \"Started 3 containers\" |"],
        },
        ...
    ],
    "total_matches": 47,
    "returned": 20,
    "offset": 0,
    "limit": 20,
    "truncated": True,
    "scanned_files": 412,
    "narrow_hint": "47 matches, showing 0-20. Narrow with pattern=, scope=, section= or raise limit (max 100).",
}
```

---

## Reused functions (do not reimplement)

- `walk_aunic_notes(root: Path | None) -> list[Path]` — [discovery.py](../../../../src/aunic/discovery.py). Note discovery, Phase 1.
- `is_aunic_note(path: Path) -> bool` — [discovery.py](../../../../src/aunic/discovery.py). Used transitively via `walk_aunic_notes`; the LRU cache works across both memory tools.
- `find_transcript_section(text) -> tuple[int, int] | None` — [transcript/parser.py](../../../../src/aunic/transcript/parser.py). Used to compute the line offset where the transcript section begins.
- `split_note_and_transcript(text) -> tuple[str, str | None]` — [transcript/parser.py](../../../../src/aunic/transcript/parser.py). Used when `section="note-content"` or `section="transcript"`.
- `ToolDefinition`, `ToolExecutionResult`, `ToolSpec`, `failure_payload`, `failure_from_payload` — [tools/base.py](../../../../src/aunic/tools/base.py), [tools/runtime.py](../../../../src/aunic/tools/runtime.py). Tool framework.
- `build_memory_manifest(registry)` — [tools/memory_manifest.py](../../../../src/aunic/tools/memory_manifest.py). Unchanged; automatically shrinks and grows based on `MEMORY_TOOL_HINTS` keys present in the registry.
- `_FakeRuntime` test pattern — [tests/test_search_transcripts_tool.py](../../../../tests/test_search_transcripts_tool.py) and [tests/test_note_edit_tools.py](../../../../tests/test_note_edit_tools.py). Template for the new tool tests.
- **Fixture tree** — [tests/fixtures/aunic_notes/](../../../../tests/fixtures/aunic_notes/). Reuse without modification. `bgp-notes.md` already contains `docker` and `ip route` transcript rows; `docker-setup.md` contains `docker compose` and `docker volume prune`; `plain-markdown.md` is the negative control. Phase 2 tests exercise the same set.

---

## Implementation outline

1. **Argument parser** in `grep_notes.py` — `_ensure_no_extra_keys`-style check, type-check each optional field, validate `section` enum, validate `context` range, validate `limit` cap. Same style as `parse_search_transcripts_args`.
2. **Scope resolution** — identical to `execute_search_transcripts`: expand `~`, resolve against `runtime.session_state.cwd` if relative, check exists + is_dir, emit `tool_error` if not.
3. **Regex compilation** — try `re.compile(re.escape(pattern) if literal_text else pattern, flags=re.IGNORECASE if not case_sensitive else 0)`; on `re.error`, emit `tool_error` with `reason="invalid_regex"`.
4. **Walk notes** — `walk_aunic_notes(scope_path)`. Track `scanned_files = len(notes)`.
5. **Per-note pass**:
   - Read file text (UTF-8, `errors="replace"`).
   - If `section == "all"`, set `haystack = text`, `base_line = 1`, and derive each hit's `section` by comparing its line to the transcript start line (from `find_transcript_section`).
   - If `section == "note-content"`, use `split_note_and_transcript(text)[0]` as haystack, `base_line = 1`, hit-section = `"note-content"`.
   - If `section == "transcript"`, use `split_note_and_transcript(text)[1]` as haystack (skip file if None), `base_line = count_lines(text) - count_lines(transcript_text) + 1`, hit-section = `"transcript"`. Verify the base_line derivation with a dedicated unit test.
   - Split haystack into lines, iterate with 1-indexed enumeration, apply compiled regex. On match, build a hit with `context_before`/`context_after` clipped to haystack bounds (not the full file), and set `line = base_line + (local_line - 1)`.
   - Stop collecting when `len(all_hits) >= 10 * limit`; set a flag to attach `"collection capped"` to the narrow_hint.
6. **Paginate + build narrow_hint** exactly like `search_transcripts`.
7. **Return payload** as shown above.

**Line-count helper** (internal): `def _count_lines(text: str) -> int: return text.count("\n") + (0 if text.endswith("\n") else 1) if text else 0` — or simpler, `len(text.splitlines())`. Pick one and use it consistently for base_line derivation.

---

## Test plan

### `tests/test_grep_notes.py`

**Argument parsing:**
- `test_parse_args_requires_pattern` — empty payload raises.
- `test_parse_args_defaults` — minimal payload → defaults populated.
- `test_parse_args_rejects_extra_keys` — `{"pattern": "x", "foo": 1}` raises.
- `test_parse_args_rejects_invalid_section` — `section="transcript-only"` raises.
- `test_parse_args_rejects_limit_over_100` — `limit=500` raises.
- `test_parse_args_rejects_context_out_of_range` — `context=50` raises.

**Section splitting + line-number mapping:**
- `test_section_all_returns_both_halves` — pattern that matches in both note-content and transcript → two hits, one with `section="note-content"`, one with `section="transcript"`, both line numbers correct against the original file.
- `test_section_note_content_excludes_transcript_matches` — pattern that only appears in transcript rows → zero hits when `section="note-content"`.
- `test_section_transcript_excludes_note_content_matches` — pattern that only appears in prose → zero hits when `section="transcript"`.
- `test_transcript_section_line_numbers_are_absolute` — hit inside transcript returns a line number that matches the raw file, not the offset-into-transcript. Assert with an explicit expected line number.
- `test_context_clipped_to_section_bounds` — hit on the first transcript line → `context_before` is empty even though the file has prose lines above.

**Matcher behavior:**
- `test_case_insensitive_by_default` — `pattern="DOCKER"` matches `docker compose`.
- `test_case_sensitive_flag_respects_case` — `case_sensitive=True` with mismatched case → no hits.
- `test_literal_text_escapes_regex` — `pattern="."` + `literal_text=True` matches literal dots, not every character.
- `test_invalid_regex_returns_tool_error` — `pattern="[oops"` → `tool_error` with `reason="invalid_regex"`.

**Scope + discovery:**
- `test_scope_walks_only_subtree` — pointing scope at `tests/fixtures/aunic_notes/networking/` → hits only from `bgp-notes.md`.
- `test_scope_rejects_nonexistent_path` — `scope="/nope/not/here"` → `tool_error` with `reason="scope_not_found"`.
- `test_scope_rejects_file_path` — scope pointing at a file → `tool_error` with `reason="scope_not_directory"`.
- `test_plain_markdown_file_is_skipped` — `tests/fixtures/aunic_notes/plain-markdown.md` never appears in any hit, even for a pattern that would match it. Uses the same Aunic-note detection as Phase 1.

**Pagination + truncation:**
- `test_pagination_returns_correct_window` — generate a fixture with many matches (tmp_path, programmatic note writer), `limit=5 offset=5` → 5 hits, `offset=5`, `total_matches=N`.
- `test_narrow_hint_populated_when_truncated` — assert `truncated=True` and `narrow_hint` is a non-empty string containing the total.
- `test_collection_cap_at_10x_limit` — pattern `.` against a large synthetic note → `total_matches <= 10*limit`, `narrow_hint` mentions the cap.
- `test_no_matches_returns_empty_hits_no_hint` — clean empty case, `narrow_hint=None`.

**Execution wrapper:**
- `test_execute_returns_completed_on_success` — `_FakeRuntime`, scope=fixtures → `status="completed"`, `in_memory_content["hits"]` non-empty.
- `test_execute_normalizes_tilde_scope` — `scope="~/some-fake"` → fails cleanly with `scope_not_found`.

**Registry + manifest:**
- `test_build_grep_notes_tool_registry_returns_one_tool` — smoke test.
- `test_grep_notes_in_chat_registry_all_modes` — parametrized over `work_mode in ["off", "read", "work"]`, asserts `grep_notes` present.
- `test_grep_notes_in_note_registry_all_modes` — same for note registry.
- `test_memory_manifest_includes_grep_notes_bullet` — `build_memory_manifest(build_memory_tool_registry())` → string contains `"grep_notes:"`.

### Modifications to existing tests

**`tests/test_note_edit_tools.py`:**
- Update `test_build_note_tool_registry_defaults_to_note_and_research_tools`:
  ```python
  assert tool_names == {
      "note_edit", "note_write",
      "web_search", "web_fetch",
      "search_transcripts", "grep_notes",
  }
  ```
- Update `test_tool_registries_expand_with_work_mode` if it enumerates memory tools.

**`tests/test_memory_manifest.py`:**
- Add `test_manifest_omits_absent_tool_bullets` — registry containing only `search_transcripts` → manifest mentions `search_transcripts` but NOT `grep_notes` (forward-compat proof).
- Add `test_manifest_includes_both_when_both_present` — full memory registry → manifest mentions both bullets, preamble still first.

---

## Verification

1. **Unit tests pass:** `uv run --no-project pytest tests/test_grep_notes.py tests/test_memory_manifest.py tests/test_note_edit_tools.py -v`
2. **No existing tests broken:** `.venv/bin/pytest` full suite. Baseline from Phase 1 end state: 316 passed, 9 skipped.
3. **Linting passes:** `uv run --no-project ruff check src/aunic/tools/grep_notes.py src/aunic/tools/memory_tools.py src/aunic/tools/memory_manifest.py`
4. **Manual smoke test:** in an Aunic session with a handful of real notes:
   - Ask the model "where did I write about BGP?" → model should call `grep_notes(pattern="BGP", section="note-content")`.
   - Ask the model "what docker commands have I run?" → model should call `grep_notes(pattern="docker", section="transcript")`.
   - Both tool calls should appear in the current note's transcript with the expected hits.
5. **Manifest visibility:** enable system-prompt debug logging and confirm the `grep_notes:` bullet appears immediately under the Phase 1 `search_transcripts:` bullet, both in chat mode and note mode prompts.
6. **Registry presence in `off` mode:** start Aunic with `/off` and ask "what tools do you have?" — confirm both `search_transcripts` and `grep_notes` appear in the model's listed tools.

---

## Known risks

1. **Pattern doom-loop.** `pattern="."` against every file on disk would nominally return millions of hits. The `10 * limit` collection cap mitigates OOM; document the cap in the tool description so the model learns to narrow. Do not add doom-loop signature tracking here — `search_transcripts` doesn't, and the cap is the same idea at a different layer.
2. **Large files.** Aunic notes can grow to multi-megabyte when a transcript accumulates thousands of rows. Pure-python line iteration is still fine at this size, but memory usage is O(file-size) per note. A future optimization is to stream line-by-line — defer until measured.
3. **Transcript section line-number math.** Off-by-one bugs here are easy and silent (a grep hit on line 84 that should be 85 is still technically useful). The `test_transcript_section_line_numbers_are_absolute` test is load-bearing — write it first, confirm it fails against a naive implementation, then fix.
4. **Section="all" and hits near the split boundary.** A match on the `---` line or the `# Transcript` heading line: which section is it? Pick a convention (transcript start line is the `---` line → anything `>= transcript_start_line` is `"transcript"`) and document it inline. Test explicitly.
5. **Phase 3 integration with `/map`.** Per the roadmap update at the end of Phase 1, `grep_notes` will be asked in Phase 3 to swap `walk_aunic_notes` for a map read with fallback. Keep the `walk_aunic_notes(scope_path)` call site in `execute_grep_notes` isolated to exactly one line so Phase 3's edit is a one-liner. Do NOT prematurely introduce a `note_source` abstraction — wait until Phase 3 needs it.
6. **Regex injection is not a concern.** The pattern runs in-process with `re.compile`; no shell-out. `re.error` is the only failure mode worth catching.

---

## Amendments to backfill into [roadmap.md](../roadmap.md) after Phase 2 merges

Per the roadmap's self-amendment rule, once Phase 2 lands, add one-sentence notes in the roadmap's Phase 2 section:

- `grep_notes` uses pure-python `re`, not shell-out to `rg`, because the file set is pre-filtered by `is_aunic_note` and the section filter requires in-process splitting.
- Section splitting happens via `split_note_and_transcript` from the existing parser; line numbers map back to the original file with a single base-line offset.
- `grep_notes` is available in every work mode (including `off`), added unconditionally to both `build_note_tool_registry` and `build_chat_tool_registry`.
- The memory-tool aggregator now lives in `src/aunic/tools/memory_tools.py` (moved out of `search_transcripts.py`). Phase 3 (`read_map`) and Phase 5 (`rag_search`/`rag_fetch`) add one line each to this file.
- `MEMORY_TOOL_HINTS` in `memory_manifest.py` gains a `"grep_notes"` key; the builder is unchanged.
- Per-hit `section` attribution is part of the result shape for `section="all"` queries.
