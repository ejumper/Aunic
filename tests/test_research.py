from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from aunic.config import ResearchSettings, SearxngSchedulerSettings
from aunic.research import (
    FetchService,
    PageFetchRequest,
    ResearchState,
    SearchService,
    canonicalize_url,
    find_invalid_citation_urls,
)
from aunic.research.searxng_scheduler import SearxngScheduler


def _client_factory(handler):
    transport = httpx.MockTransport(handler)

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    return factory


class FakeClock:
    def __init__(self) -> None:
        self.current = 0.0

    def __call__(self) -> float:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.current += seconds


def test_canonicalize_url_strips_tracking_and_sorts_query_params() -> None:
    url = "HTTPS://Example.com:443/path/?utm_source=x&b=2&a=1#frag"

    assert canonicalize_url(url) == "https://example.com/path?a=1&b=2"


@pytest.mark.asyncio
async def test_search_service_merges_duplicate_urls_and_retains_query_labels() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["q"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.com/article?utm_source=test",
                        "title": "Example Article",
                        "content": f"snippet for {query}",
                        "publishedDate": "2025-01-01",
                    }
                ]
            },
        )

    settings = ResearchSettings(
        searxng_scheduler=SearxngSchedulerSettings(
            preferred_engines=("duckduckgo", "brave"),
            per_engine_reuse_cooldown_seconds=5.0,
            engine_timeout_seconds=3600.0,
        )
    )
    clock = FakeClock()
    service = SearchService(
        settings=settings,
        client_factory=_client_factory(handler),
        scheduler=SearxngScheduler(settings, sleep=clock.sleep, monotonic_fn=clock),
    )
    state = ResearchState()

    batch = await service.search(
        queries=("python release", "python update"),
        depth="balanced",
        freshness="recent",
        purpose="Find the release page.",
        state=state,
    )

    assert len(batch.results) == 1
    assert batch.results[0].source_id == "s1"
    assert batch.results[0].canonical_url == "https://example.com/article"
    assert batch.results[0].query_labels == ("python release", "python update")
    assert batch.results[0].engine_count == 2
    assert batch.failures == ()


@pytest.mark.asyncio
async def test_search_service_ranks_results_deterministically() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["q"]
        if query == "q1":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://example.com/shared",
                            "title": "Shared",
                            "content": "shared result",
                            "publishedDate": "2024-01-01",
                        },
                        {
                            "url": "https://example.com/older",
                            "title": "Older",
                            "content": "older result",
                            "publishedDate": "2024-01-01",
                        },
                        {
                            "url": "https://example.com/zulu",
                            "title": "Zulu",
                            "content": "alphabetical later",
                        },
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.com/shared",
                        "title": "Shared",
                        "content": "shared result again",
                        "publishedDate": "2025-01-01",
                    },
                    {
                        "url": "https://example.com/newer",
                        "title": "Newer",
                        "content": "newer result",
                        "publishedDate": "2025-01-01",
                    },
                    {
                        "url": "https://example.com/alpha",
                        "title": "Alpha",
                        "content": "alphabetical earlier",
                    },
                ]
            },
        )

    settings = ResearchSettings(
        searxng_scheduler=SearxngSchedulerSettings(
            preferred_engines=("duckduckgo", "brave"),
            per_engine_reuse_cooldown_seconds=5.0,
            engine_timeout_seconds=3600.0,
        )
    )
    clock = FakeClock()
    service = SearchService(
        settings=settings,
        client_factory=_client_factory(handler),
        scheduler=SearxngScheduler(settings, sleep=clock.sleep, monotonic_fn=clock),
    )

    batch = await service.search(
        queries=("q1", "q2"),
        depth="balanced",
        freshness="none",
        purpose="Deterministic ranking.",
        state=ResearchState(),
    )

    assert [item.url for item in batch.results] == [
        "https://example.com/shared",
        "https://example.com/newer",
        "https://example.com/older",
        "https://example.com/alpha",
        "https://example.com/zulu",
    ]


