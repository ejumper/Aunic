import { indentUnit } from "@codemirror/language";
import { Annotation, EditorState, type ChangeSpec, type Extension } from "@codemirror/state";

const sequenceHandled = Annotation.define<boolean>();

const isTouchPrimary =
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia("(pointer: coarse)").matches;

// On mobile, typing >| at the start of a line adds one level of tab indentation.
// Typing |< removes one level.
export function mobileIndentTriggers(): Extension {
  if (!isTouchPrimary) return [];

  return EditorState.transactionFilter.of((tr) => {
    if (!tr.docChanged || tr.annotation(sequenceHandled)) return tr;

    const unit = tr.startState.facet(indentUnit);
    const changes: ChangeSpec[] = [];
    const seen = new Set<number>();

    tr.changes.iterChangedRanges((_fromA, _toA, fromB, toB) => {
      const fromLine = tr.newDoc.lineAt(fromB);
      const toLine = tr.newDoc.lineAt(Math.max(fromB, toB));
      for (let ln = fromLine.number; ln <= toLine.number; ln++) {
        if (seen.has(ln) || ln > tr.newDoc.lines) continue;
        seen.add(ln);
        const line = tr.newDoc.line(ln);
        const change = detectTrigger(line.from, line.text, unit);
        if (change) changes.push(change);
      }
    });

    if (changes.length === 0) return tr;
    return [tr, { changes, annotations: sequenceHandled.of(true), sequential: true }];
  });
}

// Kept for backwards-compat with existing imports.
export { mobileIndentTriggers as fourSpaceIndent };

function detectTrigger(lineFrom: number, text: string, unit: string): ChangeSpec | null {
  const indent = /^(\s*)>\|$/.exec(text);
  if (indent) {
    return { from: lineFrom, to: lineFrom + text.length, insert: indent[1] + unit };
  }

  const outdent = /^(\s*)\|<$/.exec(text);
  if (outdent) {
    const leading = outdent[1];
    const trimmed = leading.endsWith(unit)
      ? leading.slice(0, -unit.length)
      : leading.slice(0, -1);
    return { from: lineFrom, to: lineFrom + text.length, insert: trimmed };
  }

  return null;
}
