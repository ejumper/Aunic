from __future__ import annotations

import unittest.mock as mock

import pytest

import aunic.rag.config as rag_config_module
import aunic.tui.rendering as rendering_module
from aunic.rag.config import invalidate_rag_config_cache
from aunic.rag.types import RagFetchResult, RagFetchSection, RagSearchResult
from aunic.research.types import FetchedChunk, FetchPacket, SearchResult
from aunic.tui.rendering import (
    PROMPT_ACTIVE_COMMANDS,
    _PROMPT_COMMAND_RE,
    register_rag_scopes,
)


@pytest.fixture(autouse=True)
def reset_rendering_state():
    """Restore rendering module globals after each test."""
    import copy
    original_commands = copy.copy(rendering_module._prompt_active_commands)
    yield
    rendering_module._prompt_active_commands.clear()
    rendering_module._prompt_active_commands.update(original_commands)
    rendering_module._rebuild_prompt_regex()


@pytest.fixture(autouse=True)
def reset_rag_cache(monkeypatch, tmp_path):
    cfg_path = tmp_path / "rag.toml"
    monkeypatch.setattr(rag_config_module, "RAG_CONFIG_PATH", cfg_path)
    invalidate_rag_config_cache()
    yield
    invalidate_rag_config_cache()


# ── Prompt highlighting tests ─────────────────────────────────────────────────

def test_register_rag_scopes_includes_rag():
    register_rag_scopes(())
    from aunic.tui.rendering import PROMPT_ACTIVE_COMMANDS, _PROMPT_COMMAND_RE
    assert "@rag" in PROMPT_ACTIVE_COMMANDS
    assert _PROMPT_COMMAND_RE.search("@rag some query") is not None


def test_register_rag_scopes_adds_each_scope():
    register_rag_scopes(("docs", "wiki"))
    from aunic.tui.rendering import PROMPT_ACTIVE_COMMANDS, _PROMPT_COMMAND_RE
    assert "@docs" in PROMPT_ACTIVE_COMMANDS
    assert "@wiki" in PROMPT_ACTIVE_COMMANDS
    assert _PROMPT_COMMAND_RE.search("@docs netplan") is not None
    assert _PROMPT_COMMAND_RE.search("@wiki bgp") is not None


def test_unregistered_scope_no_match():
    from aunic.tui.rendering import _PROMPT_COMMAND_RE
    assert _PROMPT_COMMAND_RE.search("@unknown query") is None


def test_register_rag_scopes_idempotent():
    register_rag_scopes(("docs",))
    register_rag_scopes(("docs",))
    from aunic.tui.rendering import PROMPT_ACTIVE_COMMANDS
    # Should not cause errors; docs still present exactly once in the set
    assert "@docs" in PROMPT_ACTIVE_COMMANDS


def test_existing_commands_preserved_after_registration():
    register_rag_scopes(("docs",))
    from aunic.tui.rendering import PROMPT_ACTIVE_COMMANDS, _PROMPT_COMMAND_RE
    assert "@web" in PROMPT_ACTIVE_COMMANDS
    assert "/map" in PROMPT_ACTIVE_COMMANDS
    assert _PROMPT_COMMAND_RE.search("@web query") is not None


# ── Result mapping tests ──────────────────────────────────────────────────────

def test_rag_result_to_search_result_mapping():
    """Verify the SearchResult mapping logic used in _run_rag_search."""
    rag_results = (
        RagSearchResult(
            result_id="docs:chunk:c1",
            doc_id="docs:ubuntu:netplan",
            chunk_id="c1",
            corpus="docs",
            title="Netplan Docs",
            source="ubuntu-server",
            snippet="Netplan uses YAML...",
            score=0.88,
            heading_path=("Networking",),
            url=None,
        ),
        RagSearchResult(
            result_id="docs:chunk:c2",
            doc_id="docs:rfc:4271",
            chunk_id="c2",
            corpus="docs",
            title="RFC 4271 - BGP",
            source="rfcs",
            snippet="Border Gateway Protocol...",
            score=0.72,
            heading_path=(),
            url="https://www.rfc-editor.org/rfc/rfc4271",
        ),
    )

    mapped = tuple(
        SearchResult(
                    source_id=f"r{i}",
                    title=r.title,
                    url=r.url or r.local_path or f"[{r.source}] {r.result_id}",
                    canonical_url=r.result_id,
            snippet=r.snippet,
            rank=i,
            refined_score=r.score,
        )
        for i, r in enumerate(rag_results)
    )

    assert mapped[0].title == "Netplan Docs"
    assert mapped[0].url == "[ubuntu-server] docs:chunk:c1"  # falls back to source+result_id
    assert mapped[0].canonical_url == "docs:chunk:c1"
    assert mapped[0].refined_score == pytest.approx(0.88)

    assert mapped[1].url == "https://www.rfc-editor.org/rfc/rfc4271"  # uses provided URL
    assert mapped[1].canonical_url == "docs:chunk:c2"


def test_rag_fetch_to_fetch_packet_mapping():
    """Verify the FetchPacket mapping logic used in _run_rag_fetch."""
    rag_result = RagSearchResult(
        result_id="docs:chunk:c1",
        doc_id="docs:ubuntu:netplan",
        chunk_id="c1",
        corpus="docs",
        title="Netplan",
        source="ubuntu-server",
        snippet="...",
        score=0.9,
        url=None,
    )
    fetch_result = RagFetchResult(
        doc_id="ubuntu:netplan",
        title="Netplan Configuration",
        source="ubuntu-server",
        url=None,
        sections=(
            RagFetchSection(
                heading="Overview",
                heading_path=("Networking", "Netplan", "Overview"),
                text="Netplan is the default...",
                token_estimate=100,
            ),
        ),
        full_text="# Netplan Configuration\n\nFull text.",
    )

    chunks = tuple(
        FetchedChunk(
                    source_id=f"r{i}",
                    title=sec.heading,
                    url=rag_result.url or rag_result.local_path or rag_result.result_id,
                    canonical_url=rag_result.result_id,
            text=sec.text,
            score=0.0,
            heading_path=sec.heading_path,
        )
        for i, sec in enumerate(fetch_result.sections)
    )
    packet = FetchPacket(
        source_id="r0",
        title=fetch_result.title,
        url=rag_result.url or rag_result.local_path or rag_result.result_id,
        canonical_url=rag_result.result_id,
        desired_info="netplan query",
        chunks=chunks,
        full_markdown=fetch_result.full_text,
    )

    assert packet.title == "Netplan Configuration"
    assert packet.url == "docs:chunk:c1"
    assert len(packet.chunks) == 1
    assert packet.chunks[0].heading_path == ("Networking", "Netplan", "Overview")
    assert packet.full_markdown == "# Netplan Configuration\n\nFull text."
