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
        if c._rag_active:
            return self._render_rag_results()

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

    def _render_rag_results(self) -> StyleAndTextTuples:
        c = self._controller
        results = c._rag_results
        cursor = c._web_result_cursor
        expanded = c._web_result_expanded
        selected = c._web_selected_result
        w = max(20, self._width() - 4)
        indent = "    "
        fragments: StyleAndTextTuples = []

        for i, result in enumerate(results):
            is_focused = i == cursor
            is_selected = i == selected
            is_expanded = i in expanded
            row_style = "class:control.active" if is_focused else ""

            checkbox = "[x]" if is_selected else "[ ]"
            checkbox_style = "class:web.checkbox.checked" if is_selected else "class:web.checkbox.unchecked"

            # Header: title | source | # Heading  (heading omitted when absent)
            heading = result.heading_path[-1] if result.heading_path else None
            parts = [result.title or "(no title)"]
            if result.source:
                parts.append(result.source)
            if heading:
                parts.append(f"# {heading}")
            header_text = textwrap.shorten(" | ".join(parts), width=w - 5, placeholder="...")

            fragments.append((row_style, " "))
            fragments.append((_combine(row_style, checkbox_style), checkbox))
            fragments.append((row_style, f" {header_text}\n"))

            # Path line — only when expanded
            if is_expanded:
                path = result.local_path or result.url or f"[{result.source}] {result.result_id}"
                path = textwrap.shorten(path, width=w - len(indent), placeholder="...")
                fragments.append((row_style, f"{indent}{path}\n"))

            # Snippet: 2 lines collapsed, 5 lines expanded, trailing ellipsis if truncated
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
                for line in display:
                    fragments.append((row_style, f"{indent}{line}\n"))

        return fragments

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
        fragments: StyleAndTextTuples = []

        # "Fetch full page" row (cursor == -1)
        full_page_focused = cursor == -1
        fp_style = "class:control.active" if full_page_focused else ""
        fp_line = " [↵] Insert fetched chunks" if c._rag_active else " [↵] Fetch full page"
        if full_page_focused:
            fragments.append(("[SetCursorPosition]", ""))
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

            # Max text lines: 5 collapsed (4 if heading shown), 10 expanded (9 if heading shown)
            max_text_lines = (10 if is_expanded else 5) - (1 if heading else 0)
            wrapped = textwrap.wrap(text, width=w - len(indent)) if text else []
            has_more = len(wrapped) > max_text_lines
            display_lines = wrapped[:max_text_lines] or (["(empty)"] if not heading else [])
            if has_more and display_lines:
                avail = w - len(indent) - 3
                last = display_lines[-1]
                display_lines[-1] = (last[:avail] if len(last) > avail else last) + "..."

            if is_focused:
                fragments.append(("[SetCursorPosition]", ""))

            if heading:
                heading_text = textwrap.shorten(heading, width=w - 5, placeholder="...")
                heading_suffix = f" {heading_text}"
                used = 1 + len(checkbox) + len(heading_suffix)
                padding = " " * max(0, w - used) if is_focused else ""
                fragments.append((row_style, " "))
                fragments.append((_combine(row_style, checkbox_style), checkbox))
                fragments.append((row_style, f"{heading_suffix}{padding}\n"))
                for pline in display_lines:
                    ptext = f"{indent}{pline}"
                    if is_focused:
                        ptext = ptext.ljust(w)
                    fragments.append((row_style, f"{ptext}\n"))
            else:
                # No heading — checkbox on first text line, rest indented
                first = display_lines[0] if display_lines else "(empty)"
                rest_lines = display_lines[1:]
                first_suffix = f" {first}"
                used = 1 + len(checkbox) + len(first_suffix)
                padding = " " * max(0, w - used) if is_focused else ""
                fragments.append((row_style, " "))
                fragments.append((_combine(row_style, checkbox_style), checkbox))
                fragments.append((row_style, f"{first_suffix}{padding}\n"))
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
