import type { EditorSelection } from "@codemirror/state";
import type { EditorView } from "@codemirror/view";

const snapshots = new WeakMap<EditorView, EditorSelection>();

export const selectionSnapshotRef = {
  save(view: EditorView, selection: EditorSelection): void {
    snapshots.set(view, selection);
  },
  get(view: EditorView): EditorSelection | null {
    return snapshots.get(view) ?? null;
  },
};