@pytest.mark.asyncio
async def test_search_service_retries_zero_results_on_next_engine() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        engine = request.url.params["engines"]
        calls.append(engine)
        if engine == "duckduckgo":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.com/article",
                        "title": "Example Article",
                        "content": "Example snippet",
                    }
                ]
            },
        )

    settings = ResearchSettings(
        searxng_scheduler=SearxngSchedulerSettings(
            preferred_engines=("duckduckgo", "brave"),
            per_engine_reuse_cooldown_seconds=5.0,
            engine_timeout_seconds=3600.0,
        )
    )
    clock = FakeClock()
    service = SearchService(
        settings=settings,
        client_factory=_client_factory(handler),
        scheduler=SearxngScheduler(settings, sleep=clock.sleep, monotonic_fn=clock),
    )
    batch = await service.search(
        queries=("python release",),
        depth="balanced",
        freshness="recent",
        purpose="Find the release page.",
        state=ResearchState(),
    )

    assert calls == ["duckduckgo", "brave"]
    assert [item.title for item in batch.results] == ["Example Article"]
    assert batch.failures == ()


@pytest.mark.asyncio
async def test_search_service_returns_explicit_failure_when_all_engines_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    settings = ResearchSettings(
        searxng_scheduler=SearxngSchedulerSettings(
            preferred_engines=("duckduckgo", "brave"),
            per_engine_reuse_cooldown_seconds=5.0,
            engine_timeout_seconds=3600.0,
        )
    )
    clock = FakeClock()
    service = SearchService(
        settings=settings,
        client_factory=_client_factory(handler),
        scheduler=SearxngScheduler(settings, sleep=clock.sleep, monotonic_fn=clock),
    )
    batch = await service.search(
        queries=("python release",),
        depth="balanced",
        freshness="recent",
        purpose="Find the release page.",
        state=ResearchState(),
    )

    assert batch.results == ()
    assert len(batch.failures) == 1
    assert batch.failures[0].attempted_engines == ("duckduckgo", "brave")


@pytest.mark.asyncio
async def test_search_service_can_return_partial_success_with_query_failures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["q"]
        engine = request.url.params["engines"]
        if query == "good query" and engine == "duckduckgo":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://example.com/good",
                            "title": "Good Result",
                            "content": "good snippet",
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"results": []})

    settings = ResearchSettings(
        searxng_scheduler=SearxngSchedulerSettings(
            preferred_engines=("duckduckgo", "brave"),
            per_engine_reuse_cooldown_seconds=5.0,
            engine_timeout_seconds=3600.0,
        )
    )
    clock = FakeClock()
    service = SearchService(
        settings=settings,
        client_factory=_client_factory(handler),
        scheduler=SearxngScheduler(settings, sleep=clock.sleep, monotonic_fn=clock),
    )
    batch = await service.search(
        queries=("good query", "bad query"),
        depth="balanced",
        freshness="none",
        purpose="Mixed batch.",
        state=ResearchState(),
    )

    assert [item.title for item in batch.results] == ["Good Result"]
    assert len(batch.failures) == 1
    assert batch.failures[0].query == "bad query"


@pytest.mark.asyncio
async def test_search_service_waits_before_reusing_same_engine() -> None:
    call_times: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        call_times.append(clock())
        query = request.url.params["q"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": f"https://example.com/{query.replace(' ', '-')}",
                        "title": query,
                        "content": query,
                    }
                ]
            },
        )

    settings = ResearchSettings(
        search_results_per_query=8,
        searxng_scheduler=SearxngSchedulerSettings(
            preferred_engines=("duckduckgo",),
            per_engine_reuse_cooldown_seconds=5.0,
            engine_timeout_seconds=3600.0,
        ),
    )
    clock = FakeClock()
    service = SearchService(
        settings=settings,
        client_factory=_client_factory(handler),
        scheduler=SearxngScheduler(settings, sleep=clock.sleep, monotonic_fn=clock),
    )
    batch = await service.search(
        queries=("first query", "second query"),
        depth="balanced",
        freshness="none",
        purpose="Reuse pacing.",
        state=ResearchState(),
    )

    assert len(batch.results) == 2
    assert call_times == [0.0, 5.0]


