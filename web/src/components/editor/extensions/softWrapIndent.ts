import { RangeSetBuilder } from "@codemirror/state";
import { Decoration, EditorView, ViewPlugin, type DecorationSet, type ViewUpdate } from "@codemirror/view";

const MAX_WRAP_INDENT_CH = 24;

export function softWrapIndent() {
  return [
    EditorView.lineWrapping,
    EditorView.theme({
      ".cm-line": {
        paddingLeft: "calc(var(--aunic-line-padding-left, 1rem) + var(--aunic-wrap-indent, 0) * 1ch)",
        textIndent: "calc(var(--aunic-wrap-indent, 0) * -1ch)",
        whiteSpace: "pre-wrap",
        overflowWrap: "anywhere",
      },
    }),
    ViewPlugin.fromClass(
      class {
        decorations: DecorationSet;

        constructor(view: EditorView) {
          this.decorations = buildIndentDecorations(view);
        }

        update(update: ViewUpdate) {
          if (update.docChanged || update.viewportChanged) {
            this.decorations = buildIndentDecorations(update.view);
          }
        }
      },
      {
        decorations: (plugin) => plugin.decorations,
      },
    ),
  ];
}

function buildIndentDecorations(view: EditorView): DecorationSet {
  const builder = new RangeSetBuilder<Decoration>();
  for (const { from, to } of view.visibleRanges) {
    let pos = from;
    while (pos <= to) {
      const line = view.state.doc.lineAt(pos);
      const indent = wrapIndentForLine(line.text);
      if (indent > 0) {
        builder.add(
          line.from,
          line.from,
          Decoration.line({
            attributes: {
              style: `--aunic-wrap-indent: ${indent};`,
            },
          }),
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

function wrapIndentForLine(line: string): number {
  const match = /^(\s*)(?:(?:[-+*]|\d+[.)])\s+)?/.exec(line);
  if (!match) {
    return 0;
  }
  const prefix = match[0].replace(/\t/g, "    ");
  return Math.min(prefix.length, MAX_WRAP_INDENT_CH);
}
