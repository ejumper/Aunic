from __future__ import annotations

import hashlib
from pathlib import Path

from aunic.context.markers import _HIDDEN_HINT, analyze_note_file, parsed_span_for_raw_span, text_for_raw_span
from aunic.context.types import FileSnapshot, TextSpan


def _snapshot(path: Path, text: str) -> FileSnapshot:
    return FileSnapshot(
        path=path,
        raw_text=text,
        revision_id="test-revision",
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        mtime_ns=1,
        size_bytes=len(text.encode("utf-8")),
    )


def test_marker_parser_applies_layered_visibility_and_labels(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    text = (
        "# Note\n"
        "Intro paragraph.\n"
        "$>>@>>locked<<@<<$\n"
        "@>>editable<<@\n"
        "%>>hidden<<%\n"
    )
    analysis = analyze_note_file(_snapshot(path, text), "note.md")

    assert "@>>" not in analysis.parsed_file.parsed_text
    assert "$>>" not in analysis.parsed_file.parsed_text
    assert "%>>" not in analysis.parsed_file.parsed_text
    assert "editable" in analysis.parsed_file.parsed_text
    assert "hidden" not in analysis.parsed_file.parsed_text

    intro_index = text.index("Intro")
    locked_index = text.index("locked")
    editable_index = text.index("editable")
    hidden_index = text.index("hidden")

    assert analysis.labels_by_char[intro_index] == "READ_ONLY-NO_EDITS"
    assert analysis.labels_by_char[locked_index] == "READ_ONLY-NO_EDITS"
    assert analysis.labels_by_char[editable_index] == "WRITE-EDIT_ALLOWED"
    assert analysis.labels_by_char[hidden_index] == "HIDDEN"


def test_marker_parser_keeps_unmatched_markers_literal_and_warns(tmp_path: Path) -> None:
    path = tmp_path / "broken.md"
    text = "alpha %>> broken marker"
    analysis = analyze_note_file(_snapshot(path, text), "broken.md")

    assert analysis.parsed_file.parsed_text == text
    assert len(analysis.parsed_file.warnings) == 1
    warning = analysis.parsed_file.warnings[0]
    assert warning.code == "unmatched_open_marker"
    assert warning.line == 1
    assert warning.column == 7


def test_marker_parser_treats_inline_prompt_markers_as_literal_text(tmp_path: Path) -> None:
    path = tmp_path / "prompt.md"
    text = "Intro\n>>Prompt from note<<\nTail\n"
    analysis = analyze_note_file(_snapshot(path, text), "prompt.md")

    assert analysis.prompt_visible_spans == ()
    assert analysis.parsed_file.parsed_text == text

    raw_prompt_span = TextSpan(text.index(">>"), text.index("<<") + 2)
    parsed_span = parsed_span_for_raw_span(raw_prompt_span, analysis.parsed_file.source_map)

    assert parsed_span is not None
    assert (
        analysis.parsed_file.parsed_text[parsed_span.start : parsed_span.end]
        == ">>Prompt from note<<"
    )


# --- HTML hint injection tests ---


def test_hint_injected_for_exclude_block_with_content(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    text = "before\n%>>hidden stuff<<%\nafter\n"
    analysis = analyze_note_file(_snapshot(path, text), "note.md")

    hinted = analysis.parsed_file.hinted_parsed_text
    assert _HIDDEN_HINT in hinted
    assert "before" in hinted
    assert "after" in hinted
    assert "hidden stuff" not in hinted


def test_no_hint_for_empty_exclude_block(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    text = "before\n%>><<%\nafter\n"
    analysis = analyze_note_file(_snapshot(path, text), "note.md")

    assert _HIDDEN_HINT not in analysis.parsed_file.hinted_parsed_text


def test_hint_at_start_for_include_only_with_content_before(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    text = "above content\n!>>visible section<<!below content\n"
    analysis = analyze_note_file(_snapshot(path, text), "note.md")

    hinted = analysis.parsed_file.hinted_parsed_text
    assert _HIDDEN_HINT in hinted
    assert "visible section" in hinted
    assert "above content" not in hinted
    assert "below content" not in hinted
    # Hint should appear before the visible content (hidden content was above)
    assert hinted.index(_HIDDEN_HINT) < hinted.index("visible section")


def test_hint_at_end_for_include_only_with_content_after(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    text = "!>>visible section<<!trailing hidden\n"
    analysis = analyze_note_file(_snapshot(path, text), "note.md")

    hinted = analysis.parsed_file.hinted_parsed_text
    assert _HIDDEN_HINT in hinted
    assert "visible section" in hinted
    assert "trailing hidden" not in hinted
    # Hint should appear after the visible content (hidden content was below)
    assert hinted.index("visible section") < hinted.index(_HIDDEN_HINT)


def test_no_hint_when_no_markers(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    text = "plain note content\n"
    analysis = analyze_note_file(_snapshot(path, text), "note.md")

    assert analysis.parsed_file.hinted_parsed_text == text


def test_hint_for_exclude_at_top_no_visible_before(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    text = "%>>secret header<<%\nvisible body\n"
    analysis = analyze_note_file(_snapshot(path, text), "note.md")

    hinted = analysis.parsed_file.hinted_parsed_text
    assert _HIDDEN_HINT in hinted
    assert "visible body" in hinted
    assert "secret header" not in hinted
    # Hint appears before the visible body since the hidden block is at the top
    assert hinted.index(_HIDDEN_HINT) < hinted.index("visible body")
