from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class NoteMetadata:
    version: int = 1
    summary: str | None = None
    summary_locked: bool = False
    auto_snippet_stale: bool = False
    last_auto_snippet: str | None = None
    last_indexed_mtime_ns: int | None = None


def meta_path_for(note_path: Path) -> Path:
    """Return the .meta.json path for a note."""
    return note_path.parent / ".aunic" / f"{note_path.stem}.meta.json"


def load_meta(note_path: Path) -> NoteMetadata:
    """Load NoteMetadata from disk. Returns defaults on missing or malformed file."""
    path = meta_path_for(note_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return NoteMetadata()

    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            logger.warning("meta file is not a dict: %s", path)
            return NoteMetadata()
    except json.JSONDecodeError as exc:
        logger.warning("meta file malformed (%s): %s", exc, path)
        return NoteMetadata()

    return NoteMetadata(
        version=data.get("version", 1),
        summary=data.get("summary"),
        summary_locked=bool(data.get("summary_locked", False)),
        auto_snippet_stale=bool(data.get("auto_snippet_stale", False)),
        last_auto_snippet=data.get("last_auto_snippet"),
        last_indexed_mtime_ns=data.get("last_indexed_mtime_ns"),
    )


def save_meta(note_path: Path, meta: NoteMetadata) -> None:
    """Persist NoteMetadata to disk. Creates .aunic/ directory as needed."""
    path = meta_path_for(note_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": meta.version,
        "summary": meta.summary,
        "summary_locked": meta.summary_locked,
        "auto_snippet_stale": meta.auto_snippet_stale,
        "last_auto_snippet": meta.last_auto_snippet,
        "last_indexed_mtime_ns": meta.last_indexed_mtime_ns,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
