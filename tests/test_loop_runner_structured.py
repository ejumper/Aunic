from __future__ import annotations

from pathlib import Path

import pytest

from aunic.context import ContextBuildRequest, ContextEngine
from aunic.domain import HealthCheck, ProviderResponse, ToolCall
from aunic.loop import LoopRunRequest, ToolLoop
from aunic.providers.base import LLMProvider
from aunic.research.types import ResearchState, SearchBatch, SearchResult
from aunic.transcript.parser import parse_transcript_rows


class _SequenceProvider(LLMProvider):
    name = "sequence"

    def __init__(self, responses: list[ProviderResponse]) -> None:
        self._responses = list(responses)
        self.requests = []

    async def healthcheck(self) -> HealthCheck:
        return HealthCheck(provider=self.name, ok=True, message="ok")

    async def generate(self, request):
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("Provider received more turns than expected.")
        return self._responses.pop(0)


class _FakeSearchService:
    async def search(
        self,
        *,
        queries: tuple[str, ...],
        depth,
        freshness,
        purpose: str,
        state: ResearchState,
        query_categories=None,
        max_results_per_query=None,
    ) -> SearchBatch:
        return SearchBatch(
            queries=queries,
            depth=depth,
            freshness=freshness,
            purpose=purpose,
            results=(
                SearchResult(
                    source_id="s1",
                    title="Python",
                    url="https://www.python.org/",
                    canonical_url="https://www.python.org/",
                    snippet="Official website",
                    rank=1,
                ),
            ),
            failures=(),
        )


async def _build_context(note: Path, user_prompt: str):
    return await ContextEngine().build_context(
        ContextBuildRequest(
            active_file=note,
            user_prompt=user_prompt,
        )
    )


@pytest.mark.asyncio
async def test_tool_loop_populates_structured_provider_request_and_finishes_on_note_write(
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        "# Note\n\nEditable text.\n\n"
        "---\n"
        "# Transcript\n"
        "| # | role      | type        | tool_name  | tool_id  | content\n"
        "|---|-----------|-------------|------------|----------|-------------------------------\n"
        '| 1 | user      | message     |            |          | "Earlier message"\n',
        encoding="utf-8",
    )

    context_result = await _build_context(note, "Finish the task.")
    provider = _SequenceProvider(
        [
            ProviderResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        name="note_write",
                        arguments={"content": "Updated note content.\n"},
                        id="call_1",
                    )
                ],
            ),
        ]
    )

    result = await ToolLoop().run(
        LoopRunRequest(
            provider=provider,
            prompt_run=context_result.prompt_runs[0],
            context_result=context_result,
            active_file=note,
            persist_message_rows=False,
        )
    )

    assert result.stop_reason == "finished"
    request = provider.requests[0]
    assert request.transcript_messages is not None
    assert [row.content for row in request.transcript_messages] == [
        "Earlier message",
        "Finish the task.",
    ]
    assert request.note_snapshot is not None
    assert "NOTE SNAPSHOT" in request.note_snapshot
    assert f"ACTIVE MARKDOWN NOTE: {note.resolve()}" in request.note_snapshot
    assert request.user_prompt == "Finish the task."
    assert result.run_log_new_start == 1
    assert [row.type for row in result.run_log[result.run_log_new_start :]] == [
        "message",
        "tool_call",
        "tool_result",
    ]
    assert [row.content for row in result.run_log[result.run_log_new_start :]][0] == "Finish the task."
    rows = parse_transcript_rows(note.read_text(encoding="utf-8"))
    assert [row.content for row in rows] == ["Earlier message"]


