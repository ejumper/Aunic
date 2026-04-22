import type { Extension } from "@codemirror/state";
import { EditorView, ViewPlugin, type ViewUpdate } from "@codemirror/view";
import { measureBrowserFindState } from "../../../browserFind";
import { useFindStore } from "../../../state/find";

export function browserFindSyncExt(): Extension {
  return ViewPlugin.fromClass(
    class {
      constructor(view: EditorView) {
        syncFromView(view);
      }

      update(update: ViewUpdate) {
        if (update.selectionSet || update.docChanged) {
          syncFromView(update.view);
        }
      }

      destroy() {
        useFindStore.getState().syncMatches(0, null);
      }
    },
  );
}

function syncFromView(view: EditorView): void {
  if (!useFindStore.getState().active) {
    useFindStore.getState().syncMatches(0, null);
    return;
  }
  const measurement = measureBrowserFindState(view);
  useFindStore.getState().syncMatches(
    measurement.matchCount,
    measurement.currentMatchIndex,
  );
}
