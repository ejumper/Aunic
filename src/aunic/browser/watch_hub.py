from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path

from aunic.context import FileChange, FileManager

logger = logging.getLogger(__name__)

FileChangeCallback = Callable[[FileChange], Awaitable[None]]


class FileWatchHub:
    def __init__(self, file_manager: FileManager) -> None:
        self._file_manager = file_manager
        self._callbacks: set[FileChangeCallback] = set()
        self._task: asyncio.Task[None] | None = None
        self._paths: tuple[Path, ...] = ()

    async def start(self, paths: Iterable[Path]) -> None:
        if self._task is not None:
            return
        self._paths = tuple(dict.fromkeys(path.expanduser().resolve() for path in paths))
        if not self._paths:
            return
        self._task = asyncio.create_task(self._run(), name="aunic-browser-file-watch")

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    def subscribe(self, callback: FileChangeCallback) -> Callable[[], None]:
        self._callbacks.add(callback)

        def _unsubscribe() -> None:
            self._callbacks.discard(callback)

        return _unsubscribe

    async def _run(self) -> None:
        try:
            async for batch in self._file_manager.watch(self._paths):
                for change in batch:
                    callbacks = tuple(self._callbacks)
                    if not callbacks:
                        continue
                    await asyncio.gather(
                        *(callback(change) for callback in callbacks),
                        return_exceptions=True,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Browser file watch hub stopped unexpectedly")