@pytest.mark.asyncio
async def test_tool_loop_redirects_plain_text_output_into_note_tool_call(
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("# Note\n\nOriginal note.\n", encoding="utf-8")
    context_result = await _build_context(note, "What are the rules of baseball?")
    draft = "Baseball is played over nine innings with three outs per side."
    provider = _SequenceProvider(
        [
            ProviderResponse(text=draft, tool_calls=[]),
            ProviderResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        name="note_write",
                        arguments={"content": draft},
                        id="call_1",
                    )
                ],
            ),
        ]
    )

    result = await ToolLoop().run(
        LoopRunRequest(
            provider=provider,
            prompt_run=context_result.prompt_runs[0],
            context_result=context_result,
            active_file=note,
            persist_message_rows=False,
        )
    )

    assert result.stop_reason == "finished"
    assert result.metrics.malformed_repair_count == 1
    assert result.tool_failures[0].reason == "note_mode_plain_text_requires_note_tool"
    assert provider.requests[1].user_prompt == (
        "Your response must be written into the active markdown note using note_edit or note_write.\n"
        f"Target note: {note.resolve()}\n"
        "Only modify note-content. Do not edit transcript rows, search results, read output, or tool outputs.\n"
        "Use note_write or note_edit to integrate this content into note-content where it fits.\n\n"
        f"Draft answer:\n{draft}"
    )
    assert [row.content for row in result.run_log[result.run_log_new_start :]][0:3] == [
        "What are the rules of baseball?",
        draft,
        provider.requests[1].user_prompt,
    ]
    rows = parse_transcript_rows(note.read_text(encoding="utf-8"))
    assert rows == []


@pytest.mark.asyncio
async def test_tool_loop_persists_web_search_rows_but_not_note_mode_messages_when_message_rows_disabled(
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("# Note\n\nOriginal note.\n", encoding="utf-8")
    context_result = await _build_context(note, "Find the official Python homepage and write it down.")
    provider = _SequenceProvider(
        [
            ProviderResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        name="web_search",
                        arguments={"queries": ["python homepage"]},
                        id="call_1",
                    )
                ],
            ),
            ProviderResponse(text="Added the result.", tool_calls=[]),
            ProviderResponse(text="Added the result.", tool_calls=[]),
            ProviderResponse(text="Added the result.", tool_calls=[]),
            ProviderResponse(text="Added the result.", tool_calls=[]),
        ]
    )

    result = await ToolLoop(search_service=_FakeSearchService()).run(
        LoopRunRequest(
            provider=provider,
            prompt_run=context_result.prompt_runs[0],
            context_result=context_result,
            active_file=note,
            persist_message_rows=False,
        )
    )

    assert result.stop_reason == "malformed_turn_limit"
    rows = parse_transcript_rows(note.read_text(encoding="utf-8"))
    assert [(row.type, row.tool_name) for row in rows] == [
        ("tool_call", "web_search"),
        ("tool_result", "web_search"),
    ]
    assert rows[0].content == {"queries": ["python homepage"]}
    assert rows[1].content == [
        {
            "url": "https://www.python.org/",
            "title": "Python",
            "snippet": "Official website",
        }
    ]
    assert all(row.content != "Added the result." for row in rows)
    assert result.tool_failures[-1].reason == "note_mode_plain_text_requires_note_tool"


@pytest.mark.asyncio
async def test_tool_loop_persists_read_rows_but_not_note_mode_confirmation_when_read_mode_enabled(
    tmp_path: Path,
) -> None:
    async def _allow_permission(_request) -> str:
        return "once"

    note = tmp_path / "note.md"
    reference = tmp_path / "reference.txt"
    note.write_text("# Note\n\nOriginal note.\n", encoding="utf-8")
    reference.write_text("Reference details.\n", encoding="utf-8")
    context_result = await _build_context(note, "Read the reference and update the note.")
    provider = _SequenceProvider(
        [
            ProviderResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        name="read",
                        arguments={"file_path": str(reference)},
                        id="call_1",
                    )
                ],
            ),
            ProviderResponse(text="Updated the note.", tool_calls=[]),
            ProviderResponse(text="Updated the note.", tool_calls=[]),
            ProviderResponse(text="Updated the note.", tool_calls=[]),
            ProviderResponse(text="Updated the note.", tool_calls=[]),
        ]
    )

    result = await ToolLoop().run(
        LoopRunRequest(
            provider=provider,
            prompt_run=context_result.prompt_runs[0],
            context_result=context_result,
            active_file=note,
            persist_message_rows=False,
            work_mode="read",
            metadata={"cwd": str(tmp_path)},
            permission_handler=_allow_permission,
        )
    )

    assert result.stop_reason == "malformed_turn_limit"
    rows = parse_transcript_rows(note.read_text(encoding="utf-8"))
    assert [(row.type, row.tool_name) for row in rows] == [
        ("tool_call", "read"),
        ("tool_result", "read"),
    ]
    assert all(row.content != "Updated the note." for row in rows)
