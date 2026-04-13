# Tool-Only MCP Support For Aunic

## Summary
- Add Aunic as an MCP **client** for external MCP servers, scoped to tools only.
- Follow the reference repo’s proven pattern: discover MCP configs, connect servers, list tools, wrap each MCP tool as an internal tool, namespace names as `mcp__server__tool`, merge with built-ins, and call back through the MCP session.
- Do not implement MCP resources, prompts, skills/commands, OAuth flows, marketplace/plugin UI, or full server management in this pass.

## Key Changes
- Add MCP config loading from `.aunic/mcp.json` using the existing Aunic ancestor/fallback style:
  - Supported schema: `{ "mcpServers": { "<name>": { ...serverConfig } } }`.
  - Supported server configs: `stdio`, `http`, and `sse`, matching the Python MCP SDK already available in the environment.
  - `stdio` accepts `command`, `args`, `env`, optional `cwd`.
  - `http` and `sse` accept `url`, optional `headers`.
  - Environment placeholders like `${VAR}` are expanded conservatively; missing variables produce a clear disabled-server error.
  - Server names are normalized to API-safe names using the reference behavior: replace non `[a-zA-Z0-9_-]` characters with `_`.

- Add an internal MCP client subsystem, likely under [src/aunic/mcp](/home/ejumps/HalfaCloud/Aunic/src/aunic/mcp):
  - `config.py`: parse/validate `.aunic/mcp.json`, resolve config location, expand env vars.
  - `client.py`: manage Python MCP `ClientSession` lifecycle for stdio, streamable HTTP, and SSE.
  - `tools.py`: convert listed MCP tools into Aunic `ToolDefinition`s.
  - `names.py`: build/parse `mcp__<server>__<tool>` names and preserve original MCP names in metadata.

- Wrap external MCP tools as normal Aunic tools:
  - Tool spec name: `mcp__<normalized_server>__<normalized_tool>`.
  - Tool description: MCP tool description, capped to a practical limit like the reference repo’s 2048 chars.
  - Tool input schema: pass through MCP `inputSchema`; if missing or invalid, fallback to permissive object schema.
  - Tool execution: call `session.call_tool(original_tool_name, arguments)` and convert the result to `ToolExecutionResult`.
  - Tool result conversion: text content becomes text, structured content becomes JSON text plus metadata, unsupported/binary/resource-like content becomes a readable placeholder for v1.
  - MCP `isError` results become Aunic `tool_error` rows with `ToolFailure(category="execution_error")`.

- Merge MCP tools into Aunic’s existing registries:
  - Add async registry assembly for note and chat runs so built-ins are collected first, then MCP tools are appended.
  - Built-in tool names win on conflict; MCP names are namespaced so practical conflicts should be rare.
  - Note mode and chat mode both receive MCP tools unless a server/tool is disabled by config or permission policy.
  - Keep explicit non-TUI `note`, `chat`, and existing provider behavior unchanged except for the expanded tool list.

- Integrate with current provider paths:
  - OpenAI-compatible provider should work naturally once MCP tools are in `ProviderRequest.tools`.
  - Refactor `AunicToolBridge` so Claude/Codex SDK paths use the same expanded registry rather than rebuilding only built-ins internally.
  - Keep Aunic’s existing internal MCP server bridge for Claude/Codex, but have that bridge expose both built-in Aunic tools and external MCP-backed tool wrappers.

- Add permission and safety behavior:
  - Default external MCP tool policy is `ask`, regardless of work mode.
  - Existing `proto-settings.json` tool policy overrides apply to fully qualified names like `mcp__github__create_issue`, and server-wide prefix matching should be supported if practical.
  - Permission prompt details should include server name, original MCP tool name, normalized tool name, and arguments.
  - A denied MCP server should be omitted from the model-visible tool list when statically configured as denied.

- Add transcript/runtime behavior:
  - MCP tool calls/results render through the existing generic tool transcript path in v1.
  - Store metadata with server name, original tool name, normalized tool name, transport type, and elapsed time.
  - Startup/list/call failures should produce clear tool errors or run events, not crash the TUI.

## Test Plan
- Config tests:
  - Parses valid `.aunic/mcp.json` with stdio/http/sse servers.
  - Rejects malformed configs with clear errors.
  - Expands environment variables and reports missing variables.
  - Normalizes server/tool names consistently.
  - Finds the intended nearest/fallback `.aunic` config location.

- MCP client tests:
  - Uses a small fake stdio MCP server to verify initialize, list tools, call tool, and shutdown.
  - Verifies HTTP/SSE client construction with mocked transports or a lightweight local test server.
  - Handles server startup failure, list-tools failure, call timeout, and MCP `isError`.

- Tool adapter tests:
  - Converts MCP tools into `ToolDefinition`s with names `mcp__server__tool`.
  - Preserves original server/tool names in metadata.
  - Passes through schemas and falls back safely for malformed schemas.
  - Converts text, structured, empty, and error results correctly.

- Registry/provider tests:
  - Note mode registry includes built-ins plus MCP tools.
  - Chat mode registry includes chat tools plus MCP tools.
  - OpenAI-compatible requests include MCP tool specs.
  - Claude/Codex `AunicToolBridge` exposes MCP-backed tools through the existing SDK MCP bridge.
  - Denied MCP tools are not exposed or cannot execute, depending on policy timing.

- TUI/runtime regression tests:
  - MCP tool rows persist to transcript like normal tool calls/results.
  - Permission ask/allow/deny flows work for MCP tools.
  - A failed MCP server does not prevent normal Aunic tools from working.
  - Existing note/chat/read/write/bash/research tools remain unchanged.

## Assumptions And Defaults
- Tool-only MCP means no resources, prompts, subscriptions, sampling, roots UI, OAuth, marketplace/plugin management, or MCP server setup commands in this pass.
- Supported v1 transports are `stdio`, `http`, and `sse` because the installed Python MCP SDK supports them directly.
- MCP tools are available in both note and chat mode by default, but default to `ask` permissions.
- Aunic config uses `.aunic/mcp.json`, not the reference repo’s `.mcp.json`, to stay consistent with Aunic’s existing `.aunic` configuration model.
- Generic transcript rendering is acceptable for v1; custom MCP-specific row rendering can be added later.
