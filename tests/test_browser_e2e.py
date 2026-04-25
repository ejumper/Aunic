from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import uvicorn
import websockets

from aunic.browser.server import create_browser_app
from aunic.browser.session import BrowserSession
from aunic.browser.session_registry import BrowserSessionRegistry
from aunic.context import FileManager


class FakeProvider:
    name = "fake"


@pytest.mark.asyncio
async def test_browser_websocket_smoke_hello_and_read_file(tmp_path: Path, unused_tcp_port: int) -> None:
    note = tmp_path / "note.md"
    note.write_text("# Note\n", encoding="utf-8")
    session = BrowserSession(
        instance_id="instance-1",
        workspace_root=tmp_path,
        file_manager=FileManager(),
        note_runner=None,
        chat_runner=None,
        provider_factory=lambda _option, _cwd: FakeProvider(),
    )
    registry = BrowserSessionRegistry(session_factory=lambda _instance_id: session)
    app = create_browser_app(registry)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=unused_tcp_port, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    try:
        await _wait_until_started(server)
        async with websockets.connect(f"ws://127.0.0.1:{unused_tcp_port}/ws") as ws:
            await ws.send(
                json.dumps(
                    {
                        "id": "1",
                        "type": "hello",
                        "payload": {"instance_id": "instance-1", "page_id": "page-1"},
                    }
                )
            )
            hello = json.loads(await ws.recv())
            assert hello["id"] == "1"
            assert hello["type"] == "session_state"
            assert hello["payload"]["run_active"] is False

            await ws.send(
                json.dumps(
                    {"id": "2", "type": "read_file", "payload": {"path": "note.md"}}
                )
            )
            response = json.loads(await ws.recv())
            assert response["id"] == "2"
            assert response["type"] == "response"
            assert response["payload"]["note_content"] == "# Note\n"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=2)


async def _wait_until_started(server: uvicorn.Server) -> None:
    for _ in range(100):
        if server.started:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("uvicorn server did not start")
