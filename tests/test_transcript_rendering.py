from __future__ import annotations

from aunic.domain import TranscriptRow
from aunic.tui.transcript_renderers import (
    TranscriptRenderContext,
    get_renderer,
    render_bash_result,
    render_chat_message,
    render_fetch_result,
    render_filter_toolbar,
    render_search_result,
)
from aunic.tui.types import TranscriptViewState


def _text(fragments) -> str:
    return "".join(fragment[1] for fragment in fragments)


def _styles(fragments) -> list[str]:
    return [fragment[0] for fragment in fragments]


def _lines(fragments) -> list[str]:
    return _text(fragments).splitlines()


def _bar_positions(line: str) -> list[int]:
    return [index for index, char in enumerate(line) if char == "|"]


def _context(**overrides) -> TranscriptRenderContext:
    base = dict(
        width=80,
        tool_call_index={},
        expanded_rows=set(),
        cached_fetch_urls=set(),
        selected_row_number=None,
        delete_row=lambda row_number: None,
        delete_search_result=lambda row_number, result_index: None,
        toggle_expand=lambda row_number: None,
        set_filter=lambda mode: None,
        toggle_sort=lambda: None,
        toggle_open=lambda: None,
        toggle_maximize=lambda: None,
        open_url=lambda url: None,
        copy_text=lambda text: None,
        copy_cached_fetch=lambda url: None,
    )
    base.update(overrides)
    return TranscriptRenderContext(**base)


def test_render_filter_toolbar_marks_active_filter_and_sort() -> None:
    fragments = render_filter_toolbar(
        TranscriptViewState(filter_mode="tools", sort_order="ascending"),
        _context(),
    )

    text = _text(fragments)
    styles = _styles(fragments)

    assert "[ Chat ]" in text
    assert "[ Tools ]" in text
    assert "[ Search ]" in text
    assert "[ + ]" in text
    assert "[ Ascending ]" in text
    assert "class:transcript.filter.active" in styles
    assert "class:transcript.sort.active" in styles


def test_render_filter_toolbar_shows_minus_when_transcript_is_maximized() -> None:
    fragments = render_filter_toolbar(
        TranscriptViewState(maximized=True),
        _context(),
    )

    assert "[ - ]" in _text(fragments)


def test_render_bash_result_expanded_uses_command_from_tool_call_and_error_style() -> None:
    row = TranscriptRow(
        row_number=3,
        role="tool",
        type="tool_error",
        tool_name="bash",
        tool_id="call_1",
        content={
            "stdout": "line one\nline two",
            "stderr": "bad news",
            "exit_code": 1,
        },
    )
    context = _context(
        expanded_rows={3},
        tool_call_index={
            "call_1": TranscriptRow(
                row_number=2,
                role="assistant",
                type="tool_call",
                tool_name="bash",
                tool_id="call_1",
                content={"command": "pytest -q\npytest -q tests/test_tui.py"},
            )
        },
    )

    fragments = render_bash_result(row, context)
    text = _text(fragments)

    assert "$ pytest -q ..." in text
    assert "line one" in text
    assert "bad news" in text
    assert "exit_code=1" in text
    assert "class:transcript.error" in _styles(fragments)


def test_render_chat_message_renders_basic_markdown_and_content_driven_width() -> None:
    row = TranscriptRow(
        row_number=2,
        role="assistant",
        type="message",
        tool_name=None,
        tool_id=None,
        content="# Header\n\n**bold** and *italic*\n\n- item\n1. number",
    )

    fragments = render_chat_message(row, _context(width=120))
    text = _text(fragments)
    styles = _styles(fragments)
    lines = _lines(fragments)

    assert "Header" in text
    assert "bold and italic" in text
    assert "**bold**" not in text
    assert "*italic*" not in text
    assert " - item" in text
    assert " 1. number" in text
    assert any("transcript.chat.heading" in style for style in styles)
    assert any("transcript.chat.bold" in style for style in styles)
    assert any("transcript.chat.italic" in style for style in styles)
    assert any("┌" in line for line in lines)
    assert max(len(line) for line in lines) < 70


def test_render_chat_message_renders_markdown_tables() -> None:
    row = TranscriptRow(
        row_number=12,
        role="assistant",
        type="message",
        tool_name=None,
        tool_id=None,
        content=(
            "| Language | Paradigm | Year Created |\n"
            "| - | - | - |\n"
            "| Python | Multi-paradigm | 1991 |\n"
            "| Rust | Systems / Multi-paradigm | 2010 |"
        ),
    )

    fragments = render_chat_message(row, _context(width=100))
    text = _text(fragments)
    lines = _lines(fragments)

    assert "Language" in text
    assert "Paradigm" in text
    assert "Year Created" in text
    assert "Python" in text
    assert "Rust" in text
    assert "| Language | Paradigm |" not in text
    assert "┌" in text
    assert "┬" in text
    assert "┼" in text
    assert "┴" in text
    table_lines = [line for line in lines if "Language" in line or "Python" in line or "Rust" in line]
    assert len(table_lines) >= 3
    table_bar_positions = [_bar_positions(line) for line in table_lines]
    assert table_bar_positions[0] == table_bar_positions[1] == table_bar_positions[2]


