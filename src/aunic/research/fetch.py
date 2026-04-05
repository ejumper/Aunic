from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import httpx
import trafilatura

from aunic.config import ResearchSettings, SETTINGS
from aunic.context.structure import chunk_markdown_text
from aunic.research.search import canonicalize_url
from aunic.research.types import (
    FetchFailure,
    FetchPacket,
    FetchResult,
    FetchedChunk,
    PageFetchRequest,
    PageFetchResult,
    ResearchState,
)


class FetchService:
    def __init__(
        self,
        settings: ResearchSettings | None = None,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        html_to_markdown: Callable[[str], str | None] | None = None,
    ) -> None:
        self._settings = settings or SETTINGS.research
        self._client_factory = client_factory or self._build_client
        self._html_to_markdown = html_to_markdown or _default_html_to_markdown

    async def fetch_page(
        self,
        request: PageFetchRequest,
        *,
        state: ResearchState,
        active_file: Path | str | None = None,
    ) -> PageFetchResult:
        cache = _PageCache(self._settings, active_file)
        requested_canonical_url = canonicalize_url(request.url, settings=self._settings)
        cached = cache.read(requested_canonical_url)
        if cached is None:
            response = await self._fetch_url(request.url)
            resolved_url = canonicalize_url(str(response.url), settings=self._settings)
            content_type = response.headers.get("content-type", "")
            markdown = _response_to_markdown(
                response.text,
                content_type=content_type,
                html_to_markdown=self._html_to_markdown,
            )
            if not markdown.strip():
                raise ValueError("Fetched page did not yield readable markdown content.")
            title = _extract_title(response.text) or request.url
            cached = cache.write(
                original_url=request.url,
                requested_canonical_url=requested_canonical_url,
                resolved_canonical_url=resolved_url,
                title=title,
                markdown=markdown,
            )

        source = state.ensure_source(
            title=cached.title,
            url=request.url,
            canonical_url=cached.canonical_url,
        )
        markdown = cached.markdown
        truncated = len(markdown) > self._settings.fetch_max_chars
        rendered_markdown = markdown[: self._settings.fetch_max_chars]
        if truncated:
            rendered_markdown += "\n\n[Truncated by Aunic fetch limit.]"
        result = PageFetchResult(
            url=request.url,
            canonical_url=cached.canonical_url,
            title=source.title or cached.title,
            snippet=_snippet(markdown),
            markdown=rendered_markdown,
            truncated=truncated,
        )
        state.record_fetched_page(result)
        return result

    async def fetch_for_user_selection(
        self,
        *,
        query: str,
        url: str,
        state: ResearchState,
        active_file: Path | str | None = None,
    ) -> FetchPacket:
        page = await self.fetch_page(
            PageFetchRequest(url=url),
            state=state,
            active_file=active_file,
        )
        source = state.ensure_source(
            title=page.title,
            url=url,
            canonical_url=page.canonical_url,
        )
        chunks = _select_user_chunks(
            markdown=page.markdown,
            query=query,
            source_id=source.source_id,
            title=page.title,
            url=url,
            canonical_url=page.canonical_url,
        )
        if not chunks:
            raise ValueError("No readable chunks were found in the fetched page.")
        packet = FetchPacket(
            source_id=source.source_id,
            title=page.title,
            url=url,
            canonical_url=page.canonical_url,
            desired_info=query,
            chunks=tuple(chunks),
            full_markdown=page.markdown,
        )
        state.record_fetch_result(FetchResult(packets=(packet,), failures=()))
        return packet

    async def _fetch_url(self, url: str) -> httpx.Response:
        async with self._client_factory() as client:
            response = await client.get(url, follow_redirects=True)
        response.raise_for_status()
        return response

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._settings.fetch_request_timeout_seconds,
            headers={"User-Agent": self._settings.user_agent},
        )


@dataclass(frozen=True)
class _CachedPage:
    entry_hash: str
    canonical_url: str
    title: str
    markdown: str


