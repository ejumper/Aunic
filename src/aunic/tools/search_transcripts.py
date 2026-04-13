from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aunic.domain import ToolSpec
from aunic.tools.base import ToolDefinition, ToolExecutionResult
from aunic.tools.runtime import RunToolContext, failure_from_payload, failure_payload
from aunic.transcript.search import TranscriptSearchService

_HARD_LIMIT_CAP = 100


@dataclass(frozen=True)
class SearchTranscriptsArgs:
    query: str | None = None
    tool: str | None = None
    scope: str | None = None  # absolute or ~-prefixed path; walker root override
    limit: int = 20           # hard-capped at 100
    offset: int = 0


def build_search_transcripts_tool_registry() -> tuple[ToolDefinition[Any], ...]:
    return (
        ToolDefinition(
            spec=ToolSpec(
                name="search_transcripts",
                description=(
                    "Search past tool calls and results across every Aunic note on this system. "
                    "Returns tool_call/tool_result pairs with absolute paths and row numbers. "
                    "Filter by tool= (exact tool name, e.g. 'bash', 'web_search'), "
                    "query= (substring match over args and results JSON), "
                    "scope= (absolute path to restrict the walk to a subtree). "
                    "Note: search_transcripts calls also appear in future search results — "
                    "use tool=<other> to exclude search history. "
                    "Use the read tool with the returned path to view the full row context."
                ),
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": [],
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Substring to match against tool-call args and results "
                                "(compact JSON text)."
                            ),
                        },
                        "tool": {
                            "type": "string",
                            "description": (
                                "Exact tool name to filter by "
                                "(e.g. 'bash', 'web_search', 'note_edit')."
                            ),
                        },
                        "scope": {
                            "type": "string",
                            "description": (
                                "Absolute path to restrict the walk to a subtree. "
                                "Defaults to the user home directory."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "description": "Max hits to return. Default 20.",
                        },
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "Number of hits to skip for pagination. Default 0.",
                        },
                    },
                },
            ),
            parse_arguments=parse_search_transcripts_args,
            execute=execute_search_transcripts,
        ),
    )


def parse_search_transcripts_args(payload: dict[str, Any]) -> SearchTranscriptsArgs:
    allowed = {"query", "tool", "scope", "limit", "offset"}
    extras = sorted(set(payload) - allowed)
    if extras:
        raise ValueError(f"Unexpected fields: {', '.join(extras)}.")

    query = payload.get("query")
    if query is not None and not isinstance(query, str):
        raise ValueError("`query` must be a string.")

    tool = payload.get("tool")
    if tool is not None and not isinstance(tool, str):
        raise ValueError("`tool` must be a string.")

    scope = payload.get("scope")
    if scope is not None and not isinstance(scope, str):
        raise ValueError("`scope` must be a string.")

    limit = payload.get("limit", 20)
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("`limit` must be an integer.")
    if limit < 1 or limit > _HARD_LIMIT_CAP:
        raise ValueError(f"`limit` must be between 1 and {_HARD_LIMIT_CAP}.")

    offset = payload.get("offset", 0)
    if not isinstance(offset, int) or isinstance(offset, bool):
        raise ValueError("`offset` must be an integer.")
    if offset < 0:
        raise ValueError("`offset` must be >= 0.")

    return SearchTranscriptsArgs(
        query=query or None,
        tool=tool or None,
        scope=scope or None,
        limit=limit,
        offset=offset,
    )


async def execute_search_transcripts(
    runtime: RunToolContext,
    args: SearchTranscriptsArgs,
) -> ToolExecutionResult:
    # Resolve scope path
    scope_path: Path | None = None
    if args.scope is not None:
        raw = Path(args.scope).expanduser()
        # Resolve relative paths against runtime cwd
        if not raw.is_absolute():
            raw = runtime.session_state.cwd / raw
        scope_path = raw.resolve()
        if not scope_path.exists():
            payload = failure_payload(
                category="validation_error",
                reason="scope_not_found",
                message=f"scope path does not exist: {scope_path}",
                scope=args.scope,
            )
            return ToolExecutionResult(
                tool_name="search_transcripts",
                status="tool_error",
                in_memory_content=payload,
                transcript_content=payload,
                tool_failure=failure_from_payload(payload, tool_name="search_transcripts"),
            )
        if not scope_path.is_dir():
            payload = failure_payload(
                category="validation_error",
                reason="scope_not_directory",
                message=f"scope path is not a directory: {scope_path}",
                scope=args.scope,
            )
            return ToolExecutionResult(
                tool_name="search_transcripts",
                status="tool_error",
                in_memory_content=payload,
                transcript_content=payload,
                tool_failure=failure_from_payload(payload, tool_name="search_transcripts"),
            )

    service = TranscriptSearchService()
    result = service.search(
        query=args.query,
        tool=args.tool,
        scope=scope_path,
        limit=args.limit,
        offset=args.offset,
    )

    payload = {
        "hits": [
            {
                "path": hit.path,
                "row_number": hit.row_number,
                "tool": hit.tool,
                "tool_id": hit.tool_id,
                "args_snippet": hit.args_snippet,
                "result_snippet": hit.result_snippet,
                "result_status": hit.result_status,
            }
            for hit in result.hits
        ],
        "total_matches": result.total_matches,
        "returned": result.returned,
        "offset": result.offset,
        "limit": result.limit,
        "truncated": result.truncated,
        "scanned_files": result.scanned_files,
        "narrow_hint": result.narrow_hint,
    }

    return ToolExecutionResult(
        tool_name="search_transcripts",
        status="completed",
        in_memory_content=payload,
        transcript_content=payload,
    )