def test_render_search_result_expanded_marks_cached_titles() -> None:
    row = TranscriptRow(
        row_number=4,
        role="tool",
        type="tool_result",
        tool_name="web_search",
        tool_id="call_2",
        content=[
            {
                "title": "Python",
                "url": "https://www.python.org/",
                "snippet": "Official Python website.",
            },
            {
                "title": "Docs",
                "url": "https://docs.python.org/3/",
                "snippet": "Python documentation.",
            },
        ],
    )
    context = _context(
        expanded_rows={4},
        cached_fetch_urls={"https://www.python.org/"},
        tool_call_index={
            "call_2": TranscriptRow(
                row_number=3,
                role="assistant",
                type="tool_call",
                tool_name="web_search",
                tool_id="call_2",
                content={"queries": ["python homepage"]},
            )
        },
    )

    fragments = render_search_result(row, context)
    text = _text(fragments)
    styles = _styles(fragments)
    lines = _lines(fragments)

    assert "python homepage" in text
    assert "Python" in text
    assert "Official Python website." not in text
    assert "www.python.org" in text
    assert "docs.python.org" in text
    assert "|↗" in text
    assert " X  |" not in text
    assert "Search |" in lines[0]
    assert "| 2 |" not in lines[0]
    assert "| 1 |" in lines[1]
    assert "| 2 |" in lines[2]
    assert lines[0].endswith("|[^]")
    assert lines[1].endswith("|↗")
    assert lines[2].endswith("|↗")
    assert "class:transcript.link.cached" in styles
    assert len(lines) == 3
    assert _bar_positions(lines[0])[0] == _bar_positions(lines[1])[1] == _bar_positions(lines[2])[1]
    assert _bar_positions(lines[0])[-1] == _bar_positions(lines[1])[-1] == _bar_positions(lines[2])[-1]
    cached_title_fragment = next(fragment for fragment in fragments if "Python" in fragment[1] and "link.cached" in fragment[0])
    uncached_title_fragment = next(fragment for fragment in fragments if "Docs" in fragment[1] and "link.cached" not in fragment[0])
    assert len(cached_title_fragment) == 3
    assert len(uncached_title_fragment) == 2


def test_render_search_result_uses_domain_placeholder_for_malformed_url() -> None:
    row = TranscriptRow(
        row_number=6,
        role="tool",
        type="tool_result",
        tool_name="web_search",
        tool_id="call_4",
        content=[
            {
                "title": "Broken",
                "url": "bad url",
                "snippet": "Ignored snippet",
            }
        ],
    )
    context = _context(
        expanded_rows={6},
        tool_call_index={
            "call_4": TranscriptRow(
                row_number=5,
                role="assistant",
                type="tool_call",
                tool_name="web_search",
                tool_id="call_4",
                content={"queries": ["broken"]},
            )
        },
    )

    fragments = render_search_result(row, context)

    assert "(unknown)" in _text(fragments)


def test_render_search_result_responsive_layout_and_count_width() -> None:
    results = [
        {
            "title": f"Result {index} title that is intentionally long",
            "url": f"https://docs{index}.example.com/path",
            "snippet": f"snippet {index}",
        }
        for index in range(1, 13)
    ]
    row = TranscriptRow(
        row_number=7,
        role="tool",
        type="tool_result",
        tool_name="web_search",
        tool_id="call_5",
        content=results,
    )
    context = _context(
        width=60,
        expanded_rows={7},
        tool_call_index={
            "call_5": TranscriptRow(
                row_number=6,
                role="assistant",
                type="tool_call",
                tool_name="web_search",
                tool_id="call_5",
                content={"queries": ["very long query for layout validation"]},
            )
        },
    )

    lines = _lines(render_search_result(row, context))

    assert len(lines) == 13
    first_result_bars = _bar_positions(lines[1])
    tenth_result_bars = _bar_positions(lines[10])
    assert len(first_result_bars) == 4
    assert len(tenth_result_bars) == 4
    assert first_result_bars == tenth_result_bars
    assert lines[1].startswith("    X ")
    assert lines[10].startswith("    X ")
    assert len(lines[1]) <= 60
    assert len(lines[10]) <= 60
    assert "Search |" in lines[0]
    assert "|12 |" not in lines[0]
    assert "| 1 |" in lines[1]
    assert "|10 |" in lines[10]
    assert lines[0].endswith("|[^]")
    assert lines[1].endswith("|↗")
    assert lines[10].endswith("|↗")


