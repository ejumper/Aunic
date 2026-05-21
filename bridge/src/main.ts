// Aunic ↔ Claude Agent SDK bridge.
//
// Lifecycle:
//   1. Aunic spawns this process: `node bridge.js`.
//   2. Aunic writes a StartConfig JSON line to our stdin.
//   3. We build the Agent SDK query and emit `started`.
//   4. We iterate the SDK's AsyncGenerator, translating each message to events
//      on stdout for Aunic.
//   5. When the model calls an Aunic tool, the MCP stub handler writes a
//      `tool_call` event and awaits a matching `tool_result` on stdin.
//   6. On `end` (or fatal error), we exit 0.
//   7. On `abort` (or SIGTERM), we cancel via AbortController and exit.

import { createInterface } from 'node:readline';
import {
  createSdkMcpServer,
  query,
  tool,
  type Options,
  type SDKMessage,
} from '@anthropic-ai/claude-agent-sdk';
import { z } from 'zod';

import type {
  Event,
  InboundMsg,
  StartConfig,
  ToolDef,
} from './protocol.js';

// ── Stdio plumbing ────────────────────────────────────────────────────────────

function emit(e: Event): void {
  process.stdout.write(JSON.stringify(e) + '\n');
}

const pendingToolResults = new Map<
  string,
  (r: { json: string; isError: boolean }) => void
>();

let abortController: AbortController | null = null;

function handleInbound(line: string): void {
  let msg: InboundMsg;
  try {
    msg = JSON.parse(line) as InboundMsg;
  } catch {
    return; // ignore garbage
  }
  switch (msg.type) {
    case 'tool_result': {
      const resolve = pendingToolResults.get(msg.id);
      if (resolve) {
        pendingToolResults.delete(msg.id);
        resolve({ json: msg.json, isError: msg.isError });
      }
      return;
    }
    case 'abort': {
      abortController?.abort();
      return;
    }
  }
}

// ── MCP stub-tool registration ────────────────────────────────────────────────

// schemaToZod converts a JSON-Schema object (as emitted by Aunic's tool
// definitions) into a Zod raw shape. The SDK's `tool()` helper takes a Zod
// schema; we don't need full JSON-Schema fidelity, just enough to let the model
// see the property names. Validation happens server-side in Aunic.
function schemaToZod(schema: Record<string, unknown>): Record<string, z.ZodType> {
  const props = (schema.properties ?? {}) as Record<string, { type?: string }>;
  const required = new Set<string>((schema.required as string[]) ?? []);
  const shape: Record<string, z.ZodType> = {};
  for (const [name, def] of Object.entries(props)) {
    let field: z.ZodType;
    switch (def.type) {
      case 'string':
        field = z.string();
        break;
      case 'boolean':
        field = z.boolean();
        break;
      case 'number':
      case 'integer':
        field = z.number();
        break;
      case 'array':
        field = z.array(z.unknown());
        break;
      case 'object':
        field = z.record(z.string(), z.unknown());
        break;
      default:
        field = z.unknown();
    }
    shape[name] = required.has(name) ? field : field.optional();
  }
  return shape;
}

