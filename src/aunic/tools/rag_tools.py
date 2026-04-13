from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aunic.domain import ToolSpec
from aunic.tools.base import ToolDefinition, ToolExecutionResult
from aunic.tools.runtime import RunToolContext, failure_from_payload, failure_payload


@dataclass(frozen=True)
class RagSearchArgs:
    query: str
    scope: str | None
    limit: int


@dataclass(frozen=True)
class RagFetchArgs:
    result_id: str
    neighbors: int


def build_rag_tool_registry(project_root: Path) -> tuple[ToolDefinition[Any], ...]:
    """Return rag_search and rag_fetch ToolDefinitions, or () if RAG is not configured."""
    from aunic.proto_settings import get_rag_config
    from aunic.rag.client import RagClient

    config = get_rag_config(project_root)
    if config is None:
        return ()

    client_factory = lambda: RagClient(config.server)  # noqa: E731

    # Build dynamic description with available scope names + descriptions.
    if config.scopes:
        scope_lines = "\n".join(
            f"  - {s.name}: {s.description}" for s in config.scopes
        )
        scope_desc = f"Available scopes:\n{scope_lines}"
        scope_enum = [s.name for s in config.scopes]
    else:
        scope_desc = "No named scopes configured — omit scope to search all content."
        scope_enum = None

    search_description = (
        "Search the local RAG knowledge base for relevant chunks. "
        "Returns result_id, doc_id, chunk_id, title, source, snippet, and relevance score for each hit. "
        "Follow up with rag_fetch using result_id to retrieve the selected chunk plus optional neighbors.\n\n"
        + scope_desc
    )

    scope_schema: dict[str, Any] = {"type": "string"}
    if scope_enum:
        scope_schema["enum"] = scope_enum

    async def execute_search(runtime: RunToolContext, args: RagSearchArgs) -> ToolExecutionResult:
        await runtime.emit_status(f"searching RAG for {args.query!r}...")
        try:
            client = client_factory()
            results = await client.search(args.query, scope=args.scope, limit=args.limit)
        except Exception as exc:
            return _tool_error_result(
                "rag_search",
                failure_payload(
                    category="validation_error",
                    reason="search_failed",
                    message=str(exc),
                    query=args.query,
                ),
            )
        payload = [
            {
                "result_id": r.result_id,
                "doc_id": r.doc_id,
                "chunk_id": r.chunk_id,
                "corpus": r.corpus,
                "title": r.title,
                "source": r.source,
                "snippet": r.snippet,
                "score": r.score,
                "heading_path": list(r.heading_path),
                "url": r.url,
                "local_path": r.local_path,
            }
            for r in results
        ]
        await runtime.emit_status(f"found {len(payload)} RAG results...")
        return ToolExecutionResult(
            tool_name="rag_search",
            status="completed",
            in_memory_content=payload,
            transcript_content=payload,
        )

    async def execute_fetch(runtime: RunToolContext, args: RagFetchArgs) -> ToolExecutionResult:
        await runtime.emit_status(f"fetching RAG result {args.result_id!r}...")
        try:
            client = client_factory()
            result = await client.fetch(args.result_id, neighbors=args.neighbors)
        except Exception as exc:
            return _tool_error_result(
                "rag_fetch",
                failure_payload(
                    category="validation_error",
                    reason="fetch_failed",
                    message=str(exc),
                    result_id=args.result_id,
                ),
            )
        in_memory = {
            "type": "rag_fetch",
            "result_id": result.result_id,
            "doc_id": result.doc_id,
            "chunk_id": result.chunk_id,
            "corpus": result.corpus,
            "title": result.title,
            "source": result.source,
            "url": result.url,
            "local_path": result.local_path,
            "section_count": len(result.sections),
            "full_text": result.full_text,
        }
        transcript = {
            "type": "rag_fetch",
            "result_id": result.result_id,
            "doc_id": result.doc_id,
            "chunk_id": result.chunk_id,
            "corpus": result.corpus,
            "title": result.title,
            "source": result.source,
            "url": result.url,
            "local_path": result.local_path,
        }
        return ToolExecutionResult(
            tool_name="rag_fetch",
            status="completed",
            in_memory_content=in_memory,
            transcript_content=transcript,
        )

    return (
        ToolDefinition(
            spec=ToolSpec(
                name="rag_search",
                description=search_description,
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string"},
                        "scope": scope_schema,
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
                    },
                },
            ),
            parse_arguments=parse_rag_search_args,
            execute=execute_search,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="rag_fetch",
                description=(
                    "Fetch a RAG result by result_id. Use after rag_search. "
                    "Returns the selected chunk plus optional neighboring chunks."
                ),
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["result_id"],
                    "properties": {
                        "result_id": {"type": "string"},
                        "neighbors": {"type": "integer", "minimum": 0, "maximum": 5, "default": 1},
                    },
                },
            ),
            parse_arguments=parse_rag_fetch_args,
            execute=execute_fetch,
        ),
    )


def parse_rag_search_args(payload: dict[str, Any]) -> RagSearchArgs:
    _ensure_no_extra_keys(payload, {"query", "scope", "limit"})
    query = _require_string(payload, "query").strip()
    if not query:
        raise ValueError("`query` must not be empty.")
    scope = payload.get("scope")
    if scope is not None:
        if not isinstance(scope, str):
            raise ValueError("`scope` must be a string.")
        scope = scope.strip() or None
    limit = payload.get("limit", 10)
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("`limit` must be a positive integer.")
    if limit > 20:
        limit = 20
    return RagSearchArgs(query=query, scope=scope, limit=limit)


def parse_rag_fetch_args(payload: dict[str, Any]) -> RagFetchArgs:
    _ensure_no_extra_keys(payload, {"result_id", "neighbors"})
    result_id = _require_string(payload, "result_id").strip()
    if not result_id:
        raise ValueError("`result_id` must not be empty.")
    neighbors = payload.get("neighbors", 1)
    if not isinstance(neighbors, int) or neighbors < 0:
        raise ValueError("`neighbors` must be a non-negative integer.")
    if neighbors > 5:
        neighbors = 5
    return RagFetchArgs(result_id=result_id, neighbors=neighbors)


def _tool_error_result(tool_name: str, payload: dict[str, Any]) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=tool_name,
        status="tool_error",
        in_memory_content=payload,
        transcript_content=payload,
        tool_failure=failure_from_payload(payload, tool_name=tool_name),
    )


def _require_string(payload: dict[str, Any], key: str) -> str:
    if key not in payload:
        raise ValueError(f"Missing required field `{key}`.")
    value = payload[key]
    if not isinstance(value, str):
        raise ValueError(f"`{key}` must be a string.")
    return value


def _ensure_no_extra_keys(payload: dict[str, Any], expected: set[str]) -> None:
    extras = sorted(set(payload) - expected)
    if extras:
        raise ValueError(f"Unexpected fields: {', '.join(extras)}.")
