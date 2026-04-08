from __future__ import annotations

from aunic.tui.note_tables import (
    RenderTableCell,
    detect_markdown_table_blocks,
    normalize_markdown_tables,
    render_box_table,
)


def _line_text(fragments) -> str:
    return "".join(fragment[1] for fragment in fragments)


def test_detect_markdown_table_blocks_finds_standard_pipe_table() -> None:
    text = (
        "Intro\n"
        "| Language | Paradigm | Year |\n"
        "| --- | :---: | ---: |\n"
        "| Python | Multi-paradigm | 1991 |\n"
        "| Rust | Systems | 2010 |\n"
        "Outro\n"
    )

    blocks = detect_markdown_table_blocks(text)

    assert len(blocks) == 1
    block = blocks[0]
    assert block.start_row == 1
    assert block.end_row == 4
    assert [cell.text for cell in block.header] == ["Language", "Paradigm", "Year"]
    assert block.rows[0][0].text == "Python"


def test_detect_markdown_table_blocks_ignores_fenced_code_blocks() -> None:
    text = (
        "```\n"
        "| Name | Value |\n"
        "| --- | --- |\n"
        "| A | 1 |\n"
        "```\n"
    )

    assert detect_markdown_table_blocks(text) == ()


def test_normalize_markdown_tables_aligns_valid_tables() -> None:
    text = (
        "| Lang | Year |\n"
        "| --- | --- |\n"
        "| Python | 1991 |\n"
        "| Rust | 2010 |\n"
    )

    normalized = normalize_markdown_tables(text)

    assert normalized == (
        "| Lang   | Year |\n"
        "| :----- | :--- |\n"
        "| Python | 1991 |\n"
        "| Rust   | 2010 |\n"
    )


def test_normalize_markdown_tables_only_touches_intersecting_tables() -> None:
    text = (
        "| A | B |\n"
        "| --- | --- |\n"
        "| one | two |\n"
        "\n"
        "| X | Y |\n"
        "| --- | --- |\n"
        "| alpha | beta |\n"
    )

    normalized = normalize_markdown_tables(text, touched_row_ranges=((0, 2),))

    assert normalized == (
        "| A   | B   |\n"
        "| :-- | :-- |\n"
        "| one | two |\n"
        "\n"
        "| X | Y |\n"
        "| --- | --- |\n"
        "| alpha | beta |\n"
    )


def test_render_box_table_produces_boxed_table_lines() -> None:
    rendered = render_box_table(
        (
            RenderTableCell((("", "Protocol"),), "Protocol", "left"),
            RenderTableCell((("", "Impact"),), "Impact", "left"),
        ),
        (
            (
                RenderTableCell((("", "STP"),), "STP", "left"),
                RenderTableCell((("", "Slow recovery"),), "Slow recovery", "left"),
            ),
        ),
        max_width=60,
        header_style="class:md.bold",
        allow_vertical_fallback=False,
    )

    lines = [_line_text(line.fragments) for line in rendered.lines]

    assert rendered.vertical_fallback is False
    assert lines[0].startswith("┌")
    assert "Protocol" in lines[1]
    assert "Slow recovery" in lines[3]
    assert lines[-1].startswith("└")
