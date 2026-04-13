from __future__ import annotations

import unittest.mock as mock

import httpx
import pytest

from aunic.rag.client import RagClient
from aunic.rag.types import RagFetchResult, RagFetchSection, RagSearchResult


def _resp(status: int, body: object) -> httpx.Response:
    """Build a mock httpx.Response with a request attached for raise_for_status."""
    return httpx.Response(
        status,
        json=body,
        request=httpx.Request("POST", "http://test/"),
    )


_SEARCH_RESPONSE = {
    "results": [
        {
            "result_id": "docs:chunk:chunk_001",
            "doc_id": "docs:ubuntu:netplan",
            "chunk_id": "chunk_001",
            "corpus": "docs",
            "title": "Netplan Configuration",
            "source": "ubuntu-server",
            "snippet": "Netplan uses YAML files...",
            "score": 0.87,
            "heading_path": "Networking > Netplan",
            "citation": {
                "url": None,
                "local_path": "/docs/netplan.rst",
                "source": "ubuntu-server",
            },
        },
        {
            "result_id": "docs:chunk:chunk_002",
            "doc_id": "docs:ubuntu:netplan2",
            "chunk_id": "chunk_002",
            "corpus": "docs",
            "title": "Static IP",
            "source": "ubuntu-server",
            "snippet": "To configure a static IP...",
            "score": 0.74,
            "heading_path": [],
            "citation": {
                "url": "https://example.com/netplan",
                "local_path": None,
                "source": "ubuntu-server",
            },
        },
    ],
    "warnings": [],
}

_FETCH_RESPONSE = {
    "result_id": "docs:chunk:chunk_001",
    "doc_id": "docs:ubuntu:netplan",
    "chunk_id": "chunk_001",
    "corpus": "docs",
    "title": "Netplan Configuration",
    "source": "ubuntu-server",
    "heading_path": "Networking > Netplan > Overview",
    "content": "Netplan is the default network configuration...\n\n---\n\nTo configure a static IP address...",
    "chunks": [
        {
            "chunk_id": "chunk_001",
            "chunk_order": 0,
            "heading_path": "Networking > Netplan > Overview",
            "text": "Netplan is the default network configuration...",
        },
        {
            "chunk_id": "chunk_002",
            "chunk_order": 1,
            "heading_path": "Networking > Static IP",
            "text": "To configure a static IP address...",
        },
    ],
    "selected_chunk_id": "chunk_001",
    "selected_chunk_order": 0,
    "total_chunks": 42,
    "truncated": True,
    "warnings": ["document has 42 chunks; returned 20 chunks around selected match"],
    "citation": {
        "url": None,
        "local_path": "/docs/netplan.rst",
        "source": "ubuntu-server",
    },
}


@pytest.mark.asyncio
async def test_search_success():
    client = RagClient("http://test")
    with mock.patch("httpx.AsyncClient") as MockClient:
        inst = MockClient.return_value.__aenter__.return_value
        inst.post.return_value = _resp(200, _SEARCH_RESPONSE)
        results = await client.search("netplan static ip")

    assert len(results) == 2
    assert isinstance(results[0], RagSearchResult)
    assert results[0].result_id == "docs:chunk:chunk_001"
    assert results[0].doc_id == "docs:ubuntu:netplan"
    assert results[0].corpus == "docs"
    assert results[0].title == "Netplan Configuration"
    assert results[0].score == pytest.approx(0.87)
    assert results[0].heading_path == ("Networking", "Netplan")
    assert results[0].url is None
    assert results[0].local_path == "/docs/netplan.rst"
    assert results[1].url == "https://example.com/netplan"


@pytest.mark.asyncio
async def test_search_empty_results():
    client = RagClient("http://test")
    with mock.patch("httpx.AsyncClient") as MockClient:
        inst = MockClient.return_value.__aenter__.return_value
        inst.post.return_value = _resp(200, {"results": []})
        results = await client.search("nothing")

    assert results == ()


@pytest.mark.asyncio
async def test_search_with_scope():
    client = RagClient("http://test")
    captured: dict = {}
    with mock.patch("httpx.AsyncClient") as MockClient:
        inst = MockClient.return_value.__aenter__.return_value

        async def capture_post(url, **kwargs):
            captured["json"] = kwargs.get("json", {})
            return _resp(200, {"results": []})

        inst.post.side_effect = capture_post
        await client.search("query", scope="docs", limit=5)

    assert captured["json"]["scope"] == "docs"
    assert captured["json"]["limit"] == 5
    assert captured["json"]["query"] == "query"


