from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING, Callable

from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.containers import ScrollOffsets
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
        self.window = Window(
            FormattedTextControl(text=self._render, focusable=True, show_cursor=False),
            height=Dimension(preferred=10, max=20, min=3),
            dont_extend_height=True,
            right_margins=[ScrollbarMargin(display_arrows=False)],
            scroll_offsets=ScrollOffsets(bottom=3),
        )

    def _render(self) -> StyleAndTextTuples:
        mode = self._controller.state.web_mode
        if mode == "results":
            return self._render_results()
        if mode == "chunks":
            return self._render_chunks()
        return []

    def _render_results(self) -> StyleAndTextTuples:
        c = self._controller
        results = c._web_results
        cursor = c._web_result_cursor
        expanded = c._web_result_expanded
        selected = c._web_selected_result
        w = max(20, self._width() - 4)
        fragments: StyleAndTextTuples = []

        for i, result in enumerate(results):
            is_focused = i == cursor
            is_selected = i == selected
            row_style = "class:control.active" if is_focused else ""

            checkbox = "[x]" if is_selected else "[ ]"
            checkbox_style = "class:web.checkbox.checked" if is_selected else "class:web.checkbox.unchecked"

            title = textwrap.shorten(result.title or "(no title)", width=w - 5, placeholder="...")
            url = textwrap.shorten(result.url, width=w, placeholder="...")

            # Line 1: checkbox + title
            fragments.append((row_style, " "))
            fragments.append((_combine(row_style, checkbox_style), checkbox))
            fragments.append((row_style, f" {title}\n"))

            # Line 2: url (indented)
            fragments.append((row_style, f"    {url}\n"))

            # Lines 3+: snippet (if expanded, wrapped up to 6 lines)
            if i in expanded and result.snippet:
                for line in textwrap.wrap(result.snippet, width=w)[:6]:
                    fragments.append((row_style, f"    {line}\n"))

        return fragments

    def _render_chunks(self) -> StyleAndTextTuples:
        c = self._controller
        if not c._web_packets:
            return []
        chunks = c._web_packets[0].chunks
        cursor = c._web_chunk_cursor
        selected = c._web_chunk_selected
        w = max(20, self._width() - 4)
        fragments: StyleAndTextTuples = []

        # "Fetch full page" row (cursor == -1)
        full_page_focused = cursor == -1
        fp_style = "class:control.active" if full_page_focused else ""
        fp_line = " [↵] Fetch full page"
        if full_page_focused:
            # [SetCursorPosition] tells prompt_toolkit the cursor is here; it auto-scrolls.
            fragments.append(("[SetCursorPosition]", ""))
            fp_line = fp_line.ljust(w)
        fragments.append((fp_style, f"{fp_line}\n"))

        for i, chunk in enumerate(chunks):
            is_focused = i == cursor
            is_selected = i in selected
            row_style = "class:control.active" if is_focused else ""

            checkbox = "[x]" if is_selected else "[ ]"
            checkbox_style = "class:web.checkbox.checked" if is_selected else "class:web.checkbox.unchecked"

            heading = chunk.heading_path[-1] if chunk.heading_path else "(no heading)"
            heading = textwrap.shorten(heading, width=w - 5, placeholder="...")

            # Up to 3 lines of chunk text
            text = chunk.text.strip()
            preview_lines = textwrap.wrap(text, width=w - 4)[:3] or ["(empty)"]

            # Heading line: pad to full width when focused for rectangular background
            heading_suffix = f" {heading}"
            used = 1 + len(checkbox) + len(heading_suffix)
            padding = " " * max(0, w - used) if is_focused else ""
            if is_focused:
                # [SetCursorPosition] tells prompt_toolkit the cursor is here; it auto-scrolls.
                fragments.append(("[SetCursorPosition]", ""))
            fragments.append((row_style, " "))
            fragments.append((_combine(row_style, checkbox_style), checkbox))
            fragments.append((row_style, f"{heading_suffix}{padding}\n"))

            # Preview lines
            for pline in preview_lines:
                ptext = f"    {pline}"
                if is_focused:
                    ptext = ptext.ljust(w)
                fragments.append((row_style, f"{ptext}\n"))

        return fragments


def _combine(base: str, extra: str) -> str:
    if not base:
        return extra
    return f"{base} {extra}"
