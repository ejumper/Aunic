from __future__ import annotations

import json
import unittest.mock as mock
from pathlib import Path

import httpx
import pytest

import aunic.proto_settings as proto_settings_module
from aunic.proto_settings import get_rag_config
from aunic.rag.types import RagConfig, RagFetchResult, RagFetchSection, RagScope, RagSearchResult
from aunic.tools.rag_tools import (
    RagFetchArgs,
    RagSearchArgs,
    build_rag_tool_registry,
    parse_rag_fetch_args,
    parse_rag_search_args,
)
from aunic.transcript.flattening import flatten_tool_result_for_provider
from aunic.domain import TranscriptRow


# ── Fixtures ──────────────────────────────────────────────────────────────────

_SCOPES = (
    RagScope(name="docs", description="Official documentation"),
    RagScope(name="python", description="Python stdlib"),
)
_CONFIG = RagConfig(server="http://test-rag", scopes=_SCOPES)


@pytest.fixture()
def tmp_proto_settings(tmp_path, monkeypatch):
    """Write a proto-settings.json with RAG config and point proto_settings cache at it."""
    settings_dir = tmp_path / ".aunic"
    settings_dir.mkdir()
    cfg_path = settings_dir / "proto-settings.json"
    cfg_path.write_text(
        json.dumps({
            "rag": {
                "server": "http://test-rag",
                "scopes": [
                    {"name": "docs", "description": "Official documentation"},
                    {"name": "python", "description": "Python stdlib"},
                ],
            }
        }),
        encoding="utf-8",
    )
    # Clear cache so our tmp file is picked up
    proto_settings_module._CACHE.clear()
    yield tmp_path
    proto_settings_module._CACHE.clear()


@pytest.fixture()
def tmp_no_rag_settings(tmp_path, monkeypatch):
    """Write a proto-settings.json WITHOUT a rag section."""
    settings_dir = tmp_path / ".aunic"
    settings_dir.mkdir()
    cfg_path = settings_dir / "proto-settings.json"
    cfg_path.write_text(json.dumps({}), encoding="utf-8")
    proto_settings_module._CACHE.clear()
    yield tmp_path
    proto_settings_module._CACHE.clear()


class _FakeRuntime:
    async def emit_status(self, message: str, *, kind: str = "status") -> None:
        pass


# ── get_rag_config tests ──────────────────────────────────────────────────────

def test_get_rag_config_from_proto_settings(tmp_proto_settings):
    cfg = get_rag_config(tmp_proto_settings)
    assert cfg is not None
    assert cfg.server == "http://test-rag"
    assert len(cfg.scopes) == 2
    assert cfg.scopes[0].name == "docs"
    assert cfg.scopes[1].name == "python"


def test_get_rag_config_missing_rag_section(tmp_no_rag_settings):
    cfg = get_rag_config(tmp_no_rag_settings)
    assert cfg is None


def test_get_rag_config_empty_server(tmp_path):
    settings_dir = tmp_path / ".aunic"
    settings_dir.mkdir()
    (settings_dir / "proto-settings.json").write_text(
        json.dumps({"rag": {"server": "", "scopes": []}}), encoding="utf-8"
    )
    proto_settings_module._CACHE.clear()
    assert get_rag_config(tmp_path) is None
    proto_settings_module._CACHE.clear()


def test_get_rag_config_scope_name_stripped(tmp_path):
    settings_dir = tmp_path / ".aunic"
    settings_dir.mkdir()
    (settings_dir / "proto-settings.json").write_text(
        json.dumps({"rag": {"server": "http://x", "scopes": [{"name": "  wiki  ", "description": ""}]}}),
        encoding="utf-8",
    )
    proto_settings_module._CACHE.clear()
    cfg = get_rag_config(tmp_path)
    proto_settings_module._CACHE.clear()
    assert cfg is not None
    assert cfg.scopes[0].name == "wiki"


