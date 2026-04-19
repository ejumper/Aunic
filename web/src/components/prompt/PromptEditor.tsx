import { useCallback, useEffect, useMemo, useRef } from "react";
import { EditorState, type Extension } from "@codemirror/state";
import { bracketMatching, indentOnInput } from "@codemirror/language";
import {
  defaultKeymap,
  history,
  historyKeymap,
  indentWithTab,
} from "@codemirror/commands";
import {
  drawSelection,
  dropCursor,
  EditorView,
  keymap,
} from "@codemirror/view";
import { highlightSelectionMatches, searchKeymap } from "@codemirror/search";
import { CodeMirrorHost } from "../editor/CodeMirrorHost";
import { promptEditorRef } from "../../promptEditorRef";
import { aunicTheme } from "../editor/extensions/aunicTheme";
import { editCommandMarkersExt } from "../editor/extensions/editCommandMarkers";
import { promptAutocomplete } from "../editor/extensions/promptAutocomplete";
import { promptKeymap } from "../editor/extensions/promptKeymap";
import { promptSyntax } from "../editor/extensions/promptSyntax";
import { softWrapIndent } from "../editor/extensions/softWrapIndent";

interface PromptEditorProps {
  value: string;
  documentVersion: number;
  runActive: boolean;
  onChange: (text: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
}

export function PromptEditor({
  value,
  documentVersion,
  runActive,
  onChange,
  onSubmit,
  onCancel,
}: PromptEditorProps) {
  const initialDocRef = useVersionedInitialDoc(value, documentVersion);
  const submitRef = useRef(onSubmit);
  const cancelRef = useRef(onCancel);
  const runActiveRef = useRef(runActive);
  submitRef.current = onSubmit;
  cancelRef.current = onCancel;
  runActiveRef.current = runActive;

  const extensions = useMemo(
    () =>
      buildPromptEditorExtensions(
        () => submitRef.current(),
        () => cancelRef.current(),
        () => runActiveRef.current,
      ),
    [],
  );

  useEffect(() => {
    return () => {
      promptEditorRef.set(null);
    };
  }, []);

  const handleReady = useCallback((view: EditorView) => {
    promptEditorRef.set(view);
  }, []);

  const handleDocChanged = useCallback(
    (doc: string) => {
      onChange(doc);
    },
    [onChange],
  );

  return (
    <CodeMirrorHost
      key={documentVersion}
      className="prompt-editor-host"
      initialDoc={initialDocRef.current}
      extensions={extensions}
      onReady={handleReady}
      onDocChanged={handleDocChanged}
      ariaLabel="Prompt editor"
    />
  );
}

function useVersionedInitialDoc(value: string, documentVersion: number) {
  const initialDocRef = useRef(value);
  const versionRef = useRef(documentVersion);
  if (versionRef.current !== documentVersion) {
    versionRef.current = documentVersion;
    initialDocRef.current = value;
  }
  return initialDocRef;
}

function buildPromptEditorExtensions(
  onSubmit: () => void,
  onCancel: () => void,
  isRunActive: () => boolean,
): Extension[] {
  return [
    history(),
    drawSelection(),
    dropCursor(),
    EditorState.allowMultipleSelections.of(true),
    indentOnInput(),
    bracketMatching(),
    softWrapIndent(),
    editCommandMarkersExt(),
    promptSyntax(),
    promptAutocomplete(),
    highlightSelectionMatches(),
    promptKeymap({ onSubmit, onCancel, isRunActive }),
    keymap.of([
      indentWithTab,
      ...defaultKeymap,
      ...historyKeymap,
      ...searchKeymap,
    ]),
    aunicTheme(),
    EditorView.theme({
      "&": {
        minHeight: "3.5rem",
      },
      ".cm-scroller": {
        maxHeight: "8rem",
        overflow: "auto",
      },
      ".cm-content": {
        minHeight: "3.5rem",
      },
    }),
  ];
}
