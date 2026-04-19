import { RangeSetBuilder } from "@codemirror/state";
import {
  Decoration,
  EditorView,
  ViewPlugin,
  type DecorationSet,
  type ViewUpdate,
} from "@codemirror/view";

const MARKER_PATTERN = /(%>>|<<%|!>>|<<!|\$>>|<<\$|@>>|<<@)/g;

const MARKER_CLASS: Record<string, string> = {
  "%>>": "cm-aunic-marker-exclude",
  "<<%": "cm-aunic-marker-exclude",
  "!>>": "cm-aunic-marker-include",
  "<<!": "cm-aunic-marker-include",
  "$>>": "cm-aunic-marker-read-only",
  "<<$": "cm-aunic-marker-read-only",
  "@>>": "cm-aunic-marker-write",
  "<<@": "cm-aunic-marker-write",
};

export function editCommandMarkersExt() {
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
  const builder = new RangeSetBuilder<Decoration>();
  for (const { from, to } of view.visibleRanges) {
    const text = view.state.doc.sliceString(from, to);
    MARKER_PATTERN.lastIndex = 0;
    for (let match = MARKER_PATTERN.exec(text); match; match = MARKER_PATTERN.exec(text)) {
      const token = match[0];
      const start = from + match.index;
      builder.add(
        start,
        start + token.length,
        Decoration.mark({
          class: `cm-aunic-marker ${MARKER_CLASS[token]}`,
        }),
      );
    }
  }
  return builder.finish();
}
