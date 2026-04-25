import { EditorState } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { afterEach, describe, expect, it, vi } from "vitest";
import { monospaceTabWidth } from "./monospaceTabWidth";

describe("monospaceTabWidth", () => {
  const originalGetBoundingClientRect = HTMLElement.prototype.getBoundingClientRect;

  afterEach(() => {
    HTMLElement.prototype.getBoundingClientRect = originalGetBoundingClientRect;
    document.body.replaceChildren();
    vi.restoreAllMocks();
  });

  it("measures three monospace columns and writes the tab width variable", () => {
    HTMLElement.prototype.getBoundingClientRect = function () {
      const element = this as HTMLElement;
      return new DOMRect(0, 0, element.classList.contains("cm-aunic-tab-measure") ? 33 : 0, 0);
    };

    const parent = document.createElement("div");
    document.body.appendChild(parent);
    const view = new EditorView({
      parent,
      state: EditorState.create({
        doc: "one\ttwo",
        extensions: [monospaceTabWidth()],
      }),
    });

    expect(view.dom.style.getPropertyValue("--aunic-tab-width")).toBe("33px");
    expect(parent.querySelector(".cm-aunic-tab-measure")?.textContent).toBe("000");

    view.destroy();
  });
});
