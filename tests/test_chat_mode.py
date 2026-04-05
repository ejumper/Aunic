from __future__ import annotations

from pathlib import Path

import pytest

from aunic.context import FileManager
from aunic.domain import HealthCheck, ProviderResponse, ToolCall, Usage
from aunic.errors import ChatModeError, ServiceUnavailableError
from aunic.modes import ChatModeRunRequest, ChatModeRunner
from aunic.providers.base import LLMProvider
from aunic.research.types import PageFetchResult, SearchBatch, SearchResult
from aunic.transcript.parser import parse_transcript_rows


class ScriptedProvider(LLMProvider):
    name = "scripted"

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    async def healthcheck(self) -> HealthCheck:
        return HealthCheck(provider=self.name, ok=True, message="ok")

    async def generate(self, request):
        self.requests.append(request)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeSearchService:
    def __init__(self, batch: SearchBatch) -> None:
        self.batch = batch
        self.calls = []

    async def search(self, *, queries, depth, freshness, purpose, state):
        self.calls.append((queries, depth, freshness, purpose))
        for result in self.batch.results:
            state.ensure_source(
                title=result.title,
                url=result.url,
                canonical_url=result.canonical_url,
            )
        state.record_search_batch(self.batch)
        return self.batch


class FakeFetchService:
    def __init__(self, result: PageFetchResult) -> None:
        self.result = result
        self.calls = []

    async def fetch_page(self, request, *, state, active_file=None):
        self.calls.append({"request": request, "active_file": active_file})
        state.ensure_source(
            title=self.result.title,
            url=self.result.url,
            canonical_url=self.result.canonical_url,
        )
        state.record_fetched_page(self.result)
        return self.result


def _search_batch() -> SearchBatch:
    return SearchBatch(
        queries=("python homepage",),
        depth="quick",
        freshness="recent",
        purpose="Find the official homepage.",
        results=(
            SearchResult(
                source_id="s1",
                title="Python",
                url="https://www.python.org/",
                canonical_url="https://www.python.org/",
                snippet="The official home of the Python Programming Language.",
                rank=1,
                query_labels=("python homepage",),
            ),
        ),
    )


def _fetch_result() -> PageFetchResult:
    return PageFetchResult(
        url="https://www.python.org/",
        canonical_url="https://www.python.org/",
        title="Python",
        snippet="The official home of the Python Programming Language.",
        markdown="# Python\n\nThe official home of the Python Programming Language.\n",
    )


