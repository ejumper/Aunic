import { Annotation, EditorState, type ChangeSpec, type Extension } from "@codemirror/state";

const sequenceHandled = Annotation.define<boolean>();
export function mobileIndentTriggers(): Extension {
  if (!isTouchPrimary()) return [];

  return EditorState.transactionFilter.of((tr) => {
    if (!tr.docChanged || tr.annotation(sequenceHandled)) return tr;

    const changes: ChangeSpec[] = [];
    const seen = new Set<number>();
    const selectionHeads = tr.newSelection.ranges.map((range) => range.head);

    tr.changes.iterChangedRanges((_fromA, _toA, fromB, toB) => {
      const fromLine = tr.newDoc.lineAt(fromB);
      const toLine = tr.newDoc.lineAt(Math.max(fromB, toB));
      for (let ln = fromLine.number; ln <= toLine.number; ln++) {
        if (seen.has(ln) || ln > tr.newDoc.lines) continue;
        seen.add(ln);
        const line = tr.newDoc.line(ln);
        const change = detectTrigger(line.from, line.text, selectionHeads);
        if (change) changes.push(change);
      }
    });

    if (changes.length === 0) return tr;
    return [tr, { changes, annotations: sequenceHandled.of(true), sequential: true }];
  });
}

// Kept for backwards-compat with existing imports.
export { mobileIndentTriggers as fourSpaceIndent };

function isTouchPrimary(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(pointer: coarse)").matches
  );
}

export function detectTrigger(
  lineFrom: number,
  text: string,
  selectionHeads: readonly number[],
): ChangeSpec | null {
  const leading = /^([\t ]*)/.exec(text)?.[1] ?? "";
  if (!leading.includes("    ")) {
    return null;
  }

  const leadingEnd = lineFrom + leading.length;
  const caretInLeadingWhitespace = selectionHeads.some(
    (head) => head >= lineFrom && head <= leadingEnd,
  );
  if (!caretInLeadingWhitespace) {
    return null;
  }

  const normalized = normalizeLeadingSpacesToTabs(leading);
  if (normalized === leading) {
    return null;
  }

  return { from: lineFrom, to: lineFrom + leading.length, insert: normalized };
}

export function normalizeLeadingSpacesToTabs(leading: string): string {
  return leading.replace(/ {4}/g, "\t");
}
