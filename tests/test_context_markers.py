from __future__ import annotations

import hashlib
from pathlib import Path

from aunic.context.markers import analyze_note_file, parsed_span_for_raw_span, text_for_raw_span
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
