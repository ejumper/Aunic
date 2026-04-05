from __future__ import annotations

from pathlib import Path

import pytest

from aunic.context import FileManager
from aunic.errors import OptimisticWriteError


@pytest.mark.asyncio
async def test_file_manager_orders_active_file_first_and_dedupes(tmp_path: Path) -> None:
    active = tmp_path / "active.md"
    included = tmp_path / "included.md"
    active.write_text("active", encoding="utf-8")
    included.write_text("included", encoding="utf-8")

    manager = FileManager()
    snapshots = await manager.read_working_set(active, [included, active])

    assert [snapshot.path for snapshot in snapshots] == [
        active.resolve(),
        included.resolve(),
    ]
    assert snapshots[0].revision_id.count(":") == 2


@pytest.mark.asyncio
async def test_file_manager_optimistic_write_detects_revision_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    path.write_text("first", encoding="utf-8")
    manager = FileManager()

    original = await manager.read_snapshot(path)
    updated = await manager.write_text(path, "second", expected_revision=original.revision_id)

    assert updated.raw_text == "second"
    with pytest.raises(OptimisticWriteError):
        await manager.write_text(path, "third", expected_revision=original.revision_id)


@pytest.mark.asyncio
async def test_file_manager_watch_emits_change_batches(tmp_path: Path) -> None:
    path = tmp_path / "watch.md"
    path.write_text("watch me", encoding="utf-8")

    async def fake_awatch(*paths, **kwargs):
        assert str(path.resolve()) in {str(item) for item in paths}
        assert "debounce" in kwargs
        yield {(2, str(path.resolve()))}

    manager = FileManager(awatch_factory=fake_awatch)
    iterator = manager.watch([path])
    batch = await anext(iterator)
    await iterator.aclose()

    assert len(batch) == 1
    assert batch[0].change == "modified"
    assert batch[0].path == path.resolve()
    assert batch[0].revision_id is not None
