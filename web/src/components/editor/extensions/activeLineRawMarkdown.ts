import { syntaxTree } from "@codemirror/language";
import { RangeSetBuilder, type Text } from "@codemirror/state";
import {
  Decoration,
  EditorView,
  ViewPlugin,
  WidgetType,
  type DecorationSet,
  type ViewUpdate,
} from "@codemirror/view";

export function activeLineRawMarkdown() {
  return ViewPlugin.fromClass(
    class {
      decorations: DecorationSet;

      constructor(view: EditorView) {
        this.decorations = buildDecorations(view);
      }

      update(update: ViewUpdate) {
        if (update.docChanged || update.viewportChanged || update.selectionSet) {
          this.decorations = buildDecorations(update.view);
        }
      }
    },
    {
      decorations: (plugin) => plugin.decorations,
    },
  );
}

type PendingDecoration = {
  from: number;
  to: number;
} & ({ decoration: Decoration } | { kind: "hidden" });

type FencedLineKind = "start" | "content" | "end";
type FencedLineInfo = { kind: FencedLineKind; maxColumns: number };

const TAB_VISUAL_COLUMNS = 3;

class PageBreakWidget extends WidgetType {
  toDOM(): HTMLElement {
    const element = document.createElement("span");
    element.className = "cm-aunic-page-break";
    return element;
  }
}

const hiddenMarkupDecoration = Decoration.replace({});

function buildDecorations(view: EditorView): DecorationSet {
  const builder = new RangeSetBuilder<Decoration>();
  const activeLines = activeLineStarts(view);
  const tree = syntaxTree(view.state);
  const fencedLines = collectFencedCodeLines(view.state.doc);

  for (const { from, to } of view.visibleRanges) {
    let pos = from;
    while (pos <= to) {
      const line = view.state.doc.lineAt(pos);
      const isActive = activeLines.has(line.from);
      const fencedLine = fencedLines.get(line.from);
      if (isActive) {
        const decorations = activeLineDecorations(line.from, line.text);
        // Apply inline code styling (unrendered: backticks visible but green-styled)
        if (!isInsideCodeBlock(tree, line.from)) {
          const inlineCodeRanges = collectInlineCodeRanges(tree, line.from, line.to);
          addInlineCodeDecorations(decorations, line.from, line.text, inlineCodeRanges, false);
        }
        // Apply code block background even when cursor is on the fenced line
        if (fencedLine) {
          const fenceDecorations = fencedCodeLineDecorations(
            line.from,
            line.text,
            fencedLine.kind,
            fencedLine.maxColumns,
            true,
          );
          decorations.push(...fenceDecorations);
        }
        decorations
          .sort((left, right) => left.from - right.from || left.to - right.to)
          .forEach((item) =>
            builder.add(
              item.from,
              item.to,
              "kind" in item ? hiddenMarkupDecoration : item.decoration,
            ),
          );
      } else if (fencedLine) {
        const decorations = fencedCodeLineDecorations(
          line.from,
          line.text,
          fencedLine.kind,
          fencedLine.maxColumns,
          false,
        );
        decorations
          .sort((left, right) => left.from - right.from || left.to - right.to)
          .forEach((item) =>
            builder.add(
              item.from,
              item.to,
              "kind" in item ? hiddenMarkupDecoration : item.decoration,
            ),
          );
      } else if (!isInsideCodeBlock(tree, line.from)) {
        const inlineCodeRanges = collectInlineCodeRanges(tree, line.from, line.to);
        const decorations = lineDecorations(line.from, line.text, inlineCodeRanges);
        decorations
          .sort((left, right) => left.from - right.from || left.to - right.to)
          .forEach((item) =>
            builder.add(
              item.from,
              item.to,
              "kind" in item ? hiddenMarkupDecoration : item.decoration,
            ),
          );
      }
      if (line.to >= to) {
        break;
      }
      pos = line.to + 1;
    }
  }

  return builder.finish();
}

function activeLineDecorations(lineStart: number, text: string): PendingDecoration[] {
  const heading = headingMatch(text);
  if (!heading) {
    return [];
  }
  return [
    {
      from: lineStart,
      to: lineStart,
      decoration: Decoration.line({ class: `cm-aunic-heading-${heading[1].length}` }),
    },
  ];
}

