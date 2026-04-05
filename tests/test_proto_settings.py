from __future__ import annotations

from pathlib import Path

import pytest

from aunic.context.file_manager import FileManager
from aunic.proto_settings import get_tool_policy_override
from aunic.research.types import ResearchState
from aunic.tools.filesystem import ReadArgs, execute_read
from aunic.tools.runtime import RunToolContext, ToolSessionState


def test_get_tool_policy_override_reads_project_proto_settings(tmp_path: Path) -> None:
    settings_dir = tmp_path / ".aunic"
    settings_dir.mkdir()
    (settings_dir / "proto-settings.json").write_text(
        (
            "{\n"
            '  "tool_policy_overrides": {\n'
            '    "read": "allow",\n'
            '    "write": "ask"\n'
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    assert get_tool_policy_override(tmp_path, "read") == "allow"
    assert get_tool_policy_override(tmp_path, "write") == "ask"
    assert get_tool_policy_override(tmp_path, "grep") is None


@pytest.mark.asyncio
async def test_execute_read_uses_proto_settings_override_outside_working_directory(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    settings_dir = project_root / ".aunic"
    project_root.mkdir()
    settings_dir.mkdir()
    (settings_dir / "proto-settings.json").write_text(
        (
            "{\n"
            '  "tool_policy_overrides": {\n'
            '    "read": "allow"\n'
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    note = project_root / "note.md"
    note.write_text("# Note\n\nBody.\n", encoding="utf-8")

    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("Reference details.\n", encoding="utf-8")

    runtime = await RunToolContext.create(
        file_manager=FileManager(),
        context_result=None,
        prompt_run=None,
        active_file=note,
        session_state=ToolSessionState(cwd=project_root),
        search_service=object(),
        fetch_service=object(),
        research_state=ResearchState(),
        progress_sink=None,
        work_mode="read",
        permission_handler=None,
        metadata={"cwd": str(project_root)},
    )

    result = await execute_read(runtime, ReadArgs(file_path=str(outside_file)))

    assert result.status == "completed"
    assert result.tool_failure is None
    assert result.in_memory_content["type"] == "text_file"
    assert "Reference details." in result.in_memory_content["content"]