class _PageCache:
    def __init__(self, settings: ResearchSettings, active_file: Path | str | None) -> None:
        self._settings = settings
        self._active_file = Path(active_file).expanduser().resolve() if active_file else None

    def read(self, canonical_url: str) -> _CachedPage | None:
        manifest = self._read_manifest()
        entry_hash = self._entry_hash_for_url(canonical_url, manifest)
        if entry_hash is None:
            return None
        meta_path = self._cache_dir() / f"{entry_hash}.meta.json"
        markdown_path = self._cache_dir() / f"{entry_hash}.md"
        if not meta_path.exists() or not markdown_path.exists():
            return None
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        markdown = markdown_path.read_text(encoding="utf-8")
        now = _utc_now()
        if entry_hash in manifest["entries"]:
            manifest["entries"][entry_hash]["last_accessed"] = now
            self._write_manifest(manifest)
        return _CachedPage(
            entry_hash=entry_hash,
            canonical_url=str(metadata.get("canonical_url", canonical_url)),
            title=str(metadata.get("title", canonical_url)),
            markdown=markdown,
        )

    def write(
        self,
        *,
        original_url: str,
        requested_canonical_url: str,
        resolved_canonical_url: str,
        title: str,
        markdown: str,
    ) -> _CachedPage:
        cache_dir = self._cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        manifest = self._read_manifest()
        entry_hash = _hash_text(resolved_canonical_url)
        markdown_path = cache_dir / f"{entry_hash}.md"
        meta_path = cache_dir / f"{entry_hash}.meta.json"
        markdown_path.write_text(markdown, encoding="utf-8")
        metadata = {
            "canonical_url": resolved_canonical_url,
            "title": title,
            "original_url": original_url,
            "fetched_at": _utc_now(),
        }
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        size_bytes = markdown_path.stat().st_size + meta_path.stat().st_size
        now = _utc_now()
        manifest["entries"][entry_hash] = {
            "canonical_url": resolved_canonical_url,
            "title": title,
            "size_bytes": size_bytes,
            "fetched_at": now,
            "last_accessed": now,
        }
        manifest["aliases"][_hash_text(requested_canonical_url)] = entry_hash
        manifest["aliases"][_hash_text(resolved_canonical_url)] = entry_hash
        manifest["total_size_bytes"] = sum(
            int(entry.get("size_bytes", 0))
            for entry in manifest["entries"].values()
        )
        self._evict_if_needed(manifest)
        self._write_manifest(manifest)
        return _CachedPage(
            entry_hash=entry_hash,
            canonical_url=resolved_canonical_url,
            title=title,
            markdown=markdown,
        )

    def _evict_if_needed(self, manifest: dict[str, Any]) -> None:
        cache_dir = self._cache_dir()
        while manifest.get("total_size_bytes", 0) > self._settings.fetch_cache_max_bytes:
            entries = manifest.get("entries", {})
            if not entries:
                break
            entry_hash, _entry = min(
                entries.items(),
                key=lambda item: str(item[1].get("last_accessed", "")),
            )
            for suffix in (".md", ".meta.json"):
                path = cache_dir / f"{entry_hash}{suffix}"
                if path.exists():
                    path.unlink()
            entries.pop(entry_hash, None)
            aliases = manifest.get("aliases", {})
            manifest["aliases"] = {
                key: value for key, value in aliases.items() if value != entry_hash
            }
            manifest["total_size_bytes"] = sum(
                int(entry.get("size_bytes", 0))
                for entry in entries.values()
            )

    def _entry_hash_for_url(self, canonical_url: str, manifest: dict[str, Any]) -> str | None:
        url_hash = _hash_text(canonical_url)
        if url_hash in manifest.get("aliases", {}):
            return str(manifest["aliases"][url_hash])
        entries = manifest.get("entries", {})
        if url_hash in entries:
            return url_hash
        return None

    def _cache_dir(self) -> Path:
        base = Path.home() / ".cache"
        xdg = Path(_env("XDG_CACHE_HOME")) if _env("XDG_CACHE_HOME") else None
        if xdg is not None:
            base = xdg.expanduser()
        scope = _hash_text(str(self._active_file)) if self._active_file else "global"
        return base / "aunic" / "fetch" / scope

    def _manifest_path(self) -> Path:
        return self._cache_dir() / "manifest.json"

    def _read_manifest(self) -> dict[str, Any]:
        path = self._manifest_path()
        if not path.exists():
            return {"entries": {}, "aliases": {}, "total_size_bytes": 0}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"entries": {}, "aliases": {}, "total_size_bytes": 0}
        if not isinstance(data, dict):
            return {"entries": {}, "aliases": {}, "total_size_bytes": 0}
        data.setdefault("entries", {})
        data.setdefault("aliases", {})
        data.setdefault("total_size_bytes", 0)
        return data

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        path = self._manifest_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
def _select_user_chunks(
    *,
    markdown: str,
    query: str,
    source_id: str,
    title: str,
    url: str,
    canonical_url: str,
) -> list[FetchedChunk]:
    chunks = list(
        chunk_markdown_text(
            markdown,
            target_chars=700,
            hard_cap_chars=2000,
        )
    )
    if not chunks:
        return []

    query_terms = {term for term in re.findall(r"[a-zA-Z0-9_]{3,}", query.lower())}
    seen_texts: set[str] = set()
    scored: list[tuple[float, int, FetchedChunk]] = []
    for chunk in chunks:
        text = chunk.text.strip()
        normalized = re.sub(r"\s+", " ", text).strip()
        if len(normalized) < 40:
            continue
        if _looks_like_heading_only(text):
            continue
        dedupe_key = normalized.casefold()
        if dedupe_key in seen_texts:
            continue
        seen_texts.add(dedupe_key)

        lowered = normalized.casefold()
        score = float(sum(1 for term in query_terms if term in lowered))
        if chunk.heading_path:
            heading_text = " ".join(chunk.heading_path).casefold()
            score += 0.25 * sum(1 for term in query_terms if term in heading_text)

        scored.append(
            (
                score,
                chunk.span.start,
                FetchedChunk(
                    source_id=source_id,
                    title=title,
                    url=url,
                    canonical_url=canonical_url,
                    text=chunk.text,
                    score=score,
                    heading_path=chunk.heading_path,
                ),
            )
        )

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [chunk for _, __, chunk in scored]


def _response_to_markdown(
    body: str,
    *,
    content_type: str,
    html_to_markdown: Callable[[str], str | None],
) -> str:
    lowered = content_type.casefold()
    if "html" in lowered:
        return html_to_markdown(body) or ""
    return body


def _default_html_to_markdown(html: str) -> str | None:
    return trafilatura.extract(
        html,
        output_format="markdown",
        include_links=True,
        include_formatting=True,
    )


def _extract_title(html: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if match is None:
        return None
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    return title or None


def _snippet(markdown: str) -> str:
    text = re.sub(r"\s+", " ", markdown).strip()
    if len(text) <= 240:
        return text
    return text[:237].rstrip() + "..."


def _looks_like_heading_only(text: str) -> bool:
    stripped_lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(stripped_lines) != 1:
        return False
    line = stripped_lines[0]
    if not line.startswith("#"):
        return False
    heading_text = re.sub(r"^#+\s*", "", line).strip()
    return 0 < len(heading_text) <= 120


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _env(name: str) -> str | None:
    from os import environ

    value = environ.get(name)
    return value if value and value.strip() else None
