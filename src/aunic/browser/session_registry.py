from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from aunic.browser.errors import BrowserError
from aunic.browser.session import BrowserSession


@dataclass(slots=True)
class _RegistryEntry:
    session: BrowserSession
    connections: dict[object, str] = field(default_factory=dict)
    expiry_task: asyncio.Task[None] | None = None


class BrowserSessionRegistry:
    def __init__(
        self,
        *,
        session_factory: Callable[[str], BrowserSession],
        idle_ttl_seconds: float = 120.0,
    ) -> None:
        self._session_factory = session_factory
        self._idle_ttl_seconds = idle_ttl_seconds
        self._entries: dict[str, _RegistryEntry] = {}
        self._run_file_leases: dict[Path, str] = {}
        self._lock = asyncio.Lock()

    async def attach(
        self,
        *,
        instance_id: str,
        page_id: str,
        conn: object,
    ) -> BrowserSession:
        async with self._lock:
            entry = self._entries.get(instance_id)
            if entry is None:
                entry = _RegistryEntry(session=self._session_factory(instance_id))
                self._entries[instance_id] = entry
            if entry.expiry_task is not None:
                entry.expiry_task.cancel()
                entry.expiry_task = None
            live_page_ids = set(entry.connections.values())
            if live_page_ids and page_id not in live_page_ids:
                raise BrowserError(
                    "instance_conflict",
                    "This Aunic instance is already active in another browser tab.",
                )
            entry.connections[conn] = page_id
            session = entry.session
        try:
            await session.attach(conn)  # type: ignore[arg-type]
        except Exception:
            async with self._lock:
                current = self._entries.get(instance_id)
                if current is entry:
                    current.connections.pop(conn, None)
                    if not current.connections:
                        current.expiry_task = asyncio.create_task(
                            self._expire_instance_after_delay(instance_id, current)
                        )
            raise
        return session

    async def detach(self, *, instance_id: str, conn: object) -> None:
        async with self._lock:
            entry = self._entries.get(instance_id)
            if entry is None:
                return
            removed = entry.connections.pop(conn, None)
        if removed is None:
            return
        await entry.session.detach(conn)  # type: ignore[arg-type]
        async with self._lock:
            current = self._entries.get(instance_id)
            if current is not entry or current.connections:
                return
            if current.expiry_task is not None:
                current.expiry_task.cancel()
            current.expiry_task = asyncio.create_task(
                self._expire_instance_after_delay(instance_id, current)
            )

    async def acquire_run_file(self, *, instance_id: str, path: Path) -> None:
        normalized = path.expanduser().resolve()
        async with self._lock:
            owner = self._run_file_leases.get(normalized)
            if owner is None or owner == instance_id:
                self._run_file_leases[normalized] = instance_id
                return
        raise BrowserError(
            "file_run_in_progress",
            "A model run is already in progress for this file in another browser tab.",
            details={"path": str(normalized)},
        )

    async def release_run_file(self, *, instance_id: str, path: Path) -> None:
        normalized = path.expanduser().resolve()
        async with self._lock:
            if self._run_file_leases.get(normalized) == instance_id:
                self._run_file_leases.pop(normalized, None)

    async def shutdown(self) -> None:
        async with self._lock:
            entries = list(self._entries.items())
            self._entries = {}
            self._run_file_leases.clear()
        for _instance_id, entry in entries:
            if entry.expiry_task is not None:
                entry.expiry_task.cancel()
            await entry.session.shutdown()

    async def _expire_instance_after_delay(
        self,
        instance_id: str,
        entry: _RegistryEntry,
    ) -> None:
        try:
            await asyncio.sleep(self._idle_ttl_seconds)
        except asyncio.CancelledError:
            return
        async with self._lock:
            current = self._entries.get(instance_id)
            if current is not entry or current.connections:
                return
            self._entries.pop(instance_id, None)
            for leased_path, owner in tuple(self._run_file_leases.items()):
                if owner == instance_id:
                    self._run_file_leases.pop(leased_path, None)
        await entry.session.shutdown()
