import { redo, undo } from "@codemirror/commands";
import { indentUnit } from "@codemirror/language";
import {
  EditorSelection,
  type ChangeSpec,
  type Line,
  type SelectionRange,
} from "@codemirror/state";
import type { EditorView } from "@codemirror/view";
import { activeEditorRef } from "../../activeEditorRef";
import { noteEditorRef } from "../../noteEditorRef";
import { promptEditorRef } from "../../promptEditorRef";
import { remoteLog } from "../../remoteLog";
import { selectionSnapshotRef } from "../../selectionSnapshotRef";

type EditorCommand = "undo" | "redo" | "tab" | "untab";

const COMMANDS: Array<{
  command: EditorCommand;
  label: string;
  icon: string;
  flipped?: boolean;
}> = [
  { command: "undo", label: "Undo", icon: "/icons/undo.svg" },
  { command: "redo", label: "Redo", icon: "/icons/undo.svg", flipped: true },
  { command: "untab", label: "Untab", icon: "/icons/tab.svg", flipped: true },
  { command: "tab", label: "Tab", icon: "/icons/tab.svg" },
];

export function EditorAccessoryToolbar() {
  return (
    <div className="editor-accessory-toolbar" aria-label="Editor actions">
      {COMMANDS.map((item) => (
        <div
          key={item.command}
          role="button"
          className="editor-accessory-button"
          aria-label={item.label}
          tabIndex={-1}
          onTouchStart={(event) => {
            remoteLog("toolbar touchstart", {
              command: item.command,
              cancelable: event.cancelable,
            });
            event.preventDefault();
            event.stopPropagation();
            runEditorCommand(item.command);
          }}
          onPointerDown={(event) => {
            remoteLog("toolbar pointerdown", {
              command: item.command,
              pointerType: event.pointerType,
              cancelable: event.cancelable,
            });
            event.preventDefault();
            event.stopPropagation();
            if (event.pointerType !== "touch") {
              runEditorCommand(item.command);
            }
          }}
          onMouseDown={(event) => {
            event.preventDefault();
            event.stopPropagation();
          }}
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
          }}
          onKeyDown={(event) => {
            if (event.key !== "Enter" && event.key !== " ") {
              return;
            }
            event.preventDefault();
            event.stopPropagation();
            runEditorCommand(item.command);
          }}
        >
          <img
            className={
              item.flipped
                ? "editor-accessory-icon editor-accessory-icon--flipped"
                : "editor-accessory-icon"
            }
            src={item.icon}
            alt=""
            aria-hidden="true"
          />
        </div>
      ))}
    </div>
  );
}

function runEditorCommand(command: EditorCommand) {
  const view = getActiveEditorView();
  if (!view) {
    remoteLog("runEditorCommand: no view", { command });
    return;
  }

  const snapshot = selectionSnapshotRef.get(view);
  remoteLog("runEditorCommand enter", {
    command,
    hasFocus: view.hasFocus,
    docLength: view.state.doc.length,
    liveSelection: view.state.selection.ranges.map((r) => ({ a: r.anchor, h: r.head })),
    snapshotSelection: snapshot?.ranges.map((r) => ({ a: r.anchor, h: r.head })) ?? null,
  });

  // Focus before dispatching — on iOS, dispatching into an unfocused view can
  // leave the DOM selection inconsistent, which lets a subsequent DOMObserver
  // sync clobber the change.
  view.focus();

  const commands: Record<EditorCommand, (view: EditorView) => boolean> = {
    undo,
    redo,
    tab: indentSelectedLines,
    untab: outdentSelectedLines,
  };

  const result = commands[command](view);
  remoteLog("runEditorCommand result", {
    command,
    result,
    docLength: view.state.doc.length,
    liveSelection: view.state.selection.ranges.map((r) => ({ a: r.anchor, h: r.head })),
  });
}

function getActiveEditorView(): EditorView | null {
  const activeView = activeEditorRef.get();
  if (activeView) {
    return activeView;
  }

  const activeElement = document.activeElement;
  if (activeElement instanceof HTMLElement) {
    if (activeElement.closest(".prompt-editor-host")) {
      return promptEditorRef.get();
    }
    if (activeElement.closest(".code-editor-host")) {
      return noteEditorRef.get();
    }
  }
  return noteEditorRef.get() ?? promptEditorRef.get();
}

