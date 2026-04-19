from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING, Callable

from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.containers import ScrollOffsets, Window
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.margins import ScrollbarMargin

if TYPE_CHECKING:
    from aunic.tui.controller import TuiController


class WebSearchView:
    def __init__(
        self,
        controller: TuiController,
        *,
        width: Callable[[], int] | None = None,
    ) -> None:
        self._controller = controller
        self._width = width or (lambda: 100)
        self._scroll_pos = 0
        self._pending_scroll = False
        self.window = Window(
            FormattedTextControl(
                text=self._render,
                focusable=True,
                show_cursor=False,
                get_cursor_position=lambda: Point(0, self._scroll_pos),
            ),
            height=Dimension(preferred=10, max=20, min=3),
            dont_extend_height=True,
            wrap_lines=False,
            right_margins=[ScrollbarMargin(display_arrows=False)],
            scroll_offsets=ScrollOffsets(top=0, bottom=0),
            get_vertical_scroll=lambda w: self._scroll_pos,
        )

    def on_cursor_moved(self) -> None:
        """Mark that the focused item changed; next render updates scroll position."""
        self._pending_scroll = True

    def _update_scroll(self, cursor_line: int, item_height: int, total_lines: int) -> None:
        render_info = self.window.render_info
        visible_height = render_info.window_height if render_info is not None else 10
        cursor_end = cursor_line + item_height - 1
        if cursor_line < self._scroll_pos:
            # Item top scrolled above viewport — snap up
            self._scroll_pos = cursor_line
        elif cursor_end >= self._scroll_pos + visible_height:
            # Item bottom below viewport — scroll down, but don't go past item top
            self._scroll_pos = min(cursor_end - visible_height + 1, cursor_line)
        # Clamp to valid range
        max_scroll = max(0, total_lines - visible_height)
        self._scroll_pos = max(0, min(self._scroll_pos, max_scroll))

    def _render(self) -> StyleAndTextTuples:
        mode = self._controller.state.web_mode
        if mode == "results":
            return self._render_results()
        if mode == "chunks":
            return self._render_chunks()
        self._scroll_pos = 0
        return []

    # ── Web search results ────────────────────────────────────────────────────

    def _render_results(self) -> StyleAndTextTuples:
        c = self._controller
        if c._rag_active:
            return self._render_rag_results()

        results = c._web_results
        cursor = c._web_result_cursor
        expanded = c._web_result_expanded
        selected = c._web_selected_result
        w = max(20, self._width() - 4)
        indent = "    "

        # Pass 1: compute line positions so we can update scroll before rendering
        line = 0
        cursor_line = 0
        cursor_height = 1
        for i, result in enumerate(results):
            is_expanded = i in expanded
            snippet_count = min(10, len(textwrap.wrap(result.snippet or "", width=w - len(indent)))) if is_expanded and result.snippet else 0
            item_height = 2 + snippet_count  # title + url + snippets
            if i == cursor:
                cursor_line = line
                cursor_height = item_height
            line += item_height

        if self._pending_scroll:
            self._pending_scroll = False
            self._update_scroll(cursor_line, cursor_height, line)

        # Pass 2: build fragments
        fragments: StyleAndTextTuples = []
        for i, result in enumerate(results):
            is_focused = i == cursor
            is_selected = i == selected
            is_expanded = i in expanded
            row_style = "class:control.active" if is_focused else ""

            checkbox = "[x]" if is_selected else "[ ]"
            checkbox_style = "class:web.checkbox.checked" if is_selected else "class:web.checkbox.unchecked"

            title = textwrap.shorten(result.title or "(no title)", width=w - 5, placeholder="...")
            url = textwrap.shorten(result.url, width=w - len(indent), placeholder="...")

            # Title line — pad to w when focused for rectangular highlight
            title_pad = " " * max(0, w - 5 - len(title)) if is_focused else ""
            fragments.append((row_style, " "))
            fragments.append((_combine(row_style, checkbox_style), checkbox))
            fragments.append((row_style, f" {title}{title_pad}\n"))

            # URL line
            url_text = f"{indent}{url}"
            if is_focused:
                url_text = url_text.ljust(w)
            fragments.append((row_style, f"{url_text}\n"))

            # Snippet lines (up to 10 when expanded)
            if is_expanded and result.snippet:
                for sline in textwrap.wrap(result.snippet, width=w - len(indent))[:10]:
                    stext = f"{indent}{sline}"
                    if is_focused:
                        stext = stext.ljust(w)
                    fragments.append((row_style, f"{stext}\n"))

        return fragments

    # ── RAG search results ────────────────────────────────────────────────────

    def _render_rag_results(self) -> StyleAndTextTuples:
        c = self._controller
        results = c._rag_results
        cursor = c._web_result_cursor
        expanded = c._web_result_expanded
        selected = c._web_selected_result
        w = max(20, self._width() - 4)
        indent = "    "

        # Pass 1: line positions
        line = 0
        cursor_line = 0
        cursor_height = 1
        for i, result in enumerate(results):
            is_expanded = i in expanded
            snippet = (result.snippet or "").strip()
            max_snip = 5 if is_expanded else 2
            snip_count = min(max_snip, len(textwrap.wrap(snippet, width=w - len(indent)))) if snippet else 0
            item_height = 1 + (1 if is_expanded else 0) + snip_count
            if i == cursor:
                cursor_line = line
                cursor_height = item_height
            line += item_height

        if self._pending_scroll:
            self._pending_scroll = False
            self._update_scroll(cursor_line, cursor_height, line)

        # Pass 2: fragments
        fragments: StyleAndTextTuples = []
        for i, result in enumerate(results):
            is_focused = i == cursor
            is_selected = i == selected
            is_expanded = i in expanded
            row_style = "class:control.active" if is_focused else ""

            checkbox = "[x]" if is_selected else "[ ]"
            checkbox_style = "class:web.checkbox.checked" if is_selected else "class:web.checkbox.unchecked"

            heading = result.heading_path[-1] if result.heading_path else None
            parts = [result.title or "(no title)"]
            if result.source:
                parts.append(result.source)
            if heading:
                parts.append(f"# {heading}")
            header_text = textwrap.shorten(" | ".join(parts), width=w - 5, placeholder="...")
            header_pad = " " * max(0, w - 5 - len(header_text)) if is_focused else ""

            fragments.append((row_style, " "))
            fragments.append((_combine(row_style, checkbox_style), checkbox))
            fragments.append((row_style, f" {header_text}{header_pad}\n"))

            if is_expanded:
                raw_path = result.local_path or result.url or f"[{result.source}] {result.result_id}"
                path = textwrap.shorten(raw_path, width=w - len(indent), placeholder="...")
                path_text = f"{indent}{path}"
                if is_focused:
                    path_text = path_text.ljust(w)
                fragments.append((row_style, f"{path_text}\n"))

            snippet = (result.snippet or "").strip()
            if snippet:
                max_lines = 5 if is_expanded else 2
                wrapped = textwrap.wrap(snippet, width=w - len(indent))
                has_more = len(wrapped) > max_lines
                display = wrapped[:max_lines]
                if has_more and display:
                    avail = w - len(indent) - 3
                    last = display[-1]
                    display[-1] = (last[:avail] if len(last) > avail else last) + "..."
                for sline in display:
                    stext = f"{indent}{sline}"
                    if is_focused:
                        stext = stext.ljust(w)
                    fragments.append((row_style, f"{stext}\n"))

        return fragments

    # ── Chunk picker ──────────────────────────────────────────────────────────

    def _render_chunks(self) -> StyleAndTextTuples:
        c = self._controller
        if not c._web_packets:
            return []
        chunks = c._web_packets[0].chunks
        cursor = c._web_chunk_cursor
        selected = c._web_chunk_selected
        expanded = c._web_chunk_expanded
        w = max(20, self._width() - 4)
        indent = "    "

        # Pass 1: line positions (full-page row is line 0)
        line = 1  # full-page row
        cursor_line = 0 if cursor == -1 else -1  # -1 means not yet found
        cursor_height = 1
        for i, chunk in enumerate(chunks):
            is_expanded = i in expanded
            heading = chunk.heading_path[-1] if chunk.heading_path else None
            text = chunk.text.strip()
            max_text = (12 if is_expanded else 5) - (1 if heading else 0)
            text_count = min(max_text, len(textwrap.wrap(text, width=w - len(indent)))) if text else 1
            item_height = (1 if heading else 0) + max(1, text_count)
            if i == cursor:
                cursor_line = line
                cursor_height = item_height
            line += item_height

        if self._pending_scroll:
            self._pending_scroll = False
            if cursor_line >= 0:
                self._update_scroll(cursor_line, cursor_height, line)

        # Pass 2: fragments
        fragments: StyleAndTextTuples = []

        full_page_focused = cursor == -1
        fp_style = "class:control.active" if full_page_focused else ""
        fp_line = " [↵] Insert fetched chunks" if c._rag_active else " [↵] Fetch full page"
        if full_page_focused:
            fp_line = fp_line.ljust(w)
        fragments.append((fp_style, f"{fp_line}\n"))

        for i, chunk in enumerate(chunks):
            is_focused = i == cursor
            is_selected = i in selected
            is_expanded = i in expanded
            row_style = "class:web.chunk.match" if chunk.is_match else ""
            if is_focused:
                row_style = _combine(row_style, "class:control.active")

            checkbox = "[x]" if is_selected else "[ ]"
            checkbox_style = "class:web.checkbox.checked" if is_selected else "class:web.checkbox.unchecked"

            heading = chunk.heading_path[-1] if chunk.heading_path else None
            text = chunk.text.strip()

            # Max text lines: 5 collapsed (4 if heading), 12 expanded (11 if heading)
            max_text_lines = (12 if is_expanded else 5) - (1 if heading else 0)
            wrapped = textwrap.wrap(text, width=w - len(indent)) if text else []
            has_more = len(wrapped) > max_text_lines
            display_lines = wrapped[:max_text_lines] or (["(empty)"] if not heading else [])
            if has_more and display_lines:
                avail = w - len(indent) - 3
                last = display_lines[-1]
                display_lines[-1] = (last[:avail] if len(last) > avail else last) + "..."

            if heading:
                heading_text = textwrap.shorten(heading, width=w - 5, placeholder="...")
                heading_suffix = f" {heading_text}"
                used = 1 + len(checkbox) + len(heading_suffix)
                pad = " " * max(0, w - used) if is_focused else ""
                fragments.append((row_style, " "))
                fragments.append((_combine(row_style, checkbox_style), checkbox))
                fragments.append((row_style, f"{heading_suffix}{pad}\n"))
                for pline in display_lines:
                    ptext = f"{indent}{pline}"
                    if is_focused:
                        ptext = ptext.ljust(w)
                    fragments.append((row_style, f"{ptext}\n"))
            else:
                first = display_lines[0] if display_lines else "(empty)"
                rest_lines = display_lines[1:]
                first_suffix = f" {first}"
                used = 1 + len(checkbox) + len(first_suffix)
                pad = " " * max(0, w - used) if is_focused else ""
                fragments.append((row_style, " "))
                fragments.append((_combine(row_style, checkbox_style), checkbox))
                fragments.append((row_style, f"{first_suffix}{pad}\n"))
                for pline in rest_lines:
                    ptext = f"{indent}{pline}"
                    if is_focused:
                        ptext = ptext.ljust(w)
                    fragments.append((row_style, f"{ptext}\n"))

        return fragments


def _combine(base: str, extra: str) -> str:
    if not base:
        return extra
    return f"{base} {extra}"
