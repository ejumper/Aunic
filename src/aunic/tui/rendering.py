from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from aunic.context.types import TextSpan
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.processors import Processor, Transformation
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.styles import Style

from aunic.tui.folding import FOLD_PLACEHOLDER_PREFIX, is_fold_placeholder_line

_THEMATIC_BREAK_RE = re.compile(r"^\s*(?:\*\*\*+|---+)\s*$")

# Markdown link: [label](url) or [label](url "title") — negative lookbehind skips images ![...]
_LINK_RE = re.compile(
    r"(?<!!)"                                           # not preceded by ! (image)
    r"\[([^\]]+)\]"                                     # [label]
    r"\("                                               # (
    r"([^)\s\"'<>]+)"                                   # url/path
    r"""(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?"""       # optional title attribute
    r"\)"                                               # )
)

# Slash/prefix commands that are highlighted blue in the prompt editor.
# Mutable set — extended at runtime by register_rag_scopes().
_prompt_active_commands: set[str] = {
    "/context", "/note", "/chat", "/work", "/read", "/off", "/model", "/find", "/replace", "@web",
    "/include", "/exclude", "/isolate", "/map",
}

# Read-only view and compiled regex — rebuilt by _rebuild_prompt_regex().
PROMPT_ACTIVE_COMMANDS: frozenset[str]
_PROMPT_COMMAND_RE: re.Pattern[str]
_DESTRUCTIVE_COMMANDS = frozenset({"/clear-history"})


def _rebuild_prompt_regex() -> None:
    global PROMPT_ACTIVE_COMMANDS, _PROMPT_COMMAND_RE
    PROMPT_ACTIVE_COMMANDS = frozenset(_prompt_active_commands)
    # Sort by length descending so longer commands take priority over shorter prefixes.
    escaped = sorted(
        (re.escape(cmd) + r"\b" for cmd in _prompt_active_commands),
        key=len,
        reverse=True,
    )
    # /clear-history is in the regex (for red highlighting) but not in PROMPT_ACTIVE_COMMANDS.
    escaped.append(r"/clear-history\b")
    _PROMPT_COMMAND_RE = re.compile("(" + "|".join(escaped) + ")")


def register_rag_scopes(scope_names: tuple[str, ...]) -> None:
    """Register @rag and each @<scope> as highlighted prompt commands.

    Called once during TuiController.__init__ after RAG config is loaded.
    Safe to call multiple times — idempotent.
    """
    _prompt_active_commands.add("@rag")
    for name in scope_names:
        _prompt_active_commands.add(f"@{name}")
    _rebuild_prompt_regex()


# Initialize module-level globals.
_rebuild_prompt_regex()
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
            "indicator.attachment": "ansiblue",
            "indicator.attachment.remove": "ansired bold",
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
            "md.model_insert": "ansigreen",
            "marker.write": "ansicyan",
            "marker.include": "ansigreen",
            "marker.exclude": "ansired",
            "marker.protect": "ansimagenta",
            "web.checkbox.checked": "ansigreen",
            "web.checkbox.unchecked": "ansibrightblack",
            "web.chunk.match": "ansiblue",
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
            "md.link": "ansiblue underline",
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
            "prompt.command": "ansiblue bold",
            "prompt.command.destructive": "ansired bold",
            "prompt.find.label": "ansibrightblack",
            "context.separator.green":  "ansigreen bold",
            "context.separator.yellow": "ansiyellow bold",
            "context.separator.red":    "ansired bold",
        }
    )


def _slice_fragments(fragments: StyleAndTextTuples, start: int, end: int) -> StyleAndTextTuples:
    """Return fragments covering character positions [start, end) of the joined line text."""
    result: list = []
    pos = 0
    for fragment in fragments:
        style = fragment[0]
        text = fragment[1]
        rest = fragment[2:]
        frag_end = pos + len(text)
        if frag_end > start and pos < end:
            slice_start = max(0, start - pos)
            slice_end = min(len(text), end - pos)
            sliced = text[slice_start:slice_end]
            if sliced:
                result.append((style, sliced, *rest))
        pos = frag_end
    return result


def _make_mouse_up_handler(callback: Callable[[], None]):
    def handler(mouse_event) -> None:
        if mouse_event.event_type == MouseEventType.MOUSE_UP:
            callback()
    return handler


class MarkdownLinkProcessor(Processor):
    """Collapse markdown links to their label text (blue/underlined, clickable) on non-cursor lines."""

    def __init__(
        self,
        *,
        open_target: Callable[[str], None] | None = None,
        active_file: Callable[[], Path | None] | None = None,
    ) -> None:
        self._open_target = open_target or (lambda _: None)
        self._active_file = active_file or (lambda: None)

    def apply_transformation(self, transformation_input) -> Transformation:
        line_text = "".join(f[1] for f in transformation_input.fragments)
        if not _LINK_RE.search(line_text):
            return Transformation(transformation_input.fragments)
        if transformation_input.document.cursor_position_row == transformation_input.lineno:
            return Transformation(transformation_input.fragments)

        fragments: list = []
        pos = 0
        for m in _LINK_RE.finditer(line_text):
            start, end = m.span()
            if start > pos:
                fragments.extend(_slice_fragments(transformation_input.fragments, pos, start))
            label = m.group(1)
            raw_target = m.group(2)
            resolved = self._resolve(raw_target)
            open_fn = self._open_target
            handler = _make_mouse_up_handler(lambda t=resolved: open_fn(t))
            fragments.append(("class:md.link", label, handler))
            pos = end
        if pos < len(line_text):
            fragments.extend(_slice_fragments(transformation_input.fragments, pos, len(line_text)))

        return Transformation(fragments)

    def _resolve(self, target: str) -> str:
        """Resolve relative paths against the active file's directory; pass URLs through."""
        if "://" in target or target.startswith("mailto:"):
            return target
        if target.startswith("/"):
            return target
        active = self._active_file()
        if active is not None:
            return str((active.parent / target).resolve())
        return target


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


class PromptLexer(Lexer):
    """Lexer for the prompt input field — highlights active slash/@web commands in blue."""

    def lex_document(self, document):
        lines = document.lines

        def get_line(lineno: int) -> StyleAndTextTuples:
            if lineno >= len(lines):
                return []
            line = lines[lineno]
            fragments: StyleAndTextTuples = []
            pos = 0
            for m in _PROMPT_COMMAND_RE.finditer(line):
                start, end = m.span()
                token = m.group(0)
                # @-prefixed commands only highlighted when nothing precedes them in the prompt
                if token.startswith("@") and not (lineno == 0 and not line[:start].strip()):
                    if start > pos:
                        fragments.append(("", line[pos:start]))
                    fragments.append(("", line[start:end]))
                    pos = end
                    continue
                if start > pos:
                    fragments.append(("", line[pos:start]))
                style = "class:prompt.command.destructive" if token in _DESTRUCTIVE_COMMANDS else "class:prompt.command"
                fragments.append((style, line[start:end]))
                pos = end
            if pos < len(line):
                fragments.append(("", line[pos:]))
            return fragments

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
    def __init__(
        self,
        *,
        spans: Callable[[], tuple[TextSpan, ...]] | None = None,
        style: str = "class:md.recent",
    ) -> None:
        self._spans = spans or (lambda: ())
        self._style = style

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
                self._style,
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
