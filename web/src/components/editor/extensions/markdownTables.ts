import type { Text } from "@codemirror/state";
import { RangeSetBuilder, StateField, type EditorState } from "@codemirror/state";
import {
  Decoration,
  EditorView,
  WidgetType,
  type DecorationSet,
} from "@codemirror/view";

interface TableBlock {
  from: number;
  to: number;
  rawLines: string[];
}

class MarkdownTableWidget extends WidgetType {
  constructor(
    readonly rawLines: string[],
    readonly blockFrom: number,
  ) {
    super();
  }

  eq(other: MarkdownTableWidget): boolean {
    return (
      this.blockFrom === other.blockFrom &&
      this.rawLines.length === other.rawLines.length &&
      this.rawLines.every((l, i) => l === other.rawLines[i])
    );
  }

  toDOM(view: EditorView): HTMLElement {
    const wrap = document.createElement("div");
    wrap.className = "cm-aunic-table-wrap";
    wrap.appendChild(buildTableElement(this.rawLines));
    wrap.addEventListener("click", () => {
      view.dispatch({ selection: { anchor: this.blockFrom }, scrollIntoView: false });
      view.focus();
    });
    return wrap;
  }

  ignoreEvent(): boolean {
    return false;
  }
}

export function markdownTablesExt() {
  return StateField.define<DecorationSet>({
    create(state) {
      return buildDecorations(state);
    },
    update(decorations, transaction) {
      if (transaction.docChanged || transaction.selection) {
        return buildDecorations(transaction.state);
      }
      return decorations.map(transaction.changes);
    },
    provide(field) {
      return EditorView.decorations.from(field);
    },
  });
}

function buildDecorations(state: EditorState): DecorationSet {
  const builder = new RangeSetBuilder<Decoration>();
  const blocks = detectTableBlocks(state.doc);
  for (const block of blocks) {
    if (!selectionOverlapsBlock(state, block)) {
      builder.add(
        block.from,
        block.to,
        Decoration.replace({ widget: new MarkdownTableWidget(block.rawLines, block.from), block: true }),
      );
    }
  }
  return builder.finish();
}

function selectionOverlapsBlock(state: EditorState, block: TableBlock): boolean {
  for (const range of state.selection.ranges) {
    if (range.from <= block.to && range.to >= block.from) {
      return true;
    }
  }
  return false;
}

function detectTableBlocks(doc: Text): TableBlock[] {
  const blocks: TableBlock[] = [];
  let inFence = false;
  let i = 1;

  while (i <= doc.lines) {
    const line = doc.line(i);

    if (isFenceLine(line.text)) {
      inFence = !inFence;
      i++;
      continue;
    }
    if (inFence) {
      i++;
      continue;
    }

    const header = parseContentRow(line.text);
    if (!header || i >= doc.lines) {
      i++;
      continue;
    }

    const separatorLine = doc.line(i + 1);
    const separator = parseSeparatorRow(separatorLine.text, header.cells.length);
    if (!separator) {
      i++;
      continue;
    }

    const blockLines: Array<{ from: number; to: number; text: string }> = [
      { from: line.from, to: line.to, text: line.text },
      { from: separatorLine.from, to: separatorLine.to, text: separatorLine.text },
    ];

    let j = i + 2;
    while (j <= doc.lines) {
      const bodyLine = doc.line(j);
      if (isFenceLine(bodyLine.text)) {
        break;
      }
      const row = parseContentRow(bodyLine.text);
      if (!row || row.cells.length !== header.cells.length) {
        break;
      }
      blockLines.push({ from: bodyLine.from, to: bodyLine.to, text: bodyLine.text });
      j++;
    }

    blocks.push({
      from: blockLines[0].from,
      to: blockLines[blockLines.length - 1].to,
      rawLines: blockLines.map((l) => l.text),
    });

    i = j;
  }

  return blocks;
}

interface ParsedRow {
  cells: string[];
}