def test_render_search_result_collapsed_summary_uses_aligned_header_layout() -> None:
    row = TranscriptRow(
        row_number=8,
        role="tool",
        type="tool_result",
        tool_name="web_search",
        tool_id="call_6",
        content=[
            {
                "title": "Python",
                "url": "https://www.python.org/",
                "snippet": "Official Python website.",
            }
        ],
    )
    context = _context(
        tool_call_index={
            "call_6": TranscriptRow(
                row_number=7,
                role="assistant",
                type="tool_call",
                tool_name="web_search",
                tool_id="call_6",
                content={"queries": ["python homepage"]},
            )
        },
    )

    lines = _lines(render_search_result(row, context))

    assert len(lines) == 1
    assert "[v]" in lines[0]
    assert "python homepage" in lines[0]
    assert "Search |" in lines[0]
    assert lines[0].endswith("|[v]")
    assert _bar_positions(lines[0]) == sorted(_bar_positions(lines[0]))


def test_render_search_result_caps_row_width_at_110_columns() -> None:
    row = TranscriptRow(
        row_number=9,
        role="tool",
        type="tool_result",
        tool_name="web_search",
        tool_id="call_7",
        content=[
            {
                "title": "A very long title that should still be clipped inside the maximum row width limit",
                "url": "https://very.long.domain.example.com/path/that/is/not/shown",
                "snippet": "ignored",
            }
        ],
    )
    context = _context(
        width=160,
        expanded_rows={9},
        tool_call_index={
            "call_7": TranscriptRow(
                row_number=8,
                role="assistant",
                type="tool_call",
                tool_name="web_search",
                tool_id="call_7",
                content={"queries": ["a long query that should also stay within the hard cap for search rows"]},
            )
        },
    )

    lines = _lines(render_search_result(row, context))

    assert len(lines[0]) <= 110
    assert len(lines[1]) <= 110


def test_render_fetch_result_marks_cached_fetch_title() -> None:
    row = TranscriptRow(
        row_number=5,
        role="tool",
        type="tool_result",
        tool_name="web_fetch",
        tool_id="call_3",
        content={
            "title": "Python",
            "url": "https://www.python.org/",
            "snippet": "The official home of the Python Programming Language.",
        },
    )

    fragments = render_fetch_result(
        row,
        _context(cached_fetch_urls={"https://www.python.org/"}),
    )

    text = _text(fragments)
    lines = _lines(fragments)

    assert "Fetch |" in text
    assert "www.python.org" in text
    assert "official home" not in text
    assert lines[0].startswith(" X  Fetch |")
    assert lines[0].endswith("|↗")
    assert len(lines[0]) <= 80
    assert "class:transcript.link.cached" in _styles(fragments)


def test_render_fetch_result_aligns_with_search_header_and_caps_width() -> None:
    search_row = TranscriptRow(
        row_number=10,
        role="tool",
        type="tool_result",
        tool_name="web_search",
        tool_id="call_8",
        content=[
            {
                "title": "A long title",
                "url": "https://very.long.domain.example.com/path",
                "snippet": "ignored",
            }
        ],
    )
    fetch_row = TranscriptRow(
        row_number=11,
        role="tool",
        type="tool_result",
        tool_name="web_fetch",
        tool_id="call_9",
        content={
            "title": "A fetched page title that should be clipped inside the same maximum width as search rows",
            "url": "https://very.long.domain.example.com/path",
            "snippet": "ignored",
        },
    )

    search_lines = _lines(
        render_search_result(
            search_row,
            _context(
                width=160,
                expanded_rows={10},
                tool_call_index={
                    "call_8": TranscriptRow(
                        row_number=9,
                        role="assistant",
                        type="tool_call",
                        tool_name="web_search",
                        tool_id="call_8",
                        content={"queries": ["query text"]},
                    )
                },
            ),
        )
    )
    search_header_line = search_lines[0]
    search_result_line = search_lines[1]
    fetch_line = _lines(render_fetch_result(fetch_row, _context(width=160)))[0]

    assert _bar_positions(fetch_line)[0] == _bar_positions(search_header_line)[0]
    assert _bar_positions(fetch_line)[1] == _bar_positions(search_result_line)[2]
    assert _bar_positions(fetch_line)[-1] == _bar_positions(search_result_line)[-1]
    assert fetch_line.endswith("|↗")
    assert len(fetch_line) <= 110


def test_get_renderer_dispatches_expected_handlers() -> None:
    assert get_renderer(TranscriptRow(1, "assistant", "tool_call", "bash", "call", {})) is None
    assert get_renderer(TranscriptRow(1, "assistant", "message", None, None, "hi")) is not None
    assert get_renderer(TranscriptRow(1, "tool", "tool_result", "bash", "call", {})) is render_bash_result
    assert get_renderer(TranscriptRow(1, "tool", "tool_result", "web_search", "call", [])) is render_search_result
    assert get_renderer(TranscriptRow(1, "tool", "tool_result", "web_fetch", "call", {})) is render_fetch_result