@pytest.mark.asyncio
async def test_search_no_scope_defaults_to_rag_scope():
    client = RagClient("http://test")
    captured: dict = {}
    with mock.patch("httpx.AsyncClient") as MockClient:
        inst = MockClient.return_value.__aenter__.return_value

        async def capture_post(url, **kwargs):
            captured["json"] = kwargs.get("json", {})
            return _resp(200, {"results": []})

        inst.post.side_effect = capture_post
        await client.search("query")

    assert captured["json"]["scope"] == "rag"


@pytest.mark.asyncio
async def test_search_server_error():
    client = RagClient("http://test")
    with mock.patch("httpx.AsyncClient") as MockClient:
        inst = MockClient.return_value.__aenter__.return_value
        inst.post.return_value = _resp(500, {"error": "internal server error"})

        with pytest.raises(httpx.HTTPStatusError):
            await client.search("query")


@pytest.mark.asyncio
async def test_fetch_success():
    client = RagClient("http://test")
    with mock.patch("httpx.AsyncClient") as MockClient:
        inst = MockClient.return_value.__aenter__.return_value
        inst.post.return_value = _resp(200, _FETCH_RESPONSE)
        result = await client.fetch("docs:chunk:chunk_001")

    assert isinstance(result, RagFetchResult)
    assert result.result_id == "docs:chunk:chunk_001"
    assert result.doc_id == "docs:ubuntu:netplan"
    assert result.chunk_id == "chunk_001"
    assert result.corpus == "docs"
    assert result.title == "Netplan Configuration"
    assert result.url is None
    assert result.local_path == "/docs/netplan.rst"
    assert result.full_text == _FETCH_RESPONSE["content"]
    assert len(result.sections) == 2
    assert isinstance(result.sections[0], RagFetchSection)
    assert result.sections[0].heading == "Networking > Netplan > Overview"
    assert result.sections[0].heading_path == ("Networking", "Netplan", "Overview")
    assert result.sections[0].chunk_id == "chunk_001"
    assert result.sections[0].chunk_order == 0
    assert result.sections[0].is_match is True
    assert result.sections[0].token_estimate > 0
    assert result.sections[1].heading == "Networking > Static IP"
    assert result.sections[1].is_match is False
    assert result.selected_chunk_id == "chunk_001"
    assert result.selected_chunk_order == 0
    assert result.total_chunks == 42
    assert result.truncated is True
    assert result.warnings == ("document has 42 chunks; returned 20 chunks around selected match",)


@pytest.mark.asyncio
async def test_fetch_no_chunks():
    client = RagClient("http://test")
    body = {
        "result_id": "x:chunk:y",
        "doc_id": "x",
        "title": "X",
        "source": "s",
        "content": "",
        "chunks": [],
        "citation": {"source": "s"},
    }
    with mock.patch("httpx.AsyncClient") as MockClient:
        inst = MockClient.return_value.__aenter__.return_value
        inst.post.return_value = _resp(200, body)
        result = await client.fetch("x:chunk:y")

    assert result.sections == ()


@pytest.mark.asyncio
async def test_fetch_with_neighbors_param():
    client = RagClient("http://test")
    captured: dict = {}
    with mock.patch("httpx.AsyncClient") as MockClient:
        inst = MockClient.return_value.__aenter__.return_value

        async def capture_post(url, **kwargs):
            captured["json"] = kwargs.get("json", {})
            return _resp(200, _FETCH_RESPONSE)

        inst.post.side_effect = capture_post
        await client.fetch("docs:chunk:chunk_001", neighbors=2)

    assert captured["json"]["result_id"] == "docs:chunk:chunk_001"
    assert captured["json"]["neighbors"] == 2
    assert captured["json"]["mode"] == "neighbors"


@pytest.mark.asyncio
async def test_fetch_with_document_chunks_mode():
    client = RagClient("http://test")
    captured: dict = {}
    with mock.patch("httpx.AsyncClient") as MockClient:
        inst = MockClient.return_value.__aenter__.return_value

        async def capture_post(url, **kwargs):
            captured["json"] = kwargs.get("json", {})
            return _resp(200, _FETCH_RESPONSE)

        inst.post.side_effect = capture_post
        await client.fetch("docs:chunk:chunk_001", mode="document_chunks", max_chunks=20)

    assert captured["json"]["result_id"] == "docs:chunk:chunk_001"
    assert captured["json"]["mode"] == "document_chunks"
    assert captured["json"]["max_chunks"] == 20


def test_base_url_trailing_slash_stripped():
    client = RagClient("http://localhost:5173/")
    assert client._base_url == "http://localhost:5173"
