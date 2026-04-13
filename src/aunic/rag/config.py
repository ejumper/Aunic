from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

from aunic.rag.types import RagConfig, RagScope

logger = logging.getLogger(__name__)

RAG_CONFIG_PATH = Path.home() / ".aunic" / "rag.toml"

# (mtime_ns | None, RagConfig | None)
_CACHE: tuple[int | None, RagConfig | None] | None = None


def load_rag_config(project_root: Path | None = None) -> RagConfig | None:
    """Return a RagConfig from proto-settings.json (preferred) or rag.toml (fallback)."""
    global _CACHE
    from aunic.proto_settings import get_rag_config as _get_rag_config
    proto_cfg = _get_rag_config(project_root or Path.home())
    if proto_cfg is not None:
        return proto_cfg

    try:
        stat = RAG_CONFIG_PATH.stat()
        mtime_ns = stat.st_mtime_ns
    except FileNotFoundError:
        if _CACHE is not None and _CACHE[0] is None:
            return _CACHE[1]
        _CACHE = (None, None)
        return None
    except OSError:
        return None

    if _CACHE is not None and _CACHE[0] == mtime_ns:
        return _CACHE[1]

    result = _parse_rag_config(RAG_CONFIG_PATH)
    _CACHE = (mtime_ns, result)
    return result


def _parse_rag_config(path: Path) -> RagConfig | None:
    try:
        data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("Failed to parse rag.toml: %s", exc)
        return None

    rag = data.get("rag")
    if not isinstance(rag, dict):
        return None

    server = rag.get("server", "")
    if not isinstance(server, str) or not server.strip():
        return None

    raw_scopes = data.get("rag", {})
    # [[rag.scope]] entries are parsed by tomllib as rag["scope"] list
    raw_scope_list = rag.get("scope", [])
    if not isinstance(raw_scope_list, list):
        raw_scope_list = []

    scopes = []
    for entry in raw_scope_list:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "")
        description = entry.get("description", "")
        if isinstance(name, str) and name.strip():
            scopes.append(RagScope(name=name.strip(), description=description or ""))

    return RagConfig(server=server.strip(), scopes=tuple(scopes))


def invalidate_rag_config_cache() -> None:
    """Reset the config cache. Used in tests."""
    global _CACHE
    _CACHE = None
