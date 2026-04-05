from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aunic.domain import ToolSpec
from aunic.research.types import PageFetchRequest
from aunic.tools.base import ToolDefinition, ToolExecutionResult
from aunic.tools.runtime import RunToolContext, failure_from_payload, failure_payload


@dataclass(frozen=True)
class WebSearchArgs:
    queries: tuple[str, ...]


@dataclass(frozen=True)
class WebFetchArgs:
    url: str


def build_research_tool_registry() -> tuple[ToolDefinition[Any], ...]:
    return (
        ToolDefinition(
            spec=ToolSpec(
                name="web_search",
                description="Search the web for source-backed information. Provide exactly one query.",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["queries"],
                    "properties": {
                        "queries": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 1,
                            "minItems": 1,
                        },
                    },
                },
            ),
            parse_arguments=parse_web_search_args,
            execute=execute_web_search,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="web_fetch",
                description="Fetch a web page by URL and return its content for the current run.",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["url"],
                    "properties": {
                        "url": {"type": "string"},
                    },
                },
            ),
            parse_arguments=parse_web_fetch_args,
            execute=execute_web_fetch,
        ),
    )


def parse_web_search_args(payload: dict[str, Any]) -> WebSearchArgs:
    _ensure_no_extra_keys(payload, {"queries"})
    queries = [item.strip() for item in _require_string_list(payload, "queries") if item.strip()]
    if len(queries) != 1:
        raise ValueError("`queries` must contain exactly one query string.")
    return WebSearchArgs(queries=tuple(queries))


def parse_web_fetch_args(payload: dict[str, Any]) -> WebFetchArgs:
    _ensure_no_extra_keys(payload, {"url"})
    url = _require_string(payload, "url").strip()
    if not url:
        raise ValueError("`url` must not be empty.")
    return WebFetchArgs(url=url)


async def execute_web_search(runtime: RunToolContext, args: WebSearchArgs) -> ToolExecutionResult:
    query = args.queries[0]
    await runtime.emit_status(f"searching {query}...")
    try:
        batch = await runtime.search_service.search(
            queries=args.queries,
            depth="quick",
            freshness="none",
            purpose=query,
            state=runtime.research_state,
        )
    except Exception as exc:
        return _tool_error_result(
            "web_search",
            failure_payload(
                category="validation_error",
                reason="search_failed",
                message=str(exc),
                queries=list(args.queries),
            ),
        )
    payload = [
        {
            "url": result.url,
            "title": result.title,
            "snippet": result.snippet,
        }
        for result in batch.results
    ]
    await runtime.emit_status(f"found {len(payload)} results...")
    if batch.failures and not payload:
        return _tool_error_result(
            "web_search",
            failure_payload(
                category="validation_error",
                reason="search_failed",
                message=batch.failures[0].message,
                queries=list(args.queries),
            ),
        )
    return ToolExecutionResult(
        tool_name="web_search",
        status="completed",
        in_memory_content=payload,
        transcript_content=payload,
    )


async def execute_web_fetch(runtime: RunToolContext, args: WebFetchArgs) -> ToolExecutionResult:
    try:
        result = await runtime.fetch_service.fetch_page(
            PageFetchRequest(url=args.url),
            state=runtime.research_state,
            active_file=runtime.active_file,
        )
    except Exception as exc:
        return _tool_error_result(
            "web_fetch",
            failure_payload(
                category="validation_error",
                reason="fetch_failed",
                message=str(exc),
                url=args.url,
            ),
        )
    in_memory = {
        "type": "web_fetch_page",
        "url": result.url,
        "title": result.title,
        "snippet": result.snippet,
        "markdown": result.markdown,
        "truncated": result.truncated,
    }
    transcript = {
        "url": result.url,
        "title": result.title,
        "snippet": result.snippet,
    }
    return ToolExecutionResult(
        tool_name="web_fetch",
        status="completed",
        in_memory_content=in_memory,
        transcript_content=transcript,
    )


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


def _require_string_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"`{key}` must be an array of strings.")
    return list(value)


def _ensure_no_extra_keys(payload: dict[str, Any], expected: set[str]) -> None:
    extras = sorted(set(payload) - expected)
    if extras:
        raise ValueError(f"Unexpected fields: {', '.join(extras)}.")
