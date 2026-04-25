import { Prec, type Extension } from "@codemirror/state";
import { insertNewlineAndIndent } from "@codemirror/commands";
import { keymap } from "@codemirror/view";

const MARKER_ONLY_RE = /^\s*(?:@>>|<<@|!>>|<<!|%>>|<<%|\$>>|<<\$)\s*$/;
const UNINDENTED_STRONG_RE = /^\*{2,3}(?!\*)(?=\S)/;

export function aunicMarkerEnterProtection(): Extension {
  return Prec.highest(
    keymap.of([
      {
        key: "Enter",
        run(view) {
          if (!selectionShouldBypassMarkdownContinuation(view.state)) {
            return false;
          }
          return insertNewlineAndIndent(view);
        },
      },
    ]),
  );
}

export function isAunicMarkerOnlyLine(text: string): boolean {
  return MARKER_ONLY_RE.test(text);
}

export function isUnindentedStrongLine(text: string): boolean {
  return UNINDENTED_STRONG_RE.test(text);
}

function selectionShouldBypassMarkdownContinuation(
  state: Parameters<typeof insertNewlineAndIndent>[0]["state"],
): boolean {
  for (const range of state.selection.ranges) {
    if (!range.empty) {
      return false;
    }
    const text = state.doc.lineAt(range.head).text;
    if (!isAunicMarkerOnlyLine(text) && !isUnindentedStrongLine(text)) {
      return false;
    }
  }
  return true;
}
