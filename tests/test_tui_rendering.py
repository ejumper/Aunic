from __future__ import annotations

from aunic.tui.rendering import lex_markdown_line, soft_wrap_prefix_for_line


def _styles(fragments) -> list[str]:
    return [fragment[0] for fragment in fragments]


def test_lex_markdown_line_highlights_editor_markers() -> None:
    fragments = lex_markdown_line("@>>Editable<<@\n")

    styles = _styles(fragments)

    assert "class:marker.write" in styles


def test_lex_markdown_line_highlights_inline_markdown() -> None:
    fragments = lex_markdown_line("This has *italics*, **bold**, ***both***, and `code`.\n")

    styles = _styles(fragments)

    assert "class:md.italic" in styles
    assert "class:md.bold" in styles
    assert "class:md.bolditalic" in styles
    assert "class:md.code" in styles


def test_soft_wrap_prefix_for_list_lines_matches_marker_indent() -> None:
    prefix = soft_wrap_prefix_for_line("  - item text", wrap_count=1)

    assert prefix == "    "


def test_soft_wrap_prefix_for_plain_indented_lines_resets_to_column_zero() -> None:
    prefix = soft_wrap_prefix_for_line("    indented paragraph", wrap_count=1)

    assert prefix == ""


def test_soft_wrap_prefix_for_fenced_code_blocks_preserves_indent() -> None:
    prefix = soft_wrap_prefix_for_line("    indented code", wrap_count=1, in_code_block=True)

    assert prefix == "    "
