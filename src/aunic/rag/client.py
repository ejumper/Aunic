from __future__ import annotations

import httpx

from aunic.rag.types import (
    RagFetchResult,
    RagFetchSection,
    RagSearchResult,
)

_TIMEOUT = 30.0


class RagClient:
    """Async HTTP client for the Aunic RAG server spec."""

    def __init__(self, server_url: str) -> None:
        self._base_url = server_url.rstrip("/")

    async def search(
        self,
        query: str,
        scope: str | None = None,
        limit: int = 10,
    ) -> tuple[RagSearchResult, ...]:
        """POST /search — returns parsed results."""
        payload: dict = {"query": query, "scope": scope or "rag", "limit": limit}

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(f"{self._base_url}/search", json=payload)
            response.raise_for_status()
            data = response.json()

        results = []
        for item in data.get("results", []):
            citation = item.get("citation")
            if not isinstance(citation, dict):
                citation = {}
            results.append(
                RagSearchResult(
                    doc_id=item.get("doc_id", ""),
                    chunk_id=item.get("chunk_id", ""),
                    title=item.get("title", ""),
                    source=item.get("source", ""),
                    snippet=item.get("snippet", ""),
                    score=float(item.get("score", 0.0)),
                    result_id=item.get("result_id", ""),
                    corpus=item.get("corpus", ""),
                    heading_path=_parse_heading_path(item.get("heading_path")),
                    url=item.get("url") or citation.get("url") or None,
                    local_path=citation.get("local_path") or item.get("local_path") or None,
                )
            )
        return tuple(results)

    async def fetch(
        self,
        result_id: str,
        neighbors: int = 1,
        *,
        mode: str = "neighbors",
        max_chunks: int = 20,
    ) -> RagFetchResult:
        """POST /fetch — returns parsed result."""
        payload: dict = {
            "result_id": result_id,
            "mode": mode,
            "neighbors": neighbors,
            "max_chunks": max_chunks,
        }

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(f"{self._base_url}/fetch", json=payload)
            response.raise_for_status()
            data = response.json()

        citation = data.get("citation")
        if not isinstance(citation, dict):
            citation = {}
        selected_chunk_id = str(data.get("selected_chunk_id") or data.get("chunk_id") or "")
        selected_chunk_order = _parse_int(data.get("selected_chunk_order"))
        sections = []
        for sec in data.get("chunks", []):
            if not isinstance(sec, dict):
                continue
            chunk_id = str(sec.get("chunk_id") or "")
            heading_path = _parse_heading_path(sec.get("heading_path"))
            if not heading_path:
                heading_path = _parse_heading_path(data.get("heading_path"))
            heading = " > ".join(heading_path) or data.get("title", "") or chunk_id
            chunk_order = _parse_int(sec.get("chunk_order"))
            is_match = bool(sec.get("is_match")) or (bool(chunk_id) and chunk_id == selected_chunk_id)
            sections.append(
                RagFetchSection(
                    heading=heading,
                    heading_path=heading_path,
                    text=sec.get("text", ""),
                    token_estimate=len(str(sec.get("text", "")).split()),
                    chunk_id=chunk_id,
                    chunk_order=chunk_order,
                    is_match=is_match,
                )
            )

        return RagFetchResult(
            doc_id=data.get("doc_id", ""),
            title=data.get("title", ""),
            source=data.get("source", ""),
            url=data.get("url") or citation.get("url") or None,
            sections=tuple(sections),
            full_text=data.get("content", ""),
            result_id=data.get("result_id", result_id),
            chunk_id=data.get("chunk_id", ""),
            corpus=data.get("corpus", ""),
            local_path=citation.get("local_path") or data.get("local_path") or None,
            selected_chunk_id=selected_chunk_id,
            selected_chunk_order=selected_chunk_order,
            total_chunks=_parse_int(data.get("total_chunks")),
            truncated=bool(data.get("truncated", False)),
            warnings=tuple(str(warning) for warning in data.get("warnings", []) if str(warning).strip()),
        )


def _parse_heading_path(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        if not value.strip():
            return ()
        return tuple(part.strip() for part in value.split(">") if part.strip())
    if isinstance(value, list | tuple):
        return tuple(str(part).strip() for part in value if str(part).strip())
    return ()


def _parse_int(value: object) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
