# Plan: Marker-Aware note_write Protection

## Context

Note-mode passes the raw note text (including all marker syntax) to the model via
`note_snapshot_text()`, which means the model can call `note_write` without realising it
is about to destroy hidden content. There are two marker types at issue:

- `%>> ... <<%` ("exclude") — content inside is hidden from the model
- `!>> ... <<!` ("include_only") — only content inside is visible; everything outside is hidden

`execute_note_write` currently writes whatever the model supplies verbatim, with no
awareness of hidden regions. The goal is to:

1. Tell the model where hidden content exists (HTML hint comments in the filtered view)
2. Scope or remove `note_write` based on which markers are active, so the model cannot
   silently destroy content it cannot see

---

## Critical Files

| File | Role |
|---|---|
| `src/aunic/context/markers.py` | Builds `parsed_text` and `source_map`; where hints are injected |
| `src/aunic/context/types.py` | `ParsedNoteFile` dataclass — needs new `hinted_parsed_text` field |
| `src/aunic/tools/runtime.py` | `RunToolContext` — `note_snapshot_text()` and `write_live_note_content` |
| `src/aunic/tools/note_edit.py` | `execute_note_write` — main protection logic lives here |
| `src/aunic/loop/runner.py` | Tool registry + `NOTE_LOOP_SYSTEM_PROMPT` |
| `tests/test_context_markers.py` | Marker analysis tests |
| `tests/test_note_edit_tools.py` | note_write / note_edit tool tests |

---

## Step 1 — Inject HTML hints into parsed text (`markers.py`, `types.py`)

**Why needed:** `note_snapshot_text()` will be fixed (Step 2) to show filtered text. The
model must still know when hidden content exists so it doesn't think it sees the whole note.

**New helper** in `markers.py`:
```
_inject_hidden_hints(
    parsed_text: str,
    source_map: tuple[SourceMapSegment, ...],
    note_text: str,
    marker_spans: tuple[MarkerSpan, ...],
) -> str
```

Logic:
- Walk `source_map` segments in order; track `prev_raw_end = 0`
- At each gap between `prev_raw_end` and `seg.raw_span.start`:
  - If `note_text[gap_start:gap_end].strip()` is non-empty AND the gap overlaps a
    `content_span` from any `MarkerSpan` → insert `<!-- [hidden content] -->` at the
    corresponding position in `parsed_text`
- Also handle the leading gap (before first segment) and trailing gap (after last segment)

Store result as `hinted_parsed_text: str = ""` — add this field to `ParsedNoteFile`
in `types.py`. Call `_inject_hidden_hints` in `analyze_note_file` immediately after the
`_build_parsed_text` call and assign it to `parsed_file`.

---

## Step 2 — Fix `note_snapshot_text()` to use filtered+hinted view (`runtime.py`)

**Current bug:** `note_snapshot_text()` uses `self.working_note_content` (raw text with
markers). The filtered `parsed_text` built by `ContextEngine` is never sent to the model
because `runtime.note_snapshot_text()` is always truthy and short-circuits the `or`
at `runner.py:189`.

**Add field** to `RunToolContext`:
```python
working_parsed_content: str = ""
```

**Initialize** in `RunToolContext.create()`:
```python
working_parsed_content = (
    context_result.parsed_files[0].hinted_parsed_text
    if context_result and context_result.parsed_files
    else note_text
)
```

**Update** in `write_live_note_content()` after the write succeeds: re-run
`analyze_note_file` on the new raw content to produce a fresh `hinted_parsed_text`, then
set `self.working_parsed_content = new_hinted`. Add a lightweight helper
`_reparse_hinted(note_text: str, path: Path) -> str` in `markers.py` (or inline in
`runtime.py`) that creates a minimal snapshot-like object and calls `analyze_note_file`.

**Change `note_snapshot_text()`** to use `self.working_parsed_content` in place of
`self.working_note_content`.

---

## Step 3 — Single `!>> <<!`: scope `note_write` to the span (`note_edit.py`)

In `execute_note_write`, before the default write path, check for include_only spans:

```python
include_spans = [s for s in parsed_file.marker_spans if s.marker_type == "include_only"]

if len(include_spans) == 1:
    span = include_spans[0]
    raw = runtime.working_note_content
    new_raw = raw[: span.open_span.end] + args.content + raw[span.close_span.start :]
    # proceed with write using new_raw instead of args.content
```

