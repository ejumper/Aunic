from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SearchDepth = Literal["quick", "balanced", "deep"]
SearchFreshness = Literal["none", "recent", "very_recent"]


@dataclass(frozen=True)
class SearchResult:
    source_id: str
    title: str
    url: str
    canonical_url: str
    snippet: str
    rank: int
    engine_count: int = 1
    refined_score: float = 0.0
    query_labels: tuple[str, ...] = ()
    category_labels: tuple[str, ...] = ()
    date: str | None = None


@dataclass(frozen=True)
class SearchQueryFailure:
    query: str
    attempted_engines: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class SearchBatch:
    queries: tuple[str, ...]
    depth: SearchDepth
    freshness: SearchFreshness
    purpose: str
    results: tuple[SearchResult, ...]
    failures: tuple[SearchQueryFailure, ...] = ()


@dataclass(frozen=True)
class FetchedChunk:
    source_id: str
    title: str
    url: str
    canonical_url: str
    text: str
    score: float
    heading_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class FetchPacket:
    source_id: str
    title: str
    url: str
    canonical_url: str
    desired_info: str
    chunks: tuple[FetchedChunk, ...]
    full_markdown: str = ""


@dataclass(frozen=True)
class FetchFailure:
    url: str
    message: str
    source_id: str | None = None


@dataclass(frozen=True)
class FetchResult:
    packets: tuple[FetchPacket, ...] = ()
    failures: tuple[FetchFailure, ...] = ()


@dataclass(frozen=True)
class PageFetchRequest:
    url: str


@dataclass(frozen=True)
class PageFetchResult:
    url: str
    canonical_url: str
    title: str
    snippet: str
    markdown: str
    truncated: bool = False


@dataclass(frozen=True)
class ResearchSummary:
    search_batches: tuple[SearchBatch, ...] = ()
    fetch_packets: tuple[FetchPacket, ...] = ()
    fetch_failures: tuple[FetchFailure, ...] = ()
    fetched_pages: tuple[PageFetchResult, ...] = ()


@dataclass(frozen=True)
class ResearchSource:
    source_id: str
    title: str
    url: str
    canonical_url: str


class ResearchState:
    def __init__(self) -> None:
        self._next_source_index = 1
        self._sources_by_id: dict[str, ResearchSource] = {}
        self._source_id_by_canonical_url: dict[str, str] = {}
        self._search_batches: list[SearchBatch] = []
        self._fetch_packets: list[FetchPacket] = []
        self._fetch_failures: list[FetchFailure] = []
        self._fetched_pages: list[PageFetchResult] = []

    def ensure_source(
        self,
        *,
        title: str,
        url: str,
        canonical_url: str,
    ) -> ResearchSource:
        existing_source_id = self._source_id_by_canonical_url.get(canonical_url)
        if existing_source_id is not None:
            existing = self._sources_by_id[existing_source_id]
            updated = ResearchSource(
                source_id=existing.source_id,
                title=title or existing.title,
                url=url or existing.url,
                canonical_url=canonical_url,
            )
            self._sources_by_id[existing_source_id] = updated
            return updated

        source_id = f"s{self._next_source_index}"
        self._next_source_index += 1
        source = ResearchSource(
            source_id=source_id,
            title=title,
            url=url,
            canonical_url=canonical_url,
        )
        self._sources_by_id[source_id] = source
        self._source_id_by_canonical_url[canonical_url] = source_id
        return source

    def record_search_batch(self, batch: SearchBatch) -> None:
        self._search_batches.append(batch)

    def record_fetch_result(self, result: FetchResult) -> None:
        self._fetch_packets.extend(result.packets)
        self._fetch_failures.extend(result.failures)

    def record_fetched_page(self, result: PageFetchResult) -> None:
        self._fetched_pages.append(result)

    def resolve_source(self, source_id: str) -> ResearchSource | None:
        return self._sources_by_id.get(source_id)

    def known_source_ids(self) -> set[str]:
        return set(self._sources_by_id)

    def known_citation_urls(self) -> set[str]:
        urls = {source.canonical_url for source in self._sources_by_id.values()}
        urls.update(packet.canonical_url for packet in self._fetch_packets)
        urls.update(result.canonical_url for result in self._fetched_pages)
        return urls

    def summary(self) -> ResearchSummary:
        return ResearchSummary(
            search_batches=tuple(self._search_batches),
            fetch_packets=tuple(self._fetch_packets),
            fetch_failures=tuple(self._fetch_failures),
            fetched_pages=tuple(self._fetched_pages),
        )