# ── Registry builder tests ────────────────────────────────────────────────────

def test_build_rag_tool_registry_no_config(tmp_no_rag_settings):
    result = build_rag_tool_registry(tmp_no_rag_settings)
    assert result == ()


def test_build_rag_tool_registry_with_config(tmp_proto_settings):
    result = build_rag_tool_registry(tmp_proto_settings)
    assert len(result) == 2
    names = {d.spec.name for d in result}
    assert names == {"rag_search", "rag_fetch"}


def test_rag_search_description_includes_scopes(tmp_proto_settings):
    result = build_rag_tool_registry(tmp_proto_settings)
    search_def = next(d for d in result if d.spec.name == "rag_search")
    assert "docs" in search_def.spec.description
    assert "python" in search_def.spec.description


def test_rag_search_scope_enum_constraint(tmp_proto_settings):
    result = build_rag_tool_registry(tmp_proto_settings)
    search_def = next(d for d in result if d.spec.name == "rag_search")
    scope_prop = search_def.spec.input_schema["properties"]["scope"]
    assert "enum" in scope_prop
    assert set(scope_prop["enum"]) == {"docs", "python"}


# ── parse_rag_search_args ────────────────────────────────────────────────────

def test_parse_rag_search_args_valid():
    args = parse_rag_search_args({"query": "netplan", "scope": "docs", "limit": 5})
    assert args.query == "netplan"
    assert args.scope == "docs"
    assert args.limit == 5


def test_parse_rag_search_args_defaults():
    args = parse_rag_search_args({"query": "bgp"})
    assert args.scope is None
    assert args.limit == 10


def test_parse_rag_search_args_missing_query():
    with pytest.raises(ValueError, match="query"):
        parse_rag_search_args({"scope": "docs"})


def test_parse_rag_search_args_limit_capped():
    args = parse_rag_search_args({"query": "x", "limit": 100})
    assert args.limit == 20


# ── parse_rag_fetch_args ─────────────────────────────────────────────────────

def test_parse_rag_fetch_args_valid():
    args = parse_rag_fetch_args({"result_id": "docs:chunk:c1", "neighbors": 2})
    assert args.result_id == "docs:chunk:c1"
    assert args.neighbors == 2


def test_parse_rag_fetch_args_default_neighbors():
    args = parse_rag_fetch_args({"result_id": "docs:chunk:c1"})
    assert args.neighbors == 1


def test_parse_rag_fetch_args_missing_result_id():
    with pytest.raises(ValueError, match="result_id"):
        parse_rag_fetch_args({})


def test_parse_rag_fetch_args_neighbors_capped():
    args = parse_rag_fetch_args({"result_id": "docs:chunk:c1", "neighbors": 99})
    assert args.neighbors == 5


# ── execute_rag_search ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_rag_search_success(tmp_proto_settings):
    _search_response = {
        "results": [
            {
                "result_id": "docs:chunk:c1",
                "doc_id": "docs:ubuntu:netplan",
                "chunk_id": "c1",
                "corpus": "docs",
                "title": "Netplan",
                "source": "ubuntu-server",
                "snippet": "Netplan uses YAML...",
                "score": 0.88,
                "heading_path": "Networking",
                "citation": {"url": None, "local_path": "/docs/netplan.rst", "source": "ubuntu-server"},
            }
        ]
    }
    tools = build_rag_tool_registry(tmp_proto_settings)
    search_def = next(d for d in tools if d.spec.name == "rag_search")
    args = RagSearchArgs(query="netplan", scope="docs", limit=10)

    def _resp(status, body):
        return httpx.Response(status, json=body, request=httpx.Request("POST", "http://test/"))

    with mock.patch("httpx.AsyncClient") as MockClient:
        inst = MockClient.return_value.__aenter__.return_value
        inst.post.return_value = _resp(200, _search_response)
        result = await search_def.execute(_FakeRuntime(), args)

    assert result.status == "completed"
    assert result.tool_name == "rag_search"
    assert isinstance(result.in_memory_content, list)
    assert len(result.in_memory_content) == 1
    assert result.in_memory_content[0]["result_id"] == "docs:chunk:c1"
    assert result.in_memory_content[0]["doc_id"] == "docs:ubuntu:netplan"
    assert result.in_memory_content[0]["local_path"] == "/docs/netplan.rst"
    assert result.in_memory_content[0]["score"] == pytest.approx(0.88)
    # transcript_content is same as in_memory for search
    assert result.transcript_content == result.in_memory_content


