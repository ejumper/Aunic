from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aunic.tui.app import AunicTuiApp
    from aunic.tui.controller import TuiController
    from aunic.tui.types import ModelOption, TuiMode, TuiState


__all__ = [
    "AunicTuiApp",
    "ModelOption",
    "TuiController",
    "TuiMode",
    "TuiState",
    "run_tui",
]


def run_tui(*args, **kwargs):
    from aunic.tui.app import run_tui as _run_tui

    return _run_tui(*args, **kwargs)


def __getattr__(name: str) -> Any:
    if name == "AunicTuiApp":
        from aunic.tui.app import AunicTuiApp

        return AunicTuiApp
    if name == "TuiController":
        from aunic.tui.controller import TuiController

        return TuiController
    if name in {"ModelOption", "TuiMode", "TuiState"}:
        from aunic.tui.types import ModelOption, TuiMode, TuiState

        return {
            "ModelOption": ModelOption,
            "TuiMode": TuiMode,
            "TuiState": TuiState,
        }[name]
    if name == "run_tui":
        return run_tui
    raise AttributeError(f"module 'aunic.tui' has no attribute {name!r}")
