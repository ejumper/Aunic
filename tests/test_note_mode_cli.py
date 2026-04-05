from __future__ import annotations

import json
from pathlib import Path

from aunic import cli
from aunic.context.types import FileSnapshot
from aunic.errors import NoteModeError
from aunic.loop import LoopMetrics, LoopRunResult
from aunic.modes import NoteModePromptResult, NoteModeRunResult
from aunic.domain import Usage, UsageLog, UsageLogEntry


def _snapshot(path: Path, text: str, revision_id: str) -> FileSnapshot:
    return FileSnapshot(
        path=path.resolve(),
        raw_text=text,
        revision_id=revision_id,
        content_hash=revision_id.split(":")[0],
        mtime_ns=1,
        size_bytes=len(text.encode("utf-8")),
    )


def _loop_result(snapshot: FileSnapshot) -> LoopRunResult:
    return LoopRunResult(
        stop_reason="finished",
        events=(),
        metrics=LoopMetrics(
            valid_turn_count=1,
            successful_edit_count=1,
            stop_reason="finished",
        ),
        tool_failures=(),
        final_file_snapshots=(snapshot,),
        usage_log=UsageLog(
            entries=(
                UsageLogEntry(
                    index=1,
                    stage="tool_loop",
                    usage=Usage(total_tokens=42, input_tokens=30, output_tokens=12),
                    provider="codex",
                    model="gpt-5.4",
                    finish_reason="stop",
                ),
            ),
            total=Usage(total_tokens=42, input_tokens=30, output_tokens=12),
        ),
    )


def test_note_run_cli_outputs_structured_payload(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("Editable text.\n", encoding="utf-8")
    final_snapshot = _snapshot(note, "Updated text.\n", "abc:1:13")
    captured = {}

    class FakeRunner:
        async def run(self, request):
            captured["request"] = request
            return NoteModeRunResult(
                initial_warnings=(),
                prompt_results=(
                    NoteModePromptResult(
                        prompt_index=0,
                        prompt_run=type(
                            "PromptRun",
                            (),
                            {
                                "index": 0,
                                "prompt_text": "Rewrite the note.",
                                "mode": "direct",
                                "per_prompt_budget": 4,
                                "source_path": None,
                                "source_target_id": None,
                                "source_raw_span": None,
                                "source_parsed_span": None,
                                "target_map_text": "TARGET MAP",
                                "model_input_text": "MODEL INPUT",
                            },
                        )(),
                        loop_result=_loop_result(final_snapshot),
                    ),
                ),
                completed_prompt_runs=1,
                completed_all_prompts=True,
                final_file_snapshots=(final_snapshot,),
                stop_reason="finished",
                usage_log_path=str(tmp_path / ".aunic" / "usage" / "2026-03-22.jsonl"),
            )

    monkeypatch.setattr(cli, "NoteModeRunner", FakeRunner)
    monkeypatch.setattr(cli, "_build_llm_provider", lambda name: object())

    exit_code = cli.main(
        [
            "note",
            "run",
            str(note),
            "--provider",
            "codex",
            "--prompt",
            "Rewrite the note.",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["prompt_mode"] == "direct"
    assert payload["completed_prompt_runs"] == 1
    assert payload["prompt_results"][0]["stop_reason"] == "finished"
    assert payload["prompt_results"][0]["metrics"]["successful_edit_count"] == 1
    assert payload["prompt_results"][0]["usage_log"]["total"]["total_tokens"] == 42
    assert payload["usage_log"]["total"] is None
    assert payload["usage_log_path"].endswith(".jsonl")
    assert payload["final_file_snapshots"][0]["revision_id"] == "abc:1:13"
    assert captured["request"].user_prompt == "Rewrite the note."


def test_note_run_cli_passes_literal_prompt_text_through_unchanged(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    note = tmp_path / "note.md"
    note.write_text(">>Prompt<<\n", encoding="utf-8")
    final_snapshot = _snapshot(note, ">>Prompt<<\n", "abc:1:10")
    captured = {}

    class FakeRunner:
        async def run(self, request):
            captured["request"] = request
            return NoteModeRunResult(
                initial_warnings=(),
                prompt_results=(),
                completed_prompt_runs=0,
                completed_all_prompts=False,
                final_file_snapshots=(final_snapshot,),
                stop_reason="finished",
            )

    monkeypatch.setattr(cli, "NoteModeRunner", FakeRunner)
    monkeypatch.setattr(cli, "_build_llm_provider", lambda name: object())

    exit_code = cli.main(
        [
            "note",
            "run",
            str(note),
            "--provider",
            "codex",
            "--prompt",
            "/prompt-from-note",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["prompt_mode"] == "direct"
    assert captured["request"].prompt_mode == "direct"
    assert captured["request"].user_prompt == "/prompt-from-note"


def test_note_run_cli_returns_error_payload_for_note_mode_validation_failure(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("content\n", encoding="utf-8")

    class FakeRunner:
        async def run(self, request):
            raise NoteModeError("bad request")

    monkeypatch.setattr(cli, "NoteModeRunner", FakeRunner)
    monkeypatch.setattr(cli, "_build_llm_provider", lambda name: object())

    exit_code = cli.main(
        [
            "note",
            "run",
            str(note),
            "--provider",
            "codex",
            "--prompt",
            "",
        ]
    )

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"error": "bad request"}
