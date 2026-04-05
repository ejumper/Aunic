from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CodexSettings:
    executable: str = "codex"
    default_model: str = "gpt-5.4"
    default_reasoning_effort: str = "medium"
    sandbox_mode: str = "read-only"
    approval_policy: str = "never"
    startup_timeout_seconds: float = 10.0
    turn_timeout_seconds: float = 180.0
    stderr_log_limit: int = 200
    mcp_server_name: str = "aunic"


@dataclass(frozen=True)
class ClaudeSettings:
    executable: str = "claude"
    default_model: str = "claude-sonnet-4-6"
    haiku_model: str = "claude-haiku-4-5-20251001"
    sonnet_model: str = "claude-sonnet-4-6"
    opus_model: str = "claude-opus-4-6"
    default_reasoning_effort: str | None = None
    turn_timeout_seconds: float = 180.0
    mcp_server_name: str = "aunic"


@dataclass(frozen=True)
class LlamaCppSettings:
    base_url: str = "http://127.0.0.1:8080"
    health_endpoint: str = "/health"
    chat_endpoint: str = "/v1/chat/completions"
    startup_script: Path = Path("/path/to/model/startup.sh")
    default_model: str = "local-model"
    temperature: float = 0.2
    request_timeout_seconds: float = 120.0
    startup_timeout_seconds: float = 900.0
    poll_interval_seconds: float = 2.0
    max_output_tokens: int = 4096


@dataclass(frozen=True)
class OllamaSettings:
    base_url: str = "http://127.0.0.1:11434"
    embed_endpoint: str = "/api/embed"
    tags_endpoint: str = "/api/tags"
    embedding_model: str = "mxbai-embed-large"
    request_timeout_seconds: float = 60.0


@dataclass(frozen=True)
class SearxngSchedulerSettings:
    preferred_engines: tuple[str, ...] = (
        "duckduckgo",
        "brave",
        "bing",
        "yahoo",
        "startpage",
        "google",
        "qwant",
    )
    per_engine_reuse_cooldown_seconds: float = 5.0
    engine_timeout_seconds: float = 3600.0


@dataclass(frozen=True)
class ResearchSettings:
    searxng_base_url: str = "https://your-searxng-instance.example.com"
    searxng_search_endpoint: str = "/search"
    search_request_timeout_seconds: float = 20.0
    fetch_request_timeout_seconds: float = 30.0
    user_agent: str = "Aunic/0.1"
    search_results_per_query: int = 10
    searxng_scheduler: SearxngSchedulerSettings = field(default_factory=SearxngSchedulerSettings)
    fetch_max_chars: int = 100_000
    fetch_cache_max_bytes: int = 3 * 1024 * 1024
    strip_tracking_query_parameters: tuple[str, ...] = (
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "ref",
    )


@dataclass(frozen=True)
class ContextSettings:
    chunk_size_chars: int = 3000
    preview_chars: int = 90
    watch_debounce_ms: int = 150
    watch_step_ms: int = 50


@dataclass(frozen=True)
class LoopSettings:
    malformed_turn_limit: int = 4
    protected_rejection_limit: int = 3
    conflict_snippet_chars: int = 120


@dataclass(frozen=True)
class PermissionSettings:
    external_directory_policy: str = "ask"
    default_read_policy: str = "ask"
    default_write_policy: str = "ask"
    default_bash_policy: str = "ask"


@dataclass(frozen=True)
class ToolSettings:
    read_max_bytes: int = 256_000
    read_max_output_tokens: int = 12_000
    read_max_pdf_pages: int = 20
    edit_max_file_bytes: int = 512_000
    grep_max_matches: int = 100
    glob_max_matches: int = 100
    list_max_entries: int = 1000
    bash_timeout_ms: int = 120_000
    bash_timeout_max_ms: int = 300_000
    bash_max_output_chars: int = 32_000
    permissions: PermissionSettings = field(default_factory=PermissionSettings)


@dataclass(frozen=True)
class AppSettings:
    codex: CodexSettings = field(default_factory=CodexSettings)
    claude: ClaudeSettings = field(default_factory=ClaudeSettings)
    llama_cpp: LlamaCppSettings = field(default_factory=LlamaCppSettings)
    ollama: OllamaSettings = field(default_factory=OllamaSettings)
    research: ResearchSettings = field(default_factory=ResearchSettings)
    context: ContextSettings = field(default_factory=ContextSettings)
    loop: LoopSettings = field(default_factory=LoopSettings)
    tools: ToolSettings = field(default_factory=ToolSettings)
    package_name: str = "aunic"


SETTINGS = AppSettings()
