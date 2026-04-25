from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket

from aunic.browser.connection import ConnectionHandler
from aunic.browser.session import BrowserSession
from aunic.browser.session_registry import BrowserSessionRegistry
from aunic.context import ContextEngine, FileManager
from aunic.loop import ToolLoop
from aunic.modes import ChatModeRunner, NoteModeRunner
from aunic.research import FetchService, SearchService


def build_browser_session(
    *,
    workspace_root: Path,
    cwd: Path | None = None,
    instance_id: str = "browser",
    acquire_run_file=None,
    release_run_file=None,
) -> BrowserSession:
    file_manager = FileManager()
    search_service = SearchService()
    fetch_service = FetchService()
    return BrowserSession(
        instance_id=instance_id,
        workspace_root=workspace_root,
        cwd=cwd or workspace_root,
        file_manager=file_manager,
        note_runner=NoteModeRunner(
            context_engine=ContextEngine(file_manager),
            tool_loop=ToolLoop(
                file_manager=file_manager,
                search_service=search_service,
                fetch_service=fetch_service,
            ),
            file_manager=file_manager,
        ),
        chat_runner=ChatModeRunner(
            file_manager=file_manager,
            search_service=search_service,
            fetch_service=fetch_service,
        ),
        search_service=search_service,
        fetch_service=fetch_service,
        acquire_run_file=acquire_run_file,
        release_run_file=release_run_file,
    )


def build_browser_session_registry(
    *,
    workspace_root: Path,
    cwd: Path | None = None,
) -> BrowserSessionRegistry:
    holder: dict[str, BrowserSessionRegistry] = {}

    def session_factory(instance_id: str) -> BrowserSession:
        registry = holder["registry"]
        return build_browser_session(
            workspace_root=workspace_root,
            cwd=cwd,
            instance_id=instance_id,
            acquire_run_file=lambda path: registry.acquire_run_file(instance_id=instance_id, path=path),
            release_run_file=lambda path: registry.release_run_file(instance_id=instance_id, path=path),
        )

    registry = BrowserSessionRegistry(session_factory=session_factory)
    holder["registry"] = registry
    return registry


def create_browser_app(registry: BrowserSessionRegistry) -> Starlette:
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await ConnectionHandler(websocket=websocket, session_registry=registry).run()

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await registry.shutdown()

    return Starlette(
        routes=[WebSocketRoute("/ws", websocket_endpoint)],
        lifespan=lifespan,
    )


async def run_browser_server(
    *,
    host: str,
    port: int,
    workspace_root: Path,
    ssl_certfile: Path | None = None,
    ssl_keyfile: Path | None = None,
) -> int:
    registry = build_browser_session_registry(workspace_root=workspace_root)
    app = create_browser_app(registry)
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        ssl_certfile=str(ssl_certfile) if ssl_certfile else None,
        ssl_keyfile=str(ssl_keyfile) if ssl_keyfile else None,
    )
    server = uvicorn.Server(config)
    await server.serve()
    return 0
