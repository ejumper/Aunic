import type { BrowserMode } from "../../ws/types";

interface ModeSwitcherProps {
  mode: BrowserMode;
  disabled: boolean;
  onChange: (mode: BrowserMode) => void;
}

export function ModeSwitcher({ mode, disabled, onChange }: ModeSwitcherProps) {
  const nextMode = mode === "note" ? "chat" : "note";

  return (
    <button
      type="button"
      className="mode-pill mode-cycle-button"
      aria-label={`Mode ${labelForMode(mode)}. Switch to ${labelForMode(nextMode)}.`}
      disabled={disabled}
      onClick={() => onChange(nextMode)}
    >
      <span className="prompt-control-label">Mode: {labelForMode(mode)}</span>
      <img
        className="prompt-control-icon"
        src={iconForMode(mode)}
        alt=""
        aria-hidden="true"
      />
    </button>
  );
}

function labelForMode(mode: BrowserMode): string {
  return mode === "note" ? "Note" : "Chat";
}

function iconForMode(mode: BrowserMode): string {
  return mode === "note" ? "/icons/note.svg" : "/icons/chat.svg";
}
