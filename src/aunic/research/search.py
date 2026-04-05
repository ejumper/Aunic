from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from aunic.config import ResearchSettings, SETTINGS
from aunic.research.searxng_scheduler import SearxngScheduler
from aunic.research.types import (
    ResearchState,
    SearchBatch,
    SearchDepth,
    SearchFreshness,
    SearchQueryFailure,
    SearchResult,
)


class SearchService:
    def __init__(
        self,
        settings: ResearchSettings | None = None,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        scheduler: SearxngScheduler | None = None,
    ) -> None:
        self._settings = settings or SETTINGS.research
        self._client_factory = client_factory or self._build_client
        self._scheduler = scheduler or SearxngScheduler(self._settings)

    async def search(
        self,
        *,
        queries: tuple[str, ...],
        depth: SearchDepth,
        freshness: SearchFreshness,
        purpose: str,
        state: ResearchState,
        query_categories: dict[str, tuple[str, ...]] | None = None,
        max_results_per_query: int | None = None,
    ) -> SearchBatch:
        scheduled = await self._scheduler.run_queries(
            queries=queries,
            freshness=freshness,
            execute=self._search_one,
        )

        merged: dict[str, _MergedResult] = {}
        failures: list[SearchQueryFailure] = []
        per_query_limit = max_results_per_query or self._results_per_query(depth)
        for scheduled_result in scheduled:
            query = scheduled_result.query
            if scheduled_result.failure is not None:
                failures.append(scheduled_result.failure)
                continue
            payload = scheduled_result.payload or {}
            raw_results = payload.get("results", [])
            if not isinstance(raw_results, list):
                continue
            engine_name = scheduled_result.attempted_engines[-1] if scheduled_result.attempted_engines else ""
            for rank, item in enumerate(raw_results[:per_query_limit], start=1):
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url", "")).strip()
                if not url:
                    continue
                canonical_url = canonicalize_url(url, settings=self._settings)
                title = str(item.get("title", "")).strip() or canonical_url
                snippet = str(item.get("content") or item.get("snippet") or "").strip()
                date = _extract_date(item)
                categories = tuple(query_categories.get(query, ())) if query_categories else ()
                existing = merged.get(canonical_url)
                if existing is None:
                    merged[canonical_url] = _MergedResult(
                        title=title,
                        url=url,
                        canonical_url=canonical_url,
                        snippet=snippet,
                        rank=rank,
                        engine_names={engine_name} if engine_name else set(),
                        queries=[query],
                        categories=list(categories),
                        date=date,
                    )
                    continue

                existing.rank = min(existing.rank, rank)
                if engine_name:
                    existing.engine_names.add(engine_name)
                if query not in existing.queries:
                    existing.queries.append(query)
                for category in categories:
                    if category not in existing.categories:
                        existing.categories.append(category)
                if _prefer_new_snippet(snippet, existing.snippet):
                    existing.snippet = snippet
                if title and existing.title == existing.canonical_url:
                    existing.title = title
                existing.date = _pick_better_date(existing.date, date)

        ranked = sorted(
            merged.values(),
            key=lambda item: (
                -len(item.engine_names or {""}),
                item.rank,
                _date_sort_key(item.date),
                item.title.casefold(),
            ),
        )

        results = []
        for item in ranked:
            source = state.ensure_source(
                title=item.title,
                url=item.url,
                canonical_url=item.canonical_url,
            )
            engine_count = max(1, len(item.engine_names))
            results.append(
                SearchResult(
                    source_id=source.source_id,
                    title=source.title,
                    url=source.url,
                    canonical_url=source.canonical_url,
                    snippet=item.snippet,
                    rank=item.rank,
                    engine_count=engine_count,
                    refined_score=float(engine_count) - (item.rank / 1000.0),
                    query_labels=tuple(item.queries),
                    category_labels=tuple(item.categories),
                    date=item.date,
                )
            )

        batch = SearchBatch(
            queries=queries,
            depth=depth,
            freshness=freshness,
            purpose=purpose,
            results=tuple(results),
            failures=tuple(failures),
        )
        state.record_search_batch(batch)
        return batch

    def max_queries_for_depth(self, depth: SearchDepth) -> int:
        if depth == "quick":
            return 1
        if depth == "balanced":
            return 3
        return 5

    async def _search_one(
        self,
        *,
        query: str,
        freshness: SearchFreshness,
        engine: str,
    ) -> dict[str, Any]:
        params: dict[str, str] = {"format": "json", "q": query, "engines": engine}
        time_range = _freshness_to_time_range(freshness)
        if time_range is not None:
            params["time_range"] = time_range
        url = f"{self._settings.searxng_base_url}{self._settings.searxng_search_endpoint}"
        async with self._client_factory() as client:
            response = await client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _results_per_query(self, depth: SearchDepth) -> int:
        return self._settings.search_results_per_query

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._settings.search_request_timeout_seconds,
            headers={"User-Agent": self._settings.user_agent},
        )


@dataclass
class _MergedResult:
    title: str
    url: str
    canonical_url: str
    snippet: str
    rank: int
    engine_names: set[str] = field(default_factory=set)
    queries: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    date: str | None = None


def canonicalize_url(url: str, *, settings: ResearchSettings | None = None) -> str:
    config = settings or SETTINGS.research
    parsed = urlsplit(url)
    scheme = (parsed.scheme or "https").lower()
    hostname = (parsed.hostname or "").lower()
    port = parsed.port
    if (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
        port = None
    if port is not None:
        hostname = f"{hostname}:{port}"
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]

    filtered_params = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in config.strip_tracking_query_parameters
    ]
    filtered_params.sort()
    query = urlencode(filtered_params, doseq=True)
    return urlunsplit((scheme, hostname, path, query, ""))


def _freshness_to_time_range(freshness: SearchFreshness) -> str | None:
    if freshness == "recent":
        return "year"
    if freshness == "very_recent":
        return "month"
    return None


def _extract_date(item: dict[str, Any]) -> str | None:
    for key in ("publishedDate", "published_date", "date"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _prefer_new_snippet(candidate: str, current: str) -> bool:
    if not candidate.strip():
        return False
    if not current.strip():
        return True
    return len(candidate) > len(current)


def _pick_better_date(current: str | None, candidate: str | None) -> str | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    if _parse_date(candidate) and _parse_date(current):
        return candidate if _parse_date(candidate) >= _parse_date(current) else current
    return candidate if candidate > current else current


def _date_sort_key(value: str | None) -> tuple[int, float]:
    if value is None:
        return (1, 0.0)
    parsed = _parse_date(value)
    if parsed is None:
        return (1, 0.0)
    return (0, -parsed.timestamp())


def _parse_date(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None
