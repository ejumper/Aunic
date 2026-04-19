export const SLASH_COMMANDS = [
  "/context",
  "/note",
  "/chat",
  "/work",
  "/read",
  "/off",
  "/model",
  "/find",
  "/replace",
  "/include",
  "/exclude",
  "/isolate",
  "/map",
  "/clear-history",
] as const;

export const AT_COMMANDS = ["@web", "@rag"] as const;

export const PROMPT_COMMANDS = [...SLASH_COMMANDS, ...AT_COMMANDS] as const;

export type PromptCommand = (typeof PROMPT_COMMANDS)[number] | `@${string}`;

const COMMAND_SET = new Set<string>(PROMPT_COMMANDS);

const COMMAND_RE = new RegExp(
  `(${PROMPT_COMMANDS.map(escapeRegExp).sort((left, right) => right.length - left.length).join("|")})\\b`,
  "g",
);

export interface PromptCommandMatch {
  command: PromptCommand;
  from: number;
  to: number;
  remaining: string;
}

export function parsePromptCommand(text: string): PromptCommandMatch | null {
  COMMAND_RE.lastIndex = 0;
  const match = COMMAND_RE.exec(text);
  if (!match) {
    return null;
  }
  const command = match[0] as PromptCommand;
  const from = match.index;
  const to = from + command.length;
  return {
    command,
    from,
    to,
    remaining: `${text.slice(0, from)}${text.slice(to)}`.trim(),
  };
}

export function isKnownPromptCommand(token: string): boolean {
  return COMMAND_SET.has(token);
}

export function promptCommandPattern(): RegExp {
  return new RegExp(COMMAND_RE.source, "g");
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
