from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


# Sentinel embedded in each map entry line as an HTML comment.
# Format: <!-- aunic-map mtime=<nanos> [locked=true] -->
_SENTINEL_RE = re.compile(r"<!-- aunic-map mtime=(\d+)(?: locked=true)? -->")

# Entry line pattern: - [basename](abs_path) — snippet <!-- sentinel -->
_ENTRY_RE = re.compile(
    r"^- \[([^\]]+)\]\(([^)]+)\) — (.*?) (<!-- aunic-map mtime=\d+(?:(?: locked=true))? -->)$"
)

# Heading pattern: ## /some/absolute/dir/
_HEADING_RE = re.compile(r"^## (.+)$")


@dataclass(frozen=True)
class MapEntry:
    path: Path       # absolute path to the note
    snippet: str     # already truncated to <=200 chars
    mtime_ns: int    # from filesystem at index time; 0 for locked entries
    locked: bool     # True iff this entry came from a pinned summary


def parse_map(text: str) -> dict[Path, MapEntry]:
    """Parse a map.md file into {Path: MapEntry}.

    Skips the top-matter lines (# Aunic note map, Generated: ...).
    Unknown lines are silently ignored.
    """
    entries: dict[Path, MapEntry] = {}
    for line in text.splitlines():
        line = line.strip()
        m = _ENTRY_RE.match(line)
        if not m:
            continue
        _basename, abs_path_str, snippet, sentinel_str = m.group(1), m.group(2), m.group(3), m.group(4)
        sm = _SENTINEL_RE.search(sentinel_str)
        if not sm:
            continue
        mtime_ns = int(sm.group(1))
        locked = "locked=true" in sentinel_str
        path = Path(abs_path_str)
        entries[path] = MapEntry(
            path=path,
            snippet=snippet,
            mtime_ns=mtime_ns,
            locked=locked,
        )
    return entries


def render_map(
    entries: dict[Path, MapEntry],
    *,
    walk_root: Path,
    generated_at: datetime | None = None,
) -> str:
    """Render {Path: MapEntry} to a map.md string.

    Entries are grouped by parent directory, directories sorted by path,
    entries within each directory sorted by path. Deterministic.
    """
    if generated_at is None:
        generated_at = datetime.now(UTC)

    ts = generated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    count = len(entries)

    lines: list[str] = [
        "# Aunic note map",
        "",
        f"Generated: {ts} from {walk_root} ({count} notes).",
        "",
    ]

    # Group by parent directory
    by_dir: dict[Path, list[MapEntry]] = {}
    for entry in entries.values():
        parent = entry.path.parent
        by_dir.setdefault(parent, []).append(entry)

    for dir_path in sorted(by_dir.keys()):
        dir_entries = sorted(by_dir[dir_path], key=lambda e: e.path)
        lines.append(f"## {dir_path}/")
        lines.append("")
        for entry in dir_entries:
            sentinel = f"<!-- aunic-map mtime={entry.mtime_ns}" + (" locked=true" if entry.locked else "") + " -->"
            lines.append(f"- [{entry.path.name}]({entry.path}) — {entry.snippet} {sentinel}")
        lines.append("")

    return "\n".join(lines)
