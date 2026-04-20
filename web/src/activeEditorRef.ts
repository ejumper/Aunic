import type { EditorView } from "@codemirror/view";

let _view: EditorView | null = null;

export const activeEditorRef = {
  get(): EditorView | null {
    return _view;
  },
  set(view: EditorView | null): void {
    _view = view;
  },
  clearIf(view: EditorView): void {
    if (_view === view) {
      _view = null;
    }
  },
};
