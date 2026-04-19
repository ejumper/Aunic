from __future__ import annotations

from pathlib import Path

import pytest

from aunic.context import FileManager


@pytest.mark.asyncio
async def test_browser_workspace_watch_ignores_permission_denied_paths(tmp_path: Path) -> None:
    path = tmp_path / "watch.md"
    path.write_text("watch me", encoding="utf-8")

    async def fake_awatch(*_paths, **kwargs):
        assert kwargs["ignore_permission_denied"] is True
        return
        yield

    manager = FileManager(awatch_factory=fake_awatch)
    iterator = manager.watch([path])
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)
