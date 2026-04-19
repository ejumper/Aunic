import type { Extension } from "@codemirror/state";
import { keymap } from "@codemirror/view";

interface PromptKeymapOptions {
  onSubmit: () => void;
  onCancel: () => void;
  isRunActive: () => boolean;
}

export function promptKeymap({
  onSubmit,
  onCancel,
  isRunActive,
}: PromptKeymapOptions): Extension {
  return keymap.of([
    {
      key: "Shift-Enter",
      preventDefault: true,
      run: () => {
        onSubmit();
        return true;
      },
    },
    {
      key: "Escape",
      preventDefault: true,
      run: () => {
        if (!isRunActive()) {
          return false;
        }
        onCancel();
        return true;
      },
    },
  ]);
}
