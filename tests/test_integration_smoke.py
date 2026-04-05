from __future__ import annotations

import os
from pathlib import Path

import pytest

from aunic.context import ContextBuildRequest, ContextEngine
from aunic.domain import Message, ProviderRequest
from aunic.loop import LoopRunRequest, ToolLoop
from aunic.modes import ChatModeRunRequest, ChatModeRunner, NoteModeRunRequest, NoteModeRunner
from aunic.providers import CodexProvider, LlamaCppProvider, OllamaEmbeddingProvider


RUN_INTEGRATION = os.environ.get("RUN_AUNIC_INTEGRATION") == "1"
integration = pytest.mark.integration
skip_integration = pytest.mark.skipif(
    not RUN_INTEGRATION,
    reason="Set RUN_AUNIC_INTEGRATION=1 to run live provider smoke tests.",
)


async def _loop_request(note_path: Path, *, provider) -> LoopRunRequest:
    context_result = await ContextEngine().build_context(
        ContextBuildRequest(
            active_file=note_path,
            user_prompt=(
                "Replace the note with the single word `done` using note_write. "
                "Do not answer in chat style; complete the task by updating note-content."
            ),
        )
    )
    return LoopRunRequest(
        provider=provider,
        prompt_run=context_result.prompt_runs[0],
        context_result=context_result,
        metadata={"cwd": str(note_path.parent)},
        persist_message_rows=False,
    )


def _note_mode_direct_prompt() -> str:
    return (
        "Replace the note with the single word `done` using note_write. "
        "Do not answer in chat style; complete the task by updating note-content."
    )


def _chat_mode_direct_prompt() -> str:
    return "Say hello briefly."


@integration
@skip_integration
@pytest.mark.asyncio
async def test_codex_integration_smoke() -> None:
    provider = CodexProvider()
    response = await provider.generate(
        ProviderRequest(messages=[Message(role="user", content="Say hello briefly.")])
    )
    assert response.text
    assert response.provider_metadata["transport"] == "codex_sdk"


@integration
@skip_integration
@pytest.mark.asyncio
async def test_llama_integration_smoke() -> None:
    provider = LlamaCppProvider()
    response = await provider.generate(
        ProviderRequest(messages=[Message(role="user", content="Say hello briefly.")])
    )
    assert response.text
    assert response.provider_metadata["transport"] == "openai_compatible"


@integration
@skip_integration
@pytest.mark.asyncio
async def test_embedding_integration_smoke() -> None:
    provider = OllamaEmbeddingProvider()
    embeddings = await provider.embed_texts(["hello", "world"])
    assert len(embeddings) == 2


@integration
@skip_integration
@pytest.mark.asyncio
async def test_codex_tool_loop_integration_smoke(tmp_path: Path) -> None:
    note = tmp_path / "codex-loop.md"
    note.write_text("seed\n", encoding="utf-8")

    provider = CodexProvider()
    result = await ToolLoop().run(await _loop_request(note, provider=provider))

    assert result.stop_reason == "finished"
    assert result.metrics.successful_edit_count >= 1
    assert any(
        row.type == "tool_result" and row.tool_name in {"note_edit", "note_write"}
        for row in result.run_log
    )
    assert "done" in note.read_text(encoding="utf-8").lower()


@integration
@skip_integration
@pytest.mark.asyncio
async def test_llama_tool_loop_integration_smoke(tmp_path: Path) -> None:
    note = tmp_path / "llama-loop.md"
    note.write_text("seed\n", encoding="utf-8")

    provider = LlamaCppProvider()
    result = await ToolLoop().run(await _loop_request(note, provider=provider))

    assert result.stop_reason == "finished"
    assert result.metrics.successful_edit_count >= 1
    assert "done" in note.read_text(encoding="utf-8").lower()


@integration
@skip_integration
@pytest.mark.asyncio
async def test_codex_note_mode_direct_integration_smoke(tmp_path: Path) -> None:
    note = tmp_path / "codex-note-mode.md"
    note.write_text("seed\n", encoding="utf-8")

    result = await NoteModeRunner().run(
        NoteModeRunRequest(
            active_file=note,
            provider=CodexProvider(),
            user_prompt=_note_mode_direct_prompt(),
            metadata={"cwd": str(note.parent)},
        )
    )

    assert result.stop_reason == "finished"
    assert result.completed_all_prompts is True
    assert result.prompt_results[0].loop_result.metrics.successful_edit_count > 0
    assert "done" in note.read_text(encoding="utf-8").lower()


@integration
@skip_integration
@pytest.mark.asyncio
async def test_llama_note_mode_direct_integration_smoke(tmp_path: Path) -> None:
    note = tmp_path / "llama-note-mode.md"
    note.write_text("seed\n", encoding="utf-8")

    result = await NoteModeRunner().run(
        NoteModeRunRequest(
            active_file=note,
            provider=LlamaCppProvider(),
            user_prompt=_note_mode_direct_prompt(),
            metadata={"cwd": str(note.parent)},
        )
    )

    assert result.stop_reason == "finished"
    assert result.completed_all_prompts is True
    assert result.prompt_results[0].loop_result.metrics.successful_edit_count > 0
    assert "done" in note.read_text(encoding="utf-8").lower()


@integration
@skip_integration
@pytest.mark.asyncio
async def test_codex_chat_mode_direct_integration_smoke(tmp_path: Path) -> None:
    note = tmp_path / "codex-chat-mode.md"
    note.write_text("Existing chat context.\n", encoding="utf-8")

    result = await ChatModeRunner().run(
        ChatModeRunRequest(
            active_file=note,
            provider=CodexProvider(),
            user_prompt=_chat_mode_direct_prompt(),
            metadata={"cwd": str(note.parent)},
        )
    )

    assert result.stop_reason == "finished"
    assert result.assistant_response_appended is True
    assert result.response_text


@integration
@skip_integration
@pytest.mark.asyncio
async def test_llama_chat_mode_direct_integration_smoke(tmp_path: Path) -> None:
    note = tmp_path / "llama-chat-mode.md"
    note.write_text("Existing chat context.\n", encoding="utf-8")

    result = await ChatModeRunner().run(
        ChatModeRunRequest(
            active_file=note,
            provider=LlamaCppProvider(),
            user_prompt=_chat_mode_direct_prompt(),
            metadata={"cwd": str(note.parent)},
        )
    )

    assert result.stop_reason == "finished"
    assert result.assistant_response_appended is True
    assert result.response_text
