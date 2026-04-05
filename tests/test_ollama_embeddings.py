from __future__ import annotations

import httpx
import pytest

from aunic.config import OllamaSettings
from aunic.providers.ollama_embeddings import OllamaEmbeddingProvider


def _client_factory(handler):
    transport = httpx.MockTransport(handler)

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, base_url="http://testserver")

    return factory


@pytest.mark.asyncio
async def test_ollama_healthcheck_finds_embedding_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(
            200,
            json={"models": [{"name": "mxbai-embed-large:latest"}, {"name": "other-model"}]},
        )

    provider = OllamaEmbeddingProvider(
        OllamaSettings(base_url="http://testserver"),
        client_factory=_client_factory(handler),
    )

    check = await provider.healthcheck()

    assert check.ok is True


@pytest.mark.asyncio
async def test_ollama_embed_texts_parses_embeddings() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/embed"
        return httpx.Response(
            200,
            json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]},
        )

    provider = OllamaEmbeddingProvider(
        OllamaSettings(base_url="http://testserver"),
        client_factory=_client_factory(handler),
    )

    embeddings = await provider.embed_texts(["a", "b"])

    assert embeddings == [[0.1, 0.2], [0.3, 0.4]]
