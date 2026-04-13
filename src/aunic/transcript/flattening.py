from __future__ import annotations

import json

from aunic.domain import TranscriptRow


def flatten_tool_result_for_provider(row: TranscriptRow) -> str:
    """Convert transcript tool content into provider-facing plain text."""
    if isinstance(row.content, str):
        return row.content
    if isinstance(row.content, dict) and "message" in row.content and row.type == "tool_error":
        return str(row.content.get("message", "Tool failed."))
    if row.tool_name == "web_search" and isinstance(row.content, list):
        return _flatten_search_results(row.content)
    if row.tool_name == "web_fetch" and isinstance(row.content, dict):
        return _flatten_fetch_summary(row.content)
    if row.tool_name == "rag_search" and isinstance(row.content, list):
        return _flatten_rag_search_results(row.content)
    if row.tool_name == "rag_fetch" and isinstance(row.content, dict):
        return _flatten_rag_fetch_result(row.content)
    if row.tool_name == "read" and isinstance(row.content, dict):
        return _flatten_read_result(row.content)
    if row.tool_name in {"edit", "write", "note_edit", "note_write"} and isinstance(row.content, dict):
        return _flatten_edit_like_result(row.content)
    if row.tool_name == "bash" and isinstance(row.content, dict):
        return _flatten_bash_result(row.content)
    if row.tool_name and row.tool_name.startswith("mcp__") and isinstance(row.content, dict):
        return _flatten_mcp_result(row.content)
    return json.dumps(row.content, ensure_ascii=False, separators=(",", ":"))


def _flatten_search_results(results: list[object]) -> str:
    lines: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            lines.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
            continue
        title = str(item.get("title", "")).strip() or "(untitled)"
        url = str(item.get("url", "")).strip() or "(no url)"
        snippet = str(item.get("snippet", "")).strip()
        line = f"{title} | {url}"
        if snippet:
            line += f" | {snippet}"
        lines.append(line)
    return "\n".join(lines) if lines else "(no search results)"


def _flatten_fetch_summary(result: dict[str, object]) -> str:
    markdown = str(result.get("markdown", "")).strip()
    if markdown:
        lines = []
        title = str(result.get("title", "")).strip()
        if title:
            lines.append(f"# {title}")
        lines.append(markdown)
        return "\n\n".join(lines)
    title = str(result.get("title", "")).strip()
    url = str(result.get("url", "")).strip()
    snippet = str(result.get("snippet", "")).strip()

    lines: list[str] = []
    if title:
        lines.append(f"Title: {title}")
    if url:
        lines.append(f"URL: {url}")
    if snippet:
        lines.append(f"Snippet: {snippet}")
    if lines:
        return "\n".join(lines)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


def _flatten_read_result(result: dict[str, object]) -> str:
    result_type = str(result.get("type", "")).strip()
    if result_type == "text_file":
        return str(result.get("content", ""))
    if result_type == "file_unchanged":
        return str(result.get("message", "Earlier read result is still current."))
    if result_type == "pdf":
        return str(result.get("content", ""))
    if result_type == "image":
        file_path = str(result.get("file_path", ""))
        width = result.get("width")
        height = result.get("height")
        return f"Image: {file_path} ({width}x{height})"
    if result_type == "notebook":
        return json.dumps(result.get("content"), ensure_ascii=False, indent=2)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


def _flatten_edit_like_result(result: dict[str, object]) -> str:
    result_type = str(result.get("type", "")).strip()
    if result_type in {"file_edit", "note_content_edit"}:
        path = str(result.get("file_path") or "(active note)")
        return f"Edit applied to {path}"
    if result_type in {"file_write", "note_content_write"}:
        path = str(result.get("file_path") or "(active note)")
        return f"Write applied to {path}"
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


def _flatten_bash_result(result: dict[str, object]) -> str:
    stdout = str(result.get("stdout", ""))
    stderr = str(result.get("stderr", ""))
    if str(result.get("type", "")) == "bash_background":
        return f"Started background command {result.get('task_id')}"
    lines = [f"$ {result.get('command', '')}"]
    if stdout.strip():
        lines.append(stdout.rstrip())
    if stderr.strip():
        lines.append(stderr.rstrip())
    return "\n".join(lines)


def _flatten_rag_search_results(results: list[object]) -> str:
    lines: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            lines.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
            continue
        title = str(item.get("title", "")).strip() or "(untitled)"
        source = str(item.get("source", "")).strip()
        result_id = str(item.get("result_id", "")).strip()
        doc_id = str(item.get("doc_id", "")).strip()
        snippet = str(item.get("snippet", "")).strip()
        identifier = result_id or doc_id
        ref = f"[{source}] {identifier}" if source else identifier
        line = f"{title} | {ref}"
        if snippet:
            line += f" | {snippet}"
        lines.append(line)
    return "\n".join(lines) if lines else "(no RAG results)"


def _flatten_rag_fetch_result(result: dict[str, object]) -> str:
    full_text = str(result.get("full_text", "")).strip()
    if full_text:
        lines = []
        title = str(result.get("title", "")).strip()
        if title:
            lines.append(f"# {title}")
        lines.append(full_text)
        return "\n\n".join(lines)
    title = str(result.get("title", "")).strip()
    result_id = str(result.get("result_id", "")).strip()
    doc_id = str(result.get("doc_id", "")).strip()
    source = str(result.get("source", "")).strip()
    lines: list[str] = []
    if title:
        lines.append(f"Title: {title}")
    if result_id:
        lines.append(f"result_id: {result_id}")
    if doc_id:
        lines.append(f"doc_id: {doc_id}")
    if source:
        lines.append(f"Source: {source}")
    if lines:
        return "\n".join(lines)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


def _flatten_mcp_result(result: dict[str, object]) -> str:
    content = result.get("content")
    if isinstance(content, str) and content.strip():
        return content
    structured = result.get("structured_content")
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
