from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PlanStatus = Literal[
    "draft",
    "awaiting_approval",
    "approved",
    "implementing",
    "implemented",
    "archived",
    "rejected",
]


@dataclass(frozen=True)
class PlanEntry:
    id: str
    source_note: str
    path: str
    title: str
    status: PlanStatus
    created_at: str
    updated_at: str
    approved_at: str | None = None
    implemented_at: str | None = None

    def file_path(self, plans_dir: Path) -> Path:
        return (plans_dir / self.path).expanduser().resolve()


@dataclass(frozen=True)
class PlanDocument:
    entry: PlanEntry
    path: Path
    markdown: str
    body: str
    frontmatter: dict[str, str]
