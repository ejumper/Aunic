from __future__ import annotations

from typing import Any, Callable

import httpx

from aunic.config import OllamaSettings, SETTINGS
from aunic.domain import HealthCheck
from aunic.errors import ServiceUnavailableError
from aunic.providers.base import EmbeddingProvider


class OllamaEmbeddingProvider(EmbeddingProvider):
    name = "ollama_embeddings"

    def __init__(
        self,
        settings: OllamaSettings | None = None,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._settings = settings or SETTINGS.ollama
        self._client_factory = client_factory or self._build_client

    async def healthcheck(self) -> HealthCheck:
        url = f"{self._settings.base_url}{self._settings.tags_endpoint}"
        try:
            async with self._client_factory() as client:
                response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return HealthCheck(
                provider=self.name,
                ok=False,
                message=f"Ollama healthcheck failed: {exc}",
                details={"base_url": self._settings.base_url},
            )

        model_names = set(_extract_ollama_model_names(payload))
        ok = _model_name_matches(self._settings.embedding_model, model_names)
        message = (
            f"Ollama embedding model {self._settings.embedding_model!r} is available."
            if ok
            else f"Ollama is reachable, but {self._settings.embedding_model!r} is missing."
        )
        return HealthCheck(
            provider=self.name,
            ok=ok,
            message=message,
            details={"models": sorted(model_names)},
        )

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        url = f"{self._settings.base_url}{self._settings.embed_endpoint}"
        payload = {"model": self._settings.embedding_model, "input": texts}
        try:
            async with self._client_factory() as client:
                response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ServiceUnavailableError(f"Ollama embed request failed: {exc}") from exc

        embeddings = data.get("embeddings")
        if isinstance(embeddings, list) and embeddings and isinstance(embeddings[0], list):
            return [list(map(float, embedding)) for embedding in embeddings]

        embedding = data.get("embedding")
        if isinstance(embedding, list):
            return [list(map(float, embedding))]

        raise ServiceUnavailableError("Ollama embed response did not include embeddings.")

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._settings.request_timeout_seconds)


def _extract_ollama_model_names(payload: dict[str, Any]) -> list[str]:
    models = payload.get("models", [])
    names: list[str] = []
    if not isinstance(models, list):
        return names
    for item in models:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str):
            names.append(name)
    return names


def _model_name_matches(expected: str, available_names: set[str]) -> bool:
    normalized_expected = _normalize_model_name(expected)
    return any(_normalize_model_name(name) == normalized_expected for name in available_names)


def _normalize_model_name(name: str) -> str:
    return name.split(":", 1)[0]
