from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from aunic.plans.types import PlanDocument, PlanEntry, PlanStatus

INDEX_VERSION = 1
VALID_STATUSES: frozenset[str] = frozenset(
    {
        "draft",
        "awaiting_approval",
        "approved",
        "implementing",
        "implemented",
        "archived",
        "rejected",
    }
)


class PlanService:
    """File-backed plan storage attached to a source markdown note."""

    def __init__(self, source_note: Path) -> None:
        self.source_note = source_note.expanduser().resolve()
        self.plans_dir = self.source_note.parent / ".aunic" / "plans"
        self.index_path = self.plans_dir / "index.json"

    def list_plans_for_source_note(self) -> tuple[PlanEntry, ...]:
        index = self._load_index()
        source_ref = self._source_note_ref()
        entries = [
            entry
            for entry in index
            if entry.source_note == source_ref or self._source_ref_matches(entry.source_note)
        ]
        entries.sort(key=lambda entry: (entry.updated_at, entry.created_at, entry.id), reverse=True)
        return tuple(entries)

    def create_plan(
        self,
        title: str,
        *,
        content: str | None = None,
        status: PlanStatus = "draft",
    ) -> PlanDocument:
        clean_title = _clean_title(title) or "Untitled Plan"
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        slug = slugify_plan_title(clean_title)
        plan_id, file_name = self._unique_plan_identity(slug)
        now = _now_iso()
        entry = PlanEntry(
            id=plan_id,
            source_note=self._source_note_ref(),
            path=file_name,
            title=clean_title,
            status=status,
            created_at=now,
            updated_at=now,
        )
        body = content if content is not None else f"# {clean_title}\n\n"
        markdown = compose_plan_markdown(entry, body)
        path = entry.file_path(self.plans_dir)
        path.write_text(markdown, encoding="utf-8")
        self._upsert_entry(entry)
        return PlanDocument(
            entry=entry,
            path=path,
            markdown=markdown,
            body=body,
            frontmatter=_frontmatter_from_entry(entry),
        )

    def get_plan(self, plan_id: str) -> PlanDocument:
        for entry in self.list_plans_for_source_note():
            if entry.id == plan_id:
                return self.read_plan(entry)
        raise FileNotFoundError(f"Plan not found: {plan_id}")

    def read_plan(self, entry_or_id: PlanEntry | str) -> PlanDocument:
        entry = self._entry_for(entry_or_id)
        path = entry.file_path(self.plans_dir)
        markdown = path.read_text(encoding="utf-8")
        frontmatter, body = parse_plan_markdown(markdown)
        merged_entry = _entry_from_frontmatter(
            frontmatter,
            path=entry.path,
            fallback=entry,
        )
        return PlanDocument(
            entry=merged_entry,
            path=path,
            markdown=markdown,
            body=body,
            frontmatter=frontmatter,
        )

    def save_plan_content(self, plan_id: str, content: str) -> PlanDocument:
        existing = self.get_plan(plan_id)
        frontmatter, body = parse_plan_markdown(content)
        title = _extract_title(body) if frontmatter else _extract_title(content)
        now = _now_iso()
        entry = PlanEntry(
            id=existing.entry.id,
            source_note=frontmatter.get("source_note", existing.entry.source_note),
            path=existing.entry.path,
            title=title or existing.entry.title,
            status=_coerce_status(frontmatter.get("status"), existing.entry.status),
            created_at=frontmatter.get("created_at", existing.entry.created_at),
            updated_at=now,
            approved_at=frontmatter.get("approved_at") or existing.entry.approved_at,
            implemented_at=frontmatter.get("implemented_at") or existing.entry.implemented_at,
        )
        markdown = compose_plan_markdown(entry, body if frontmatter else content)
        existing.path.write_text(markdown, encoding="utf-8")
        self._upsert_entry(entry)
        return PlanDocument(
            entry=entry,
            path=existing.path,
            markdown=markdown,
            body=body if frontmatter else content,
            frontmatter=_frontmatter_from_entry(entry),
        )

    def set_status(self, plan_id: str, status: PlanStatus) -> PlanDocument:
        existing = self.get_plan(plan_id)
        now = _now_iso()
        entry = PlanEntry(
            id=existing.entry.id,
            source_note=existing.entry.source_note,
            path=existing.entry.path,
            title=existing.entry.title,
            status=status,
            created_at=existing.entry.created_at,
            updated_at=now,
            approved_at=now if status == "approved" else existing.entry.approved_at,
            implemented_at=now if status == "implemented" else existing.entry.implemented_at,
        )
        markdown = compose_plan_markdown(entry, existing.body)
        existing.path.write_text(markdown, encoding="utf-8")
        self._upsert_entry(entry)
        return PlanDocument(
            entry=entry,
            path=existing.path,
            markdown=markdown,
            body=existing.body,
            frontmatter=_frontmatter_from_entry(entry),
        )

    def delete_plan(self, plan_id: str) -> PlanEntry:
        entry = self._entry_for(plan_id)
        path = entry.file_path(self.plans_dir)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        entries = [existing for existing in self._load_index() if existing.id != entry.id]
        if entries or self.index_path.exists():
            self._write_index(entries)
        return entry

    def recover_index(self) -> tuple[PlanEntry, ...]:
        entries: list[PlanEntry] = []
        if not self.plans_dir.exists():
            return ()
        for path in sorted(self.plans_dir.glob("*.md")):
            try:
                markdown = path.read_text(encoding="utf-8")
            except OSError:
                continue
            frontmatter, body = parse_plan_markdown(markdown)
            if frontmatter.get("aunic_type") != "plan":
                continue
            entry = _entry_from_frontmatter(
                frontmatter,
                path=path.name,
                fallback=PlanEntry(
                    id=path.stem,
                    source_note=frontmatter.get("source_note", self._source_note_ref()),
                    path=path.name,
                    title=_extract_title(body) or path.stem,
                    status=_coerce_status(frontmatter.get("status"), "draft"),
                    created_at=frontmatter.get("created_at", _now_iso()),
                    updated_at=frontmatter.get("updated_at", _now_iso()),
                    approved_at=frontmatter.get("approved_at") or None,
                    implemented_at=frontmatter.get("implemented_at") or None,
                ),
            )
            entries.append(entry)
        if entries or self.index_path.exists():
            self._write_index(entries)
        return tuple(entries)

    def _entry_for(self, entry_or_id: PlanEntry | str) -> PlanEntry:
        if isinstance(entry_or_id, PlanEntry):
            return entry_or_id
        for entry in self.list_plans_for_source_note():
            if entry.id == entry_or_id:
                return entry
        raise FileNotFoundError(f"Plan not found: {entry_or_id}")

    def _load_index(self) -> list[PlanEntry]:
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return list(self.recover_index())
        if not isinstance(raw, dict) or raw.get("version") != INDEX_VERSION:
            return list(self.recover_index())
        entries: list[PlanEntry] = []
        for item in raw.get("plans", []):
            if not isinstance(item, dict):
                continue
            try:
                entries.append(_entry_from_index_item(item))
            except (TypeError, ValueError):
                continue
        return entries

    def _write_index(self, entries: list[PlanEntry]) -> None:
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": INDEX_VERSION,
            "plans": [_index_item_from_entry(entry) for entry in entries],
        }
        self.index_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _upsert_entry(self, entry: PlanEntry) -> None:
        entries = [existing for existing in self._load_index() if existing.id != entry.id]
        entries.append(entry)
        entries.sort(key=lambda item: (item.source_note, item.created_at, item.id))
        self._write_index(entries)

    def _unique_plan_identity(self, slug: str) -> tuple[str, str]:
        date = datetime.now().astimezone().date().isoformat()
        base_id = f"{date}-{slug}"
        existing_ids = {entry.id for entry in self._load_index()}
        existing_paths = {entry.path for entry in self._load_index()}
        suffix = 1
        while True:
            plan_id = base_id if suffix == 1 else f"{base_id}-{suffix}"
            file_name = f"{slug}.md" if suffix == 1 else f"{slug}-{suffix}.md"
            if (
                plan_id not in existing_ids
                and file_name not in existing_paths
                and not (self.plans_dir / file_name).exists()
            ):
                return plan_id, file_name
            suffix += 1

    def _source_note_ref(self) -> str:
        return os.path.relpath(self.source_note, self.plans_dir).replace(os.sep, "/")

    def _source_ref_matches(self, source_ref: str) -> bool:
        try:
            return (self.plans_dir / source_ref).expanduser().resolve() == self.source_note
        except OSError:
            return False