@pytest.mark.asyncio
async def test_fetch_service_fetch_page_converts_and_caches_by_note_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            text="<html><head><title>Example</title></head><body>body</body></html>",
            headers={"content-type": "text/html"},
        )

    cache_home = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    service = FetchService(
        client_factory=_client_factory(handler),
        html_to_markdown=lambda html: "# Example\n\nConverted body.\n",
    )
    state = ResearchState()
    active_file = tmp_path / "note.md"
    active_file.write_text("note\n", encoding="utf-8")

    first = await service.fetch_page(
        PageFetchRequest(url="https://example.com/page"),
        state=state,
        active_file=active_file,
    )
    second = await service.fetch_page(
        PageFetchRequest(url="https://example.com/page"),
        state=state,
        active_file=active_file,
    )

    assert calls == 1
    assert first.title == "Example"
    assert "Converted body." in first.markdown
    assert second.markdown == first.markdown
    cache_root = next((cache_home / "aunic" / "fetch").iterdir())
    manifest = json.loads((cache_root / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["entries"]) == 1
    assert len(state.summary().fetched_pages) == 2


@pytest.mark.asyncio
async def test_fetch_service_cache_is_isolated_per_active_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            text="# Example\n\nScoped cache.\n",
            headers={"content-type": "text/markdown"},
        )

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    service = FetchService(client_factory=_client_factory(handler))
    state = ResearchState()
    note_a = tmp_path / "a.md"
    note_b = tmp_path / "b.md"
    note_a.write_text("a\n", encoding="utf-8")
    note_b.write_text("b\n", encoding="utf-8")

    await service.fetch_page(PageFetchRequest(url="https://example.com/page"), state=state, active_file=note_a)
    await service.fetch_page(PageFetchRequest(url="https://example.com/page"), state=state, active_file=note_b)

    assert calls == 2


@pytest.mark.asyncio
async def test_fetch_service_evicts_lru_entries_when_cache_is_over_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies = {
        "https://example.com/one": "# One\n\n" + ("A" * 220),
        "https://example.com/two": "# Two\n\n" + ("B" * 220),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=bodies[str(request.url)],
            headers={"content-type": "text/markdown"},
        )

    cache_home = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    settings = ResearchSettings(fetch_cache_max_bytes=450)
    service = FetchService(settings=settings, client_factory=_client_factory(handler))
    state = ResearchState()
    active_file = tmp_path / "note.md"
    active_file.write_text("note\n", encoding="utf-8")

    await service.fetch_page(PageFetchRequest(url="https://example.com/one"), state=state, active_file=active_file)
    await service.fetch_page(PageFetchRequest(url="https://example.com/two"), state=state, active_file=active_file)

    cache_root = next((cache_home / "aunic" / "fetch").iterdir())
    manifest = json.loads((cache_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["total_size_bytes"] <= settings.fetch_cache_max_bytes
    assert len(manifest["entries"]) == 1


@pytest.mark.asyncio
async def test_fetch_service_fetch_for_user_selection_returns_ranked_chunks() -> None:
    markdown = (
        "# Python Releases\n\n"
        "Python 3.13 release notes are important.\n\n"
        "This paragraph is less relevant.\n\n"
        "## Release Notes\n\n"
        "Python 3.13 release notes mention packaging changes and installer updates.\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=markdown,
            headers={"content-type": "text/markdown"},
        )

    service = FetchService(client_factory=_client_factory(handler))
    state = ResearchState()

    packet = await service.fetch_for_user_selection(
        query="python release notes",
        url="https://example.com/releases",
        state=state,
    )

    assert packet.source_id == "s1"
    assert packet.chunks
    assert packet.chunks[0].score >= packet.chunks[-1].score
    assert "Python 3.13 release notes" in packet.chunks[0].text
    assert state.summary().fetch_packets == (packet,)


def test_find_invalid_citation_urls_uses_canonical_matching() -> None:
    text = (
        "Supported ([One](https://example.com/path?utm_source=x)) and "
        "unsupported ([Two](https://elsewhere.com/path))."
    )

    invalid = find_invalid_citation_urls(
        text,
        allowed_canonical_urls={"https://example.com/path"},
    )

    assert invalid == ("https://elsewhere.com/path",)


def test_search_service_depth_limits_are_exposed() -> None:
    service = SearchService(
        settings=ResearchSettings(),
        client_factory=_client_factory(lambda request: httpx.Response(200, json={"results": []})),
    )

    assert service.max_queries_for_depth("quick") == 1
    assert service.max_queries_for_depth("balanced") == 3
    assert service.max_queries_for_depth("deep") == 5
