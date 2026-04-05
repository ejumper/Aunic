# Implementation Guidance

## Status
This phase is implemented. Current Aunic renders transcript history in a dedicated transcript pane instead of exposing the raw markdown table directly in the note editor.

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
    - notes-and-plans/zfuture-features/* features that the user wanted to make note of but are not being implemented yet, ignore these.

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

# Phase 5: Transcript Rendering — Implementation Plan

## Context

Phases 1-4 are complete. Aunic has a working transcript system: rows are parsed from a markdown table (`TranscriptRow` with role, type, tool_name, tool_id, content), written to disk, translated to API messages, and used by the run loop. However, the TUI still shows the **raw markdown table** in the editor TextArea alongside the note content. Phase 5 replaces that raw table display with a human-readable rendered transcript view — chat messages in a 67/33 column layout, tool results in specialized collapsed/expanded formats, filter/sort controls, and row deletion.

---

## Architecture

### Core idea: split the single editor into note-editor + transcript-view

Currently `app.py` line 147 places a single `self.editor` TextArea in the root `HSplit`. That editor shows `_full_text` (note-content + raw transcript table) with folds applied.

After Phase 5:
- The editor TextArea shows **only note-content** (everything above `---\n# Transcript`)
- A new `TranscriptView` widget below the editor renders parsed `TranscriptRow` objects as styled `FormattedText`
- The transcript view is conditionally visible (hidden when no transcript exists)

### Rendering pattern: `FormattedTextControl` in a scrollable `Window`

This matches the existing `WebSearchView` pattern (`web_search_view.py`):
- A `Window` wraps a `FormattedTextControl` whose `text` callable returns `StyleAndTextTuples`
- Mouse handlers attached as the 3rd tuple element for interactive elements (X delete, toggles, links)
- `scrollbar=True` for scrolling long transcripts

### Dispatch-based rendering

A dispatcher maps `(row.type, row.tool_name)` to renderer functions. Each renderer returns `StyleAndTextTuples` for that row. `tool_call` rows are **always hidden** (only `tool_result`/`tool_error` rows are rendered).

---

## New Files

### 1. `src/aunic/tui/transcript_view.py` (~300-400 lines)

The main transcript view widget. Contains:
- `TranscriptView` class — owns `Window` + `FormattedTextControl`, orchestrates rendering
- `_render()` method — the text callable that builds full formatted output
- Filter/sort toolbar rendering
- Mouse handler dispatch (delegates to renderers)
- `_build_tool_call_index()` — maps `tool_id -> tool_call TranscriptRow` (needed by bash/search renderers to extract command/query from hidden tool_call rows)

### 2. `src/aunic/tui/transcript_renderers.py` (~400-500 lines)

Individual renderer functions, one per row-type category:
- `render_chat_message()` — 67/33 column split, borders
- `render_tool_result()` — 2-column (tool_name | content) for default tools
- `render_bash_result()` — collapsed/expanded with command from tool_call row
- `render_search_result()` — collapsed/expanded dropdown with individual results
- `render_fetch_result()` — single row with title/snippet/link
- `render_delete_button()` — the "X" prefix fragment
- `render_filter_toolbar()` — the filter/sort control bar
- `TranscriptRenderContext` dataclass — shared rendering state

---

## Files to Modify

### 3. `src/aunic/tui/types.py`
- Add `TranscriptFilter = Literal["all", "chat", "tools", "search"]`
- Add `TranscriptSortOrder = Literal["descending", "ascending"]`
- Add `TranscriptViewState` dataclass with: `filter_mode`, `sort_order`, `expanded_rows: set[int]`

### 4. `src/aunic/tui/rendering.py`
- Add transcript styles to `build_tui_style()`:
  - `transcript.border`, `transcript.assistant`, `transcript.user`
  - `transcript.tool.name`, `transcript.tool.content`
  - `transcript.error` (ansired)
  - `transcript.delete` (ansired)
  - `transcript.toggle` (ansibrightblack)
  - `transcript.link` (ansiblue underline), `transcript.link.cached` (ansiblue underline bold)
  - `transcript.filter`, `transcript.filter.active` (reverse)
  - `transcript.sort`, `transcript.sort.active` (reverse)
  - `transcript.bash.command` (ansigreen)
  - `transcript.search.count` (ansiblue bold), `transcript.search.snippet` (ansibrightblack italic)
  - `transcript.fetch.snippet` (ansibrightblack italic)

### 5. `src/aunic/tui/controller.py`

**New fields:**
- `self._transcript_rows: list[TranscriptRow] = []`
- `self._transcript_text: str | None = None` — raw transcript section for reconstruction
- `self._note_content_text: str = ""` — note portion only
- `self.transcript_view_state: TranscriptViewState`
- `self._cached_fetch_urls: set[str] = set()` — populated from filesystem cache manifest

**Modified methods:**

`_load_active_file()` (line 471):
- After loading `_full_text`, call `split_note_and_transcript()` to split into `_note_content_text` and `_transcript_text`
- Parse `_transcript_text` into `_transcript_rows` via `parse_transcript_rows()`
- Apply folds **only** to `_note_content_text`, not `_full_text`
- Sync editor with note-content only

`_sync_editor_from_full_text()` (line 533) → rename to `_sync_editor_from_note_content()`:
- Apply folds to `_note_content_text` instead of `_full_text`
- Rest stays the same (cursor preservation, fold detection, buffer update)

`_handle_editor_buffer_changed()` (line 391):
- Reconstruct note-content from editor buffer (same as now)
- Reconstruct `_full_text` by joining note-content + `_transcript_text` (instead of treating the whole buffer as `_full_text`)

**New methods:**
- `has_transcript() -> bool` — `len(self._transcript_rows) > 0`
- `visible_transcript_rows() -> list[TranscriptRow]` — applies filter + sort from `transcript_view_state`
- `tool_call_index() -> dict[str, TranscriptRow]` — `{row.tool_id: row for row in self._transcript_rows if row.type == "tool_call"}`
- `toggle_transcript_expand(row_number: int)` — toggles in `transcript_view_state.expanded_rows`
- `cycle_transcript_filter()` — cycles all → chat → tools → search → all
- `toggle_transcript_sort()` — flips descending ↔ ascending
- `delete_transcript_row(row_number: int)` — calls `delete_row_by_number()` from `transcript/writer.py` on `_full_text`, then reloads by re-splitting and re-parsing. Also saves file.
- `_refresh_cached_fetch_urls()` — scans `~/.cache/aunic/fetch/<note-hash>/manifest.json` for known URLs

### 6. `src/aunic/tui/app.py`

**Layout change** (line 143-150):
Replace `self.editor` in the root HSplit with a new `note_and_transcript` container:
```python
self.transcript_view = TranscriptView(controller=self.controller, width=self._editor_width)

self.note_and_transcript = HSplit([
    self.editor,
    ConditionalContainer(
        content=HSplit([
            Window(height=1, char="─", style="class:md.thematic"),
            self.transcript_view.window,
        ]),
        filter=Condition(lambda: self.controller.has_transcript()),
    ),
])
# In root HSplit, replace self.editor with self.note_and_transcript
```

**Focus toggle** (line 416-422):
Change `_toggle_focus_between_editor_and_prompt()` to cycle: editor → transcript → prompt → editor (3-way when transcript exists, 2-way when it doesn't).

**Key bindings:**
- When transcript view has focus: up/down to scroll, Enter/Space to toggle expand, Delete to delete row
- `f` filter cycling, `s` sort toggling (only when transcript focused)
- Add transcript view to `_editing_text_area_has_focus` / navigation conditions as appropriate

**Dimensions:**
- Add `_refresh_transcript_dimensions()` method to set transcript view height based on row count
- Call it from `_invalidate()`

---

## Step-by-Step Implementation Order

### Step 1: Types and styles (no dependencies)
**Files:** `types.py`, `rendering.py`

1a. Add to `types.py`:
```python
TranscriptFilter = Literal["all", "chat", "tools", "search"]
TranscriptSortOrder = Literal["descending", "ascending"]

@dataclass
class TranscriptViewState:
    filter_mode: TranscriptFilter = "all"
    sort_order: TranscriptSortOrder = "descending"
    expanded_rows: set[int] = field(default_factory=set)
```

1b. Add all `transcript.*` styles to `build_tui_style()` in `rendering.py`.

### Step 2: Render context and renderer functions
**Files:** `transcript_renderers.py` (NEW)

Create `TranscriptRenderContext`:
```python
@dataclass(frozen=True)
class TranscriptRenderContext:
    width: int
    tool_call_index: dict[str, TranscriptRow]  # tool_id -> tool_call row
    expanded_rows: set[int]                     # row_numbers currently expanded
    cached_fetch_urls: set[str]                 # URLs with cached pages
```

Implement renderers in this order:

**2a. `render_filter_toolbar(state, width) -> StyleAndTextTuples`**
- Render `[ Chat ] [ Tools ] [ Search ] | [ Descending ]` bar
- Active filter/sort gets `class:transcript.filter.active`
- Each button fragment gets a mouse handler

**2b. `render_delete_button(row_number) -> StyleAndTextTuples`**
- Returns `("class:transcript.delete", " X ", mouse_handler)` fragment
- Mouse handler on MOUSE_UP calls deletion callback

**2c. `render_chat_message(row, context) -> StyleAndTextTuples`**
- Calculate `content_width = int(context.width * 0.67)`
- Wrap text content into `content_width` characters using `textwrap.wrap()`
- **Assistant**: left-aligned — delete button + `│` border + content + `│` border + empty right column
- **User**: right-aligned — delete button + empty left column + `│` border + content + `│` border
- Top/bottom borders with `┌─┐` / `└─┘` box-drawing characters on the populated column only

**2d. `render_tool_result(row, context) -> StyleAndTextTuples`**
- 2-column: `[X] [tool_name] | [result content]`
- Tool name in `class:transcript.tool.name`, content truncated to 3 lines
- `tool_error` rows: content in `class:transcript.error`

**2e. `render_bash_result(row, context) -> StyleAndTextTuples`**
- Extract command from `context.tool_call_index[row.tool_id].content` (the `"command"` key)
- If multi-line command: show first line + `...`
- **Collapsed** (default): `[X] bash | $ <command> | [v]`
- **Expanded** (when `row.row_number in context.expanded_rows`):
  - Command line + toggle `[^]`
  - Parse `row.content` as structured JSON: extract `stdout`, `stderr`, `exit_code`
  - Show stdout capped at 25 lines + `[... N more lines]` if truncated
  - If `stderr` non-empty: show in `class:transcript.error`
  - If `exit_code != 0` or `row.type == "tool_error"`: error styling on exit code line
- Toggle button `[v]`/`[^]` with mouse handler

**2f. `render_search_result(row, context) -> StyleAndTextTuples`**
- Extract query from `context.tool_call_index[row.tool_id].content` (the `"queries"` or `"query"` key)
- Result list is `row.content` (list of dicts with `url`, `title`, `snippet`)
- **Collapsed**: `[X] <count> | <query> | [v]`
- **Expanded**:
  - Header row + toggle `[^]`
  - Per result: `| <n> | <title> | <snippet> | [link] |`
  - Title in `class:transcript.link.cached` if URL in `context.cached_fetch_urls`, else normal
  - `[link]` button (`↗`) with mouse handler to open URL

**2g. `render_fetch_result(row, context) -> StyleAndTextTuples`**
- Extract `title`, `snippet`, `url` from `row.content` (dict)
- Single row: `[X] <title> | <snippet> | [link]`
- Title in `class:transcript.link.cached` if URL in `context.cached_fetch_urls`
- `[link]` button opens URL

**2h. Dispatch function:**
```python
def get_renderer(row: TranscriptRow):
    if row.type == "tool_call":
        return None  # always hidden
    if row.type == "message":
        return render_chat_message
    if row.type in ("tool_result", "tool_error"):
        match row.tool_name:
            case "bash": return render_bash_result
            case "web_search": return render_search_result
            case "web_fetch": return render_fetch_result
            case _: return render_tool_result
    return None
```

### Step 3: TranscriptView widget
**Files:** `transcript_view.py` (NEW)

**Depends on:** Steps 1, 2

3a. Create `TranscriptView` class following `WebSearchView` pattern:
```python
class TranscriptView:
    def __init__(self, controller, *, width=None):
        self._controller = controller
        self._width = width or (lambda: 100)
        self.window = Window(
            FormattedTextControl(text=self._render, focusable=True, show_cursor=False),
            height=Dimension(preferred=15, max=40, min=3),
            dont_extend_height=True,
            scrollbar=True,
        )
```

3b. Implement `_render() -> StyleAndTextTuples`:
1. Get `visible_transcript_rows()` from controller
2. Build `tool_call_index` from controller
3. Build `TranscriptRenderContext`
4. Render filter toolbar at top
5. For each visible row: dispatch to renderer, concatenate fragments
6. Return complete fragments

3c. Implement row-level fragment caching:
- Dict `{row_number: (content_hash, expanded, fragments)}`
- Only re-render a row if content or expanded state changed
- Invalidate cache on any transcript reload

### Step 4: Controller modifications
**Files:** `controller.py`

**Depends on:** Steps 1, 3

4a. Add new imports at top:
```python
from aunic.transcript.parser import split_note_and_transcript, parse_transcript_rows
from aunic.transcript.writer import delete_row_by_number
from aunic.tui.types import TranscriptViewState
```

4b. Add new fields in `__init__`:
```python
self._transcript_rows: list[TranscriptRow] = []
self._transcript_text: str | None = None
self._note_content_text: str = ""
self.transcript_view_state = TranscriptViewState()
self._cached_fetch_urls: set[str] = set()
```

4c. Modify `_load_active_file()` (line 471):
After `self._full_text = snapshot.raw_text`:
```python
self._note_content_text, self._transcript_text = split_note_and_transcript(self._full_text)
if self._transcript_text:
    self._transcript_rows = parse_transcript_rows(self._transcript_text)
else:
    self._transcript_rows = []
```
Then change fold detection and `_sync_editor_from_full_text` to operate on `_note_content_text` only.

4d. Rename `_sync_editor_from_full_text` → `_sync_editor_from_note_content`:
- Replace `self._full_text` with `self._note_content_text` for fold detection and editor buffer
- Update all call sites (lines 370, 492, 637)

4e. Modify `_handle_editor_buffer_changed()` (line 391):
```python
def _handle_editor_buffer_changed(self, _event) -> None:
    if self._syncing_editor or self._editor_buffer is None:
        return
    self._clear_recent_change_highlight()
    note_content = reconstruct_full_text(
        self._editor_buffer.text,
        self._fold_render.placeholder_map,
    )
    # Reconstruct full text by joining note-content with transcript
    if self._transcript_text:
        self._full_text = note_content.rstrip("\n") + "\n\n" + self._transcript_text
    else:
        self._full_text = note_content
    self.state.editor_dirty = self._full_text != self._last_saved_text
    self._invalidate()
```

4f. Add new methods:
- `has_transcript()` — `bool(self._transcript_rows)`
- `visible_transcript_rows()` — filter by `transcript_view_state.filter_mode`, sort by `transcript_view_state.sort_order`
  - `"chat"` filter: rows where `row.type == "message"`
  - `"tools"` filter: rows where `row.type in ("tool_call", "tool_result", "tool_error")`
  - `"search"` filter: rows where `row.tool_name in ("web_search", "web_fetch")`
  - `"descending"` sort: rows in original order (oldest first / smallest row_number first)
  - `"ascending"` sort: reversed (newest first)
- `tool_call_index()` — `{row.tool_id: row for row in self._transcript_rows if row.type == "tool_call" and row.tool_id}`
- `toggle_transcript_expand(row_number)` — toggle in `expanded_rows` set, call `_invalidate()`
- `cycle_transcript_filter()` — cycle through all → chat → tools → search, call `_invalidate()`
- `toggle_transcript_sort()` — flip order, call `_invalidate()`
- `delete_transcript_row(row_number)`:
  1. Call `delete_row_by_number(self._full_text, row_number)` (already handles cascading by tool_id)
  2. Update `_full_text` with result
  3. Re-split and re-parse (update `_note_content_text`, `_transcript_text`, `_transcript_rows`)
  4. Save file to disk via `_file_manager`
  5. Call `_invalidate()`
- `_refresh_cached_fetch_urls()` — scan cache manifest for known URLs (if cache exists)

### Step 5: App layout integration
**Files:** `app.py`

**Depends on:** Steps 3, 4

5a. Import `TranscriptView` and create instance (after line 96):
```python
from aunic.tui.transcript_view import TranscriptView
# ...
self._transcript_view = TranscriptView(self.controller, width=self._editor_width)
```

5b. Build `note_and_transcript` container:
```python
self.note_and_transcript = HSplit([
    self.editor,
    ConditionalContainer(
        content=HSplit([
            Window(height=1, char="─", style="class:md.thematic"),
            self._transcript_view.window,
        ]),
        filter=Condition(lambda: self.controller.has_transcript()),
    ),
])
```

5c. Replace `self.editor` with `self.note_and_transcript` in root HSplit (line 147).

5d. Update `_toggle_focus_between_editor_and_prompt()` (line 416) for 3-way focus cycle:
- editor → transcript (if exists) → prompt → editor
- Skip transcript view if `not controller.has_transcript()`

5e. Add key bindings for transcript-focused state:
- Up/Down: scroll transcript
- Enter/Space: toggle expand/collapse current row
- Delete/Backspace: delete current row (with confirmation?)
- `f`: cycle filter
- `s`: toggle sort
- Guard all with `filter=Condition(lambda: self.application.layout.has_focus(self._transcript_view.window))`

5f. Add transcript view height management in `_refresh_dimensions()` or `_invalidate()`:
- Preferred height based on number of visible rows, capped at reasonable max

### Step 6: Tests
**Files:** New `tests/test_transcript_rendering.py`, updates to existing test files

6a. **Renderer unit tests** (`tests/test_transcript_rendering.py`, NEW):
- Test each renderer function with sample `TranscriptRow` + `TranscriptRenderContext`
- Verify fragments contain expected text and styles
- Test collapsed vs expanded states for bash/search
- Test error styling for `tool_error` rows and non-zero exit codes
- Test filter toolbar rendering with each active filter

6b. **Controller tests** (update `tests/test_tui_controller.py`):
- Test `_load_active_file` correctly splits note-content from transcript
- Test `visible_transcript_rows()` with each filter mode
- Test `delete_transcript_row()` cascading behavior
- Test `_handle_editor_buffer_changed` correctly reconstructs `_full_text` with transcript preserved

6c. **Integration tests**:
- Load a file with transcript, verify editor buffer contains only note-content
- Verify transcript view renders without errors
- Verify filter cycling produces correct subsets

---

## Rendering Specification Details

### Chat messages
- 2-column layout: 67% / 33% width split
- **Assistant**: content in LEFT column (67%), right column empty. Border on left cell only.
- **User**: content in RIGHT column (67%), left column empty. Border on right cell only.
- Box-drawing borders: `┌─┐`, `│`, `└─┘` around the populated cell
- Height grows to fit wrapped text (multi-line fragments)

### Tool results (default: edit, write, read, grep, glob, list)
- 2-column: `tool_name | result content`
- tool_name in `class:transcript.tool.name`
- `tool_error`: content in `class:transcript.error`

### Bash tool
- Command extracted from **hidden tool_call row** via `tool_call_index[row.tool_id].content["command"]`
- Collapsed (default): `bash | $ <command> | [v]`
- Expanded: command + stdout (25 line cap) + stderr (red) + exit code
- Error: red styling when `exit_code != 0` or `row.type == "tool_error"`
- Multi-line commands: first line + `...`

### Search results
- Query from hidden tool_call row; results from `row.content` (list of dicts)
- Collapsed: `<count> | <query> | [v]`
- Expanded: individual rows with `<title> | <snippet> | [link]`
- Cached URLs get blue underlined title

### Fetch results
- Single row: `<title> | <snippet> | [link]`
- Cached URLs get blue underlined title

### Filter toolbar
- `[ Chat ] [ Tools ] [ Search ] | [ Descending ]`
- Active state: `class:transcript.filter.active` (reverse)
- Each is a clickable fragment

### Row deletion
- `X` button on each rendered row, far left
- Clicking deletes via `delete_row_by_number()` which already handles cascading (tool_result deletion also removes matching tool_call by tool_id)

---

## Key Existing Code to Reuse

| What | Where | How |
|------|-------|-----|
| `split_note_and_transcript()` | `transcript/parser.py:20` | Split file into note-content and transcript text |
| `parse_transcript_rows()` | `transcript/parser.py:28` | Parse transcript text into `TranscriptRow` list |
| `delete_row_by_number()` | `transcript/writer.py:113` | Delete row with cascading by tool_id |
| `WebSearchView` pattern | `tui/web_search_view.py:15` | Window + FormattedTextControl + mouse handlers |
| `build_tui_style()` | `tui/rendering.py:36` | Add transcript styles |
| `_editor_width()` | `tui/app.py:389` | Terminal width for renderers |
| `flatten_tool_result_for_provider()` | `transcript/flattening.py:8` | Fallback content flattening |
| `_combine()` helper | `tui/web_search_view.py:109` | Style combination |

---

## Verification

1. **Unit tests**: Run `pytest tests/test_transcript_rendering.py` — all renderer functions produce correct fragments
2. **Controller tests**: Run `pytest tests/test_tui_controller.py` — split/parse/delete work correctly
3. **Visual verification**: Launch TUI with a note that has transcript rows, verify:
   - Editor shows only note-content (no raw markdown table)
   - Transcript view shows below editor with rendered messages
   - Chat messages display with correct alignment (assistant left, user right)
   - Tool results show tool_name + content
   - Bash results collapse/expand on click, show command + output
   - Search results collapse/expand, show individual results
   - Fetch results show title + snippet
   - Filter buttons cycle correctly, hiding/showing appropriate rows
   - Sort toggle reverses row order
   - X delete button removes rows (cascading for tool entries)
   - File is saved after deletion
   - File changes (from runs) trigger transcript view refresh
4. **Edge cases**: Empty transcript, transcript with only messages, transcript with only tool calls, very long bash output (>25 lines), multi-line bash commands
