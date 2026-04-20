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
} from "@codemirror/view";
import { highlightSelectionMatches, searchKeymap } from "@codemirror/search";
import { useExplorerStore } from "../../state/explorer";
import { useNoteEditorStore } from "../../state/noteEditor";
import { useWs } from "../../ws/context";
import { noteEditorRef } from "../../noteEditorRef";
import { CodeMirrorHost } from "./CodeMirrorHost";
import { activeLineRawMarkdown } from "./extensions/activeLineRawMarkdown";
import { markdownTablesExt } from "./extensions/markdownTables";
import { aunicTheme } from "./extensions/aunicTheme";
import { editCommandMarkersExt } from "./extensions/editCommandMarkers";
import { fourSpaceIndent } from "./extensions/fourSpaceIndent";
import { applyManagedSectionAutoFolds } from "./extensions/managedSectionAutoFold";
import { selectionSnapshotExt } from "./extensions/selectionSnapshot";
import { selectionTrackerExt } from "./extensions/selectionTracker";
import { softWrapIndent } from "./extensions/softWrapIndent";

export function NoteEditor() {
  const { client } = useWs();
  const openFile = useExplorerStore((store) => store.openFile);
  const path = useNoteEditorStore((store) => store.path);
  const initialDoc = useNoteEditorStore((store) => store.initialDoc);
  const currentDoc = useNoteEditorStore((store) => store.currentDoc);
  const status = useNoteEditorStore((store) => store.status);
  const error = useNoteEditorStore((store) => store.error);
  const notice = useNoteEditorStore((store) => store.notice);
  const conflict = useNoteEditorStore((store) => store.conflict);
  const externalReloadPending = useNoteEditorStore((store) => store.externalReloadPending);
  const documentVersion = useNoteEditorStore((store) => store.documentVersion);
  const loadForPath = useNoteEditorStore((store) => store.loadForPath);
  const markDirty = useNoteEditorStore((store) => store.markDirty);
  const save = useNoteEditorStore((store) => store.save);
  const resolveConflict = useNoteEditorStore((store) => store.resolveConflict);
  const resolveExternalReload = useNoteEditorStore((store) => store.resolveExternalReload);
  const clearNotice = useNoteEditorStore((store) => store.clearNotice);
  const reset = useNoteEditorStore((store) => store.reset);
  const viewRef = useRef<EditorView | null>(null);
  const saveRef = useRef<() => void>(() => {});

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
    if (!notice) {
      return;
    }
    const timer = setTimeout(() => clearNotice(), 3_000);
    return () => clearTimeout(timer);
  }, [clearNotice, notice]);

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

  const handleReady = useCallback((view: EditorView) => {
    viewRef.current = view;
    noteEditorRef.set(view);
    requestAnimationFrame(() => {
      if (viewRef.current === view) {
        applyManagedSectionAutoFolds(view);
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
      {notice ? <p className="notice-text">{notice}</p> : null}
      {error ? <p className="error-text">{error}</p> : null}

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
    indentOnInput(),
    bracketMatching(),
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
    highlightActiveLine(),
    highlightSelectionMatches(),
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
      ...searchKeymap,
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