def slugify_plan_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKD", title)
    ascii_title = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_title.lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "untitled-plan"


def parse_plan_markdown(markdown: str) -> tuple[dict[str, str], str]:
    if not markdown.startswith("---\n"):
        return {}, markdown
    end = markdown.find("\n---", 4)
    if end == -1:
        return {}, markdown
    close_end = end + len("\n---")
    if len(markdown) > close_end and markdown[close_end] == "\n":
        close_end += 1
    raw_frontmatter = markdown[4:end]
    frontmatter: dict[str, str] = {}
    for line in raw_frontmatter.splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip()
    return frontmatter, markdown[close_end:].lstrip("\n")


def compose_plan_markdown(entry: PlanEntry, body: str) -> str:
    frontmatter = _frontmatter_from_entry(entry)
    lines = ["---"]
    for key in (
        "aunic_type",
        "plan_id",
        "source_note",
        "status",
        "created_at",
        "updated_at",
        "approved_at",
        "implemented_at",
    ):
        value = frontmatter.get(key)
        if value is None:
            continue
        lines.append(f"{key}: {value}")
    lines.append("---")
    clean_body = body.lstrip("\n")
    return "\n".join(lines) + "\n\n" + clean_body


def _frontmatter_from_entry(entry: PlanEntry) -> dict[str, str]:
    data = {
        "aunic_type": "plan",
        "plan_id": entry.id,
        "source_note": entry.source_note,
        "status": entry.status,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }
    if entry.approved_at:
        data["approved_at"] = entry.approved_at
    if entry.implemented_at:
        data["implemented_at"] = entry.implemented_at
    return data


