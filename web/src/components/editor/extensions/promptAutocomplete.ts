import {
  autocompletion,
  type Completion,
  type CompletionContext,
  type CompletionResult,
} from "@codemirror/autocomplete";
import type { Extension } from "@codemirror/state";
import { AT_COMMANDS, SLASH_COMMANDS } from "../../../promptCommands";

const SLASH_COMPLETIONS: Completion[] = SLASH_COMMANDS.map((label) => ({
  label,
  type: "keyword",
}));

const AT_COMPLETIONS: Completion[] = AT_COMMANDS.map((label) => ({
  label,
  type: "keyword",
}));

const MARKER_COMPLETIONS: Completion[] = [
  "@>>",
  "<<@",
  "!>>",
  "<<!",
  "%>>",
  "<<%",
  "$>>",
  "<<$",
  ">>",
  "<<",
].map((label) => ({
  label,
  type: "constant",
}));

export function promptAutocomplete(): Extension {
  return autocompletion({
    activateOnTyping: true,
    override: [promptCompletionSource],
  });
}

function promptCompletionSource(context: CompletionContext): CompletionResult | null {
  const token = context.matchBefore(/(?:[/@][\w-]*|[!%$@]?>>|<<[!%$@]?)$/);
  if (!token) {
    return null;
  }
  if (!context.explicit && token.from === token.to) {
    return null;
  }
  if (!startsAtPromptBoundary(context, token.from)) {
    return null;
  }

  const text = token.text;
  if (text.startsWith("/")) {
    return {
      from: token.from,
      options: SLASH_COMPLETIONS,
      validFor: /^\/[\w-]*$/,
    };
  }
  if (text.startsWith("@") && !text.endsWith(">>")) {
    return {
      from: token.from,
      options: AT_COMPLETIONS,
      validFor: /^@[\w-]*$/,
    };
  }
  return {
    from: token.from,
    options: MARKER_COMPLETIONS,
    validFor: /^[!%$@]?(?:>>|$)|^<<[!%$@]?$/,
  };
}

function startsAtPromptBoundary(context: CompletionContext, from: number): boolean {
  if (from === 0) {
    return true;
  }
  const previous = context.state.doc.sliceString(from - 1, from);
  return /\s|[([{]/.test(previous);
}
