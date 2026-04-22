import { EditorSelection, type SelectionRange } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { SearchQuery, getSearchQuery, setSearchQuery } from "@codemirror/search";
import { noteEditorRef } from "./noteEditorRef";
import { promptEditorRef } from "./promptEditorRef";
import { useFindStore } from "./state/find";
import { useNoteEditorStore } from "./state/noteEditor";

interface FindMatch {
  from: number;
  to: number;
}

interface MatchMeasurement {
  matchCount: number;
  currentMatchIndex: number | null;
}

type RestoreFocus = "note" | "prompt" | "none";

export function openBrowserFind(options: { replaceMode?: boolean } = {}): void {
  const store = useFindStore.getState();
  const selectedText = useNoteEditorStore.getState().selectedText;
  const suggestedFindText = selectedText.trim() ? selectedText : "";
  const nextFindText = store.findText || suggestedFindText;

  store.open({
    replaceMode: options.replaceMode ?? store.replaceMode,
    findText: nextFindText,
  });
  syncBrowserFindQuery();

  if (nextFindText) {
    activateMatch(0);
    return;
  }
  syncBrowserFindMeasurements();
}

export function closeBrowserFind(options: { restoreFocus?: RestoreFocus } = {}): void {
  const view = noteEditorRef.get();
  if (view) {
    applySearchQuery(view, {
      search: "",
      replace: "",
      caseSensitive: false,
    });
    collapseSelection(view);
  }
  useFindStore.getState().close();

  const restoreFocus = options.restoreFocus ?? "none";
  if (restoreFocus === "note") {
    view?.focus();
    return;
  }
  if (restoreFocus === "prompt") {
    requestAnimationFrame(() => {
      promptEditorRef.get()?.focus();
    });
  }
}

export function setBrowserFindText(text: string): void {
  useFindStore.getState().setFindText(text);
  syncBrowserFindQuery();
  if (!text) {
    clearActiveMatch();
    return;
  }
  activateMatch(0);
}

export function setBrowserReplaceText(text: string): void {
  useFindStore.getState().setReplaceText(text);
  syncBrowserFindQuery();
}

export function setBrowserFindReplaceMode(replaceMode: boolean): void {
  useFindStore.getState().setReplaceMode(replaceMode);
}

export function toggleBrowserFindCaseSensitive(): void {
  useFindStore.getState().toggleCaseSensitive();
  syncBrowserFindQuery();
  const state = useFindStore.getState();
  if (!state.findText) {
    clearActiveMatch();
    return;
  }
  activateMatch(0);
}

export function findNextBrowserMatch(): boolean {
  return moveBrowserFindMatch(1);
}

export function findPreviousBrowserMatch(): boolean {
  return moveBrowserFindMatch(-1);
}

export function replaceCurrentBrowserMatch(): boolean {
  const view = noteEditorRef.get();
  if (!view) {
    return false;
  }

  const state = useFindStore.getState();
  const matches = getMatches(view);
  if (!matches.length) {
    syncBrowserFindMeasurements(view);
    return false;
  }

  const currentIndex = currentMatchIndex(matches, view.state.selection.main);
  const matchIndex = currentIndex ?? 0;
  const match = matches[matchIndex];
  const replacement = state.replaceText;

  view.dispatch({
    changes: { from: match.from, to: match.to, insert: replacement },
    selection: EditorSelection.cursor(match.from + replacement.length),
    scrollIntoView: true,
  });

  syncBrowserFindQuery(view);
  const updatedMatches = getMatches(view);
  if (!updatedMatches.length) {
    syncBrowserFindMeasurements(view);
    return true;
  }

  const nextStart = match.from + replacement.length;
  const nextIndex = updatedMatches.findIndex((candidate) => candidate.from >= nextStart);
  activateMatch(nextIndex >= 0 ? nextIndex : 0, { view });
  return true;
}

export function replaceAllBrowserMatches(): number {
  const view = noteEditorRef.get();
  if (!view) {
    return 0;
  }

  const state = useFindStore.getState();
  const matches = getMatches(view);
  if (!matches.length) {
    syncBrowserFindMeasurements(view);
    return 0;
  }

  const changes = matches
    .filter((match) => view.state.sliceDoc(match.from, match.to) !== state.replaceText)
    .map((match) => ({
      from: match.from,
      to: match.to,
      insert: state.replaceText,
    }));

  if (!changes.length) {
    syncBrowserFindMeasurements(view);
    return 0;
  }

  view.dispatch({ changes, scrollIntoView: true });
  syncBrowserFindQuery(view);

  const updatedMatches = getMatches(view);
  if (!updatedMatches.length) {
    syncBrowserFindMeasurements(view);
    return changes.length;
  }

  activateMatch(0, { view });
  return changes.length;
}

