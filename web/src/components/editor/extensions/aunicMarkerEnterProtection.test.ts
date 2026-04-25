import { EditorSelection, EditorState, type Extension } from "@codemirror/state";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { GFM } from "@lezer/markdown";
import { describe, expect, it } from "vitest";
import { EditorView, keymap } from "@codemirror/view";
import { insertNewlineContinueMarkup } from "@codemirror/lang-markdown";
import {
  aunicMarkerEnterProtection,
  isAunicMarkerOnlyLine,
  isUnindentedStrongLine,
} from "./aunicMarkerEnterProtection";

describe("aunicMarkerEnterProtection", () => {
  it("recognizes marker-only lines with optional whitespace", () => {
    expect(isAunicMarkerOnlyLine("@>>")).toBe(true);
    expect(isAunicMarkerOnlyLine(" <<@")).toBe(true);
    expect(isAunicMarkerOnlyLine("\t!>>  ")).toBe(true);
    expect(isAunicMarkerOnlyLine("prefix <<@")).toBe(false);
    expect(isAunicMarkerOnlyLine("<<@ suffix")).toBe(false);
  });

  it("keeps Enter on a closing marker after a list from continuing the list", () => {
    const state = createMarkdownState(
      [
        "@>>",
        "Minimum Frame Size = 64B",
        "- Non-packet =",
        "\t- Preamble + SFD = 8B",
        "\t- Header = 14B",
        "\t- Trailer = 4B",
        "<<@",
      ].join("\n"),
      7,
      [aunicMarkerEnterProtection()],
    );
    const updated = runEnter(state);

    expect(updated.doc.toString().split("\n")).toEqual([
      "@>>",
      "Minimum Frame Size = 64B",
      "- Non-packet =",
      "\t- Preamble + SFD = 8B",
      "\t- Header = 14B",
      "\t- Trailer = 4B",
      "<<@",
      "",
    ]);
  });

  it("keeps Enter after an unindented strong line from inheriting list indentation", () => {
    const state = createMarkdownState(
      ["- some list item", "**bold**"].join("\n"),
      2,
      [aunicMarkerEnterProtection()],
    );
    const updated = runEnter(state);

    expect(updated.doc.toString().split("\n")).toEqual([
      "- some list item",
      "**bold**",
      "",
    ]);
  });

  it("keeps Enter after an unindented bold-italic line from inheriting list indentation", () => {
    const state = createMarkdownState(
      ["- some list item", "***bold-italic***"].join("\n"),
      2,
      [aunicMarkerEnterProtection()],
    );
    const updated = runEnter(state);

    expect(updated.doc.toString().split("\n")).toEqual([
      "- some list item",
      "***bold-italic***",
      "",
    ]);
  });

  it("only protects unindented strong markers", () => {
    expect(isUnindentedStrongLine("**bold**")).toBe(true);
    expect(isUnindentedStrongLine("***bold-italic***")).toBe(true);
    expect(isUnindentedStrongLine("*italic*")).toBe(false);
    expect(isUnindentedStrongLine(" **nested bold**")).toBe(false);
  });

  it("documents the markdown behavior being guarded against", () => {
    const state = createMarkdownState(
      [
        "@>>",
        "Minimum Frame Size = 64B",
        "- Non-packet =",
        "\t- Preamble + SFD = 8B",
        "\t- Header = 14B",
        "\t- Trailer = 4B",
        "<<@",
      ].join("\n"),
      7,
      [],
    );
    const updated = runEnter(state);

    expect(updated.doc.line(7).text).toBe("- ");
  });

  it("documents the markdown strong-line lazy-list behavior being guarded against", () => {
    const state = createMarkdownState(
      ["- some list item", "**bold**"].join("\n"),
      2,
      [],
    );
    const updated = runEnter(state);

    expect(updated.doc.line(3).text).toBe("  ");
  });
});

function createMarkdownState(doc: string, cursorLine: number, extensions: Extension[]): EditorState {
  const base = EditorState.create({ doc });
  return EditorState.create({
    doc,
    selection: EditorSelection.cursor(base.doc.line(cursorLine).to),
    extensions: [
      markdown({
        base: markdownLanguage,
        extensions: GFM,
      }),
      ...extensions,
      keymap.of([{ key: "Enter", run: insertNewlineContinueMarkup }]),
    ],
  });
}

function runEnter(state: EditorState): EditorState {
  const parent = document.createElement("div");
  document.body.appendChild(parent);
  const view = new EditorView({ state, parent });
  const binding = state.facet(keymap).flat().find((item) => item.key === "Enter");
  expect(binding).toBeDefined();
  if (!binding?.run) {
    throw new Error("Enter binding not found.");
  }
  const handled = binding.run(view);
  expect(handled).toBe(true);
  const updated = view.state;
  view.destroy();
  parent.remove();
  return updated;
}
