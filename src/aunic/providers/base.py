from __future__ import annotations

from abc import ABC, abstractmethod

from aunic.domain import HealthCheck, ProviderRequest, ProviderResponse


class LLMProvider(ABC):
    name: str

    @abstractmethod
    async def healthcheck(self) -> HealthCheck:
        """Return a health status for the provider."""

    async def ensure_ready(self) -> HealthCheck:
        """Ensure the provider can serve a request."""
        return await self.healthcheck()

    @abstractmethod
    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Execute a provider request."""

    async def close_run(self, run_session_id: str | None) -> None:
        """Release any provider-side resources associated with a prompt run."""
        return None


class EmbeddingProvider(ABC):
    name: str

    @abstractmethod
    async def healthcheck(self) -> HealthCheck:
        """Return a health status for the provider."""

    async def ensure_ready(self) -> HealthCheck:
        """Ensure the embedding provider can serve a request."""
        return await self.healthcheck()

    @abstractmethod
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return embeddings for the provided texts."""
