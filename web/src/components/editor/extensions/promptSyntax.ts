import { RangeSetBuilder, type Extension } from "@codemirror/state";
import {
  Decoration,
  type DecorationSet,
  EditorView,
  ViewPlugin,
  type ViewUpdate,
} from "@codemirror/view";
import { isKnownPromptCommand, promptCommandPattern } from "../../../promptCommands";

const commandDecoration = Decoration.mark({ class: "cm-prompt-slash" });
const atDecoration = Decoration.mark({ class: "cm-prompt-at" });
const markerDecoration = Decoration.mark({ class: "cm-prompt-marker" });

const COMMAND_RE = promptCommandPattern();
const MARKER_RE = /@>>|<<@|!>>|<<!|%>>|<<%|\$>>|<<\$|>>|<</g;

type PendingDecoration = {
  from: number;
  to: number;
  decoration: Decoration;
};

export function promptSyntax(): Extension {
  return ViewPlugin.fromClass(
    class {
      decorations: DecorationSet;

      constructor(view: EditorView) {
        this.decorations = buildDecorations(view);
      }

      update(update: ViewUpdate) {
        if (update.docChanged || update.viewportChanged) {
          this.decorations = buildDecorations(update.view);
        }
      }
    },
    {
      decorations: (plugin) => plugin.decorations,
    },
  );
}

function buildDecorations(view: EditorView): DecorationSet {
  const pending: PendingDecoration[] = [];
  for (const range of view.visibleRanges) {
    const text = view.state.doc.sliceString(range.from, range.to);
    collectCommandDecorations(text, range.from, pending);
    collectMarkerDecorations(text, range.from, pending);
  }

  pending.sort((left, right) => left.from - right.from || left.to - right.to);
  const builder = new RangeSetBuilder<Decoration>();
  let lastTo = -1;
  for (const item of pending) {
    if (item.from < item.to && item.from >= lastTo) {
      builder.add(item.from, item.to, item.decoration);
      lastTo = item.to;
    }
  }
  return builder.finish();
}

function collectCommandDecorations(
  text: string,
  offset: number,
  pending: PendingDecoration[],
): void {
  for (const match of text.matchAll(COMMAND_RE)) {
    const token = match[0] ?? "";
    if (!isKnownPromptCommand(token)) {
      continue;
    }
    const from = offset + (match.index ?? 0);
    if (token.startsWith("@") && viewTextBefore(offset, text, match.index ?? 0).trim()) {
      continue;
    }
    pending.push({
      from,
      to: from + token.length,
      decoration: token.startsWith("@") ? atDecoration : commandDecoration,
    });
  }
}

function viewTextBefore(offset: number, visibleText: string, matchIndex: number): string {
  if (offset !== 0) {
    return "non-empty";
  }
  return visibleText.slice(0, matchIndex);
}

function collectMarkerDecorations(
  text: string,
  offset: number,
  pending: PendingDecoration[],
): void {
  for (const match of text.matchAll(MARKER_RE)) {
    const token = match[0];
    const from = offset + (match.index ?? 0);
    pending.push({
      from,
      to: from + token.length,
      decoration: markerDecoration,
    });
  }
}
