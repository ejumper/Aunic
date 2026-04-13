from __future__ import annotations

from aunic.map.snippet import compute_auto_snippet


def _note_with_transcript(content: str) -> str:
    return f"{content}\n\n---\n# Transcript\n\nsome transcript row\n"


def test_empty_note_returns_empty_sentinel() -> None:
    assert compute_auto_snippet("") == "(empty)"


def test_whitespace_only_returns_empty_sentinel() -> None:
    assert compute_auto_snippet("   \n\n\t") == "(empty)"


def test_short_note_returned_verbatim() -> None:
    text = "Hello, world!"
    assert compute_auto_snippet(text) == text


def test_long_note_truncated_with_ellipsis() -> None:
    text = "x" * 300
    result = compute_auto_snippet(text)
    assert result.endswith("…")
    # The content before ellipsis should be exactly max_len chars
    assert len(result) == 201  # 200 + "…"


def test_custom_max_len() -> None:
    text = "a" * 50
    result = compute_auto_snippet(text, max_len=20)
    assert result == "a" * 20 + "…"


def test_whitespace_collapsed() -> None:
    text = "Hello\n\nWorld\t\tFoo"
    result = compute_auto_snippet(text)
    assert result == "Hello World Foo"


def test_transcript_half_not_included() -> None:
    text = _note_with_transcript("Note content here")
    result = compute_auto_snippet(text)
    assert "Note content here" in result
    assert "Transcript" not in result
    assert "transcript row" not in result


def test_frontmatter_stripped() -> None:
    text = "---\ntitle: My Note\ndate: 2026-01-01\n---\nActual content here."
    result = compute_auto_snippet(text)
    assert "title" not in result
    assert "Actual content here." in result


def test_frontmatter_not_stripped_if_no_closing_fence() -> None:
    # Without the closing ---, it's not treated as frontmatter
    text = "---\ntitle: My Note\nActual content here."
    result = compute_auto_snippet(text)
    # No crash, just returns the raw text snippet
    assert result  # non-empty


def test_deterministic() -> None:
    text = "The quick brown fox jumps over the lazy dog"
    assert compute_auto_snippet(text) == compute_auto_snippet(text)