@pytest.mark.asyncio
async def test_execute_rag_search_server_error(tmp_proto_settings):
    tools = build_rag_tool_registry(tmp_proto_settings)
    search_def = next(d for d in tools if d.spec.name == "rag_search")
    args = RagSearchArgs(query="fail", scope=None, limit=10)

    def _resp(status, body):
        return httpx.Response(status, json=body, request=httpx.Request("POST", "http://test/"))

    with mock.patch("httpx.AsyncClient") as MockClient:
        inst = MockClient.return_value.__aenter__.return_value
        inst.post.return_value = _resp(500, {"error": "internal error"})
        result = await search_def.execute(_FakeRuntime(), args)

    assert result.status == "tool_error"
    assert result.tool_name == "rag_search"


@pytest.mark.asyncio
async def test_execute_rag_search_connection_error(tmp_proto_settings):
    tools = build_rag_tool_registry(tmp_proto_settings)
    search_def = next(d for d in tools if d.spec.name == "rag_search")
    args = RagSearchArgs(query="fail", scope=None, limit=10)

    with mock.patch("httpx.AsyncClient") as MockClient:
        inst = MockClient.return_value.__aenter__.return_value
        inst.post.side_effect = httpx.ConnectError("connection refused")
        result = await search_def.execute(_FakeRuntime(), args)

    assert result.status == "tool_error"


# ── execute_rag_fetch ─────────────────────────────────────────────────────────

_FETCH_RESPONSE = {
    "result_id": "docs:chunk:c1",
    "doc_id": "docs:ubuntu:netplan",
    "chunk_id": "c1",
    "corpus": "docs",
    "title": "Netplan Configuration",
    "source": "ubuntu-server",
    "heading_path": "Networking > Netplan > Overview",
    "content": "# Netplan Configuration\n\nFull text here.",
    "chunks": [
        {
            "chunk_id": "c1",
            "chunk_order": 0,
            "text": "Netplan is the default...",
        }
    ],
    "citation": {"url": None, "local_path": "/docs/netplan.rst", "source": "ubuntu-server"},
}


def _resp(status, body):
    return httpx.Response(status, json=body, request=httpx.Request("POST", "http://test/"))


@pytest.mark.asyncio
async def test_execute_rag_fetch_success(tmp_proto_settings):
    tools = build_rag_tool_registry(tmp_proto_settings)
    fetch_def = next(d for d in tools if d.spec.name == "rag_fetch")
    args = RagFetchArgs(result_id="docs:chunk:c1", neighbors=1)

    with mock.patch("httpx.AsyncClient") as MockClient:
        inst = MockClient.return_value.__aenter__.return_value
        inst.post.return_value = _resp(200, _FETCH_RESPONSE)
        result = await fetch_def.execute(_FakeRuntime(), args)

    assert result.status == "completed"
    assert result.tool_name == "rag_fetch"
    # in_memory has full_text
    assert isinstance(result.in_memory_content, dict)
    assert result.in_memory_content["full_text"] == "# Netplan Configuration\n\nFull text here."
    assert result.in_memory_content["type"] == "rag_fetch"
    assert result.in_memory_content["result_id"] == "docs:chunk:c1"
    # transcript_content does NOT have full_text
    assert isinstance(result.transcript_content, dict)
    assert "full_text" not in result.transcript_content
    assert result.transcript_content["result_id"] == "docs:chunk:c1"