function indentSelectedLines(view: EditorView): boolean {
  if (view.state.readOnly) {
    remoteLog("indent: readOnly abort");
    return false;
  }

  const ranges = effectiveRanges(view);
  const unit = view.state.facet(indentUnit);
  remoteLog("indent ranges", {
    count: ranges.length,
    ranges: ranges.map((r) => ({ from: r.from, to: r.to })),
    unitLen: unit.length,
  });
  if (!hasMultilineSelection(view, ranges)) {
    const changes: ChangeSpec[] = [];
    const nextRanges = ranges.map((range) => {
      changes.push({ from: range.from, to: range.to, insert: unit });
      return EditorSelection.cursor(range.from + unit.length);
    });

    view.dispatch({
      changes,
      selection: EditorSelection.create(nextRanges),
      userEvent: "input.indent",
      scrollIntoView: true,
    });
    remoteLog("indent: dispatched single-line");
    return true;
  }

  const changes: ChangeSpec[] = [];
  forEachSelectedLine(view, ranges, (line) => {
    changes.push({ from: line.from, insert: unit });
  });

  if (changes.length === 0) {
    return false;
  }
  view.dispatch({ changes, userEvent: "input.indent", scrollIntoView: true });
  return true;
}

function outdentSelectedLines(view: EditorView): boolean {
  if (view.state.readOnly) {
    return false;
  }

  const ranges = effectiveRanges(view);
  const unit = view.state.facet(indentUnit);
  if (!hasMultilineSelection(view, ranges)) {
    const changes: ChangeSpec[] = [];
    const nextRanges = ranges.map((range) => {
      const removal = removableIndentBefore(view, range.from, unit);
      if (!removal) {
        return range;
      }
      changes.push({ from: removal.from, to: removal.to, insert: "" });
      return EditorSelection.cursor(removal.from);
    });

    if (changes.length === 0) {
      return false;
    }

    view.dispatch({
      changes,
      selection: EditorSelection.create(nextRanges),
      userEvent: "delete.dedent",
      scrollIntoView: true,
    });
    return true;
  }

  const changes: ChangeSpec[] = [];
  forEachSelectedLine(view, ranges, (line) => {
    const leading = /^\s*/.exec(line.text)?.[0] ?? "";
    if (leading.length === 0) {
      return;
    }

    let remove = 0;
    if (leading.startsWith(unit)) {
      remove = unit.length;
    } else if (leading[0] === "\t") {
      remove = 1;
    } else {
      remove = Math.min(unit.length, leading.length);
    }

    changes.push({ from: line.from, to: line.from + remove, insert: "" });
  });

  if (changes.length === 0) {
    return false;
  }
  view.dispatch({ changes, userEvent: "delete.dedent", scrollIntoView: true });
  return true;
}

// iOS can clear the contenteditable's DOM selection when a toolbar button is
// tapped; CodeMirror then mirrors that empty selection into state.selection,
// so we prefer the snapshot captured while the view was focused.
function effectiveRanges(view: EditorView): readonly SelectionRange[] {
  const snapshot = selectionSnapshotRef.get(view);
  const docLength = view.state.doc.length;
  const ranges = snapshot?.ranges ?? view.state.selection.ranges;
  return ranges.map((range) =>
    EditorSelection.range(
      Math.min(range.anchor, docLength),
      Math.min(range.head, docLength),
    ),
  );
}

function hasMultilineSelection(
  view: EditorView,
  ranges: readonly SelectionRange[],
): boolean {
  return ranges.some(
    (range) => view.state.doc.lineAt(range.from).number !== view.state.doc.lineAt(range.to).number,
  );
}

function removableIndentBefore(
  view: EditorView,
  position: number,
  unit: string,
): { from: number; to: number } | null {
  if (position === 0) {
    return null;
  }
  const line = view.state.doc.lineAt(position);
  const beforeCursor = view.state.doc.sliceString(line.from, position);
  const whitespace = /[ \t]*$/.exec(beforeCursor)?.[0] ?? "";
  if (!whitespace) {
    return null;
  }

  let remove = 0;
  if (whitespace.endsWith(unit)) {
    remove = unit.length;
  } else if (whitespace.endsWith("\t")) {
    remove = 1;
  } else {
    remove = Math.min(unit.length, whitespace.length);
  }

  return { from: position - remove, to: position };
}

function forEachSelectedLine(
  view: EditorView,
  ranges: readonly SelectionRange[],
  callback: (line: Line) => void,
) {
  const seen = new Set<number>();
  for (const range of ranges) {
    const fromLine = view.state.doc.lineAt(range.from);
    const to =
      range.to > range.from && range.to === view.state.doc.lineAt(range.to).from
        ? range.to - 1
        : range.to;
    const toLine = view.state.doc.lineAt(Math.max(range.from, to));

    for (let lineNumber = fromLine.number; lineNumber <= toLine.number; lineNumber += 1) {
      if (seen.has(lineNumber)) {
        continue;
      }
      seen.add(lineNumber);
      callback(view.state.doc.line(lineNumber));
    }
  }
}