function collectFencedCodeLines(doc: Text): Map<number, FencedLineInfo> {
  const result = new Map<number, FencedLineInfo>();
  let fence: string | null = null;
  let blockLines: Array<{ from: number; columns: number; kind: FencedLineKind }> = [];

  for (let lineNo = 1; lineNo <= doc.lines; lineNo++) {
    const line = doc.line(lineNo);
    const fenceMatch = fenceLineMatch(line.text);
    if (!fence) {
      if (!fenceMatch) {
        continue;
      }
      fence = fenceMatch[1];
      blockLines = [
        { from: line.from, columns: codeBlockVisualColumns(line.text), kind: "start" },
      ];
      continue;
    }

    if (fenceMatch && fenceMatch[1][0] === fence[0] && fenceMatch[1].length >= fence.length) {
      blockLines.push({
        from: line.from,
        columns: codeBlockVisualColumns(line.text),
        kind: "end",
      });
      const maxColumns = Math.max(...blockLines.map((l) => l.columns));
      for (const { from, kind } of blockLines) {
        result.set(from, { kind, maxColumns });
      }
      blockLines = [];
      fence = null;
    } else {
      blockLines.push({
        from: line.from,
        columns: codeBlockVisualColumns(line.text),
        kind: "content",
      });
    }
  }

  return result;
}

export function codeBlockVisualColumns(text: string): number {
  let columns = 0;
  for (const char of text) {
    columns += char === "\t" ? TAB_VISUAL_COLUMNS : 1;
  }
  return columns;
}

function fenceLineMatch(text: string): RegExpExecArray | null {
  return /^\s*(`{3,}|~{3,})/.exec(text);
}

function isInsideCodeBlock(tree: ReturnType<typeof syntaxTree>, pos: number): boolean {
  let node = tree.resolve(pos, 1);
  while (true) {
    if (node.name === "FencedCode" || node.name === "CodeBlock") return true;
    const parent = node.parent;
    if (!parent) break;
    node = parent;
  }
  return false;
}

function collectInlineCodeRanges(
  tree: ReturnType<typeof syntaxTree>,
  from: number,
  to: number,
): Array<[number, number]> {
  const ranges: Array<[number, number]> = [];
  tree.iterate({
    from,
    to,
    enter(node) {
      if (node.name === "InlineCode") {
        ranges.push([node.from, node.to]);
        return false;
      }
    },
  });
  return ranges;
}

function activeLineStarts(view: EditorView): Set<number> {
  const starts = new Set<number>();
  for (const range of view.state.selection.ranges) {
    starts.add(view.state.doc.lineAt(range.head).from);
  }
  return starts;
}

function lineDecorations(
  lineStart: number,
  text: string,
  inlineCodeRanges: Array<[number, number]>,
): PendingDecoration[] {
  const decorations: PendingDecoration[] = [];
  const heading = headingMatch(text);
  if (heading) {
    const level = heading[1].length;
    decorations.push({
      from: lineStart,
      to: lineStart,
      decoration: Decoration.line({ class: `cm-aunic-heading-${level}` }),
    });
    addHidden(decorations, lineStart, 0, heading[1].length + heading[2].length);
  }

  if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(text)) {
    decorations.push({
      from: lineStart,
      to: lineStart + text.length,
      decoration: Decoration.replace({ widget: new PageBreakWidget() }),
    });
    return decorations;
  }
  if (looksLikeTableLine(text)) {
    decorations.push({
      from: lineStart,
      to: lineStart,
      decoration: Decoration.line({ class: "cm-aunic-table-line" }),
    });
  }

  addPairedTokenHides(decorations, lineStart, text, "***", inlineCodeRanges);
  addPairedTokenHides(decorations, lineStart, text, "___", inlineCodeRanges);
  addPairedTokenHides(decorations, lineStart, text, "**", inlineCodeRanges);
  addPairedTokenHides(decorations, lineStart, text, "__", inlineCodeRanges);
  addPairedTokenHides(decorations, lineStart, text, "*", inlineCodeRanges);
  addPairedTokenHides(decorations, lineStart, text, "_", inlineCodeRanges);
  addInlineCodeDecorations(decorations, lineStart, text, inlineCodeRanges, true);
  addLinkHides(decorations, lineStart, text, inlineCodeRanges);
  return decorations;
}

function headingMatch(text: string): RegExpExecArray | null {
  return /^(#{1,6})(\s+)/.exec(text);
}

function fencedCodeLineDecorations(
  lineStart: number,
  text: string,
  kind: FencedLineKind,
  maxColumns: number,
  isActive: boolean,
): PendingDecoration[] {
  const decorations: PendingDecoration[] = [
    {
      from: lineStart,
      to: lineStart,
      decoration: Decoration.line({
        class: `cm-aunic-code-block-line cm-aunic-code-block-line--${kind}`,
        attributes: { style: `--code-block-max-cols: ${maxColumns}` },
      }),
    },
  ];
  // In rendered state, make fence markers invisible while preserving line height.
  // visibility:hidden (cm-aunic-hidden-fence) is used instead of display:none so the
  // line doesn't collapse to zero height when the only content is the backtick sequence.
  if (!isActive && (kind === "start" || kind === "end")) {
    const match = fenceLineMatch(text);
    if (match && match.index < match.index + match[1].length) {
      decorations.push({
        from: lineStart + match.index,
        to: lineStart + match.index + match[1].length,
        decoration: Decoration.mark({ class: "cm-aunic-hidden-fence" }),
      });
    }
  }
  return decorations;
}

function isInInlineCode(absPos: number, ranges: Array<[number, number]>): boolean {
  return ranges.some(([from, to]) => absPos >= from && absPos < to);
}

function addInlineCodeDecorations(
  decorations: PendingDecoration[],
  lineStart: number,
  text: string,
  inlineCodeRanges: Array<[number, number]>,
  hideMarkup: boolean,
): void {
  for (const [from, to] of inlineCodeRanges) {
    const localFrom = from - lineStart;
    const localTo = to - lineStart;
    const ticks = inlineCodeTickLength(text, localFrom, localTo);
    if (ticks <= 0 || localFrom + ticks > localTo - ticks) {
      continue;
    }
    if (hideMarkup) {
      addHidden(decorations, lineStart, localFrom, localFrom + ticks);
      addHidden(decorations, lineStart, localTo - ticks, localTo);
    }
    // Rendered: mark only the content (no ticks). Unrendered: mark full range (ticks visible but styled).
    decorations.push({
      from: lineStart + localFrom + (hideMarkup ? ticks : 0),
      to: lineStart + localTo - (hideMarkup ? ticks : 0),
      decoration: Decoration.mark({ class: "cm-aunic-inline-code" }),
    });
  }
}

function inlineCodeTickLength(text: string, from: number, to: number): number {
  let ticks = 0;
  while (from + ticks < to && text[from + ticks] === "`") {
    ticks++;
  }
  if (ticks === 0) {
    return 0;
  }
  for (let index = 0; index < ticks; index++) {
    if (text[to - ticks + index] !== "`") {
      return 0;
    }
  }
  return ticks;
}

