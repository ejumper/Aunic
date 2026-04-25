from __future__ import annotations

import json
from pathlib import Path

from aunic.plans import PlanService, parse_plan_markdown, slugify_plan_title


def test_slugify_plan_title_ascii_folds_and_collapses() -> None:
    assert slugify_plan_title("Résumé: Import Preview!!") == "resume-import-preview"
    assert slugify_plan_title("...") == "untitled-plan"


def test_create_plan_writes_frontmatter_and_index(tmp_path: Path) -> None:
    source_note = tmp_path / "task.md"
    source_note.write_text("# Task\n", encoding="utf-8")

    service = PlanService(source_note)
    first = service.create_plan("Foo Bar")
    second = service.create_plan("Foo Bar")

    assert first.path == tmp_path / ".aunic" / "plans" / "foo-bar.md"
    assert second.path == tmp_path / ".aunic" / "plans" / "foo-bar-2.md"
    frontmatter, body = parse_plan_markdown(first.path.read_text(encoding="utf-8"))
    assert frontmatter["aunic_type"] == "plan"
    assert frontmatter["plan_id"].endswith("-foo-bar")
    assert frontmatter["source_note"] == "../../task.md"
    assert frontmatter["status"] == "draft"
    assert body.startswith("# Foo Bar")

    index = json.loads(service.index_path.read_text(encoding="utf-8"))
    assert index["version"] == 1
    assert [entry["path"] for entry in index["plans"]] == ["foo-bar.md", "foo-bar-2.md"]


def test_list_recovers_index_by_scanning_frontmatter(tmp_path: Path) -> None:
    source_note = tmp_path / "task.md"
    source_note.write_text("# Task\n", encoding="utf-8")
    service = PlanService(source_note)
    document = service.create_plan("Recover Me")
    service.index_path.unlink()

    entries = service.list_plans_for_source_note()

    assert len(entries) == 1
    assert entries[0].id == document.entry.id
    assert entries[0].path == "recover-me.md"
    assert service.index_path.exists()


def test_save_plan_content_wraps_missing_frontmatter(tmp_path: Path) -> None:
    source_note = tmp_path / "task.md"
    source_note.write_text("# Task\n", encoding="utf-8")
    service = PlanService(source_note)
    document = service.create_plan("Original")

    updated = service.save_plan_content(document.entry.id, "# New Title\n\n- Step 1\n")

    assert updated.entry.title == "New Title"
    frontmatter, body = parse_plan_markdown(updated.path.read_text(encoding="utf-8"))
    assert frontmatter["plan_id"] == document.entry.id
    assert frontmatter["status"] == "draft"
    assert body.startswith("# New Title")


def test_delete_plan_removes_markdown_and_index_entry(tmp_path: Path) -> None:
    source_note = tmp_path / "task.md"
    source_note.write_text("# Task\n", encoding="utf-8")
    service = PlanService(source_note)
    first = service.create_plan("Keep Me")
    second = service.create_plan("Delete Me")

    deleted = service.delete_plan(second.entry.id)

    assert deleted.id == second.entry.id
    assert not second.path.exists()
    index = json.loads(service.index_path.read_text(encoding="utf-8"))
    assert [entry["path"] for entry in index["plans"]] == [first.entry.path]
