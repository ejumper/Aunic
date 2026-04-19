import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ModelOptionPayload } from "../../ws/types";

interface ModelPickerProps {
  models: ModelOptionPayload[];
  selectedIndex: number;
  disabled: boolean;
  onChange: (index: number) => void;
}

export function ModelPicker({ models, selectedIndex, disabled, onChange }: ModelPickerProps) {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const currentModel = models[selectedIndex];

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
    const openUp = window.innerHeight - rect.bottom < 250;
    return {
      position: "fixed",
      left: rect.left,
      minWidth: rect.width,
      zIndex: 1000,
      ...(openUp ? { bottom: window.innerHeight - rect.top + 4 } : { top: rect.bottom + 4 }),
    };
  }

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className="mode-pill model-picker-btn"
        disabled={disabled || models.length === 0}
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="picker-btn-label">{currentModel?.label ?? "Model"}</span>
        <span className="picker-caret" aria-hidden="true">{open ? "∧" : "∨"}</span>
      </button>
      {open &&
        createPortal(
          <div ref={dropdownRef} className="picker-dropdown" style={dropdownStyle()} role="listbox">
            {models.map((model, index) => (
              <button
                key={`${model.provider_name}:${model.profile_id ?? model.model}:${index}`}
                type="button"
                className={`picker-dropdown-item${index === selectedIndex ? " picker-dropdown-item--active" : ""}`}
                role="option"
                aria-selected={index === selectedIndex}
                onClick={() => {
                  onChange(index);
                  setOpen(false);
                }}
              >
                {model.label}
              </button>
            ))}
          </div>,
          document.body,
        )}
    </>
  );
}
