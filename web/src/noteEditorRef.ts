import type { EditorView } from "@codemirror/view";

let _view: EditorView | null = null;

export const noteEditorRef = {
  get(): EditorView | null {
    return _view;
  },
  set(view: EditorView | null): void {
    _view = view;
  },
};
