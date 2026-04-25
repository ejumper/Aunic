import { useCallback, useEffect, useMemo, useRef } from "react";
import { EditorState, type Extension } from "@codemirror/state";
import {
  codeFolding,
  foldEffect,
  foldedRanges,
  foldable,
  foldGutter,
  foldKeymap,
  indentOnInput,
  indentUnit,
  bracketMatching,
  unfoldEffect,
} from "@codemirror/language";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { GFM } from "@lezer/markdown";
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
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
  scrollPastEnd,
} from "@codemirror/view";
import { highlightSelectionMatches, search } from "@codemirror/search";
import { useFindStore } from "../../state/find";
import { syncBrowserFindMeasurements, syncBrowserFindQuery } from "../../browserFind";
import { useExplorerStore } from "../../state/explorer";
import { useNoteEditorStore } from "../../state/noteEditor";
import { useSessionStore } from "../../state/session";
import { useWs } from "../../ws/context";
import { noteEditorRef } from "../../noteEditorRef";
import { CodeMirrorHost } from "./CodeMirrorHost";
import { activeLineRawMarkdown } from "./extensions/activeLineRawMarkdown";
import { aunicMarkerEnterProtection } from "./extensions/aunicMarkerEnterProtection";
import { markdownTablesExt } from "./extensions/markdownTables";
import { aunicTheme } from "./extensions/aunicTheme";
import { browserFindSyncExt } from "./extensions/browserFindSync";
import { editCommandMarkersExt } from "./extensions/editCommandMarkers";
import { fourSpaceIndent } from "./extensions/fourSpaceIndent";
import { applyManagedSectionAutoFolds } from "./extensions/managedSectionAutoFold";
import { monospaceTabWidth } from "./extensions/monospaceTabWidth";
import { selectionSnapshotExt } from "./extensions/selectionSnapshot";
import { selectionTrackerExt } from "./extensions/selectionTracker";
import { softWrapIndent } from "./extensions/softWrapIndent";

const AUTO_SAVE_DELAY_MS = 1200;

export function NoteEditor() {
  const { client } = useWs();
  const openFile = useExplorerStore((store) => store.openFile);
  const session = useSessionStore((store) => store.session);
  const runActive = useSessionStore((store) => store.runActive);
  const path = useNoteEditorStore((store) => store.path);
  const initialDoc = useNoteEditorStore((store) => store.initialDoc);
  const currentDoc = useNoteEditorStore((store) => store.currentDoc);
  const dirty = useNoteEditorStore((store) => store.dirty);
  const status = useNoteEditorStore((store) => store.status);
  const conflict = useNoteEditorStore((store) => store.conflict);
  const externalReloadPending = useNoteEditorStore((store) => store.externalReloadPending);
  const documentVersion = useNoteEditorStore((store) => store.documentVersion);
  const findActive = useFindStore((store) => store.active);
  const findText = useFindStore((store) => store.findText);
  const replaceText = useFindStore((store) => store.replaceText);
  const caseSensitive = useFindStore((store) => store.caseSensitive);
  const loadForPath = useNoteEditorStore((store) => store.loadForPath);
  const markDirty = useNoteEditorStore((store) => store.markDirty);
  const save = useNoteEditorStore((store) => store.save);
  const resolveConflict = useNoteEditorStore((store) => store.resolveConflict);
  const resolveExternalReload = useNoteEditorStore((store) => store.resolveExternalReload);
  const reset = useNoteEditorStore((store) => store.reset);
  const viewRef = useRef<EditorView | null>(null);
  const saveRef = useRef<() => void>(() => {});
  const autoSaveEnabled = session?.editor_settings?.save_mode === "auto";

  useEffect(() => {
    if (!openFile) {
      const state = useNoteEditorStore.getState();
      if (
        state.path ||
        state.initialDoc ||
        state.currentDoc ||
        state.dirty ||
        state.status !== "idle"
      ) {
        reset();
      }
      return;
    }
    if (openFile !== path) {
      void loadForPath(client, openFile);
    }
  }, [client, loadForPath, openFile, path, reset]);

  useEffect(() => {
    if (
      !autoSaveEnabled ||
      !path ||
      !dirty ||
      status === "loading" ||
      status === "saving" ||
      runActive ||
      conflict !== null ||
      externalReloadPending !== null
    ) {
      return;
    }
    const timer = setTimeout(() => {
      void save(client, currentDoc);
    }, AUTO_SAVE_DELAY_MS);
    return () => clearTimeout(timer);
  }, [
    autoSaveEnabled,
    client,
    conflict,
    currentDoc,
    dirty,
    externalReloadPending,
    path,
    runActive,
    save,
    status,
  ]);

  saveRef.current = () => {
    const doc = viewRef.current?.state.doc.toString() ?? currentDoc;
    void save(client, doc);
  };

  const extensions = useMemo(() => buildNoteEditorExtensions(() => saveRef.current()), []);

  useEffect(() => {
    return () => {
      noteEditorRef.set(null);
    };
  }, []);

  useEffect(() => {
    syncBrowserFindQuery(viewRef.current);
    syncBrowserFindMeasurements(viewRef.current);
  }, [caseSensitive, documentVersion, findActive, findText, replaceText]);

  const handleReady = useCallback((view: EditorView) => {
    viewRef.current = view;
    noteEditorRef.set(view);
    requestAnimationFrame(() => {
      if (viewRef.current === view) {
        applyManagedSectionAutoFolds(view);
        syncBrowserFindQuery(view);
        syncBrowserFindMeasurements(view);
        view.requestMeasure();
      }
    });
  }, []);

  const handleDocChanged = useCallback(
    (doc: string) => {
      markDirty(doc);
    },
    [markDirty],
  );

  const resolveConflictWithCurrentDoc = useCallback(
    (strategy: "reload" | "overwrite" | "cancel") => {
      const doc = viewRef.current?.state.doc.toString() ?? currentDoc;
      void resolveConflict(client, doc, strategy);
    },
    [client, currentDoc, resolveConflict],
  );

  return (
    <section className="note-editor-panel" aria-label="Note editor">
      {!openFile ? (
        <p className="muted panel-empty">Select a markdown file from the explorer.</p>
      ) : null}
      {status === "loading" ? <p className="muted panel-empty">Loading note...</p> : null}

      {externalReloadPending ? (
        <div className="editor-banner" role="status">
          <p>This file changed on disk while browser edits are unsaved.</p>
          <div>
            <button type="button" onClick={() => resolveExternalReload("reload")}>
              Reload
            </button>
            <button
              type="button"
              className="secondary-button"
              onClick={() => resolveExternalReload("keep")}
            >
              Keep Mine
            </button>
          </div>
        </div>
      ) : null}

      {conflict ? (
        <div className="editor-banner editor-banner-conflict" role="alert">
          {conflict.reason === "model_update" ? (
            <>
              <p>Your note changed during the run. Choose whether the model update or your edits should win.</p>
              <div>
                <button type="button" onClick={() => resolveConflictWithCurrentDoc("reload")}>
                  Use Model Version
                </button>
                <button type="button" onClick={() => resolveConflictWithCurrentDoc("overwrite")}>
                  Keep Mine
                </button>
              </div>
            </>
          ) : (
            <>
              <p>The file changed before this save completed.</p>
              <div>
                <button type="button" onClick={() => resolveConflictWithCurrentDoc("reload")}>
                  Reload Their Version
                </button>
                <button type="button" onClick={() => resolveConflictWithCurrentDoc("overwrite")}>
                  Overwrite
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => resolveConflictWithCurrentDoc("cancel")}
                >
                  Cancel
                </button>
              </div>
            </>
          )}
        </div>
      ) : null}

      {path && status !== "loading" ? (
        <CodeMirrorHost
          key={`${path}:${documentVersion}`}
          className="code-editor-host"
          initialDoc={initialDoc}
          extensions={extensions}
          onReady={handleReady}
          onDocChanged={handleDocChanged}
          ariaLabel={`Editing ${path}`}
        />
      ) : null}
    </section>
  );
}

