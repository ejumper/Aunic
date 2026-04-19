import type { WorkMode } from "../../ws/types";

interface WorkModeSwitcherProps {
  workMode: WorkMode;
  disabled: boolean;
  onChange: (mode: WorkMode) => void;
}

const WORK_MODES: WorkMode[] = ["off", "read", "work"];

export function WorkModeSwitcher({
  workMode,
  disabled,
  onChange,
}: WorkModeSwitcherProps) {
  const nextMode = nextWorkMode(workMode);

  return (
    <button
      type="button"
      className="mode-pill mode-cycle-button"
      aria-label={`Agent mode ${labelForWorkMode(workMode)}. Switch to ${labelForWorkMode(nextMode)}.`}
      disabled={disabled}
      onClick={() => onChange(nextMode)}
    >
      Agent: {labelForWorkMode(workMode)}
    </button>
  );
}

function labelForWorkMode(mode: WorkMode): string {
  if (mode === "off") {
    return "Off";
  }
  if (mode === "read") {
    return "Read";
  }
  if (mode === "work") {
    return "Work";
  }
  return titleCase(String(mode));
}

function nextWorkMode(mode: WorkMode): WorkMode {
  const currentIndex = WORK_MODES.indexOf(mode);
  if (currentIndex === -1) {
    return WORK_MODES[0];
  }
  return WORK_MODES[(currentIndex + 1) % WORK_MODES.length];
}

function titleCase(value: string): string {
  if (!value) {
    return "Unknown";
  }
  return value.slice(0, 1).toUpperCase() + value.slice(1);
}
