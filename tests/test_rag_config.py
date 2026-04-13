from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import aunic.rag.config as rag_config_module
from aunic.rag.config import invalidate_rag_config_cache, load_rag_config
from aunic.rag.types import RagConfig, RagScope


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch, tmp_path):
    """Point RAG_CONFIG_PATH at a temp location, suppress proto-settings lookup,
    and reset cache between tests."""
    cfg_path = tmp_path / "rag.toml"
    monkeypatch.setattr(rag_config_module, "RAG_CONFIG_PATH", cfg_path)
    invalidate_rag_config_cache()
    # Prevent real ~/.aunic/proto-settings.json from interfering with rag.toml tests
    with patch("aunic.proto_settings.get_rag_config", return_value=None):
        yield
    invalidate_rag_config_cache()


def write_toml(tmp_path, content: str) -> None:
    cfg_path = tmp_path / "rag.toml"
    cfg_path.write_text(content, encoding="utf-8")


def test_load_valid_config(tmp_path):
    write_toml(tmp_path, """
[rag]
server = "http://localhost:5173"

[[rag.scope]]
name = "docs"
description = "Ubuntu server docs."

[[rag.scope]]
name = "wiki"
description = "Wikipedia."
""")
    cfg = load_rag_config()
    assert isinstance(cfg, RagConfig)
    assert cfg.server == "http://localhost:5173"
    assert len(cfg.scopes) == 2
    assert cfg.scopes[0] == RagScope(name="docs", description="Ubuntu server docs.")
    assert cfg.scopes[1] == RagScope(name="wiki", description="Wikipedia.")


def test_load_missing_file(tmp_path):
    # rag.toml does not exist in tmp_path
    assert load_rag_config() is None


def test_load_empty_server(tmp_path):
    write_toml(tmp_path, """
[rag]
server = ""
""")
    assert load_rag_config() is None


def test_load_whitespace_server(tmp_path):
    write_toml(tmp_path, """
[rag]
server = "   "
""")
    assert load_rag_config() is None


def test_load_no_scopes(tmp_path):
    write_toml(tmp_path, """
[rag]
server = "http://localhost:5173"
""")
    cfg = load_rag_config()
    assert cfg is not None
    assert cfg.server == "http://localhost:5173"
    assert cfg.scopes == ()


def test_load_malformed_toml(tmp_path):
    cfg_path = tmp_path / "rag.toml"
    cfg_path.write_text("this is not [ valid toml !!!", encoding="utf-8")
    assert load_rag_config() is None


def test_load_missing_rag_section(tmp_path):
    write_toml(tmp_path, """
[other]
key = "value"
""")
    assert load_rag_config() is None


def test_cache_returns_same_object(tmp_path):
    write_toml(tmp_path, """
[rag]
server = "http://localhost:5173"
""")
    cfg1 = load_rag_config()
    cfg2 = load_rag_config()
    assert cfg1 is cfg2


def test_cache_invalidation(tmp_path):
    write_toml(tmp_path, """
[rag]
server = "http://localhost:5173"
""")
    cfg1 = load_rag_config()
    invalidate_rag_config_cache()
    cfg2 = load_rag_config()
    # Both valid but distinct objects after re-parse
    assert cfg1 == cfg2


def test_scope_without_description(tmp_path):
    write_toml(tmp_path, """
[rag]
server = "http://localhost:5173"

[[rag.scope]]
name = "docs"
""")
    cfg = load_rag_config()
    assert cfg is not None
    assert cfg.scopes[0].description == ""


def test_scope_name_stripped(tmp_path):
    write_toml(tmp_path, """
[rag]
server = "http://localhost:5173"

[[rag.scope]]
name = "  docs  "
description = "Test"
""")
    cfg = load_rag_config()
    assert cfg is not None
    assert cfg.scopes[0].name == "docs"
