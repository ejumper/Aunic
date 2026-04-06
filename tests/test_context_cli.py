from __future__ import annotations

import json
from pathlib import Path

from aunic import cli
from aunic.context.types import FileChange, FileSnapshot


def test_default_tui_argv_rewrite_only_applies_to_bare_paths() -> None:
    assert cli._coerce_default_tui_argv(["note", "run", "file.md"]) == (
        ["note", "run", "file.md"],
        False,
    )
    assert cli._coerce_default_tui_argv(["file.md"]) == (["tui", "file.md"], True)
    assert cli._coerce_default_tui_argv(["-p", "file.md"]) == (["tui", "-p", "file.md"], True)


def test_bare_path_cli_launches_tui_with_parent_as_default_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("body\n", encoding="utf-8")
    captured: dict[str, object] = {}

    async def _fake_run_tui(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "run_tui", _fake_run_tui)

    exit_code = cli.main([str(note)])

    assert exit_code == 0
    assert captured["active_file"] == note.resolve()
    assert captured["display_root"] == note.resolve().parent
    assert captured["cwd"] == note.resolve().parent
    assert captured["allow_missing_active_file"] is False


def test_bare_path_cli_with_parents_allows_missing_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "new" / "dir" / "note.md"
    captured: dict[str, object] = {}

    async def _fake_run_tui(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "run_tui", _fake_run_tui)

    exit_code = cli.main(["-p", str(target)])

    assert exit_code == 0
    assert captured["active_file"] == target.resolve()
    assert captured["allow_missing_active_file"] is True
    assert captured["create_missing_parents_on_save"] is True
    assert target.exists() is False


def test_bare_path_cli_missing_parent_without_parents_fails(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    target = tmp_path / "missing" / "dir" / "note.md"
    called = False

    async def _fake_run_tui(**kwargs):
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(cli, "run_tui", _fake_run_tui)

    exit_code = cli.main([str(target)])

    assert exit_code == 1
    assert called is False
    assert "Re-run with -p/--parents" in capsys.readouterr().err


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
