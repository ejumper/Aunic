from __future__ import annotations

from aunic.domain import TranscriptRow
from aunic.tui.transcript_renderers import (
    TranscriptRenderContext,
    get_renderer,
    render_bash_result,
    render_fetch_result,
    render_filter_toolbar,
    render_search_result,
)
from aunic.tui.types import TranscriptViewState


def _text(fragments) -> str:
    return "".join(fragment[1] for fragment in fragments)


def _styles(fragments) -> list[str]:
    return [fragment[0] for fragment in fragments]


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
    assert "[ Ascending ]" in text
    assert "class:transcript.filter.active" in styles
    assert "class:transcript.sort.active" in styles


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

    assert "python homepage" in text
    assert "Python" in text
    assert "Official Python website." in text
    assert "↗" in text
    assert "class:transcript.link.cached" in styles


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

    assert "Python" in _text(fragments)
    assert "official home" in _text(fragments)
    assert "class:transcript.link.cached" in _styles(fragments)


def test_get_renderer_dispatches_expected_handlers() -> None:
    assert get_renderer(TranscriptRow(1, "assistant", "tool_call", "bash", "call", {})) is None
    assert get_renderer(TranscriptRow(1, "assistant", "message", None, None, "hi")) is not None
    assert get_renderer(TranscriptRow(1, "tool", "tool_result", "bash", "call", {})) is render_bash_result
    assert get_renderer(TranscriptRow(1, "tool", "tool_result", "web_search", "call", [])) is render_search_result
    assert get_renderer(TranscriptRow(1, "tool", "tool_result", "web_fetch", "call", {})) is render_fetch_result
