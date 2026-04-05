class AunicError(Exception):
    """Base exception for the project."""


class ProviderError(AunicError):
    """Base provider failure."""


class ConfigurationError(ProviderError):
    """Raised when provider configuration is invalid."""


class ServiceUnavailableError(ProviderError):
    """Raised when a dependency cannot be reached."""


class StructuredOutputError(ProviderError):
    """Raised when a model fails to return the expected envelope."""


class CodexProtocolError(ProviderError):
    """Raised when the Codex app-server protocol is invalid or incomplete."""


class ClaudeSDKError(ProviderError):
    """Raised when the Claude SDK session encounters an unexpected error."""


class ContextError(AunicError):
    """Base context-engine failure."""


class FileReadError(ContextError):
    """Raised when a note file cannot be read or decoded."""


class OptimisticWriteError(ContextError):
    """Raised when a write is attempted against the wrong file revision."""


class LoopError(AunicError):
    """Base tool-loop failure."""


class NoteModeError(AunicError):
    """Raised when note-mode orchestration cannot proceed."""


class ChatModeError(AunicError):
    """Raised when chat-mode orchestration cannot proceed."""
