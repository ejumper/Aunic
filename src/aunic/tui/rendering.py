from __future__ import annotations

import re
from typing import Callable

from aunic.context.types import TextSpan
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.processors import Processor, Transformation
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.styles import Style

from aunic.tui.folding import FOLD_PLACEHOLDER_PREFIX, is_fold_placeholder_line

_THEMATIC_BREAK_RE = re.compile(r"^\s*(?:\*\*\*+|---+)\s*$")
_LIST_RE = re.compile(r"^(\s*)(?:[-+*]|\d+[.)])\s+")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

_TOKEN_RE = re.compile(
    r"(@>>|<<@|!>>|<<!|%>>|<<%|\$>>|<<\$|`[^`]+`|\*\*\*[^*]+\*\*\*|\*\*[^*]+\*\*|\*[^*]+\*)"
)

_MARKER_STYLES = {
    "@>>": "class:marker.write",
    "<<@": "class:marker.write",
    "!>>": "class:marker.include",
    "<<!": "class:marker.include",
    "%>>": "class:marker.exclude",
    "<<%": "class:marker.exclude",
    "$>>": "class:marker.protect",
    "<<$": "class:marker.protect",
}


def build_tui_style() -> Style:
    return Style.from_dict(
        {
            "topbar": "bold",
            "topbar.title": "ansiblue bold underline",
            "indicator.status": "italic",
            "indicator.error": "ansired italic",
            "control": "",
            "control.active": "reverse",
            "control.disabled": "ansibrightblack",
            "control.send": "ansiblue bold",
            "control.send.disabled": "ansibrightblack",
            "md.heading": "bold italic",
            "md.bold": "bold",
            "md.italic": "italic",
            "md.bolditalic": "bold italic",
            "md.code": "ansiblue",
            "md.codeblock": "ansiblue",
            "md.thematic": "ansiblue",
            "md.fold": "ansibrightblack italic",
            "md.recent": "ansibrightblue",
            "marker.write": "ansicyan",
            "marker.include": "ansigreen",
            "marker.exclude": "ansired",
            "marker.protect": "ansimagenta",
            "web.checkbox.checked": "ansigreen",
            "web.checkbox.unchecked": "ansibrightblack",
            "model.selected": "ansigreen",
            "transcript.border": "ansibrightblack",
            "transcript.assistant": "",
            "transcript.user": "",
            "transcript.chat.heading": "ansiblue bold italic",
            "transcript.chat.bold": "bold",
            "transcript.chat.italic": "italic",
            "transcript.chat.code": "ansiblue",
            "transcript.chat.link": "ansiblue underline",
            "transcript.chat.quote": "ansibrightblack",
            "transcript.tool.name": "ansicyan bold",
            "transcript.tool.content": "",
            "transcript.error": "ansired",
            "transcript.delete": "ansired bold",
            "transcript.toggle": "ansibrightblack",
            "transcript.link": "ansiblue underline",
            "transcript.link.cached": "ansiblue underline bold",
            "transcript.filter": "",
            "transcript.filter.active": "reverse",
            "transcript.sort": "",
            "transcript.sort.active": "reverse",
            "transcript.bash.command": "ansigreen",
            "transcript.search.count": "ansiblue bold",
            "transcript.search.snippet": "ansibrightblack italic",
            "transcript.fetch.snippet": "ansibrightblack italic",
            "context.separator.green":  "ansigreen bold",
            "context.separator.yellow": "ansiyellow bold",
            "context.separator.red":    "ansired bold",
        }
    )


class AunicMarkdownLexer(Lexer):
    def lex_document(self, document):
        lines = document.lines
        in_code_block_before: list[bool] = []
        in_code = False
        for line in lines:
            in_code_block_before.append(in_code)
            if line.strip().startswith("```"):
                in_code = not in_code

        def get_line(lineno: int) -> StyleAndTextTuples:
            if lineno >= len(lines):
                return []
            return lex_markdown_line(lines[lineno], in_code_block=in_code_block_before[lineno])

        return get_line


class ThematicBreakProcessor(Processor):
    def __init__(self, *, width: Callable[[], int] | None = None) -> None:
        self._width = width or (lambda: 60)

    def apply_transformation(self, transformation_input) -> Transformation:
        line_text = "".join(fragment[1] for fragment in transformation_input.fragments)
        if not _THEMATIC_BREAK_RE.match(line_text):
            return Transformation(transformation_input.fragments)
        if transformation_input.document.cursor_position_row == transformation_input.lineno:
            return Transformation(transformation_input.fragments)
        width = max(3, self._width())
        return Transformation([("class:md.thematic", "─" * width)])


