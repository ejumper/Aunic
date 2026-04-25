from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "MAP_PATH",
    "BuildResult",
    "GLOBAL_MAP_STALENESS_SECONDS",
    "MapEntry",
    "MapLocation",
    "NoteMetadata",
    "build_map",
    "clear_summary",
    "compute_auto_snippet",
    "ensure_map_ready",
    "ensure_map_ready_shared",
    "load_meta",
    "mark_map_entry_stale",
    "meta_path_for",
    "parse_map",
    "refresh_map_entry_if_stale",
    "render_map",
    "resolve_map_location",
    "save_meta",
    "set_summary",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "MAP_PATH": ("aunic.map.builder", "MAP_PATH"),
    "BuildResult": ("aunic.map.builder", "BuildResult"),
    "build_map": ("aunic.map.builder", "build_map"),
    "clear_summary": ("aunic.map.builder", "clear_summary"),
    "ensure_map_ready": ("aunic.map.builder", "ensure_map_ready"),
    "ensure_map_ready_shared": ("aunic.map.builder", "ensure_map_ready_shared"),
    "mark_map_entry_stale": ("aunic.map.builder", "mark_map_entry_stale"),
    "refresh_map_entry_if_stale": ("aunic.map.builder", "refresh_map_entry_if_stale"),
    "set_summary": ("aunic.map.builder", "set_summary"),
    "NoteMetadata": ("aunic.map.manifest", "NoteMetadata"),
    "load_meta": ("aunic.map.manifest", "load_meta"),
    "meta_path_for": ("aunic.map.manifest", "meta_path_for"),
    "save_meta": ("aunic.map.manifest", "save_meta"),
    "MapEntry": ("aunic.map.render", "MapEntry"),
    "parse_map": ("aunic.map.render", "parse_map"),
    "render_map": ("aunic.map.render", "render_map"),
    "GLOBAL_MAP_STALENESS_SECONDS": ("aunic.map.runtime", "GLOBAL_MAP_STALENESS_SECONDS"),
    "MapLocation": ("aunic.map.runtime", "MapLocation"),
    "resolve_map_location": ("aunic.map.runtime", "resolve_map_location"),
    "compute_auto_snippet": ("aunic.map.snippet", "compute_auto_snippet"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
