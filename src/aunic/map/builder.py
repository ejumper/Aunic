from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from aunic.discovery import is_aunic_note, walk_aunic_notes
from aunic.map.manifest import NoteMetadata, load_meta, save_meta
from aunic.map.render import MapEntry, parse_map, render_map
from aunic.map.runtime import (
    is_map_globally_stale,
    resolve_map_location,
)
from aunic.map.snippet import compute_auto_snippet

logger = logging.getLogger(__name__)

MAP_PATH = Path.home() / ".aunic" / "map.md"
_SHARED_ENSURE_LOCK = asyncio.Lock()
_SHARED_ENSURE_TASKS: dict[Path, asyncio.Task[BuildResult | None]] = {}


@dataclass(frozen=True)
class BuildResult:
    map_path: Path
    entry_count: int
    walk_root: Path
    entries_added: int
    entries_updated: int
    entries_removed: int
    entries_reused_from_cache: int
    elapsed_seconds: float


def build_map(
    scope: Path | None = None,
    *,
    subject_path: Path | None = None,
    fallback_root: Path | None = None,
) -> BuildResult:
    """Walk Aunic notes and write the canonical map (and a local scoped copy).

    When scope is None, walks from the resolved canonical anchor root.
    When scope is provided, out-of-scope entries from the existing canonical map
    are preserved.
    """
    t0 = time.monotonic()
    resolved_subject = (
        (subject_path or scope or fallback_root or Path.home())
        .expanduser()
        .resolve()
    )
    resolved_fallback = (fallback_root or resolved_subject).expanduser().resolve()
    location = resolve_map_location(
        resolved_subject,
        fallback_root=resolved_fallback,
        create=True,
    )
    walk_root = scope.resolve() if scope is not None else location.anchor_root

    # Load existing map for incremental refresh
    prev_entries: dict[Path, MapEntry] = {}
    if location.map_path.exists():
        try:
            prev_entries = parse_map(location.map_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not parse existing map: %s", exc)

    notes = walk_aunic_notes(walk_root)

    new_entries: dict[Path, MapEntry] = {}
    entries_added = 0
    entries_updated = 0
    entries_reused = 0

    generated_at = datetime.now(UTC)

    for note_path in notes:
        note_path = note_path.resolve()
        meta = load_meta(note_path)

        # Pinned summary wins
        if meta.summary_locked and meta.summary:
            snippet = meta.summary[:200]
            new_entries[note_path] = MapEntry(
                path=note_path,
                snippet=snippet,
                mtime_ns=0,
                locked=True,
            )
            if note_path not in prev_entries:
                entries_added += 1
            else:
                entries_reused += 1
            continue

        # Check current mtime
        try:
            current_mtime = note_path.stat().st_mtime_ns
        except OSError:
            continue

        prev = prev_entries.get(note_path)
        if prev is not None and not prev.locked and prev.mtime_ns == current_mtime:
            # Reuse cached snippet
            new_entries[note_path] = prev
            entries_reused += 1
            continue

        # Read and compute snippet
        try:
            text = note_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        snippet = compute_auto_snippet(text)

        new_entries[note_path] = MapEntry(
            path=note_path,
            snippet=snippet,
            mtime_ns=current_mtime,
            locked=False,
        )

        # Persist meta update
        updated_meta = NoteMetadata(
            version=meta.version,
            summary=meta.summary,
            summary_locked=meta.summary_locked,
            auto_snippet_stale=False,
            last_auto_snippet=snippet,
            last_indexed_mtime_ns=current_mtime,
        )
        try:
            save_meta(note_path, updated_meta)
        except Exception as exc:
            logger.warning("Could not save meta for %s: %s", note_path, exc)

        if note_path not in prev_entries:
            entries_added += 1
        else:
            entries_updated += 1

    # When walking a subtree, preserve out-of-scope entries from prev map
    combined_entries = dict(new_entries)
    if scope is not None:
        scope_resolved = scope.resolve()
        for path, entry in prev_entries.items():
            if not _is_under(path, scope_resolved) and path not in combined_entries:
                combined_entries[path] = entry

    entries_removed = len(prev_entries) - len(
        [p for p in prev_entries if p in combined_entries]
    )

    # Atomic write of canonical map
    map_text = render_map(combined_entries, walk_root=walk_root, generated_at=generated_at)
    _atomic_write(location.map_path, map_text)

    # Write local scoped copy (entries under walk_root only)
    local_map_path = walk_root / ".aunic" / "map.md"
    if local_map_path != location.map_path:
        local_entries = {p: e for p, e in combined_entries.items() if _is_under(p, walk_root)}
        if local_entries:
            local_text = render_map(local_entries, walk_root=walk_root, generated_at=generated_at)
            try:
                _atomic_write(local_map_path, local_text)
            except Exception as exc:
                logger.warning("Could not write local map at %s: %s", local_map_path, exc)

    elapsed = time.monotonic() - t0
    return BuildResult(
        map_path=location.map_path,
        entry_count=len(combined_entries),
        walk_root=walk_root,
        entries_added=entries_added,
        entries_updated=entries_updated,
        entries_removed=max(0, entries_removed),
        entries_reused_from_cache=entries_reused,
        elapsed_seconds=elapsed,
    )


def mark_map_entry_stale(note_path: Path) -> None:
    """Mark a note's map entry as stale after a save.

    No-op if the file is not an Aunic note or has a locked summary.
    Never raises.
    """
    try:
        if not is_aunic_note(note_path):
            return
        meta = load_meta(note_path)
        if meta.summary_locked:
            return
        updated = NoteMetadata(
            version=meta.version,
            summary=meta.summary,
            summary_locked=meta.summary_locked,
            auto_snippet_stale=True,
            last_auto_snippet=meta.last_auto_snippet,
            last_indexed_mtime_ns=meta.last_indexed_mtime_ns,
        )
        save_meta(note_path, updated)
    except Exception as exc:
        logger.warning("mark_map_entry_stale failed for %s: %s", note_path, exc)


def refresh_map_entry_if_stale(
    note_path: Path,
    *,
    fallback_root: Path | None = None,
) -> None:
    """Rebuild a single map entry if marked stale. No-op in many conditions.

    No-op when:
    - the canonical map does not exist
    - note is not an Aunic note
    - meta.auto_snippet_stale is False
    - summary_locked is True
    Never raises.
    """
    try:
        location = resolve_map_location(note_path, fallback_root=fallback_root)
        if not location.map_path.exists():
            return
        if not is_aunic_note(note_path):
            return
        meta = load_meta(note_path)
        if not meta.auto_snippet_stale or meta.summary_locked:
            return

        # Read file and compute new snippet
        try:
            text = note_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return

        snippet = compute_auto_snippet(text)

        try:
            current_mtime = note_path.stat().st_mtime_ns
        except OSError:
            return

        # If snippet unchanged and mtime unchanged, just clear the flag
        if snippet == meta.last_auto_snippet and current_mtime == meta.last_indexed_mtime_ns:
            _clear_stale_flag(note_path, meta)
            return

        # Update map entry
        try:
            map_text = location.map_path.read_text(encoding="utf-8")
            entries = parse_map(map_text)
        except Exception as exc:
            logger.warning("Could not read map for refresh: %s", exc)
            return

        entries[note_path] = MapEntry(
            path=note_path,
            snippet=snippet,
            mtime_ns=current_mtime,
            locked=False,
        )

        # Re-derive walk_root from existing top-matter or fall back to home
        walk_root = _parse_walk_root(map_text, fallback_root=location.anchor_root)
        new_text = render_map(entries, walk_root=walk_root)
        _atomic_write(location.map_path, new_text)

        # Persist updated meta
        updated_meta = NoteMetadata(
            version=meta.version,
            summary=meta.summary,
            summary_locked=False,
            auto_snippet_stale=False,
            last_auto_snippet=snippet,
            last_indexed_mtime_ns=current_mtime,
        )
        save_meta(note_path, updated_meta)

    except Exception as exc:
        logger.warning("refresh_map_entry_if_stale failed for %s: %s", note_path, exc)


def ensure_map_ready(
    subject_path: Path,
    *,
    fallback_root: Path | None = None,
) -> BuildResult | None:
    location = resolve_map_location(
        subject_path,
        fallback_root=fallback_root,
        create=True,
    )
    if location.map_path.exists() and not is_map_globally_stale(location.map_path):
        return None
    return build_map(
        None,
        subject_path=subject_path,
        fallback_root=fallback_root or location.anchor_root,
    )


async def ensure_map_ready_shared(
    subject_path: Path,
    *,
    fallback_root: Path | None = None,
) -> BuildResult | None:
    location = resolve_map_location(subject_path, fallback_root=fallback_root)
    async with _SHARED_ENSURE_LOCK:
        task = _SHARED_ENSURE_TASKS.get(location.map_path)
        if task is None or task.done():
            task = asyncio.create_task(
                asyncio.to_thread(
                    ensure_map_ready,
                    subject_path,
                    fallback_root=fallback_root,
                )
            )
            _SHARED_ENSURE_TASKS[location.map_path] = task
    try:
        return await task
    finally:
        async with _SHARED_ENSURE_LOCK:
            if _SHARED_ENSURE_TASKS.get(location.map_path) is task and task.done():
                _SHARED_ENSURE_TASKS.pop(location.map_path, None)


def set_summary(
    note_path: Path,
    text: str,
    *,
    fallback_root: Path | None = None,
) -> None:
    """Set a locked summary for a note and update map.md if present."""
    meta = load_meta(note_path)
    snippet = text[:200]
    updated_meta = NoteMetadata(
        version=meta.version,
        summary=snippet,
        summary_locked=True,
        auto_snippet_stale=False,
        last_auto_snippet=meta.last_auto_snippet,
        last_indexed_mtime_ns=meta.last_indexed_mtime_ns,
    )
    save_meta(note_path, updated_meta)

    location = resolve_map_location(note_path, fallback_root=fallback_root)
    if location.map_path.exists():
        _update_single_entry(
            note_path,
            MapEntry(
                path=note_path,
                snippet=snippet,
                mtime_ns=0,
                locked=True,
            ),
            map_path=location.map_path,
            fallback_root=location.anchor_root,
        )


def clear_summary(
    note_path: Path,
    *,
    fallback_root: Path | None = None,
) -> None:
    """Clear a locked summary for a note, recompute auto snippet, update map.md."""
    meta = load_meta(note_path)

    # Recompute auto snippet
    try:
        text = note_path.read_text(encoding="utf-8", errors="replace")
        snippet = compute_auto_snippet(text)
        try:
            current_mtime = note_path.stat().st_mtime_ns
        except OSError:
            current_mtime = None
    except OSError:
        snippet = "(empty)"
        current_mtime = None

    updated_meta = NoteMetadata(
        version=meta.version,
        summary=None,
        summary_locked=False,
        auto_snippet_stale=False,
        last_auto_snippet=snippet,
        last_indexed_mtime_ns=current_mtime,
    )
    save_meta(note_path, updated_meta)

    location = resolve_map_location(note_path, fallback_root=fallback_root)
    if location.map_path.exists() and current_mtime is not None:
        _update_single_entry(
            note_path,
            MapEntry(
                path=note_path,
                snippet=snippet,
                mtime_ns=current_mtime,
                locked=False,
            ),
            map_path=location.map_path,
            fallback_root=location.anchor_root,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_under(path: Path, parent: Path) -> bool:
    """Return True if path is under parent (both should be resolved)."""
    try:
        path.resolve().relative_to(parent)
        return True
    except ValueError:
        return False


def _atomic_write(path: Path, text: str) -> None:
    """Write text to path atomically via a temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _parse_walk_root(map_text: str, *, fallback_root: Path | None = None) -> Path:
    """Extract the walk root from the Generated: line, or fall back to the anchor."""
    for line in map_text.splitlines():
        if line.startswith("Generated:") and " from " in line:
            # "Generated: 2026-... from /home/user (N notes)."
            after_from = line.split(" from ", 1)[1]
            root_str = after_from.split(" (")[0].strip()
            if root_str:
                return Path(root_str)
    return (fallback_root or Path.home()).expanduser().resolve()


def _clear_stale_flag(note_path: Path, meta: NoteMetadata) -> None:
    updated_meta = NoteMetadata(
        version=meta.version,
        summary=meta.summary,
        summary_locked=meta.summary_locked,
        auto_snippet_stale=False,
        last_auto_snippet=meta.last_auto_snippet,
        last_indexed_mtime_ns=meta.last_indexed_mtime_ns,
    )
    save_meta(note_path, updated_meta)


def _update_single_entry(
    note_path: Path,
    new_entry: MapEntry,
    *,
    map_path: Path,
    fallback_root: Path | None = None,
) -> None:
    """Parse map.md, update one entry, atomic-write."""
    try:
        map_text = map_path.read_text(encoding="utf-8")
        entries = parse_map(map_text)
        entries[note_path] = new_entry
        walk_root = _parse_walk_root(map_text, fallback_root=fallback_root)
        new_text = render_map(entries, walk_root=walk_root)
        _atomic_write(map_path, new_text)
    except Exception as exc:
        logger.warning("_update_single_entry failed for %s: %s", note_path, exc)