@pytest.mark.asyncio
async def test_chat_mode_runner_allows_immediate_plain_response_without_research(
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("Existing context.\n", encoding="utf-8")
    provider = ScriptedProvider(
        [ProviderResponse(text="Assistant reply.", usage=Usage(total_tokens=17, input_tokens=10, output_tokens=7))]
    )

    result = await ChatModeRunner().run(
        ChatModeRunRequest(
            active_file=note,
            provider=provider,
            user_prompt="How is the note shaped?",
            model="gpt-5.4",
            reasoning_effort="medium",
            display_root=tmp_path,
            metadata={"cwd": str(tmp_path)},
        )
    )

    assert result.stop_reason == "finished"
    assert result.assistant_response_appended is True
    request = provider.requests[0]
    assert request.model == "gpt-5.4"
    assert request.reasoning_effort == "medium"
    assert request.metadata["cwd"] == str(tmp_path)
    assert request.transcript_messages is not None
    assert [row.content for row in request.transcript_messages] == ["How is the note shaped?"]
    assert request.note_snapshot is not None
    assert "FILE: note.md" in request.note_snapshot
    assert request.user_prompt == "How is the note shaped?"
    assert request.messages == []
    assert result.research_summary.search_batches == ()
    assert result.usage_log.total is not None
    assert result.usage_log.total.total_tokens == 17
    assert result.usage_log_path is not None
    assert Path(result.usage_log_path).exists()
    rows = parse_transcript_rows(note.read_text(encoding="utf-8"))
    assert [(row.role, row.type, row.content) for row in rows] == [
        ("user", "message", "How is the note shaped?"),
        ("assistant", "message", "Assistant reply."),
    ]


@pytest.mark.asyncio
async def test_chat_mode_runner_searches_then_appends_cited_response(
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("Existing.\n", encoding="utf-8")
    provider = ScriptedProvider(
        [
            ProviderResponse(
                text="",
                tool_calls=[
                        ToolCall(
                            name="web_search",
                            arguments={
                                "queries": ["python homepage"],
                            },
                        )
                    ],
            ),
            ProviderResponse(
                text=(
                    "Python's official website is python.org "
                    "([Python](https://www.python.org/))."
                )
            ),
        ]
    )
    runner = ChatModeRunner(
        search_service=FakeSearchService(_search_batch()),
        fetch_service=FakeFetchService(_fetch_result()),
    )

    result = await runner.run(
        ChatModeRunRequest(
            active_file=note,
            provider=provider,
            user_prompt="Find the official Python website.",
            total_turn_budget=2,
        )
    )

    assert result.stop_reason == "finished"
    assert result.assistant_response_appended is True
    assert len(result.research_summary.search_batches) == 1
    rows = parse_transcript_rows(note.read_text(encoding="utf-8"))
    assert [row.type for row in rows] == ["message", "tool_call", "tool_result", "message"]
    assert rows[1].tool_name == "web_search"
    assert "python.org" in str(rows[2].content)


@pytest.mark.asyncio
async def test_chat_mode_runner_fetches_then_appends_cited_response(
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("Existing.\n", encoding="utf-8")
    provider = ScriptedProvider(
        [
            ProviderResponse(
                text="",
                tool_calls=[
                        ToolCall(
                            name="web_search",
                            arguments={
                                "queries": ["python homepage"],
                            },
                        )
                    ],
            ),
            ProviderResponse(
                text="",
                tool_calls=[
                        ToolCall(
                            name="web_fetch",
                            arguments={
                                "url": "https://www.python.org/",
                            },
                        )
                    ],
            ),
            ProviderResponse(
                text=(
                    "Python describes itself as the official home of the Python Programming Language "
                    "([Python](https://www.python.org/))."
                )
            ),
        ]
    )
    runner = ChatModeRunner(
        search_service=FakeSearchService(_search_batch()),
        fetch_service=FakeFetchService(_fetch_result()),
    )

    result = await runner.run(
        ChatModeRunRequest(
            active_file=note,
            provider=provider,
            user_prompt="Summarize the official site description.",
            total_turn_budget=3,
        )
    )

    assert result.stop_reason == "finished"
    assert len(result.research_summary.fetched_pages) == 1
    rows = parse_transcript_rows(note.read_text(encoding="utf-8"))
    assert [(row.type, row.tool_name) for row in rows if row.tool_name] == [
        ("tool_call", "web_search"),
        ("tool_result", "web_search"),
        ("tool_call", "web_fetch"),
        ("tool_result", "web_fetch"),
    ]
    assert "official home of the Python Programming Language" in rows[-1].content


@pytest.mark.asyncio
async def test_chat_mode_runner_repairs_invalid_citation_and_then_finishes(
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("Existing.\n", encoding="utf-8")
    provider = ScriptedProvider(
        [
            ProviderResponse(
                text="",
                tool_calls=[
                        ToolCall(
                            name="web_search",
                            arguments={
                                "queries": ["python homepage"],
                            },
                        )
                    ],
            ),
            ProviderResponse(
                text="Python is official ([Bad](https://elsewhere.example/))."
            ),
            ProviderResponse(
                text="Python is official ([Python](https://www.python.org/))."
            ),
        ]
    )
    runner = ChatModeRunner(search_service=FakeSearchService(_search_batch()))

    result = await runner.run(
        ChatModeRunRequest(
            active_file=note,
            provider=provider,
            user_prompt="Find the official site.",
            total_turn_budget=2,
        )
    )

    assert result.stop_reason == "finished"
    assert result.metrics.citation_repair_count == 1
    assert result.metrics.malformed_repair_count == 1
    assert "elsewhere.example" not in note.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_chat_mode_runner_repairs_malformed_tool_turn_then_finishes(
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("Existing.\n", encoding="utf-8")
    provider = ScriptedProvider(
        [
            ProviderResponse(
                text="bad",
                tool_calls=[
                    ToolCall(name="web_search", arguments={}),
                    ToolCall(name="web_fetch", arguments={}),
                ],
            ),
            ProviderResponse(text="Assistant reply."),
        ]
    )

    result = await ChatModeRunner().run(
        ChatModeRunRequest(
            active_file=note,
            provider=provider,
            user_prompt="Hello?",
        )
    )

    assert result.stop_reason == "finished"
    assert result.metrics.malformed_repair_count == 1


@pytest.mark.asyncio
async def test_chat_mode_runner_leaves_only_prompt_when_provider_fails(
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("Existing.\n", encoding="utf-8")
    provider = ScriptedProvider([ServiceUnavailableError("offline")])

    result = await ChatModeRunner().run(
        ChatModeRunRequest(
            active_file=note,
            provider=provider,
            user_prompt="Hello?",
        )
    )

    assert result.stop_reason == "provider_error"
    assert result.assistant_response_appended is False
    assert result.error_message == "offline"
    final_text = note.read_text(encoding="utf-8")
    rows = parse_transcript_rows(final_text)
    assert "offline" not in final_text
    assert len(rows) == 1
    assert rows[0].role == "user"
    assert rows[0].content == "Hello?"


@pytest.mark.asyncio
async def test_chat_mode_runner_rejects_empty_prompt_before_mutation(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("Existing.\n", encoding="utf-8")

    with pytest.raises(ChatModeError, match="non-empty prompt"):
        await ChatModeRunner().run(
            ChatModeRunRequest(
                active_file=note,
                provider=ScriptedProvider([]),
                user_prompt="",
            )
        )

    assert note.read_text(encoding="utf-8") == "Existing.\n"


@pytest.mark.asyncio
async def test_chat_mode_runner_treats_prompt_from_note_literal_as_plain_text(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("Existing.\n", encoding="utf-8")
    provider = ScriptedProvider([ProviderResponse(text="Assistant reply.")])

    result = await ChatModeRunner().run(
        ChatModeRunRequest(
            active_file=note,
            provider=provider,
            user_prompt="/prompt-from-note",
        )
    )

    assert result.stop_reason == "finished"
    rows = parse_transcript_rows(note.read_text(encoding="utf-8"))
    assert [row.content for row in rows] == ["/prompt-from-note", "Assistant reply."]


@pytest.mark.asyncio
async def test_chat_mode_runner_refreshes_final_snapshots(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("Existing.\n", encoding="utf-8")
    provider = ScriptedProvider([ProviderResponse(text="Assistant reply.")])
    runner = ChatModeRunner(file_manager=FileManager())

    result = await runner.run(
        ChatModeRunRequest(
            active_file=note,
            provider=provider,
            user_prompt="Hello?",
        )
    )

    assert result.final_file_snapshots[0].raw_text == note.read_text(encoding="utf-8")
