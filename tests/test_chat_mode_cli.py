from __future__ import annotations

import json
from pathlib import Path

from aunic import cli
from aunic.context.types import FileSnapshot
from aunic.domain import Usage, UsageLog, UsageLogEntry
from aunic.errors import ChatModeError
from aunic.transcript.parser import parse_transcript_rows
from aunic.modes import ChatModeRunResult


def _snapshot(path: Path, text: str, revision_id: str) -> FileSnapshot:
    return FileSnapshot(
        path=path.resolve(),
        raw_text=text,
        revision_id=revision_id,
        content_hash=revision_id.split(":")[0],
        mtime_ns=1,
        size_bytes=len(text.encode("utf-8")),
    )


def test_chat_run_cli_outputs_structured_payload(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    note = tmp_path / "chat.md"
    note.write_text("Existing.\n", encoding="utf-8")
    final_snapshot = _snapshot(note, "Existing.\n\n***\n\n> Hi\n\n***\n\nHello!\n", "abc:1:31")
    captured = {}

    class FakeRunner:
        async def run(self, request):
            captured["request"] = request
            return ChatModeRunResult(
                initial_warnings=(),
                response_text="Hello!",
                assistant_response_appended=True,
                final_file_snapshots=(final_snapshot,),
                stop_reason="finished",
                usage_log=UsageLog(
                    entries=(
                        UsageLogEntry(
                            index=1,
                            stage="chat",
                            usage=Usage(total_tokens=21, input_tokens=13, output_tokens=8),
                            provider="codex",
                            model="gpt-5.4",
                            finish_reason="stop",
                        ),
                    ),
                    total=Usage(total_tokens=21, input_tokens=13, output_tokens=8),
                ),
                provider_metadata={"provider": "codex"},
                error_message=None,
            )

    monkeypatch.setattr(cli, "ChatModeRunner", FakeRunner)
    monkeypatch.setattr(cli, "_build_llm_provider", lambda name, **kwargs: object())

    exit_code = cli.main(
        [
            "chat",
            "run",
            str(note),
            "--provider",
            "codex",
            "--prompt",
            "Hi",
            "--total-turn-budget",
            "5",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["response_text"] == "Hello!"
    assert payload["assistant_response_appended"] is True
    assert payload["provider_metadata"]["provider"] == "codex"
    assert payload["usage_log"]["total"]["total_tokens"] == 21
    assert payload["research_summary"] == {
        "fetch_failures": [],
        "fetch_packets": [],
        "search_batches": [],
    }
    assert payload["final_file_snapshots"][0]["revision_id"] == "abc:1:31"
    assert captured["request"].user_prompt == "Hi"
    assert captured["request"].total_turn_budget == 5


def test_chat_run_cli_formats_multiline_prompt_through_real_runner(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    note = tmp_path / "chat.md"
    note.write_text("Existing.\n", encoding="utf-8")

    class FakeProvider:
        async def generate(self, request):
            from aunic.domain import ProviderResponse

            return ProviderResponse(text="Hello!")

    monkeypatch.setattr(cli, "_build_llm_provider", lambda name, **kwargs: FakeProvider())

    exit_code = cli.main(
        [
            "chat",
            "run",
            str(note),
            "--provider",
            "codex",
            "--prompt",
            "Line one\nLine two",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["assistant_response_appended"] is True
    rows = parse_transcript_rows(note.read_text(encoding="utf-8"))
    assert rows[0].content == "Line one\nLine two"
    assert rows[1].content == "Hello!"


def test_chat_run_cli_returns_error_payload_for_chat_mode_validation_failure(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    note = tmp_path / "chat.md"
    note.write_text("Existing.\n", encoding="utf-8")

    class FakeRunner:
        async def run(self, request):
            raise ChatModeError("bad request")

    monkeypatch.setattr(cli, "ChatModeRunner", FakeRunner)
    monkeypatch.setattr(cli, "_build_llm_provider", lambda name, **kwargs: object())

    exit_code = cli.main(
        [
            "chat",
            "run",
            str(note),
            "--provider",
            "codex",
            "--prompt",
            "/prompt-from-note",
        ]
    )

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"error": "bad request"}
