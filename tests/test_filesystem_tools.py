from __future__ import annotations

import base64
from pathlib import Path

import pytest
from pypdf import PdfWriter

from aunic.context.file_manager import FileManager
from aunic.research.types import ResearchState
from aunic.tools.filesystem import ReadArgs, execute_read
from aunic.tools.runtime import RunToolContext, ToolSessionState


def _write_read_allow_settings(project_root: Path) -> None:
    settings_dir = project_root / ".aunic"
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


async def _build_runtime(project_root: Path) -> RunToolContext:
    _write_read_allow_settings(project_root)
    note = project_root / "note.md"
    note.write_text("# Note\n\nBody.\n", encoding="utf-8")
    return await RunToolContext.create(
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


def _write_blank_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with path.open("wb") as handle:
        writer.write(handle)


def _write_png(path: Path) -> None:
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z9xQAAAAASUVORK5CYII="
    )
    path.write_bytes(png_bytes)


@pytest.mark.asyncio
async def test_execute_read_reads_existing_pdf_without_not_found(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    pdf_path = project_root / "sample.pdf"
    _write_blank_pdf(pdf_path)
    runtime = await _build_runtime(project_root)

    result = await execute_read(runtime, ReadArgs(file_path=str(pdf_path)))

    assert result.status == "completed"
    assert result.tool_failure is None
    assert result.in_memory_content["type"] == "pdf"
    assert result.in_memory_content["file_path"] == str(pdf_path)


@pytest.mark.asyncio
async def test_execute_read_reads_existing_image_without_text_decode(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    image_path = project_root / "sample.png"
    _write_png(image_path)
    runtime = await _build_runtime(project_root)

    result = await execute_read(runtime, ReadArgs(file_path=str(image_path)))

    assert result.status == "completed"
    assert result.tool_failure is None
    assert result.in_memory_content["type"] == "image"
    assert result.in_memory_content["file_path"] == str(image_path)


@pytest.mark.asyncio
async def test_execute_read_missing_file_returns_not_found(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    runtime = await _build_runtime(project_root)
    missing = project_root / "missing.pdf"

    result = await execute_read(runtime, ReadArgs(file_path=str(missing)))

    assert result.status == "tool_error"
    assert result.tool_failure is not None
    assert result.tool_failure.reason == "not_found"


@pytest.mark.asyncio
async def test_execute_read_invalid_utf8_returns_specific_error(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    text_path = project_root / "binary.txt"
    text_path.write_bytes(b"\xff\xfe\x00\x01")
    runtime = await _build_runtime(project_root)

    result = await execute_read(runtime, ReadArgs(file_path=str(text_path)))

    assert result.status == "tool_error"
    assert result.tool_failure is not None
    assert result.tool_failure.reason == "invalid_utf8"


@pytest.mark.asyncio
async def test_execute_read_invalid_pdf_returns_pdf_specific_error(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    pdf_path = project_root / "broken.pdf"
    pdf_path.write_text("not actually a pdf", encoding="utf-8")
    runtime = await _build_runtime(project_root)

    result = await execute_read(runtime, ReadArgs(file_path=str(pdf_path)))

    assert result.status == "tool_error"
    assert result.tool_failure is not None
    assert result.tool_failure.reason == "invalid_pdf"

