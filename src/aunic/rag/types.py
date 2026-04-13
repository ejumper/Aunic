from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RagScope:
    name: str
    description: str


@dataclass(frozen=True)
class RagConfig:
    server: str
    scopes: tuple[RagScope, ...]
    tui_scopes: tuple[RagScope, ...] | None = None


@dataclass(frozen=True)
class RagSearchResult:
    doc_id: str
    chunk_id: str
    title: str
    source: str
    snippet: str
    score: float
    result_id: str = ""
    corpus: str = ""
    heading_path: tuple[str, ...] = ()
    url: str | None = None
    local_path: str | None = None


@dataclass(frozen=True)
class RagFetchSection:
    heading: str
    heading_path: tuple[str, ...]
    text: str
    token_estimate: int = 0
    chunk_id: str = ""
    chunk_order: int | None = None
    is_match: bool = False


@dataclass(frozen=True)
class RagFetchResult:
    doc_id: str
    title: str
    source: str
    url: str | None
    sections: tuple[RagFetchSection, ...]
    full_text: str = ""
    result_id: str = ""
    chunk_id: str = ""
    corpus: str = ""
    local_path: str | None = None
    selected_chunk_id: str = ""
    selected_chunk_order: int | None = None
    total_chunks: int | None = None
    truncated: bool = False
    warnings: tuple[str, ...] = ()
