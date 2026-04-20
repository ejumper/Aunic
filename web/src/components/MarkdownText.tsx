import type { ReactNode } from "react";

interface MarkdownTextProps {
  text: string;
  className?: string;
}

export function MarkdownText({ text, className }: MarkdownTextProps) {
  return <div className={className}>{renderBlocks(text)}</div>;
}

function renderBlocks(text: string): ReactNode[] {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;
  let key = 0;

  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();

    if (!trimmed) {
      index += 1;
      continue;
    }

    if (trimmed.startsWith("```")) {
      const codeLines: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith("```")) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) {
        index += 1;
      }
      blocks.push(
        <pre className="md-render md-render__code-block" key={key++}>
          <code>{codeLines.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    if (isTableStart(lines, index)) {
      const tableLines: string[] = [lines[index], lines[index + 1]];
      index += 2;
      while (index < lines.length && isTableRow(lines[index])) {
        tableLines.push(lines[index]);
        index += 1;
      }
      blocks.push(renderTable(tableLines, key++));
      continue;
    }

    const heading = /^(#{1,6})\s+(.+)$/.exec(trimmed);
    if (heading) {
      const level = heading[1].length;
      blocks.push(
        <div className={`md-render md-render__heading md-render__heading--${level}`} key={key++}>
          {renderInline(heading[2])}
        </div>,
      );
      index += 1;
      continue;
    }

    if (/^-{3,}$/.test(trimmed)) {
      blocks.push(<div aria-hidden="true" className="md-render md-render__page-break" key={key++} />);
      index += 1;
      continue;
    }

    if (isListStart(trimmed)) {
      const listLines: string[] = [];
      const ordered = /^\d+\.\s+/.test(trimmed);
      while (
        index < lines.length &&
        lines[index].trim() &&
        (ordered ? /^\d+\.\s+/.test(lines[index].trim()) : /^[-*]\s+/.test(lines[index].trim()))
      ) {
        listLines.push(lines[index].trim().replace(ordered ? /^\d+\.\s+/ : /^[-*]\s+/, ""));
        index += 1;
      }
      const Tag = ordered ? "ol" : "ul";
      blocks.push(
        <Tag className="md-render md-render__list" key={key++}>
          {listLines.map((item, itemIndex) => (
            <li key={itemIndex}>{renderInline(item)}</li>
          ))}
        </Tag>,
      );
      continue;
    }

    const paragraphLines: string[] = [];
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines, index)) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    if (paragraphLines.length > 0) {
      blocks.push(
        <p className="md-render md-render__paragraph" key={key++}>
          {paragraphLines.map((paragraphLine, lineIndex) => (
            <span key={lineIndex}>
              {lineIndex > 0 ? <br /> : null}
              {renderInline(paragraphLine)}
            </span>
          ))}
        </p>,
      );
      continue;
    }

    index += 1;
  }

  return blocks;
}

function renderTable(lines: string[], key: number): ReactNode {
  const header = splitTableRow(lines[0]);
  const body = lines.slice(2).map(splitTableRow);

  return (
    <div className="md-render md-render__table-wrap" key={key}>
      <table className="md-render__table">
        <thead>
          <tr>
            {header.map((cell, cellIndex) => (
              <th key={cellIndex}>{renderInline(cell)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {header.map((_headerCell, cellIndex) => (
                <td key={cellIndex}>{renderInline(row[cellIndex] ?? "")}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const tokenPattern = /(`[^`\n]+`|\*\*[^*\n]+?\*\*|\*[^*\n]+?\*)/g;
  let cursor = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = tokenPattern.exec(text)) !== null) {
    if (match.index > cursor) {
      nodes.push(text.slice(cursor, match.index));
    }
    const token = match[0];
    if (token.startsWith("`")) {
      nodes.push(
        <code className="md-render__inline-code" key={key++}>
          {token.slice(1, -1)}
        </code>,
      );
    } else if (token.startsWith("**")) {
      nodes.push(<strong key={key++}>{token.slice(2, -2)}</strong>);
    } else {
      nodes.push(<em key={key++}>{token.slice(1, -1)}</em>);
    }
    cursor = match.index + token.length;
  }

  if (cursor < text.length) {
    nodes.push(text.slice(cursor));
  }
  return nodes;
}

function isBlockStart(lines: string[], index: number): boolean {
  const trimmed = lines[index].trim();
  return (
    trimmed.startsWith("```") ||
    isTableStart(lines, index) ||
    /^(#{1,6})\s+/.test(trimmed) ||
    /^-{3,}$/.test(trimmed) ||
    isListStart(trimmed)
  );
}

function isTableStart(lines: string[], index: number): boolean {
  return (
    index + 1 < lines.length &&
    isTableRow(lines[index]) &&
    /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[index + 1])
  );
}

function isTableRow(line: string): boolean {
  return line.includes("|") && line.trim().replace(/\|/g, "").trim().length > 0;
}

function splitTableRow(line: string): string[] {
  let normalized = line.trim();
  if (normalized.startsWith("|")) normalized = normalized.slice(1);
  if (normalized.endsWith("|")) normalized = normalized.slice(0, -1);
  return normalized.split("|").map((cell) => cell.trim());
}

function isListStart(trimmed: string): boolean {
  return /^[-*]\s+/.test(trimmed) || /^\d+\.\s+/.test(trimmed);
}
