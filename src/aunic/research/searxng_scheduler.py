from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Any, Awaitable, Callable

from aunic.config import ResearchSettings, SETTINGS
from aunic.research.types import SearchFreshness, SearchQueryFailure


@dataclass
class _EngineState:
    name: str
    in_flight: bool = False
    next_eligible_at: float = 0.0
    timeout_until: float = 0.0


@dataclass(frozen=True)
class SearxngScheduledResult:
    query: str
    payload: dict[str, Any] | None = None
    attempted_engines: tuple[str, ...] = ()
    failure: SearchQueryFailure | None = None


class SearxngScheduler:
    def __init__(
        self,
        settings: ResearchSettings | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
    ) -> None:
        self._settings = settings or SETTINGS.research
        self._sleep = sleep or asyncio.sleep
        self._monotonic = monotonic_fn or monotonic
        self._states = {
            name: _EngineState(name=name)
            for name in self._settings.searxng_scheduler.preferred_engines
        }
        self._condition = asyncio.Condition()

    async def run_queries(
        self,
        *,
        queries: tuple[str, ...],
        freshness: SearchFreshness,
        execute: Callable[[str, SearchFreshness, str], Awaitable[dict[str, Any]]],
    ) -> tuple[SearxngScheduledResult, ...]:
        tasks = [
            asyncio.create_task(
                self._run_single_query(
                    query=query,
                    freshness=freshness,
                    execute=execute,
                )
            )
            for query in queries
        ]
        return tuple(await asyncio.gather(*tasks))

    async def _run_single_query(
        self,
        *,
        query: str,
        freshness: SearchFreshness,
        execute: Callable[[str, SearchFreshness, str], Awaitable[dict[str, Any]]],
    ) -> SearxngScheduledResult:
        attempted_engines: list[str] = []
        max_attempts = len(self._settings.searxng_scheduler.preferred_engines)
        for _ in range(max_attempts):
            engine = await self._acquire_engine(excluded=frozenset(attempted_engines))
            if engine is None:
                return SearxngScheduledResult(
                    query=query,
                    attempted_engines=tuple(attempted_engines),
                    failure=SearchQueryFailure(
                        query=query,
                        attempted_engines=tuple(attempted_engines),
                        message="All configured search engines are in timeout.",
                    ),
                )

            attempted_engines.append(engine)
            try:
                payload = await execute(query=query, freshness=freshness, engine=engine)
            except Exception:
                await self._release_engine(engine)
                raise

            if _payload_has_results(payload):
                await self._release_engine(engine)
                return SearxngScheduledResult(
                    query=query,
                    payload=payload,
                    attempted_engines=tuple(attempted_engines),
                )

            await self._timeout_engine(engine)

        return SearxngScheduledResult(
            query=query,
            attempted_engines=tuple(attempted_engines),
            failure=SearchQueryFailure(
                query=query,
                attempted_engines=tuple(attempted_engines),
                message="Query exhausted available search engines.",
            ),
        )

    async def _acquire_engine(self, *, excluded: frozenset[str]) -> str | None:
        while True:
            next_wait: float | None = None
            saw_available_engine = False
            async with self._condition:
                now = self._monotonic()
                for engine_name in self._settings.searxng_scheduler.preferred_engines:
                    if engine_name in excluded:
                        continue
                    state = self._states[engine_name]
                    if state.timeout_until > now:
                        continue
                    saw_available_engine = True
                    if state.in_flight:
                        next_wait = min(next_wait, 0.1) if next_wait is not None else 0.1
                        continue
                    if state.next_eligible_at > now:
                        wait = max(0.05, state.next_eligible_at - now)
                        next_wait = min(next_wait, wait) if next_wait is not None else wait
                        continue
                    state.in_flight = True
                    state.next_eligible_at = now + self._settings.searxng_scheduler.per_engine_reuse_cooldown_seconds
                    return engine_name

                if not saw_available_engine:
                    return None

            await self._sleep(next_wait or 0.1)

    async def _release_engine(self, engine_name: str) -> None:
        async with self._condition:
            self._states[engine_name].in_flight = False
            self._condition.notify_all()

    async def _timeout_engine(self, engine_name: str) -> None:
        async with self._condition:
            state = self._states[engine_name]
            state.in_flight = False
            state.timeout_until = self._monotonic() + self._settings.searxng_scheduler.engine_timeout_seconds
            self._condition.notify_all()


def _payload_has_results(payload: dict[str, Any]) -> bool:
    results = payload.get("results", [])
    return isinstance(results, list) and len(results) > 0
