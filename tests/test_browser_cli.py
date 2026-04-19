from __future__ import annotations

from pathlib import Path

from aunic import cli
import aunic.browser as browser


def test_serve_cli_dispatches_browser_server(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def fake_run_browser_server(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(browser, "run_browser_server", fake_run_browser_server)

    exit_code = cli.main(
        [
            "serve",
            "--host",
            "0.0.0.0",
            "--port",
            "9999",
            "--workspace-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9999
    assert captured["workspace_root"] == tmp_path.resolve()
