from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import Any, Callable

from watchfiles import awatch

from aunic.config import ContextSettings, SETTINGS
from aunic.context.types import FileChange, FileMetadata, FileSnapshot
from aunic.errors import FileReadError, OptimisticWriteError


class FileManager:
    def __init__(
        self,
        settings: ContextSettings | None = None,
        *,
        awatch_factory: Callable[..., AsyncIterator[set[tuple[Any, str]]]] | None = None,
    ) -> None:
        self._settings = settings or SETTINGS.context
        self._awatch_factory = awatch_factory or awatch

    async def read_snapshot(self, path: Path | str) -> FileSnapshot:
        normalized = _normalize_path(path)
        try:
            raw_text = await asyncio.to_thread(
                normalized.read_text,
                encoding="utf-8",
                errors="strict",
            )
            stat = await asyncio.to_thread(normalized.stat)
        except FileNotFoundError as exc:
            raise FileReadError(f"File does not exist: {normalized}") from exc
        except UnicodeDecodeError as exc:
            raise FileReadError(f"File is not valid UTF-8: {normalized}") from exc
        except OSError as exc:
            raise FileReadError(f"Could not read file: {normalized}") from exc

        content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        revision_id = f"{content_hash}:{stat.st_mtime_ns}:{stat.st_size}"
        return FileSnapshot(
            path=normalized,
            raw_text=raw_text,
            revision_id=revision_id,
            content_hash=content_hash,
            mtime_ns=stat.st_mtime_ns,
            size_bytes=stat.st_size,
        )

    async def read_metadata(self, path: Path | str) -> FileMetadata:
        normalized = _normalize_path(path)
        try:
            stat = await asyncio.to_thread(normalized.stat)
        except FileNotFoundError as exc:
            raise FileReadError(f"File does not exist: {normalized}") from exc
        except OSError as exc:
            raise FileReadError(f"Could not stat file: {normalized}") from exc

        return FileMetadata(
            path=normalized,
            revision_id=f"meta:{stat.st_mtime_ns}:{stat.st_size}",
            mtime_ns=stat.st_mtime_ns,
            size_bytes=stat.st_size,
        )

    async def read_working_set(
        self,
        active_file: Path | str,
        included_files: Iterable[Path | str] = (),
    ) -> tuple[FileSnapshot, ...]:
        ordered_paths = _ordered_paths(active_file, included_files)
        snapshots = await asyncio.gather(
            *(self.read_snapshot(path) for path in ordered_paths)
        )
        return tuple(snapshots)

    async def write_text(
        self,
        path: Path | str,
        new_text: str,
        expected_revision: str | None = None,
    ) -> FileSnapshot:
        normalized = _normalize_path(path)
        if expected_revision is not None:
            current = await self.read_snapshot(normalized)
            if current.revision_id != expected_revision:
                raise OptimisticWriteError(
                    f"Revision mismatch for {normalized}: "
                    f"expected {expected_revision}, found {current.revision_id}."
                )
        await asyncio.to_thread(normalized.write_text, new_text, encoding="utf-8")
        return await self.read_snapshot(normalized)

    async def watch(
        self,
        paths: Iterable[Path | str],
    ) -> AsyncIterator[tuple[FileChange, ...]]:
        normalized_paths = tuple(sorted({_normalize_path(path) for path in paths}))
        if not normalized_paths:
            return

        async for changes in self._awatch_factory(
            *normalized_paths,
            debounce=self._settings.watch_debounce_ms,
            step=self._settings.watch_step_ms,
            ignore_permission_denied=True,
        ):
            batch: list[FileChange] = []
            for change, raw_path in sorted(changes, key=lambda item: str(item[1])):
                path = _normalize_path(raw_path)
                exists = path.exists()
                revision_id = None
                if exists:
                    try:
                        revision_id = (await self.read_snapshot(path)).revision_id
                    except FileReadError:
                        revision_id = None
                batch.append(
                    FileChange(
                        path=path,
                        change=_normalize_change(change),
                        exists=exists,
                        revision_id=revision_id,
                    )
                )
            if batch:
                yield tuple(batch)


def _ordered_paths(
    active_file: Path | str,
    included_files: Iterable[Path | str],
) -> tuple[Path, ...]:
    active = _normalize_path(active_file)
    seen = {active}
    ordered = [active]
    for path in sorted((_normalize_path(item) for item in included_files), key=str):
        if path in seen:
            continue
        ordered.append(path)
        seen.add(path)
    return tuple(ordered)


def _normalize_path(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


def _normalize_change(change: Any) -> str:
    name = getattr(change, "name", "").lower()
    if name in {"added", "modified", "deleted"}:
        return name
    if change == 1:
        return "added"
    if change == 2:
        return "modified"
    if change == 3:
        return "deleted"
    return "modified"