function buildAunicTool(def: ToolDef) {
  return tool(
    def.name,
    def.description,
    schemaToZod(def.schema),
    async (args: unknown) => {
      const id = `aunic-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const argsJSON = JSON.stringify(args);
      emit({ type: 'tool_call', id, name: def.name, args: argsJSON });

      const reply = await new Promise<{ json: string; isError: boolean }>(
        (resolve) => {
          pendingToolResults.set(id, resolve);
        },
      );

      emit({
        type: 'tool_result',
        id,
        name: def.name,
        result: reply.json,
        isError: reply.isError,
        summary: '',
      });

      return {
        content: [{ type: 'text', text: reply.json }],
        isError: reply.isError,
      };
    },
  );
}

// ── Event translation from SDKMessage stream ──────────────────────────────────

function isAunicToolName(name: string, aunicTools: ToolDef[]): boolean {
  // The SDK prefixes MCP-server tools as `mcp__<server-name>__<tool-name>`.
  // We register under server name "aunic", so anything matching that prefix
  // is one of ours.
  if (name.startsWith('mcp__aunic__')) return true;
  // Belt-and-suspenders: also match raw names in case the prefix changes.
  return aunicTools.some((t) => t.name === name);
}

function unprefix(name: string): string {
  const prefix = 'mcp__aunic__';
  return name.startsWith(prefix) ? name.slice(prefix.length) : name;
}

// Maps tool_use block IDs to their tool names so tool_result_builtin events
// can carry the name of the tool that produced each result.
const builtinToolIds = new Map<string, string>();

function processSDKMessage(msg: SDKMessage, aunicTools: ToolDef[]): void {
  switch (msg.type) {
    case 'assistant': {
      // Model output: text, thinking, tool_use blocks.
      const content = msg.message.content;
      if (!Array.isArray(content)) return;
      for (const block of content) {
        const b = block as unknown as { type: string; id?: string; [k: string]: unknown };
        switch (b.type) {
          case 'text':
            emit({ type: 'text', text: String(b.text ?? '') });
            break;
          case 'thinking':
            emit({ type: 'thinking', text: String(b.thinking ?? '') });
            break;
          case 'tool_use': {
            const name = String(b.name ?? '');
            // Aunic tool_use is reported via the MCP stub handler — skip here
            // to avoid duplicate tool_call events. Built-in tools surface
            // through this path only.
            if (isAunicToolName(name, aunicTools)) break;
            if (b.id) builtinToolIds.set(b.id, name);
            emit({
              type: 'tool_call_builtin',
              name,
              args: JSON.stringify(b.input ?? {}),
            });
            break;
          }
        }
      }
      // Token usage from the underlying API response.
      const usage = (msg.message as { usage?: { input_tokens?: number; output_tokens?: number } }).usage;
      if (usage) {
        emit({
          type: 'usage',
          inputTokens: usage.input_tokens ?? 0,
          outputTokens: usage.output_tokens ?? 0,
        });
      }
      return;
    }
    case 'user': {
      // Tool results — surface built-in tool results so Aunic can display them.
      // Aunic-tool results are already emitted by the MCP stub.
      const content = msg.message.content;
      if (!Array.isArray(content)) return;
      for (const block of content) {
        const b = block as unknown as { type: string; [k: string]: unknown };
        if (b.type !== 'tool_result') continue;
        const id = String(b.tool_use_id ?? '');
        const name = builtinToolIds.get(id) ?? '';
        builtinToolIds.delete(id);
        const isError = Boolean(b.is_error);
        const raw = b.content;
        let summary = '';
        if (typeof raw === 'string') {
          summary = raw;
        } else if (Array.isArray(raw)) {
          summary = raw
            .map((r: { type?: string; text?: string }) =>
              r?.type === 'text' ? String(r.text ?? '') : '',
            )
            .join('');
        }
        // Trim to a reasonable preview.
        if (summary.length > 200) summary = summary.slice(0, 200) + '…';
        emit({
          type: 'tool_result_builtin',
          id,
          name,
          summary,
          isError,
        });
      }
      return;
    }
    case 'result': {
      // The SDK signals end-of-run via SDKResultMessage. The result message
      // carries authoritative total usage for the entire run — include it in
      // the end event so Go can use it instead of the accumulated per-turn sum.
      const subtype = String((msg as { subtype?: string }).subtype ?? '');
      let reason: 'stop' | 'max_turns' | 'error' | 'cancelled' = 'stop';
      if (subtype.includes('max_turns')) reason = 'max_turns';
      else if (subtype.includes('error')) reason = 'error';
      const message = String((msg as { result?: string }).result ?? '');
      const resultUsage = (msg as { usage?: { input_tokens?: number; output_tokens?: number } }).usage;
      emit({
        type: 'end',
        reason,
        message,
        ...(resultUsage ? {
          inputTokens: resultUsage.input_tokens ?? 0,
          outputTokens: resultUsage.output_tokens ?? 0,
        } : {}),
      });
      return;
    }
    default:
      return; // ignore system / partial / status / etc.
  }
}

// ── Main entry ────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  // Read StartConfig from stdin (first line).
  const rl = createInterface({ input: process.stdin, terminal: false });
  const startConfig = await new Promise<StartConfig>((resolve, reject) => {
    let first = true;
    rl.on('line', (line: string) => {
      if (first) {
        first = false;
        try {
          resolve(JSON.parse(line) as StartConfig);
        } catch (e) {
          reject(e);
        }
        return;
      }
      handleInbound(line);
    });
    rl.on('close', () => {
      if (first) reject(new Error('stdin closed before StartConfig'));
    });
  });

  // Build the in-process MCP server hosting our Aunic tool stubs.
  const aunicServer = createSdkMcpServer({
    name: 'aunic',
    version: '1.0.0',
    tools: startConfig.aunicTools.map(buildAunicTool),
  });

  abortController = new AbortController();

  // Convert builtinTools to the SDK's `tools` option. Empty array disables
  // all built-ins, which is what `agent: off` mode requires.
  const tools: string[] = startConfig.builtinTools;

  const options: Options = {
    model: startConfig.model,
    effort: startConfig.effort ?? 'medium',
    maxTurns: startConfig.maxTurns,
    systemPrompt: startConfig.systemPrompt,
    tools,
    mcpServers: { aunic: aunicServer },
    persistSession: false,
    abortController,
    includePartialMessages: false,
  };

  emit({ type: 'started' });

  try {
    for await (const msg of query({ prompt: startConfig.userPrompt, options })) {
      processSDKMessage(msg, startConfig.aunicTools);
    }
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    if (abortController.signal.aborted) {
      emit({ type: 'end', reason: 'cancelled', message });
    } else {
      emit({ type: 'end', reason: 'error', message });
    }
    process.exit(0);
  }

  // If we reached here without `result`, emit a generic stop.
  // (Defensive — the SDK normally emits result.)
  emit({ type: 'end', reason: 'stop' });
}

process.on('SIGTERM', () => {
  abortController?.abort();
});

main().catch((e) => {
  const message = e instanceof Error ? e.message : String(e);
  emit({ type: 'end', reason: 'error', message });
  process.exit(1);
});
