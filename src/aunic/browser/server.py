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
from aunic.context import ContextEngine, FileManager
from aunic.loop import ToolLoop
from aunic.modes import ChatModeRunner, NoteModeRunner
from aunic.research import FetchService, SearchService


def build_browser_session(*, workspace_root: Path, cwd: Path | None = None) -> BrowserSession:
    file_manager = FileManager()
    search_service = SearchService()
    fetch_service = FetchService()
    return BrowserSession(
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
    )


def create_browser_app(session: BrowserSession) -> Starlette:
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await ConnectionHandler(websocket=websocket, session=session).run()

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await session.shutdown()

    return Starlette(
        routes=[WebSocketRoute("/ws", websocket_endpoint)],
        lifespan=lifespan,
    )


async def run_browser_server(
    *,
    host: str,
    port: int,
    workspace_root: Path,
) -> int:
    session = build_browser_session(workspace_root=workspace_root)
    app = create_browser_app(session)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()
    return 0
