from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from aunic.browser.errors import BrowserError, RevisionConflict
from aunic.browser.session import BrowserSession
from aunic.context import FileManager
from aunic.model_options import ModelOption
from aunic.progress import ProgressEvent
from aunic.research.types import (
    FetchPacket,
    FetchedChunk,
    PageFetchResult,
    ResearchState,
    SearchBatch,
    SearchResult,
)
from aunic.rag.types import RagFetchResult, RagFetchSection, RagSearchResult
from aunic.tools.runtime import PermissionRequest, join_note_and_transcript
from aunic.transcript.writer import append_transcript_row, append_transcript_rows


class FakeConnection:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def send_event(
        self,
        message_type: str,
        payload: dict[str, Any],
        *,
        message_id: str | None = None,
    ) -> None:
        self.events.append({"id": message_id, "type": message_type, "payload": payload})


class FakeProvider:
    name = "fake"


class ScriptedNoteRunner:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def run(self, request: Any) -> object:
        self.requests.append(request)
        await request.progress_sink(
            ProgressEvent(kind="status", message="runner status", path=request.active_file)
        )
        return object()


class BlockingNoteRunner:
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


class PermissionNoteRunner:
    def __init__(self) -> None:
        self.resolution: str | None = None

    async def run(self, request: Any) -> object:
        self.resolution = await request.permission_handler(
            PermissionRequest(
                tool_name="bash",
                action="run",
                target="pwd",
                message="Run command?",
                policy="ask",
            )
        )
        return object()


class FakeSearchService:
    async def search(
        self,
        *,
        queries: tuple[str, ...],
        depth: str,
        freshness: str,
        purpose: str,
        state: ResearchState,
    ) -> SearchBatch:
        return SearchBatch(
            queries=queries,
            depth="quick",
            freshness="none",
            purpose=purpose,
            results=(
                SearchResult(
                    source_id="s1",
                    title="Python",
                    url="https://www.python.org/",
                    canonical_url="https://www.python.org/",
                    snippet="Official Python site",
                    rank=1,
                ),
            ),
        )


class FakeFetchService:
    def __init__(self) -> None:
        self.fetches: list[dict[str, Any]] = []

    async def fetch_for_user_selection(
        self,
        *,
        query: str,
        url: str,
        state: ResearchState,
        active_file: Path | str | None = None,
    ) -> FetchPacket:
        self.fetches.append({"query": query, "url": url, "active_file": active_file})
        state.record_fetched_page(
            PageFetchResult(
                url=url,
                canonical_url=url,
                title="Python",
                snippet="Fetched Python page",
                markdown="Full Python page",
            )
        )
        return FetchPacket(
            source_id="s1",
            title="Python",
            url=url,
            canonical_url=url,
            desired_info=query,
            chunks=(
                FetchedChunk(
                    source_id="s1",
                    title="Overview",
                    url=url,
                    canonical_url=url,
                    text="Python overview chunk",
                    score=1.0,
                    heading_path=("Overview",),
                ),
                FetchedChunk(
                    source_id="s1",
                    title="Install",
                    url=url,
                    canonical_url=url,
                    text="Python install chunk",
                    score=0.8,
                    heading_path=("Install",),
                ),
            ),
            full_markdown="Full Python page",
        )


def _session(
    tmp_path: Path,
    *,
    note_runner: Any | None = None,
    model_options: tuple[ModelOption, ...] | None = None,
) -> BrowserSession:
    return BrowserSession(
        workspace_root=tmp_path,
        file_manager=FileManager(),
        note_runner=note_runner or ScriptedNoteRunner(),
        chat_runner=None,
        provider_factory=lambda _option, _cwd: FakeProvider(),
        model_options=model_options
        or (
            ModelOption(label="Fake One", provider_name="codex", model="fake-one"),
            ModelOption(label="Fake Two", provider_name="codex", model="fake-two"),
        ),
    )


