import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNoteEditorStore } from "../../state/noteEditor";
import { usePromptStore } from "../../state/prompt";
import { noteEditorRef } from "../../noteEditorRef";
import { promptEditorRef } from "../../promptEditorRef";
import { AT_COMMANDS, SLASH_COMMANDS } from "../../promptCommands";

const EDIT_COMMANDS = [
  { label: "Write/Edit Here", markers: "@>> <<@", open: "@>>", close: "<<@" },
  { label: "Include Only",    markers: "!>> <<!",  open: "!>>", close: "<<!" },
  { label: "Exclude Text",    markers: "%>> <<%",  open: "%>>", close: "<<%" },
  { label: "Protect Text",    markers: "$>> <<$",  open: "$>>", close: "<<$" },
] as const;

interface CmdsMenuProps {
  disabled?: boolean;
}

export function CmdsMenu({ disabled }: CmdsMenuProps) {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const selectedText = useNoteEditorStore((store) => store.selectedText);
  const draft = usePromptStore((store) => store.draft);
  const setDraft = usePromptStore((store) => store.setDraft);

  useEffect(() => {
    if (!open) return;
    function handleMousedown(e: MouseEvent) {
      if (buttonRef.current?.contains(e.target as Node)) return;
      if (!dropdownRef.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleMousedown);
    return () => document.removeEventListener("mousedown", handleMousedown);
  }, [open]);

  function dropdownStyle(): React.CSSProperties {
    const rect = buttonRef.current?.getBoundingClientRect();
    if (!rect) return { display: "none" };
    const openUp = window.innerHeight - rect.bottom < 400;
    return {
      position: "fixed",
      right: window.innerWidth - rect.right,
      minWidth: 230,
      zIndex: 1000,
      ...(openUp ? { bottom: window.innerHeight - rect.top + 4 } : { top: rect.bottom + 4 }),
    };
  }

  function applyEditCommand(openMarker: string, closeMarker: string) {
    const view = noteEditorRef.get();
    if (!view) return;
    const { from, to } = view.state.selection.main;
    view.dispatch({
      changes: [
        { from, insert: `${openMarker} ` },
        { from: to, insert: ` ${closeMarker}` },
      ],
    });
    view.focus();
    setOpen(false);
  }

  function insertPromptCommand(cmd: string) {
    const view = promptEditorRef.get();
    if (view) {
      const { from } = view.state.selection.main;
      view.dispatch({
        changes: { from, insert: `${cmd} ` },
        selection: { anchor: from + cmd.length + 1 },
      });
      view.focus();
    } else {
      const sep = draft.length > 0 && !draft.endsWith(" ") ? " " : "";
      setDraft(draft + sep + cmd + " ");
    }
    setOpen(false);
  }

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className="mode-pill cmds-picker-btn"
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="true"
        onClick={() => setOpen((v) => !v)}
      >
        Cmds
        <span className="picker-caret" aria-hidden="true">{open ? "∧" : "∨"}</span>
      </button>
      {open &&
        createPortal(
          <div ref={dropdownRef} className="picker-dropdown" style={dropdownStyle()}>
            <div className="picker-dropdown-group">
              <div className="picker-dropdown-group-label">Edit Commands</div>
              {EDIT_COMMANDS.map((cmd) => (
                <button
                  key={cmd.label}
                  type="button"
                  className="picker-dropdown-item"
                  disabled={!selectedText}
                  onClick={() => applyEditCommand(cmd.open, cmd.close)}
                >
                  <code className="picker-dropdown-code">{cmd.markers}</code>
                  <span>{cmd.label}</span>
                </button>
              ))}
            </div>
            <div className="picker-dropdown-group">
              <div className="picker-dropdown-group-label">Slash Commands</div>
              {SLASH_COMMANDS.map((cmd) => (
                <button
                  key={cmd}
                  type="button"
                  className="picker-dropdown-item"
                  onClick={() => insertPromptCommand(cmd)}
                >
                  <code className="picker-dropdown-code">{cmd}</code>
                </button>
              ))}
            </div>
            <div className="picker-dropdown-group">
              <div className="picker-dropdown-group-label">@ Commands</div>
              {AT_COMMANDS.map((cmd) => (
                <button
                  key={cmd}
                  type="button"
                  className="picker-dropdown-item"
                  onClick={() => insertPromptCommand(cmd)}
                >
                  <code className="picker-dropdown-code">{cmd}</code>
                </button>
              ))}
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}