export function syncBrowserFindQuery(view: EditorView | null = noteEditorRef.get()): void {
  if (!view) {
    return;
  }
  const state = useFindStore.getState();
  applySearchQuery(view, {
    search: state.active ? state.findText : "",
    replace: state.active ? state.replaceText : "",
    caseSensitive: state.active ? state.caseSensitive : false,
  });
}

export function syncBrowserFindMeasurements(view: EditorView | null = noteEditorRef.get()): void {
  if (!view || !useFindStore.getState().active) {
    useFindStore.getState().syncMatches(0, null);
    return;
  }
  const measurement = measureBrowserFindState(view);
  useFindStore.getState().syncMatches(
    measurement.matchCount,
    measurement.currentMatchIndex,
  );
}

export function measureBrowserFindState(view: EditorView): MatchMeasurement {
  const matches = getMatches(view);
  return {
    matchCount: matches.length,
    currentMatchIndex: currentMatchIndex(matches, view.state.selection.main),
  };
}

function moveBrowserFindMatch(direction: 1 | -1): boolean {
  const view = noteEditorRef.get();
  if (!view) {
    return false;
  }

  const matches = getMatches(view);
  if (!matches.length) {
    syncBrowserFindMeasurements(view);
    return false;
  }

  const currentIndex = currentMatchIndex(matches, view.state.selection.main);
  const nextIndex =
    currentIndex === null
      ? direction > 0
        ? 0
        : matches.length - 1
      : (currentIndex + direction + matches.length) % matches.length;
  activateMatch(nextIndex, { view });
  return true;
}

function activateMatch(
  index: number,
  options: { view?: EditorView | null } = {},
): void {
  const view = options.view ?? noteEditorRef.get();
  if (!view) {
    useFindStore.getState().syncMatches(0, null);
    return;
  }
  const matches = getMatches(view);
  if (!matches.length) {
    clearActiveMatch(view);
    return;
  }

  const safeIndex = ((index % matches.length) + matches.length) % matches.length;
  const match = matches[safeIndex];
  view.dispatch({
    selection: EditorSelection.range(match.from, match.to),
    effects: EditorView.scrollIntoView(match.from, {
      y: "center",
      yMargin: view.defaultLineHeight * 2,
    }),
  });
  useFindStore.getState().syncMatches(matches.length, safeIndex);
}

function clearActiveMatch(view: EditorView | null = noteEditorRef.get()): void {
  if (view) {
    collapseSelection(view);
  }
  syncBrowserFindMeasurements(view);
}

function collapseSelection(view: EditorView): void {
  const main = view.state.selection.main;
  if (main.empty) {
    return;
  }
  view.dispatch({ selection: EditorSelection.cursor(main.head) });
}

function getMatches(view: EditorView): FindMatch[] {
  const state = useFindStore.getState();
  return literalMatches(view.state.doc.toString(), state.findText, state.caseSensitive);
}

function literalMatches(text: string, query: string, caseSensitive: boolean): FindMatch[] {
  if (!query) {
    return [];
  }

  const haystack = caseSensitive ? text : text.toLocaleLowerCase();
  const needle = caseSensitive ? query : query.toLocaleLowerCase();
  const matches: FindMatch[] = [];
  let searchFrom = 0;

  while (searchFrom <= haystack.length - needle.length) {
    const index = haystack.indexOf(needle, searchFrom);
    if (index < 0) {
      break;
    }
    matches.push({ from: index, to: index + query.length });
    searchFrom = index + Math.max(query.length, 1);
  }

  return matches;
}

function currentMatchIndex(
  matches: FindMatch[],
  selection: SelectionRange,
): number | null {
  if (!matches.length) {
    return null;
  }
  const exactIndex = matches.findIndex(
    (match) => match.from === selection.from && match.to === selection.to,
  );
  if (exactIndex >= 0) {
    return exactIndex;
  }
  if (!selection.empty) {
    return null;
  }
  const containingIndex = matches.findIndex(
    (match) => selection.from >= match.from && selection.from < match.to,
  );
  return containingIndex >= 0 ? containingIndex : null;
}

function applySearchQuery(
  view: EditorView,
  state: { search: string; replace: string; caseSensitive: boolean },
): void {
  const nextQuery = new SearchQuery({
    search: state.search,
    replace: state.replace,
    caseSensitive: state.caseSensitive,
    literal: true,
  });
  const currentQuery = getSearchQuery(view.state);
  if (currentQuery.eq(nextQuery)) {
    return;
  }
  view.dispatch({ effects: setSearchQuery.of(nextQuery) });
}