function addPairedTokenHides(
  decorations: PendingDecoration[],
  lineStart: number,
  text: string,
  token: string,
  inlineCodeRanges: Array<[number, number]>,
): void {
  let searchFrom = 0;
  while (searchFrom < text.length) {
    const open = text.indexOf(token, searchFrom);
    if (open < 0) {
      return;
    }
    const close = text.indexOf(token, open + token.length);
    if (close < 0) {
      return;
    }
    if (token.length === 1 && isPartOfDoubleToken(text, open, token)) {
      searchFrom = open + 1;
      continue;
    }
    if (token.length === 1 && isPartOfDoubleToken(text, close, token)) {
      searchFrom = close + 1;
      continue;
    }
    if (
      !isInInlineCode(lineStart + open, inlineCodeRanges) &&
      !isInInlineCode(lineStart + close, inlineCodeRanges)
    ) {
      addHidden(decorations, lineStart, open, open + token.length);
      addHidden(decorations, lineStart, close, close + token.length);
    }
    searchFrom = close + token.length;
  }
}

function addLinkHides(
  decorations: PendingDecoration[],
  lineStart: number,
  text: string,
  inlineCodeRanges: Array<[number, number]>,
): void {
  const pattern = /\[([^\]]+)\]\(([^)]+)\)/g;
  for (let match = pattern.exec(text); match; match = pattern.exec(text)) {
    const openBracket = match.index;
    if (isInInlineCode(lineStart + openBracket, inlineCodeRanges)) continue;
    const closeBracket = openBracket + match[1].length + 1;
    addHidden(decorations, lineStart, openBracket, openBracket + 1);
    addHidden(decorations, lineStart, closeBracket, match.index + match[0].length);
  }
}

function addHidden(
  decorations: PendingDecoration[],
  lineStart: number,
  from: number,
  to: number,
): void {
  if (from >= to) {
    return;
  }
  let mergedFrom = lineStart + from;
  let mergedTo = lineStart + to;
  for (let index = decorations.length - 1; index >= 0; index -= 1) {
    const item = decorations[index];
    if (!("kind" in item) || item.kind !== "hidden") {
      continue;
    }
    if (item.to < mergedFrom || item.from > mergedTo) {
      continue;
    }
    mergedFrom = Math.min(mergedFrom, item.from);
    mergedTo = Math.max(mergedTo, item.to);
    decorations.splice(index, 1);
  }
  decorations.push({
    from: mergedFrom,
    to: mergedTo,
    kind: "hidden",
  });
}

function isPartOfDoubleToken(text: string, index: number, token: string): boolean {
  return text[index - 1] === token || text[index + 1] === token;
}

function looksLikeTableLine(text: string): boolean {
  const trimmed = text.trim();
  return trimmed.includes("|") && !trimmed.startsWith("```");
}
