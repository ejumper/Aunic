# Remove `text_tool_blocks` and Move to Native Llama + SDK Tool Adapters

## Summary
- Narrow the provider stack to three supported lanes for now:
  - `llama` = llama.cpp OpenAI-compatible endpoint
  - `codex` = Codex SDK/app-server lane
  - `claude` = Claude Code SDK lane
- Remove all markdown-based `text_tool_blocks` fallback logic. If a lane cannot produce structured tool use through its native/API/SDK path, it should fail explicitly rather than degrading to fenced markdown parsing.
- Treat the transcript table as the single durable source of truth. `llama` should continue using the OpenAI-compatible translation path; `codex` and `claude` should gain real SDK-side adapters that expose Aunic tools through MCP/native SDK tool surfaces and round-trip tool events back into `ProviderResponse` / transcript rows.
- Keep adapter selection lane-driven. Do not infer transport from the model string.

## Important Interface Changes
- Keep the current user-facing provider names for now: `llama`, `codex`, `claude`.
- Replace provider-name-based transcript translation dispatch with an explicit adapter capability/protocol declaration.
  - `llama` declares `openai_compatible`
  - `claude` declares `claude_code_sdk`
  - `codex` declares `codex_sdk`
- Remove `ToolInvocationStrategy.text_tool_blocks` and related fallback/session-budget settings from config and provider request handling.
- Add provider metadata fields that make the active transport inspectable in usage logs and debug output, for example:
  - `transport`
  - `session_reused`
  - `history_seeded`
  - `tool_runtime`
- If an SDK lane cannot initialize its MCP/native tool surface, raise a provider configuration/runtime error immediately. Do not silently switch to prompt-serialized tool calling.

## Implementation Changes
- **Provider core cleanup**
  - Delete the fenced-markdown tool block builders/parsers and all fallback branches that depend on them.
  - Simplify provider generation flow so each lane has exactly one structured tool path.
  - Keep only the shared helpers that still make sense after fallback removal, such as extraction/metadata helpers that are not text-block-specific.

- **Transcript translation hardening**
  - Verify the existing OpenAI-compatible transcript translation is the canonical path for llama.cpp and keep it.
  - Verify the Anthropic-style translation remains correct as a canonical intermediate form for Claude-facing history reconstruction where useful.
  - Decouple translation choice from provider string matching; each adapter should explicitly choose the translation/protocol it uses.
  - Add or tighten tests around:
    - grouped assistant text + tool calls
    - tool results vs tool errors
    - final combined note-snapshot + user-prompt message
    - role alternation constraints

- **`llama` lane**
  - Confirm the current native llama.cpp path is already using OpenAI-compatible messages plus real `tools` / `tool_choice`.
  - Remove any remaining `text_tool_blocks` code path from the llama provider and tests.
  - Keep llama out of live smoke scope for now; only unit/integration verification in this pass.

- **`codex` SDK lane**
  - Rework the current Codex provider so it no longer relies on `outputSchema` JSON or text-block fallback as its tool bridge.
  - Use the Codex runtime’s MCP/tool integration surface to expose Aunic tools.
  - Extend the app-server client/session layer to:
    - configure/register the Aunic MCP tool server for the session
    - parse raw response/tool items into structured `ProviderResponse.text` and `ProviderResponse.tool_calls`
    - preserve provider-side session/thread reuse across Aunic loop turns via `run_session_id`
  - Seed transcript history once per Aunic run into the live Codex session, then continue incrementally with only new user/tool turns for token efficiency.
  - Normalize SDK-emitted tool events back into the same vendor-neutral transcript rows Aunic already uses.

- **`claude` SDK lane**
  - Rework the Claude provider so it no longer relies on prompt-serialized structured JSON or markdown text blocks.
  - Configure Claude Code SDK with Aunic tools via MCP/SDK tool registration and a locked-down allowed-tool set.
  - Extend the Claude SDK client/session layer to:
    - initialize the session with the Aunic MCP tool server
    - capture structured assistant/tool-use/tool-result events from the SDK stream instead of only concatenating text blocks
    - preserve session reuse across loop turns via `run_session_id`
  - Seed transcript history once per Aunic run into the live Claude session, then continue incrementally for token efficiency.
  - Normalize SDK tool-use events into `ProviderResponse.tool_calls` so the loop and transcript writer remain vendor-neutral.

- **Aunic MCP tool runtime**
  - Introduce one shared Aunic MCP server/bridge that exposes the same tool registry Aunic already owns.
  - Use the same tool definitions and execution layer as the normal loop; the SDK adapters should not fork tool behavior.
  - Ensure SDK lanes can restrict the exposed tool set to the current mode/work-mode exactly the same way the existing provider loop does.

- **Selection and logging**
  - Make the selected lane the only determinant of adapter/translation choice.
  - Update the TUI/CLI/provider factory only as needed so current `llama` / `codex` / `claude` selections map to the revised lanes.
  - Record enough metadata in `.aunic/usage/*.jsonl` to tell which transport ran and whether the session was reseeded or reused.

## Test Plan
- **Unit / non-live**
  - Transcript translation tests prove the OpenAI-compatible and Anthropic-style translators still produce correct message shapes from the markdown transcript table.
  - Provider tests prove `codex`, `claude`, and `llama` no longer reference or exercise `text_tool_blocks`.
  - Codex provider tests cover SDK raw-event parsing into `ProviderResponse.tool_calls`.
  - Claude provider tests cover SDK event parsing into `ProviderResponse.tool_calls`.
  - Session reuse tests prove SDK lanes seed history once per Aunic run and then continue incrementally.
  - Failure tests prove missing SDK MCP/tool setup fails explicitly instead of falling back.

- **Live smoke**
  - Run one very simple `codex` note-mode prompt and one very simple Claude Haiku note-mode prompt.
  - Inspect the resulting transcript/note behavior to confirm tool calls round-trip correctly and note-mode writes to the note rather than chat.
  - Inspect `.aunic/usage/2026-04-03.jsonl` and confirm:
    - the correct transport metadata is recorded for each run
    - token usage is broadly reasonable
    - subsequent turns within the same run do not look like full-history replay every time
  - Do not run a live llama.cpp smoke in this pass.

## Assumptions and Defaults
- Keep the current provider labels for now: `llama`, `codex`, `claude`.
- `llama` remains the only OpenAI-compatible API lane in scope right now.
- No new direct Anthropic API or direct OpenAI HTTP provider lanes are part of this pass.
- Lane selection decides the adapter; model names only configure the selected lane.
- If Codex SDK or Claude Code SDK lack a usable MCP/native tool surface for a required scenario, the lane should error clearly rather than reintroducing any markdown text-tool bridge.
