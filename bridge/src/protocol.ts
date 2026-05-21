// Wire protocol between the Aunic Go parent process and this bridge.
// Mirror of aunic/bridge/protocol.go — keep field names in sync.

export interface StartConfig {
  model: string;
  effort?: 'low' | 'medium' | 'high' | 'xhigh' | 'max';
  maxTurns: number;
  systemPrompt: string;
  userPrompt: string;
  builtinTools: string[];
  aunicTools: ToolDef[];
}

export interface ToolDef {
  name: string;
  description: string;
  schema: Record<string, unknown>;
}

export interface ToolResultMsg {
  type: 'tool_result';
  id: string;
  json: string;
  isError: boolean;
}

export interface AbortMsg {
  type: 'abort';
}

export type InboundMsg = ToolResultMsg | AbortMsg;

export interface Event {
  type:
    | 'started'
    | 'thinking'
    | 'text'
    | 'tool_call'
    | 'tool_result'
    | 'tool_call_builtin'
    | 'tool_result_builtin'
    | 'usage'
    | 'end';
  text?: string;
  name?: string;
  id?: string;
  summary?: string;
  args?: string;
  result?: string;
  isError?: boolean;
  inputTokens?: number;
  outputTokens?: number;
  reason?: 'stop' | 'max_turns' | 'error' | 'cancelled';
  message?: string;
}
