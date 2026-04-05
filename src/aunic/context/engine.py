from __future__ import annotations

from pathlib import Path

from aunic.config import ContextSettings, SETTINGS
from aunic.context.file_manager import FileManager
from aunic.context.markers import (
    MarkerAnalysis,
    analyze_note_file,
)
from aunic.context.structure import (
    build_structural_nodes,
    render_parsed_note_text,
    render_target_map,
)
from aunic.context.types import (
    ContextBuildRequest,
    ContextBuildResult,
    PromptRun,
    StructuralNode,
    TextSpan,
)
from aunic.transcript.parser import parse_transcript_rows


class ContextEngine:
    def __init__(
        self,
        file_manager: FileManager | None = None,
        *,
        settings: ContextSettings | None = None,
    ) -> None:
        self._settings = settings or SETTINGS.context
        self._file_manager = file_manager or FileManager(self._settings)

    async def build_context(self, request: ContextBuildRequest) -> ContextBuildResult:
        snapshots = await self._file_manager.read_working_set(
            request.active_file,
            request.included_files,
        )

        active_root = request.display_root
        if active_root is None:
            active_root = request.active_file.expanduser().resolve().parent
        display_root = active_root.expanduser().resolve()

        analyses = tuple(
            analyze_note_file(snapshot, _display_path(snapshot.path, display_root))
            for snapshot in snapshots
        )
        structural_nodes = build_structural_nodes(analyses, self._settings)
        parsed_note_text = render_parsed_note_text(analyses)
        warnings = tuple(
            warning
            for analysis in analyses
            for warning in analysis.parsed_file.warnings
        )

        prompt_runs = self._build_prompt_runs(
            request=request,
            analyses=analyses,
            structural_nodes=structural_nodes,
            parsed_note_text=parsed_note_text,
        )
        target_map_text = prompt_runs[0].target_map_text if prompt_runs else ""
        read_only_map_text = prompt_runs[0].read_only_map_text if prompt_runs else ""
        model_input_text = prompt_runs[0].model_input_text if prompt_runs else ""
        raw_transcript = analyses[0].parsed_file.transcript_text if analyses else None
        transcript_rows = parse_transcript_rows(raw_transcript) if raw_transcript else None

        return ContextBuildResult(
            prompt_runs=prompt_runs,
            file_snapshots=snapshots,
            parsed_files=tuple(analysis.parsed_file for analysis in analyses),
            structural_nodes=structural_nodes,
            parsed_note_text=parsed_note_text,
            target_map_text=target_map_text,
            read_only_map_text=read_only_map_text,
            model_input_text=model_input_text,
            warnings=warnings,
            transcript_text=raw_transcript,
            transcript_rows=transcript_rows,
        )

    def _build_prompt_runs(
        self,
        *,
        request: ContextBuildRequest,
        analyses: tuple[MarkerAnalysis, ...],
        structural_nodes: tuple[StructuralNode, ...],
        parsed_note_text: str,
    ) -> tuple[PromptRun, ...]:
        target_map_text, read_only_map_text = render_target_map(structural_nodes)
        note_snapshot_text = _build_note_snapshot(
            parsed_note_text,
            target_map_text,
            read_only_map_text,
        )
        model_input_text = _assemble_model_input(
            request.user_prompt, parsed_note_text, target_map_text, read_only_map_text,
        )
        return (
            PromptRun(
                index=1,
                prompt_text=request.user_prompt,
                mode="direct",
                per_prompt_budget=request.total_turn_budget,
                target_map_text=target_map_text,
                model_input_text=model_input_text,
                read_only_map_text=read_only_map_text,
                note_snapshot_text=note_snapshot_text,
                user_prompt_text=request.user_prompt,
            ),
        )


def _display_path(path: Path, display_root: Path) -> str:
    try:
        return str(path.relative_to(display_root))
    except ValueError:
        return str(path)


def _assemble_model_input(
    prompt_text: str,
    parsed_note_text: str,
    target_map_text: str,
    read_only_map_text: str = "",
) -> str:
    parts = [
        f"USER PROMPT\n{prompt_text}",
        _build_note_snapshot(parsed_note_text, target_map_text, read_only_map_text),
    ]
    return "\n\n".join(parts).strip()


def _build_note_snapshot(
    parsed_note_text: str,
    target_map_text: str,
    read_only_map_text: str = "",
) -> str:
    parts = [
        f"NOTE SNAPSHOT\n{parsed_note_text}",
        f"TARGET MAP\n{target_map_text}",
    ]
    if read_only_map_text:
        parts.append(f"READ-ONLY MAP\n{read_only_map_text}")
    return "\n\n".join(parts)
