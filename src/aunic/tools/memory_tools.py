from __future__ import annotations

from pathlib import Path
from typing import Any

from aunic.tools.base import ToolDefinition
from aunic.tools.grep_notes import build_grep_notes_tool_registry
from aunic.tools.read_map import build_read_map_tool_registry
from aunic.tools.search_transcripts import build_search_transcripts_tool_registry


def build_memory_tool_registry(*, project_root: Path | None = None) -> tuple[ToolDefinition[Any], ...]:
    """Return all memory tools in registration order.

    Phase 1: search_transcripts
    Phase 2: grep_notes
    Phase 3: read_map
    Phase 5: rag_search, rag_fetch (when project_root is provided and RAG is configured)
    """
    base: tuple[ToolDefinition[Any], ...] = (
        *build_search_transcripts_tool_registry(),
        *build_grep_notes_tool_registry(),
        *build_read_map_tool_registry(),
    )
    if project_root is not None:
        from aunic.tools.rag_tools import build_rag_tool_registry
        base = (*base, *build_rag_tool_registry(project_root))
    return base
