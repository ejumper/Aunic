from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from aunic.browser.errors import BrowserError
from aunic.browser.session import BrowserSession
from aunic.browser.session_registry import BrowserSessionRegistry
from aunic.context import FileManager
from aunic.model_options import ModelOption


class FakeConnection:
    async def send_event(
        self,
        message_type: str,
        payload: dict[str, Any],
        *,
        message_id: str | None = None,
    ) -> None:
        return None


class FakeProvider:
    name = "fake"


class IdleRunner:
    async def run(self, request: Any) -> object:
        return object()


class BlockingRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def run(self, request: Any) -> object:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class FakeSession:
    def __init__(self, instance_id: str) -> None:
        self.instance_id = instance_id
        self.attached: list[object] = []
        self.detached: list[object] = []
        self.shutdown_calls = 0

    async def attach(self, conn: object) -> None:
        self.attached.append(conn)

    async def detach(self, conn: object) -> None:
        self.detached.append(conn)

    async def shutdown(self) -> None:
        self.shutdown_calls += 1


def _browser_session(
    tmp_path: Path,
    instance_id: str,
    *,
    note_runner: Any | None = None,
    acquire_run_file=None,
    release_run_file=None,
) -> BrowserSession:
    return BrowserSession(
        instance_id=instance_id,
        workspace_root=tmp_path,
        file_manager=FileManager(),
        note_runner=note_runner or IdleRunner(),
        chat_runner=None,
        provider_factory=lambda _option, _cwd: FakeProvider(),
        model_options=(ModelOption(label="Fake", provider_name="codex", model="fake"),),
        acquire_run_file=acquire_run_file,
        release_run_file=release_run_file,
    )


async def _wait_for(predicate, *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Condition was not met before timeout.")


@pytest.mark.asyncio
async def test_registry_reuses_session_after_disconnect_within_ttl() -> None:
    created: list[FakeSession] = []

    def session_factory(instance_id: str) -> FakeSession:
        session = FakeSession(instance_id)
        created.append(session)
        return session

    registry = BrowserSessionRegistry(session_factory=session_factory, idle_ttl_seconds=0.2)
    conn_one = FakeConnection()
    session_one = await registry.attach(instance_id="tab-1", page_id="page-1", conn=conn_one)
    await registry.detach(instance_id="tab-1", conn=conn_one)

    conn_two = FakeConnection()
    session_two = await registry.attach(instance_id="tab-1", page_id="page-2", conn=conn_two)

    assert session_two is session_one
    assert len(created) == 1
    await registry.shutdown()


@pytest.mark.asyncio
async def test_registry_rejects_second_live_page_for_same_instance() -> None:
    registry = BrowserSessionRegistry(
        session_factory=lambda instance_id: FakeSession(instance_id),
        idle_ttl_seconds=0.2,
    )
    await registry.attach(instance_id="tab-1", page_id="page-1", conn=FakeConnection())

    with pytest.raises(BrowserError) as exc_info:
        await registry.attach(instance_id="tab-1", page_id="page-2", conn=FakeConnection())

    assert exc_info.value.reason == "instance_conflict"
    await registry.shutdown()


@pytest.mark.asyncio
async def test_registry_expires_idle_sessions_after_ttl() -> None:
    created: list[FakeSession] = []

    def session_factory(instance_id: str) -> FakeSession:
        session = FakeSession(instance_id)
        created.append(session)
        return session

    registry = BrowserSessionRegistry(session_factory=session_factory, idle_ttl_seconds=0.05)
    conn = FakeConnection()
    first_session = await registry.attach(instance_id="tab-1", page_id="page-1", conn=conn)
    await registry.detach(instance_id="tab-1", conn=conn)

    await _wait_for(lambda: first_session.shutdown_calls == 1)

    second_session = await registry.attach(
        instance_id="tab-1",
        page_id="page-2",
        conn=FakeConnection(),
    )
    assert second_session is not first_session
    assert len(created) == 2
    await registry.shutdown()


@pytest.mark.asyncio
async def test_registry_allows_parallel_runs_for_different_files_but_blocks_same_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "one.md").write_text("# One\n", encoding="utf-8")
    (tmp_path / "two.md").write_text("# Two\n", encoding="utf-8")
    runners: dict[str, BlockingRunner] = {}

    registry_holder: dict[str, BrowserSessionRegistry] = {}

    def session_factory(instance_id: str) -> BrowserSession:
        runner = BlockingRunner()
        runners[instance_id] = runner
        registry = registry_holder["registry"]
        return _browser_session(
            tmp_path,
            instance_id,
            note_runner=runner,
            acquire_run_file=lambda path: registry.acquire_run_file(instance_id=instance_id, path=path),
            release_run_file=lambda path: registry.release_run_file(instance_id=instance_id, path=path),
        )

    registry = BrowserSessionRegistry(session_factory=session_factory, idle_ttl_seconds=0.2)
    registry_holder["registry"] = registry

    session_one = await registry.attach(instance_id="tab-1", page_id="page-1", conn=FakeConnection())
    session_two = await registry.attach(instance_id="tab-2", page_id="page-2", conn=FakeConnection())

    run_one = await session_one.submit_prompt(active_file="one.md", included_files=[], text="Do it")
    await runners["tab-1"].started.wait()

    with pytest.raises(BrowserError) as exc_info:
        await session_two.submit_prompt(active_file="one.md", included_files=[], text="Do it too")
    assert exc_info.value.reason == "file_run_in_progress"

    run_two = await session_two.submit_prompt(active_file="two.md", included_files=[], text="Do it there")
    await runners["tab-2"].started.wait()

    assert await session_one.cancel_run(run_one) is True
    assert await session_two.cancel_run(run_two) is True
    await runners["tab-1"].cancelled.wait()
    await runners["tab-2"].cancelled.wait()
    await registry.shutdown()
