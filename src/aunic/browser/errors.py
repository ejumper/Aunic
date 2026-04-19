from __future__ import annotations

from typing import Any


class BrowserError(Exception):
    def __init__(
        self,
        reason: str,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or reason)
        self.reason = reason
        self.details = dict(details or {})


class MessageProtocolError(BrowserError):
    pass


class PathError(BrowserError):
    pass


class RevisionConflict(BrowserError):
    pass


class RunInProgress(BrowserError):
    def __init__(self) -> None:
        super().__init__("run_in_progress", "A model run is already in progress.")


class RunNotFound(BrowserError):
    pass


class PermissionRequestNotFound(BrowserError):
    pass