@pytest.mark.asyncio
async def test_session_reads_and_writes_note_content_preserving_transcript(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    text, _row_number = append_transcript_row("# Note\n", "user", "message", None, None, "hi")
    note.write_text(text, encoding="utf-8")
    session = _session(tmp_path)

    snapshot = await session.read_file("note.md")
    written = await session.write_file(
        "note.md",
        text="# Changed\n",
        expected_revision=snapshot["revision_id"],
    )

    assert snapshot["note_content"] == "# Note"
    assert written["note_content"] == "# Changed"
    assert written["transcript_rows"][0]["content"] == "hi"
    assert join_note_and_transcript("# Changed\n", None).strip() in note.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_session_write_rejects_revision_conflict(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("one", encoding="utf-8")
    session = _session(tmp_path)
    snapshot = await session.read_file("note.md")
    note.write_text("external", encoding="utf-8")

    with pytest.raises(RevisionConflict):
        await session.write_file(
            "note.md",
            text="browser",
            expected_revision=snapshot["revision_id"],
        )


@pytest.mark.asyncio
async def test_delete_transcript_row_removes_row_and_bumps_revision(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    text, _row_numbers = append_transcript_rows(
        "# Note\n",
        [
            ("user", "message", None, None, "first"),
            ("assistant", "message", None, None, "second"),
        ],
    )
    note.write_text(text, encoding="utf-8")
    session = _session(tmp_path)
    snapshot = await session.read_file("note.md")

    updated = await session.delete_transcript_row(
        "note.md",
        row_number=1,
        expected_revision=snapshot["revision_id"],
    )

    assert updated["revision_id"] != snapshot["revision_id"]
    assert [row["content"] for row in updated["transcript_rows"]] == ["second"]
    assert updated["transcript_rows"][0]["row_number"] == 1


@pytest.mark.asyncio
async def test_delete_transcript_row_rejects_revision_conflict(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    text, _row_number = append_transcript_row("# Note\n", "user", "message", None, None, "first")
    note.write_text(text, encoding="utf-8")
    session = _session(tmp_path)
    snapshot = await session.read_file("note.md")
    note.write_text(note.read_text(encoding="utf-8") + "\nexternal", encoding="utf-8")

    with pytest.raises(RevisionConflict):
        await session.delete_transcript_row(
            "note.md",
            row_number=1,
            expected_revision=snapshot["revision_id"],
        )


@pytest.mark.asyncio
async def test_delete_transcript_row_unknown_number_returns_current_snapshot(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    text, _row_number = append_transcript_row("# Note\n", "user", "message", None, None, "first")
    note.write_text(text, encoding="utf-8")
    session = _session(tmp_path)
    snapshot = await session.read_file("note.md")

    updated = await session.delete_transcript_row(
        "note.md",
        row_number=999,
        expected_revision=snapshot["revision_id"],
    )

    assert updated["revision_id"] == snapshot["revision_id"]
    assert updated["transcript_rows"] == snapshot["transcript_rows"]


@pytest.mark.asyncio
async def test_delete_search_result_removes_indexed_result(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    text, _row_numbers = append_transcript_rows(
        "# Note\n",
        [
            ("assistant", "tool_call", "web_search", "call_1", {"queries": ["python"]}),
            (
                "tool",
                "tool_result",
                "web_search",
                "call_1",
                [
                    {"title": "Python", "url": "https://www.python.org/"},
                    {"title": "Docs", "url": "https://docs.python.org/"},
                ],
            ),
        ],
    )
    note.write_text(text, encoding="utf-8")
    session = _session(tmp_path)
    snapshot = await session.read_file("note.md")

    updated = await session.delete_search_result(
        "note.md",
        row_number=2,
        result_index=0,
        expected_revision=snapshot["revision_id"],
    )

    search_row = updated["transcript_rows"][1]
    assert search_row["content"] == [{"title": "Docs", "url": "https://docs.python.org/"}]
    assert updated["revision_id"] != snapshot["revision_id"]


@pytest.mark.asyncio
async def test_delete_search_result_rejects_revision_conflict(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    text, _row_number = append_transcript_row(
        "# Note\n",
        "tool",
        "tool_result",
        "web_search",
        "call_1",
        [{"title": "Python"}],
    )
    note.write_text(text, encoding="utf-8")
    session = _session(tmp_path)
    snapshot = await session.read_file("note.md")
    note.write_text(note.read_text(encoding="utf-8") + "\nexternal", encoding="utf-8")

    with pytest.raises(RevisionConflict):
        await session.delete_search_result(
            "note.md",
            row_number=1,
            result_index=0,
            expected_revision=snapshot["revision_id"],
        )


@pytest.mark.asyncio
async def test_create_file_writes_empty_markdown(tmp_path: Path) -> None:
    session = _session(tmp_path)

    created = await session.create_file("scratch.md")

    assert (tmp_path / "scratch.md").read_text(encoding="utf-8") == ""
    assert created["path"] == "scratch.md"
    assert created["note_content"] == ""
    assert created["has_transcript"] is False


@pytest.mark.asyncio
async def test_create_file_rejects_non_md_suffix(tmp_path: Path) -> None:
    session = _session(tmp_path)

    with pytest.raises(BrowserError) as exc_info:
        await session.create_file("scratch.txt")

    assert exc_info.value.reason == "invalid_extension"


@pytest.mark.asyncio
async def test_create_file_rejects_existing_path(tmp_path: Path) -> None:
    (tmp_path / "scratch.md").write_text("existing", encoding="utf-8")
    session = _session(tmp_path)

    with pytest.raises(BrowserError) as exc_info:
        await session.create_file("scratch.md")

    assert exc_info.value.reason == "already_exists"


@pytest.mark.asyncio
async def test_create_file_rejects_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    session = _session(root)

    with pytest.raises(BrowserError) as exc_info:
        await session.create_file("escape/scratch.md")

    assert exc_info.value.reason == "path_escape"


@pytest.mark.asyncio
async def test_create_directory_creates_dir_and_rejects_existing(tmp_path: Path) -> None:
    session = _session(tmp_path)

    created = await session.create_directory("scratch-dir")

    assert created == {"path": "scratch-dir", "kind": "dir"}
    assert (tmp_path / "scratch-dir").is_dir()
    with pytest.raises(BrowserError) as exc_info:
        await session.create_directory("scratch-dir")
    assert exc_info.value.reason == "already_exists"


@pytest.mark.asyncio
async def test_create_entry_rejects_long_name(tmp_path: Path) -> None:
    session = _session(tmp_path)

    with pytest.raises(BrowserError) as exc_info:
        await session.create_file(f"{'a' * 253}.md")

    assert exc_info.value.reason == "name_too_long"


@pytest.mark.asyncio
async def test_delete_entry_removes_file(tmp_path: Path) -> None:
    (tmp_path / "scratch.md").write_text("remove me", encoding="utf-8")
    session = _session(tmp_path)

    deleted = await session.delete_entry("scratch.md")

    assert deleted == {"path": "scratch.md", "kind": "file"}
    assert not (tmp_path / "scratch.md").exists()


@pytest.mark.asyncio
async def test_delete_entry_removes_directory_recursively(tmp_path: Path) -> None:
    nested = tmp_path / "scratch-dir" / "nested.md"
    nested.parent.mkdir()
    nested.write_text("remove me", encoding="utf-8")
    session = _session(tmp_path)

    deleted = await session.delete_entry("scratch-dir")

    assert deleted == {"path": "scratch-dir", "kind": "dir"}
    assert not (tmp_path / "scratch-dir").exists()


@pytest.mark.asyncio
async def test_delete_entry_refuses_workspace_root(tmp_path: Path) -> None:
    session = _session(tmp_path)

    with pytest.raises(BrowserError) as exc_info:
        await session.delete_entry(".")

    assert exc_info.value.reason == "refused"


@pytest.mark.asyncio
async def test_set_mode_updates_state_and_broadcasts(tmp_path: Path) -> None:
    session = _session(tmp_path)
    conn = FakeConnection()
    await session.attach(conn)  # type: ignore[arg-type]

    await session.set_mode("chat")
    await session.shutdown()

    assert session.mode == "chat"
    assert conn.events[-1]["type"] == "session_state"
    assert conn.events[-1]["payload"]["mode"] == "chat"


@pytest.mark.asyncio
async def test_set_mode_rejects_invalid_input(tmp_path: Path) -> None:
    session = _session(tmp_path)

    with pytest.raises(BrowserError) as exc_info:
        await session.set_mode("plan")

    assert exc_info.value.reason == "invalid_mode"


@pytest.mark.asyncio
async def test_set_work_mode_updates_state_and_broadcasts(tmp_path: Path) -> None:
    session = _session(tmp_path)
    conn = FakeConnection()
    await session.attach(conn)  # type: ignore[arg-type]

    await session.set_work_mode("work")
    await session.shutdown()

    assert session.work_mode == "work"
    assert conn.events[-1]["type"] == "session_state"
    assert conn.events[-1]["payload"]["work_mode"] == "work"


@pytest.mark.asyncio
async def test_set_work_mode_rejects_invalid_input(tmp_path: Path) -> None:
    session = _session(tmp_path)

    with pytest.raises(BrowserError) as exc_info:
        await session.set_work_mode("write")

    assert exc_info.value.reason == "invalid_work_mode"


@pytest.mark.asyncio
async def test_select_model_updates_state_and_broadcasts(tmp_path: Path) -> None:
    session = _session(tmp_path)
    conn = FakeConnection()
    await session.attach(conn)  # type: ignore[arg-type]

    await session.select_model(1)
    await session.shutdown()

    assert session.selected_model_index == 1
    assert session.selected_model.model == "fake-two"
    assert conn.events[-1]["type"] == "session_state"
    assert conn.events[-1]["payload"]["selected_model_index"] == 1


@pytest.mark.asyncio
async def test_select_model_rejects_invalid_input(tmp_path: Path) -> None:
    session = _session(tmp_path)

    with pytest.raises(BrowserError) as exc_info:
        await session.select_model(99)
    assert exc_info.value.reason == "invalid_model_index"

    with pytest.raises(BrowserError) as bool_exc_info:
        await session.select_model(True)  # type: ignore[arg-type]
    assert bool_exc_info.value.reason == "invalid_model_index"


@pytest.mark.asyncio
async def test_session_controls_reject_while_run_active(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body", encoding="utf-8")
    runner = BlockingNoteRunner()
    session = _session(tmp_path, note_runner=runner)

    run_id = await session.submit_prompt(active_file="note.md", included_files=[], text="Do it")
    await asyncio.wait_for(runner.started.wait(), timeout=1)

    for action in (
        lambda: session.set_mode("chat"),
        lambda: session.set_work_mode("work"),
        lambda: session.select_model(1),
    ):
        with pytest.raises(BrowserError) as exc_info:
            await action()
        assert exc_info.value.reason == "run_active"

    assert await session.cancel_run(run_id) is True
    await _wait_for(lambda: not session.run_active)
    await session.shutdown()


@pytest.mark.asyncio
async def test_run_prompt_command_switches_mode_without_model_run(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body", encoding="utf-8")
    runner = ScriptedNoteRunner()
    session = _session(tmp_path, note_runner=runner)

    response = await session.run_prompt_command(active_file="note.md", text="/chat continue this")

    assert response["draft"] == "continue this"
    assert response["run_id"] is None
    assert session.mode == "chat"
    assert runner.requests == []


@pytest.mark.asyncio
async def test_include_command_affects_next_browser_run(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    other = tmp_path / "other.md"
    note.write_text("body", encoding="utf-8")
    other.write_text("other", encoding="utf-8")
    runner = ScriptedNoteRunner()
    session = _session(tmp_path, note_runner=runner)

    response = await session.run_prompt_command(active_file="note.md", text="/include ./other.md")
    await session.submit_prompt(active_file="note.md", included_files=[], text="Do it")
    await _wait_for(lambda: not session.run_active)

    assert response["message"] == "Included file: ./other.md"
    assert runner.requests[0].included_files == (other.resolve(),)


@pytest.mark.asyncio
async def test_web_command_persists_search_transcript_pair(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body", encoding="utf-8")
    session = _session(tmp_path)
    session.search_service = FakeSearchService()  # type: ignore[assignment]

    response = await session.run_prompt_command(active_file="note.md", text="@web python")

    rows = response["snapshot"]["transcript_rows"]
    assert response["draft"] == ""
    assert response["message"] == "Found 1 web result."
    assert [(row["type"], row["tool_name"]) for row in rows] == [
        ("tool_call", "web_search"),
        ("tool_result", "web_search"),
    ]
    assert rows[0]["content"] == {"queries": ["python"]}
    assert rows[1]["content"][0]["title"] == "Python"
    state = session.session_state()["research_state"]
    assert state["mode"] == "results"
    assert state["source"] == "web"
    assert state["results"][0]["title"] == "Python"
    assert session.session_state()["capabilities"]["research_flow"] is True


@pytest.mark.asyncio
async def test_web_research_fetch_and_insert_selected_chunks(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body", encoding="utf-8")
    session = _session(tmp_path)
    session.search_service = FakeSearchService()  # type: ignore[assignment]
    session.fetch_service = FakeFetchService()  # type: ignore[assignment]

    await session.run_prompt_command(active_file="note.md", text="@web python")
    fetched = await session.research_fetch_result(active_file="note.md", result_index=0)

    assert [row["tool_name"] for row in fetched["transcript_rows"]] == [
        "web_search",
        "web_search",
        "web_fetch",
        "web_fetch",
    ]
    state = session.session_state()["research_state"]
    assert state["mode"] == "chunks"
    assert state["packet"]["chunks"][0]["text"] == "Python overview chunk"

    inserted = await session.research_insert_chunks(
        active_file="note.md",
        mode="selected_chunks",
        chunk_indices=[1],
    )

    assert inserted["note_content"] == "body\n\n# Python\n\nPython install chunk"
    assert session.session_state()["research_state"]["mode"] == "idle"


@pytest.mark.asyncio
async def test_web_research_insert_full_page(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body", encoding="utf-8")
    session = _session(tmp_path)
    session.search_service = FakeSearchService()  # type: ignore[assignment]
    session.fetch_service = FakeFetchService()  # type: ignore[assignment]

    await session.run_prompt_command(active_file="note.md", text="@web python")
    await session.research_fetch_result(active_file="note.md", result_index=0)
    inserted = await session.research_insert_chunks(
        active_file="note.md",
        mode="full_page",
        chunk_indices=None,
    )

    assert inserted["note_content"] == "body\n\n# Python\n\nFull Python page"


@pytest.mark.asyncio
async def test_research_back_and_cancel_transition_state(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body", encoding="utf-8")
    session = _session(tmp_path)
    session.search_service = FakeSearchService()  # type: ignore[assignment]
    session.fetch_service = FakeFetchService()  # type: ignore[assignment]

    await session.run_prompt_command(active_file="note.md", text="@web python")
    await session.research_fetch_result(active_file="note.md", result_index=0)
    assert await session.research_back() == {"ok": True}
    assert session.session_state()["research_state"]["mode"] == "results"
    assert await session.research_cancel() == {"ok": True}
    assert session.session_state()["research_state"]["mode"] == "idle"


@pytest.mark.asyncio
async def test_rag_research_fetch_enters_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    note = tmp_path / "note.md"
    note.write_text("body", encoding="utf-8")
    session = _session(tmp_path)

    class FakeRagClient:
        def __init__(self, server: str) -> None:
            self.server = server

        async def search(self, query: str, scope: str | None = None, limit: int = 10):
            return (
                RagSearchResult(
                    doc_id="doc-1",
                    chunk_id="chunk-2",
                    title="STP",
                    source="docs",
                    snippet="Spanning tree",
                    score=0.9,
                    result_id="docs:chunk:chunk-2",
                    heading_path=("Networking", "STP"),
                ),
            )

        async def fetch(
            self,
            result_id: str,
            neighbors: int = 1,
            *,
            mode: str = "neighbors",
            max_chunks: int = 20,
        ):
            return RagFetchResult(
                doc_id="doc-1",
                title="STP",
                source="docs",
                url=None,
                local_path="docs/stp.md",
                result_id=result_id,
                sections=(
                    RagFetchSection(
                        heading="Networking > STP",
                        heading_path=("Networking", "STP"),
                        text="Matched STP chunk",
                        chunk_id="chunk-2",
                        is_match=True,
                    ),
                ),
                full_text="Full STP document",
                total_chunks=1,
            )

    monkeypatch.setattr("aunic.rag.config.load_rag_config", lambda _cwd: type("Cfg", (), {"server": "http://rag"})())
    monkeypatch.setattr("aunic.rag.client.RagClient", FakeRagClient)

    response = await session.run_prompt_command(active_file="note.md", text="@rag spanning tree")
    assert response["message"] == "Found 1 RAG result."
    assert session.session_state()["research_state"]["source"] == "rag"

    fetched = await session.research_fetch_result(active_file="note.md", result_index=0)

    assert fetched["transcript_rows"][-1]["tool_name"] == "rag_fetch"
    state = session.session_state()["research_state"]
    assert state["mode"] == "chunks"
    assert state["packet"]["chunks"][0]["is_match"] is True


@pytest.mark.asyncio
async def test_submit_prompt_broadcasts_run_state_and_progress(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body", encoding="utf-8")
    runner = ScriptedNoteRunner()
    session = _session(tmp_path, note_runner=runner)
    conn = FakeConnection()
    await session.attach(conn)  # type: ignore[arg-type]

    run_id = await session.submit_prompt(active_file="note.md", included_files=[], text="Do it")
    await _wait_for(lambda: not session.run_active)
    await session.shutdown()

    assert run_id
    assert runner.requests[0].active_file == note.resolve()
    assert any(event["type"] == "session_state" and event["payload"]["run_active"] for event in conn.events)
    assert any(event["type"] == "progress_event" for event in conn.events)
    assert conn.events[-1]["type"] == "session_state"
    assert conn.events[-1]["payload"]["run_active"] is False


@pytest.mark.asyncio
async def test_provider_response_updates_context_usage(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("# Note\n\nbody", encoding="utf-8")
    session = _session(
        tmp_path,
        model_options=(
            ModelOption(
                label="Fake",
                provider_name="codex",
                model="fake",
                context_window=10_000,
            ),
        ),
    )
    conn = FakeConnection()
    await session.attach(conn)  # type: ignore[arg-type]

    await session.handle_progress_event(
        ProgressEvent(
            kind="loop_event",
            message="Provider response.",
            path=note,
            details={
                "loop_kind": "provider_response",
                "usage": {
                    "input_tokens": 2_500,
                    "model_context_window": 10_000,
                },
            },
        )
    )
    await session.shutdown()

    context_state = session.session_state()["context_usage"]
    assert context_state["tokens_used"] == 2_500
    assert context_state["context_window"] == 10_000
    assert context_state["fraction"] == 0.25
    assert context_state["last_note_chars"] == len("# Note\n\nbody")
    assert any(
        event["type"] == "session_state"
        and event["payload"]["context_usage"]["tokens_used"] == 2_500
        for event in conn.events
    )


@pytest.mark.asyncio
async def test_cancel_run_cancels_active_task(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body", encoding="utf-8")
    runner = BlockingNoteRunner()
    session = _session(tmp_path, note_runner=runner)
    conn = FakeConnection()
    await session.attach(conn)  # type: ignore[arg-type]

    run_id = await session.submit_prompt(active_file="note.md", included_files=[], text="Do it")
    await asyncio.wait_for(runner.started.wait(), timeout=1)
    assert await session.cancel_run(run_id) is True
    await asyncio.wait_for(runner.cancelled.wait(), timeout=1)
    await _wait_for(lambda: not session.run_active)
    await session.shutdown()

    assert any(
        event["type"] == "progress_event" and event["payload"]["details"].get("cancelled")
        for event in conn.events
    )


@pytest.mark.asyncio
async def test_permission_request_round_trips_resolution(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("body", encoding="utf-8")
    runner = PermissionNoteRunner()
    session = _session(tmp_path, note_runner=runner)
    conn = FakeConnection()
    await session.attach(conn)  # type: ignore[arg-type]

    await session.submit_prompt(active_file="note.md", included_files=[], text="Do it")
    await _wait_for(lambda: any(event["type"] == "permission_request" for event in conn.events))
    request_event = next(event for event in conn.events if event["type"] == "permission_request")
    await session.resolve_permission(request_event["payload"]["permission_id"], "once")
    await _wait_for(lambda: not session.run_active)
    await session.shutdown()

    assert runner.resolution == "once"


async def _wait_for(predicate, *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("condition was not met")
        await asyncio.sleep(0.01)
