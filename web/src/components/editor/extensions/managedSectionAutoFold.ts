import { foldEffect } from "@codemirror/language";
import type { EditorView } from "@codemirror/view";

const MANAGED_TITLES = new Set(["search results", "work log"]);

type Heading = {
  lineNumber: number;
  level: number;
  title: string;
  from: number;
  to: number;
};

export function applyManagedSectionAutoFolds(view: EditorView): void {
  const headings = collectHeadings(view);
  const effects = headings
    .map((heading, index) => foldRangeForHeading(view, headings, heading, index))
    .filter((range): range is { from: number; to: number } => range !== null)
    .map((range) => foldEffect.of(range));

  if (effects.length > 0) {
    view.dispatch({ effects });
  }
}

function collectHeadings(view: EditorView): Heading[] {
  const headings: Heading[] = [];
  for (let lineNumber = 1; lineNumber <= view.state.doc.lines; lineNumber += 1) {
    const line = view.state.doc.line(lineNumber);
    const match = /^(#{1,6})\s+(.*\S)\s*$/.exec(line.text);
    if (!match) {
      continue;
    }
    headings.push({
      lineNumber,
      level: match[1].length,
      title: match[2].trim(),
      from: line.from,
      to: line.to,
    });
  }
  return headings;
}

function foldRangeForHeading(
  view: EditorView,
  headings: Heading[],
  heading: Heading,
  index: number,
): { from: number; to: number } | null {
  if (!MANAGED_TITLES.has(heading.title.toLocaleLowerCase())) {
    return null;
  }

  const nextHeading = headings
    .slice(index + 1)
    .find((candidate) => candidate.level <= heading.level);
  const nextLineNumber = nextHeading?.lineNumber ?? view.state.doc.lines + 1;
  if (nextLineNumber <= heading.lineNumber + 1) {
    return null;
  }

  const from = Math.min(heading.to + 1, view.state.doc.length);
  const to =
    nextHeading === undefined
      ? view.state.doc.length
      : Math.max(from, nextHeading.from - 1);
  return from < to ? { from, to } : null;
}
