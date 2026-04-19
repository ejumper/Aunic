import { ViewPlugin, type ViewUpdate } from "@codemirror/view";
import type { EditorView } from "@codemirror/view";
import { useNoteEditorStore } from "../../../state/noteEditor";

export function selectionTrackerExt() {
  return ViewPlugin.fromClass(
    class {
      constructor(view: EditorView) {
        const main = view.state.selection.main;
        useNoteEditorStore
          .getState()
          .setSelectedText(main.empty ? "" : view.state.sliceDoc(main.from, main.to));
      }
      update(update: ViewUpdate) {
        if (update.selectionSet || update.docChanged) {
          const main = update.state.selection.main;
          useNoteEditorStore
            .getState()
            .setSelectedText(
              main.empty ? "" : update.state.sliceDoc(main.from, main.to),
            );
        }
      }
      destroy() {
        useNoteEditorStore.getState().setSelectedText("");
      }
    },
  );
}
