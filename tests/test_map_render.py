from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from aunic.map.render import MapEntry, parse_map, render_map


def _dt() -> datetime:
    return datetime(2026, 4, 11, 17, 22, 4, tzinfo=UTC)


def _entry(path: Path, snippet: str = "A snippet", mtime_ns: int = 12345, locked: bool = False) -> MapEntry:
    return MapEntry(path=path, snippet=snippet, mtime_ns=mtime_ns, locked=locked)


# ---------------------------------------------------------------------------
# render_map
# ---------------------------------------------------------------------------


def test_render_empty() -> None:
    text = render_map({}, walk_root=Path("/home/user"), generated_at=_dt())
    assert "# Aunic note map" in text
    assert "0 notes" in text


def test_render_single_entry() -> None:
    path = Path("/home/user/notes/bgp.md")
    entries = {path: _entry(path)}
    text = render_map(entries, walk_root=Path("/home/user"), generated_at=_dt())
    assert "## /home/user/notes/" in text
    assert "bgp.md" in text
    assert "A snippet" in text
    assert "<!-- aunic-map mtime=12345 -->" in text


def test_render_locked_entry_has_locked_sentinel() -> None:
    path = Path("/home/user/notes/bgp.md")
    entries = {path: _entry(path, locked=True, mtime_ns=0)}
    text = render_map(entries, walk_root=Path("/home/user"), generated_at=_dt())
    assert "locked=true" in text
    assert "mtime=0" in text


def test_render_sorts_by_directory_then_filename() -> None:
    pa = Path("/home/user/z/alpha.md")
    pb = Path("/home/user/a/beta.md")
    pc = Path("/home/user/a/alpha.md")
    entries = {pa: _entry(pa), pb: _entry(pb), pc: _entry(pc)}
    text = render_map(entries, walk_root=Path("/home/user"), generated_at=_dt())
    lines = [l for l in text.splitlines() if l.startswith("## ") or l.startswith("- ")]
    # /home/user/a/ should come before /home/user/z/
    a_idx = next(i for i, l in enumerate(lines) if "a/" in l and l.startswith("##"))
    z_idx = next(i for i, l in enumerate(lines) if "z/" in l and l.startswith("##"))
    assert a_idx < z_idx
    # Within /home/user/a/, alpha.md before beta.md
    alpha_idx = next(i for i, l in enumerate(lines) if "alpha.md" in l)
    beta_idx = next(i for i, l in enumerate(lines) if "beta.md" in l)
    assert alpha_idx < beta_idx


def test_render_generated_at_timestamp() -> None:
    text = render_map({}, walk_root=Path("/home/user"), generated_at=_dt())
    assert "2026-04-11T17:22:04Z" in text


# ---------------------------------------------------------------------------
# parse_map
# ---------------------------------------------------------------------------


def test_parse_empty_file() -> None:
    assert parse_map("") == {}


def test_parse_map_header_lines_ignored() -> None:
    text = "# Aunic note map\n\nGenerated: 2026-04-11T17:22:04Z from /home/user (0 notes).\n"
    assert parse_map(text) == {}


def test_parse_round_trip() -> None:
    paths = [
        Path("/home/user/notes/bgp.md"),
        Path("/home/user/notes/ospf.md"),
        Path("/home/user/homelab/docker.md"),
    ]
    entries = {p: _entry(p, snippet=f"Snippet for {p.name}", mtime_ns=i * 1000) for i, p in enumerate(paths)}
    text = render_map(entries, walk_root=Path("/home/user"), generated_at=_dt())
    parsed = parse_map(text)
    assert set(parsed.keys()) == set(paths)
    for p in paths:
        assert parsed[p].snippet == entries[p].snippet
        assert parsed[p].mtime_ns == entries[p].mtime_ns
        assert parsed[p].locked == entries[p].locked


def test_parse_locked_round_trip() -> None:
    path = Path("/home/user/notes/pinned.md")
    entries = {path: _entry(path, snippet="Pinned summary", mtime_ns=0, locked=True)}
    text = render_map(entries, walk_root=Path("/home/user"), generated_at=_dt())
    parsed = parse_map(text)
    assert parsed[path].locked is True
    assert parsed[path].mtime_ns == 0


def test_parse_malformed_lines_skipped() -> None:
    text = "# Aunic note map\n\n- not a real entry\nrandom garbage\n"
    assert parse_map(text) == {}


def test_parse_snippet_with_dashes() -> None:
    """Snippets may contain em-dashes; separator is ' — ' (em-dash surrounded by spaces)."""
    path = Path("/home/user/notes/bgp.md")
    entries = {path: _entry(path, snippet="BGP config — ECMP setup")}
    text = render_map(entries, walk_root=Path("/home/user"), generated_at=_dt())
    parsed = parse_map(text)
    assert parsed[path].snippet == "BGP config — ECMP setup"
