from __future__ import annotations

import json
from pathlib import Path

from aunic import cli
from aunic.context.types import FileChange, FileSnapshot


def test_context_inspect_cli_outputs_context_payload(tmp_path: Path, capsys) -> None:
    note = tmp_path / "note.md"
    note.write_text("# Note\n@>>Editable core<<@\n", encoding="utf-8")

    exit_code = cli.main(
        [
            "context",
            "inspect",
            str(note),
            "--user-prompt",
            "Name the editable section.",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["prompt_runs"][0]["prompt_text"] == "Name the editable section."
    assert any(
        node["label"] == "WRITE-EDIT_ALLOWED"
        for node in payload["structural_nodes"]
    )


def test_context_watch_cli_prints_initial_state_and_changes(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    note = tmp_path / "watch.md"
    note.write_text("watch me", encoding="utf-8")

    snapshot = FileSnapshot(
        path=note.resolve(),
        raw_text="watch me",
        revision_id="abc:1:8",
        content_hash="abc",
        mtime_ns=1,
        size_bytes=8,
    )

    class FakeFileManager:
        async def read_working_set(self, active_file, included_files=()):
            return (snapshot,)

        async def watch(self, paths):
            yield (
                FileChange(
                    path=note.resolve(),
                    change="modified",
                    exists=True,
                    revision_id="def:2:8",
                ),
            )

    monkeypatch.setattr(cli, "FileManager", FakeFileManager)
    exit_code = cli.main(["context", "watch", str(note), "--max-events", "1"])

    assert exit_code == 0
    lines = [line for line in capsys.readouterr().out.strip().splitlines() if line]
    initial = json.loads(lines[0])
    change = json.loads(lines[1])
    assert initial["type"] == "initial"
    assert change["type"] == "changes"
    assert change["changes"][0]["change"] == "modified"