def _entry_from_frontmatter(
    frontmatter: dict[str, str],
    *,
    path: str,
    fallback: PlanEntry,
) -> PlanEntry:
    return PlanEntry(
        id=frontmatter.get("plan_id", fallback.id),
        source_note=frontmatter.get("source_note", fallback.source_note),
        path=path,
        title=_extract_title_from_fallback(frontmatter, fallback),
        status=_coerce_status(frontmatter.get("status"), fallback.status),
        created_at=frontmatter.get("created_at", fallback.created_at),
        updated_at=frontmatter.get("updated_at", fallback.updated_at),
        approved_at=frontmatter.get("approved_at") or fallback.approved_at,
        implemented_at=frontmatter.get("implemented_at") or fallback.implemented_at,
    )


def _entry_from_index_item(item: dict[str, Any]) -> PlanEntry:
    return PlanEntry(
        id=_require_string(item, "id"),
        source_note=_require_string(item, "source_note"),
        path=_require_string(item, "path"),
        title=_require_string(item, "title"),
        status=_coerce_status(item.get("status"), "draft"),
        created_at=_require_string(item, "created_at"),
        updated_at=_require_string(item, "updated_at"),
        approved_at=item.get("approved_at") if isinstance(item.get("approved_at"), str) else None,
        implemented_at=(
            item.get("implemented_at") if isinstance(item.get("implemented_at"), str) else None
        ),
    )


def _index_item_from_entry(entry: PlanEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "source_note": entry.source_note,
        "path": entry.path,
        "title": entry.title,
        "status": entry.status,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
        "approved_at": entry.approved_at,
        "implemented_at": entry.implemented_at,
    }


def _extract_title_from_fallback(frontmatter: dict[str, str], fallback: PlanEntry) -> str:
    title = frontmatter.get("title")
    if title:
        return title
    return fallback.title


def _extract_title(markdown: str) -> str | None:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return _clean_title(stripped[2:])
    return None


def _clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip()


def _coerce_status(raw: object, fallback: PlanStatus) -> PlanStatus:
    if isinstance(raw, str) and raw in VALID_STATUSES:
        return raw  # type: ignore[return-value]
    return fallback


def _require_string(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Missing string field {key!r}.")
    return value


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
