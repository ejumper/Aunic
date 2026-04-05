# Plan: Web Fetch Modal Improvements

## Context

The `@web` command's chunk selection modal (`web_mode == "chunks"`) has several UX issues:
- Scroll doesn't follow the cursor when navigating past the visible area
- Chunk headings show the full breadcrumb hierarchy ("Page > Section > Subsection") when only the leaf heading is useful
- Each chunk only shows one line of preview text — not enough to judge relevance
- The selected-row highlight only applies to characters, leaving a ragged background
- There's no way to insert the entire page without selecting individual chunks

## Files to Modify

| File | Purpose |
|------|---------|
| `src/aunic/research/types.py` | Add `full_markdown` field to `FetchPacket` |
| `src/aunic/research/fetch.py` | Populate `full_markdown` in `fetch_for_user_selection` |
| `src/aunic/tui/web_search_view.py` | All rendering changes + scroll |
| `src/aunic/tui/controller.py` | Cursor range, insert logic, height calc |

---

## Changes

### 1. Store full markdown in `FetchPacket` (types.py + fetch.py)

**`research/types.py`** — append to `FetchPacket` dataclass:
```python
full_markdown: str = ""
```
Must come after `chunks` (existing required fields) so the default doesn't cause a dataclass ordering error.

**`research/fetch.py`** — in `fetch_for_user_selection`, update the `FetchPacket(...)` constructor call (line ~119) to pass:
```python
full_markdown=page.markdown,
```
`page` is already the `PageFetchResult` computed on line ~99 — no extra fetch needed.

---

### 2. Scroll following (web_search_view.py)

Add `ScrollbarMargin` to the chunks window and implement `_ensure_chunk_visible()`.

**In `__init__`**, update Window construction:
```python
from prompt_toolkit.layout.margins import ScrollbarMargin
self.window = Window(
    FormattedTextControl(text=self._render, focusable=True, show_cursor=False),
    height=Dimension(preferred=10, max=20, min=3),
    dont_extend_height=True,
    right_margins=[ScrollbarMargin(display_arrows=False)],
)
```

**Add `_ensure_chunk_visible()`** — line heights are fixed so no dict tracking needed:
```python
_FULL_PAGE_LINES = 1
_CHUNK_LINES = 4  # 1 heading + 3 content

def _ensure_chunk_visible(self) -> None:
    cursor = self._controller._web_chunk_cursor
    if cursor == -1:
        start_line, end_line = 0, _FULL_PAGE_LINES
    else:
        start_line = _FULL_PAGE_LINES + cursor * _CHUNK_LINES
        end_line = start_line + _CHUNK_LINES
    render_info = self.window.render_info
    visible_height = render_info.window_height if render_info is not None else 10
    scroll_top = self.window.vertical_scroll
    scroll_bottom = scroll_top + max(1, visible_height - 1)
    if start_line < scroll_top:
        self.window.vertical_scroll = max(0, start_line)
    elif end_line > scroll_bottom:
        self.window.vertical_scroll = max(0, end_line - visible_height)
```

Call `self._ensure_chunk_visible()` at the end of `_render_chunks()`.

---

### 3. Updated `_render_chunks()` (web_search_view.py)

Replace the entire method body. Key changes vs. current code:

**a) "Fetch full page" row at top** (cursor == -1 selects it):
```python
full_page_focused = (cursor == -1)
fp_style = "class:control.active" if full_page_focused else ""
fp_line = " [↵] Fetch full page"
if full_page_focused:
    fp_line = fp_line.ljust(w)
fragments.append((fp_style, f"{fp_line}\n"))
```

**b) Heading = last element only**:
```python
heading = chunk.heading_path[-1] if chunk.heading_path else "(no heading)"
heading = textwrap.shorten(heading, width=w - 5, placeholder="...")
```

**c) Up to 3 lines of preview** (wrapped, indented 4 spaces):
```python
text = chunk.text.strip()
preview_lines = textwrap.wrap(text, width=w - 4)[:3] or ["(empty)"]
```

**d) Full-width background padding for focused row** — pad the last character of each line before `\n`:
```python
# Heading line
heading_content = f" {checkbox} {heading}"
if is_focused:
    heading_content = heading_content.ljust(w)
# Preview lines
for pline in preview_lines:
    ptext = f"    {pline}"
    if is_focused:
        ptext = ptext.ljust(w)
    fragments.append((row_style, f"{ptext}\n"))
```

For the heading line, the checkbox and heading are separate fragments — so the padding goes on the last fragment before `\n`. Compute: `used = 1 + len(checkbox) + 1 + len(heading)` and append `" " * max(0, w - used)` before `\n`.

---

### 4. Controller updates (controller.py)

**`web_move_cursor`** — extend range to include -1:
```python
elif self.state.web_mode == "chunks" and self._web_packets:
    n = len(self._web_packets[0].chunks)
    if n:
        self._web_chunk_cursor = max(-1, min(n - 1, self._web_chunk_cursor + delta))
```

**`send_prompt` validation** (around line 825) — allow insert when on full page row:
```python
elif self.state.web_mode == "chunks":
    if self._web_chunk_cursor == -1 or self._web_chunk_selected:
        self._run_task = asyncio.create_task(self._insert_web_chunks())
    else:
        self._set_error("Select chunks with [Space] or navigate to 'Fetch full page'.")
        self._invalidate()
        return
```

**`_insert_web_chunks`** — handle full page branch:
```python
async def _insert_web_chunks(self) -> None:
    packet = self._web_packets[0]
    if self._web_chunk_cursor == -1:
        content_block = f"# {packet.title}\n\n{packet.full_markdown}"
        label = "full page"
    else:
        selected_texts = [packet.chunks[i].text for i in sorted(self._web_chunk_selected)]
        content_block = f"# {packet.title}\n\n" + "\n\n".join(selected_texts)
        label = f"{len(selected_texts)} chunk(s)"
    updated_note = _append_block_to_note_content(self._note_content_text, content_block)
    updated = join_note_and_transcript(updated_note, self._transcript_text)
    if not await self._write_active_file_text(updated):
        return
    self._set_status(f"Inserted {label} from \"{packet.title[:40]}\".")
    self._web_cancel(status_message=None)
```

**`web_view_preferred_height`** — update for new per-chunk line count:
```python
if self.state.web_mode == "chunks" and self._web_packets:
    return 1 + len(self._web_packets[0].chunks) * 4  # 1 full-page row + 4 lines/chunk
```

**`web_space_pressed`** — make Space a no-op when on full page row:
```python
elif self.state.web_mode == "chunks":
    i = self._web_chunk_cursor
    if i == -1:
        return  # no toggle for full-page option
    if i in self._web_chunk_selected:
        self._web_chunk_selected.discard(i)
    else:
        self._web_chunk_selected.add(i)
```

---

## Verification

1. Run `python -c "from aunic.tui.web_search_view import WebSearchView; print('OK')"` to check imports
2. In the app, trigger `@web` → search for something → select a result → enter chunk mode:
   - Confirm "Fetch full page" appears as the top row
   - Navigate up from row 0 to reach it; confirm it gets highlighted with full-width black background
   - Confirm navigating down past the visible area scrolls the list
   - Confirm headings show only the leaf name (not full breadcrumb)
   - Confirm up to 3 lines of text are visible per chunk
   - Confirm focused row has a clean rectangular background
   - Navigate to "Fetch full page" and hit Ctrl+R; confirm full markdown inserted
   - Select chunks with Space and hit Ctrl+R; confirm selected chunks inserted