@pytest.mark.asyncio
async def test_execute_rag_fetch_with_neighbors(tmp_proto_settings):
    tools = build_rag_tool_registry(tmp_proto_settings)
    fetch_def = next(d for d in tools if d.spec.name == "rag_fetch")
    args = RagFetchArgs(result_id="docs:chunk:c1", neighbors=2)
    captured: dict = {}

    body = dict(_FETCH_RESPONSE)

    with mock.patch("httpx.AsyncClient") as MockClient:
        inst = MockClient.return_value.__aenter__.return_value

        async def capture_post(url, **kwargs):
            captured["json"] = kwargs.get("json", {})
            return _resp(200, body)

        inst.post.side_effect = capture_post
        await fetch_def.execute(_FakeRuntime(), args)

    assert captured["json"].get("result_id") == "docs:chunk:c1"
    assert captured["json"].get("neighbors") == 2


# ── Registry integration tests ────────────────────────────────────────────────

def test_rag_tools_in_chat_registry(tmp_proto_settings):
    from aunic.tools.note_edit import build_chat_tool_registry
    registry = build_chat_tool_registry(project_root=tmp_proto_settings)
    names = {d.spec.name for d in registry}
    assert "rag_search" in names
    assert "rag_fetch" in names


def test_rag_tools_absent_without_project_root():
    from aunic.tools.note_edit import build_chat_tool_registry
    registry = build_chat_tool_registry()
    names = {d.spec.name for d in registry}
    assert "rag_search" not in names
    assert "rag_fetch" not in names


# ── Memory manifest test ──────────────────────────────────────────────────────

def test_memory_manifest_includes_rag_search(tmp_proto_settings):
    from aunic.tools.note_edit import build_chat_tool_registry
    from aunic.tools.memory_manifest import build_memory_manifest
    registry = build_chat_tool_registry(project_root=tmp_proto_settings)
    manifest = build_memory_manifest(registry)
    assert manifest is not None
    assert "rag_search" in manifest


# ── Flattening tests ──────────────────────────────────────────────────────────

def _make_row(tool_name, content, row_type="tool_result"):
    return TranscriptRow(
        row_number=1,
        role="tool",
        type=row_type,
        tool_name=tool_name,
        content=content,
    )


def test_flatten_rag_search_results():
    content = [
        {
            "doc_id": "ubuntu:netplan",
            "title": "Netplan",
            "source": "ubuntu-server",
            "snippet": "Netplan uses YAML...",
            "score": 0.88,
        }
    ]
    row = _make_row("rag_search", content)
    text = flatten_tool_result_for_provider(row)
    assert "Netplan" in text
    assert "ubuntu-server" in text
    assert "ubuntu:netplan" in text
    assert "Netplan uses YAML" in text


def test_flatten_rag_search_empty():
    row = _make_row("rag_search", [])
    text = flatten_tool_result_for_provider(row)
    assert "no RAG results" in text


def test_flatten_rag_fetch_with_full_text():
    content = {
        "type": "rag_fetch",
        "doc_id": "ubuntu:netplan",
        "title": "Netplan Configuration",
        "source": "ubuntu-server",
        "url": None,
        "full_text": "# Netplan\n\nFull text here.",
    }
    row = _make_row("rag_fetch", content)
    text = flatten_tool_result_for_provider(row)
    assert "Netplan Configuration" in text
    assert "Full text here." in text


def test_flatten_rag_fetch_transcript_only():
    # transcript_content has no full_text — should fall back to metadata
    content = {
        "type": "rag_fetch",
        "doc_id": "ubuntu:netplan",
        "title": "Netplan Configuration",
        "source": "ubuntu-server",
        "url": None,
    }
    row = _make_row("rag_fetch", content)
    text = flatten_tool_result_for_provider(row)
    assert "Netplan Configuration" in text
    assert "ubuntu:netplan" in text
    assert "ubuntu-server" in text
