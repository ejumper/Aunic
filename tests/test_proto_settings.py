from __future__ import annotations

from pathlib import Path

import pytest

from aunic.context.file_manager import FileManager
from aunic.proto_settings import (
    get_openai_compatible_profiles,
    get_selected_openai_compatible_profile_id,
    get_tool_policy_override,
    resolve_proto_settings_path,
    resolve_openai_compatible_profile,
)
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


def test_get_tool_policy_override_supports_mcp_server_prefix(tmp_path: Path) -> None:
    settings_dir = tmp_path / ".aunic"
    settings_dir.mkdir()
    (settings_dir / "proto-settings.json").write_text(
        (
            "{\n"
            '  "tool_policy_overrides": {\n'
            '    "mcp__github": "deny",\n'
            '    "mcp__github__search": "allow"\n'
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    assert get_tool_policy_override(tmp_path, "mcp__github__search") == "allow"
    assert get_tool_policy_override(tmp_path, "mcp__github__create_issue") == "deny"


def test_proto_settings_resolve_from_nearest_ancestor(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    nested = repo_root / "notes" / "deep"
    nested.mkdir(parents=True)
    settings_dir = repo_root / ".aunic"
    settings_dir.mkdir()
    settings_path = settings_dir / "proto-settings.json"
    settings_path.write_text(
        (
            "{\n"
            '  "tool_policy_overrides": {\n'
            '    "read": "allow"\n'
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    assert resolve_proto_settings_path(nested) == settings_path
    assert get_tool_policy_override(nested, "read") == "allow"


def test_proto_settings_fall_back_to_home_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "workspace" / "project" / "notes"
    project.mkdir(parents=True)
    home_settings_dir = home / ".aunic"
    home_settings_dir.mkdir(parents=True)
    settings_path = home_settings_dir / "proto-settings.json"
    settings_path.write_text(
        (
            "{\n"
            '  "selected_openai_compatible_profile": "openrouter_nemo",\n'
            '  "openai_compatible_profiles": {\n'
            '    "openrouter_nemo": {\n'
            '      "provider_label": "OpenRouter",\n'
            '      "custom_model_name": "Nemo",\n'
            '      "model": "nvidia/nemotron-3-super-120b-a12b",\n'
            '      "base_url": "https://openrouter.ai/api/v1",\n'
            '      "chat_endpoint": "/chat/completions"\n'
            "    }\n"
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))

    assert resolve_proto_settings_path(project) == settings_path
    assert [profile.display_label for profile in get_openai_compatible_profiles(project)] == ["OpenRouter Nemo"]
    assert get_selected_openai_compatible_profile_id(project) == "openrouter_nemo"


def test_openai_compatible_profiles_load_from_proto_settings(tmp_path: Path) -> None:
    settings_dir = tmp_path / ".aunic"
    settings_dir.mkdir()
    (settings_dir / "proto-settings.json").write_text(
        (
            "{\n"
            '  "selected_openai_compatible_profile": "openrouter_nemo",\n'
            '  "openai_compatible_profiles": {\n'
            '    "llama_addie": {\n'
            '      "provider_label": "Llama",\n'
            '      "custom_model_name": "Addie",\n'
            '      "model": "local-model",\n'
            '      "base_url": "http://127.0.0.1:8080",\n'
            '      "chat_endpoint": "/v1/chat/completions",\n'
            '      "health_endpoint": "/health",\n'
            '      "startup_script": "/tmp/addie.sh"\n'
            "    },\n"
            '    "openrouter_nemo": {\n'
            '      "provider_label": "OpenRouter",\n'
            '      "custom_model_name": "Nemo",\n'
            '      "model": "nvidia/nemotron-3-super-120b-a12b",\n'
            '      "base_url": "https://openrouter.ai/api/v1",\n'
            '      "chat_endpoint": "/chat/completions",\n'
            '      "api_key": "placeholder",\n'
            '      "headers": {"X-Test": "yes"},\n'
            '      "replay_reasoning_details": true,\n'
            '      "reasoning_replay_turns": 2\n'
            "    }\n"
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    profiles = get_openai_compatible_profiles(tmp_path)

    assert [profile.profile_id for profile in profiles] == ["llama_addie", "openrouter_nemo"]
    assert profiles[0].display_label == "Llama Addie"
    assert profiles[1].display_label == "OpenRouter Nemo"
    assert profiles[1].headers == {"X-Test": "yes"}
    assert profiles[1].replay_reasoning_details is True
    assert profiles[1].reasoning_replay_turns == 2
    assert get_selected_openai_compatible_profile_id(tmp_path) == "openrouter_nemo"
    assert resolve_openai_compatible_profile(tmp_path).profile_id == "openrouter_nemo"
    assert resolve_openai_compatible_profile(tmp_path, profile_id="llama_addie").model == "local-model"


def test_openai_compatible_profiles_skip_invalid_entries(tmp_path: Path) -> None:
    settings_dir = tmp_path / ".aunic"
    settings_dir.mkdir()
    (settings_dir / "proto-settings.json").write_text(
        (
            "{\n"
            '  "openai_compatible_profiles": {\n'
            '    "broken": {"provider_label": "Broken"},\n'
            '    "good": {\n'
            '      "provider_label": "OpenRouter",\n'
            '      "custom_model_name": "Nemo",\n'
            '      "model": "nvidia/nemotron-3-super-120b-a12b",\n'
            '      "base_url": "https://openrouter.ai/api/v1",\n'
            '      "chat_endpoint": "/chat/completions"\n'
            "    }\n"
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    profiles = get_openai_compatible_profiles(tmp_path)

    assert len(profiles) == 1
    assert profiles[0].profile_id == "good"


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