function buildNoteEditorExtensions(onSave: () => void): Extension[] {
  return [
    lineNumbers({
      domEventHandlers: {
        click(view, line) {
          return toggleFoldAtLine(view, line.from);
        },
      },
    }),
    highlightActiveLineGutter(),
    history(),
    drawSelection(),
    dropCursor(),
    EditorState.allowMultipleSelections.of(true),
    EditorState.tabSize.of(8),
    indentUnit.of("\t"),
    indentOnInput(),
    bracketMatching(),
    aunicMarkerEnterProtection(),
    markdown({
      base: markdownLanguage,
      extensions: GFM,
    }),
    codeFolding(),
    foldGutter({
      markerDOM(open) {
        const span = document.createElement("span");
        span.className = open ? "cm-foldGutter-open" : "cm-foldGutter-folded";
        span.textContent = open ? "⌄" : "›";
        return span;
      },
    }),
    softWrapIndent(),
    editCommandMarkersExt(),
    fourSpaceIndent(),
    selectionSnapshotExt(),
    selectionTrackerExt(),
    markdownTablesExt(),
    activeLineRawMarkdown(),
    monospaceTabWidth(),
    highlightActiveLine(),
    highlightSelectionMatches(),
    search(),
    browserFindSyncExt(),
    keymap.of([
      {
        key: "Mod-s",
        preventDefault: true,
        run: () => {
          onSave();
          return true;
        },
      },
      indentWithTab,
      ...defaultKeymap,
      ...historyKeymap,
      ...foldKeymap,
    ]),
    aunicTheme(),
  ];
}

function toggleFoldAtLine(view: EditorView, pos: number): boolean {
  const line = view.state.doc.lineAt(pos);
  const range = foldable(view.state, line.from, line.to);
  if (range) {
    view.dispatch({ effects: foldEffect.of(range) });
    return true;
  }
  const iter = foldedRanges(view.state).iter();
  while (iter.value !== null) {
    if (iter.from >= line.from && iter.from <= line.to) {
      view.dispatch({ effects: unfoldEffect.of({ from: iter.from, to: iter.to }) });
      return true;
    }
    iter.next();
  }
  return false;
}