The model's content replaces only the bytes inside `!>> ... <<!`; the markers and
everything outside are preserved verbatim.

---

## Step 4 — Multiple `!>> <<!`: remove `note_write` from registry (`runner.py`, `note_edit.py`)

**Registry filter** (proactive — model never sees the tool):

Add `_apply_marker_tool_filter(registry, context_result) -> tuple` in `runner.py`.
Called immediately after the base registry is built (before MCP merge):

```python
base_registry = request.tool_registry or build_note_tool_registry(...)
registry = _apply_marker_tool_filter(base_registry, request.context_result)
```

Filter removes `note_write` when:
- `len(include_only spans) > 1`
- OR any exclude span is classified as "middle" (see Step 5)

**Execute guard** (defensive backup) in `execute_note_write`:
- If multiple include_only spans are present at execution time, return
  `protected_rejection` tool error with message explaining to use `note_edit`.

---

## Step 5 — `%>> <<%` edge/middle classification and reconstruction (`note_edit.py`)

**Classification helper** (can be a module-level function in `note_edit.py` or `markers.py`):

```python
def _classify_exclude_span(
    span: MarkerSpan,
    source_map: tuple[SourceMapSegment, ...],
) -> Literal["top", "bottom", "middle"]:
    has_before = any(seg.raw_span.start < span.open_span.start for seg in source_map)
    has_after  = any(seg.raw_span.end   > span.close_span.end  for seg in source_map)
    if not has_before:
        return "top"
    if not has_after:
        return "bottom"
    return "middle"
```

**Empty-note case** (no visible content at all → `source_map` is empty):
- `has_before` and `has_after` both False → classified as "top"
- Writes are placed beneath all top blocks. This matches the intended behaviour for
  all-hidden notes.

**In `_apply_marker_tool_filter` (runner.py):** remove `note_write` if any exclude span
is "middle".

**In `execute_note_write`:** when exclude spans exist and none are middle:
- Collect top spans, bottom spans
- `top_end   = max(s.close_span.end   for s in top_spans)   if top_spans   else 0`
- `bot_start = min(s.open_span.start  for s in bot_spans)   if bot_spans   else len(raw)`
- Reconstruct:
  ```python
  top_raw = raw[:top_end].rstrip("\n")
  bot_raw = raw[bot_start:].lstrip("\n")
  separator = "\n\n"
  new_raw = separator.join(filter(None, [top_raw, args.content, bot_raw]))
  ```
- Write `new_raw` via `write_live_note_content`

**Execute guard for middle spans:** return `protected_rejection` error with message
explaining to use `note_edit`.

---

## Step 6 — System prompt addition (`runner.py`)

Append to `NOTE_LOOP_SYSTEM_PROMPT`:

```
HTML comments like <!-- [hidden content] --> in the note snapshot mark regions where
content exists in the file but is not visible to you. Do not remove these comments;
they are replaced by the system with the actual hidden content on save.
```

And when `note_write` is absent (filtered out):

The filter function logs its reasoning into the system prompt via `extra_system_prompt`
mechanism already present in `_build_system_prompt`. Add a conditional line there:
```
note_write is unavailable for this note because hidden-content markers prevent safe
full-document replacement. Use note_edit with exact old_string/new_string pairs instead.
```

---

## Verification

**Unit tests** (`tests/test_context_markers.py`):
- `_inject_hidden_hints`: verify comment injected for `%>>hidden<<%`, no comment for
  empty exclude block, correct placement for top/bottom/middle positions

**Unit tests** (`tests/test_note_edit_tools.py`):
- Single `!>> <<!`: verify write replaces only span content, markers and outer text survive
- Multiple `!>> <<!`: verify `protected_rejection` error returned
- `%>> <<%` top: verify block preserved above new content
- `%>> <<%` bottom: verify block preserved below new content
- `%>> <<%` middle: verify `protected_rejection` error returned
- All-hidden note (`source_map` empty): treated same as top, write placed beneath

**Manual smoke test:**
1. Create a note with `%>>some hidden text<<%` at the top and visible content below
2. Trigger note mode with a prompt that would normally cause a full rewrite
3. Confirm hidden block is still present after the write
4. Repeat for `!>> <<!` single and multiple
