import { useEffect, useRef } from "react";
import { EditorState, type Extension, type Transaction } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { activeEditorRef } from "../../activeEditorRef";

export interface CodeMirrorHostProps {
  initialDoc: string;
  extensions: Extension[];
  onReady?: (view: EditorView) => void;
  onDocChanged?: (doc: string, transaction: Transaction) => void;
  className?: string;
  ariaLabel?: string;
}

export function CodeMirrorHost({
  initialDoc,
  extensions,
  onReady,
  onDocChanged,
  className,
  ariaLabel,
}: CodeMirrorHostProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const initialDocRef = useRef(initialDoc);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) {
      return;
    }

    const state = EditorState.create({
      doc: initialDocRef.current,
      extensions: [
        ...extensions,
        EditorView.updateListener.of((update) => {
          if (!update.docChanged) {
            return;
          }
          const transaction = update.transactions.at(-1);
          if (transaction) {
            onDocChanged?.(update.state.doc.toString(), transaction);
          }
        }),
        EditorView.domEventHandlers({
          focus: (_event, view) => {
            activeEditorRef.set(view);
          },
        }),
      ],
    });
    const view = new EditorView({ state, parent: host });
    viewRef.current = view;
    if (ariaLabel) {
      view.contentDOM.setAttribute("aria-label", ariaLabel);
    }
    onReady?.(view);

    return () => {
      viewRef.current = null;
      activeEditorRef.clearIf(view);
      view.destroy();
    };
  // initialDoc intentionally omitted — key prop on the parent handles full resets
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ariaLabel, extensions, onDocChanged, onReady]);

  return <div ref={hostRef} className={className} />;
}
