import { ViewPlugin, type EditorView, type ViewUpdate } from "@codemirror/view";
import { selectionSnapshotRef } from "../../../selectionSnapshotRef";

export function selectionSnapshotExt() {
  return ViewPlugin.fromClass(
    class {
      constructor(view: EditorView) {
        selectionSnapshotRef.save(view, view.state.selection);
      }
      update(update: ViewUpdate) {
        if (update.selectionSet || update.docChanged) {
          selectionSnapshotRef.save(update.view, update.state.selection);
        }
      }
    },
  );
}