function isFenceLine(text: string): boolean {
  return /^\s*(?:```|~~~)/.test(text);
}

function isTableLine(text: string): boolean {
  return parseContentRow(text) !== null || parseSeparatorRow(text) !== null;
}

function isSeparatorRow(text: string): boolean {
  return parseSeparatorRow(text) !== null;
}

function parseContentRow(text: string): ParsedRow | null {
  if (isFenceLine(text)) {
    return null;
  }
  const cells = splitRow(text);
  if (cells.length < 2 || cells.every((cell) => cell.trim() === "")) {
    return null;
  }
  if (cells.every((cell) => isSeparatorCell(cell))) {
    return null;
  }
  return { cells };
}

function parseSeparatorRow(text: string, expectedCells?: number): ParsedRow | null {
  if (isFenceLine(text)) {
    return null;
  }
  const cells = splitRow(text);
  if (cells.length < 2) {
    return null;
  }
  if (expectedCells !== undefined && cells.length !== expectedCells) {
    return null;
  }
  if (!cells.every((cell) => isSeparatorCell(cell))) {
    return null;
  }
  return { cells };
}

function isSeparatorCell(cell: string): boolean {
  return /^:?-{3,}:?$/.test(cell.trim());
}

function splitRow(text: string): string[] {
  const t = text.trim();
  if (!t.includes("|")) {
    return [];
  }
  const inner = t.startsWith("|") ? t.slice(1) : t;
  const stripped = inner.endsWith("|") ? inner.slice(0, -1) : inner;
  return stripped.split("|");
}

function buildTableElement(rawLines: string[]): HTMLTableElement {
  const sepIdx = rawLines.findIndex((l) => isSeparatorRow(l));
  const headerLines = sepIdx >= 0 ? rawLines.slice(0, sepIdx) : [];
  const bodyLines = sepIdx >= 0 ? rawLines.slice(sepIdx + 1) : rawLines;

  const table = document.createElement("table");
  table.className = "cm-aunic-md-table";

  if (headerLines.length > 0) {
    const thead = table.createTHead();
    for (const text of headerLines) {
      const tr = document.createElement("tr");
      for (const cell of splitRow(text)) {
        const th = document.createElement("th");
        appendInlineMarkdown(th, cell.trim());
        tr.appendChild(th);
      }
      thead.appendChild(tr);
    }
  }

  if (bodyLines.length > 0) {
    const tbody = table.createTBody();
    for (const text of bodyLines) {
      if (!isTableLine(text)) continue;
      const tr = document.createElement("tr");
      for (const cell of splitRow(text)) {
        const td = document.createElement("td");
        appendInlineMarkdown(td, cell.trim());
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
  }

  return table;
}

function appendInlineMarkdown(parent: HTMLElement, text: string): void {
  appendInlineSegments(parent, text, 0, text.length);
}

function appendInlineSegments(
  parent: HTMLElement,
  text: string,
  from: number,
  to: number,
): void {
  let pos = from;

  while (pos < to) {
    const token = nextInlineToken(text, pos, to);
    if (!token) {
      parent.appendChild(document.createTextNode(text.slice(pos, to)));
      return;
    }

    if (token.from > pos) {
      parent.appendChild(document.createTextNode(text.slice(pos, token.from)));
    }

    if (token.kind === "strongEmphasis") {
      const strong = document.createElement("strong");
      const emphasis = document.createElement("em");
      appendInlineSegments(emphasis, text, token.contentFrom, token.contentTo);
      strong.appendChild(emphasis);
      parent.appendChild(strong);
    } else {
      const element =
        token.kind === "strong"
          ? document.createElement("strong")
          : document.createElement("em");
      appendInlineSegments(element, text, token.contentFrom, token.contentTo);
      parent.appendChild(element);
    }
    pos = token.to;
  }
}

interface InlineToken {
  kind: "strong" | "emphasis" | "strongEmphasis";
  from: number;
  to: number;
  contentFrom: number;
  contentTo: number;
}

function nextInlineToken(text: string, from: number, to: number): InlineToken | null {
  for (let index = from; index < to; index++) {
    if (text[index] !== "*" || isEscaped(text, index)) {
      continue;
    }

    if (text.startsWith("***", index)) {
      const close = findClosingToken(text, "***", index + 3, to);
      if (close >= 0) {
        return {
          kind: "strongEmphasis",
          from: index,
          to: close + 3,
          contentFrom: index + 3,
          contentTo: close,
        };
      }
    }

    if (text.startsWith("**", index)) {
      const close = findClosingToken(text, "**", index + 2, to);
      if (close >= 0) {
        return {
          kind: "strong",
          from: index,
          to: close + 2,
          contentFrom: index + 2,
          contentTo: close,
        };
      }
    }

    const close = findClosingToken(text, "*", index + 1, to);
    if (close >= 0) {
      return {
        kind: "emphasis",
        from: index,
        to: close + 1,
        contentFrom: index + 1,
        contentTo: close,
      };
    }
  }

  return null;
}

function findClosingToken(
  text: string,
  token: string,
  from: number,
  to: number,
): number {
  let index = from;
  while (index < to) {
    const found = text.indexOf(token, index);
    if (found < 0 || found + token.length > to) {
      return -1;
    }
    if (!isEscaped(text, found) && found > from) {
      return found;
    }
    index = found + token.length;
  }
  return -1;
}

function isEscaped(text: string, index: number): boolean {
  let slashCount = 0;
  for (let cursor = index - 1; cursor >= 0 && text[cursor] === "\\"; cursor--) {
    slashCount++;
  }
  return slashCount % 2 === 1;
}