class RecentChangeProcessor(Processor):
    def __init__(self, *, spans: Callable[[], tuple[TextSpan, ...]] | None = None) -> None:
        self._spans = spans or (lambda: ())

    def apply_transformation(self, transformation_input) -> Transformation:
        spans = self._spans()
        if not spans:
            return Transformation(transformation_input.fragments)

        line_text = "".join(fragment[1] for fragment in transformation_input.fragments)
        if not line_text:
            return Transformation(transformation_input.fragments)

        line_start = transformation_input.document.translate_row_col_to_index(
            transformation_input.lineno,
            0,
        )
        line_end = line_start + len(line_text)
        line_spans = tuple(
            TextSpan(
                start=max(0, span.start - line_start),
                end=min(len(line_text), span.end - line_start),
            )
            for span in spans
            if span.end > line_start and span.start < line_end
        )
        if not line_spans:
            return Transformation(transformation_input.fragments)

        return Transformation(
            _apply_style_to_fragments(
                transformation_input.fragments,
                line_spans,
                "class:md.recent",
            )
        )


def lex_markdown_line(
    line: str,
    *,
    in_code_block: bool = False,
    hide_emphasis_markers: bool = False,
) -> StyleAndTextTuples:
    if is_fold_placeholder_line(line):
        return [("class:md.fold", line)]
    if in_code_block or line.strip().startswith("```"):
        return [("class:md.codeblock", line)]
    if _THEMATIC_BREAK_RE.match(line):
        return [("class:md.thematic", line)]
    heading_match = _HEADING_RE.match(line)
    if heading_match is not None:
        return [("class:md.heading", line)]
    return _tokenize_inline_markdown(line, hide_emphasis_markers=hide_emphasis_markers)


def soft_wrap_prefix_for_line(
    line_text: str,
    wrap_count: int,
    *,
    in_code_block: bool = False,
) -> str:
    if wrap_count <= 0:
        return ""
    if in_code_block:
        stripped = line_text.lstrip(" \t")
        indent = len(line_text) - len(stripped)
        return " " * indent
    match = _LIST_RE.match(line_text)
    if match is not None:
        return " " * len(match.group(0))
    return ""


def _tokenize_inline_markdown(
    line: str,
    *,
    hide_emphasis_markers: bool = False,
) -> StyleAndTextTuples:
    fragments: StyleAndTextTuples = []
    position = 0
    for match in _TOKEN_RE.finditer(line):
        start, end = match.span()
        if start > position:
            fragments.append(("", line[position:start]))
        token = match.group(0)
        if token in _MARKER_STYLES:
            fragments.append((_MARKER_STYLES[token], token))
        elif token.startswith("`"):
            fragments.append(("class:md.code", token))
        elif token.startswith("***") and token.endswith("***"):
            fragments.append(("class:md.bolditalic", token[3:-3] if hide_emphasis_markers else token))
        elif token.startswith("**") and token.endswith("**"):
            fragments.append(("class:md.bold", token[2:-2] if hide_emphasis_markers else token))
        elif token.startswith("*") and token.endswith("*"):
            fragments.append(("class:md.italic", token[1:-1] if hide_emphasis_markers else token))
        else:
            fragments.append(("", token))
        position = end
    if position < len(line):
        fragments.append(("", line[position:]))
    return fragments


def _apply_style_to_fragments(
    fragments: StyleAndTextTuples,
    spans: tuple[TextSpan, ...],
    extra_style: str,
) -> StyleAndTextTuples:
    styled: StyleAndTextTuples = []
    cursor = 0
    for fragment in fragments:
        style = fragment[0]
        text = fragment[1]
        rest = fragment[2:]
        fragment_start = cursor
        fragment_end = cursor + len(text)
        cursor = fragment_end

        if not text:
            styled.append(fragment)
            continue

        boundaries = {0, len(text)}
        for span in spans:
            overlap_start = max(fragment_start, span.start)
            overlap_end = min(fragment_end, span.end)
            if overlap_start < overlap_end:
                boundaries.add(overlap_start - fragment_start)
                boundaries.add(overlap_end - fragment_start)

        ordered = sorted(boundaries)
        if len(ordered) == 2:
            if _offset_in_spans(fragment_start, spans):
                styled.append((_combine_styles(style, extra_style), text, *rest))
            else:
                styled.append(fragment)
            continue

        for start, end in zip(ordered, ordered[1:]):
            if start == end:
                continue
            segment_text = text[start:end]
            absolute_start = fragment_start + start
            segment_style = (
                _combine_styles(style, extra_style)
                if _offset_in_spans(absolute_start, spans)
                else style
            )
            styled.append((segment_style, segment_text, *rest))
    return styled


def _offset_in_spans(offset: int, spans: tuple[TextSpan, ...]) -> bool:
    return any(span.start <= offset < span.end for span in spans)


def _combine_styles(base_style: str, extra_style: str) -> str:
    if not base_style:
        return extra_style
    return f"{base_style} {extra_style}"
