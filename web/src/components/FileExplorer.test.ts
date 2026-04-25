import { describe, expect, it } from "vitest";

import { filterEntries } from "./FileExplorer";

describe("filterEntries", () => {
  const entries = [
    { name: "docs", kind: "dir", path: "docs" },
    { name: "note.md", kind: "file", path: "note.md" },
    { name: "image.png", kind: "file", path: "image.png" },
    { name: ".secret.md", kind: "file", path: ".secret.md" },
  ] as const;

  it("shows only markdown files by default while keeping directories", () => {
    expect(
      filterEntries(entries.slice(), {
        showHidden: false,
        showOnlyMarkdown: true,
      }),
    ).toEqual([
      { name: "docs", kind: "dir", path: "docs" },
      { name: "note.md", kind: "file", path: "note.md" },
    ]);
  });

  it("shows all non-hidden files when markdown-only filtering is disabled", () => {
    expect(
      filterEntries(entries.slice(), {
        showHidden: false,
        showOnlyMarkdown: false,
      }),
    ).toEqual([
      { name: "docs", kind: "dir", path: "docs" },
      { name: "note.md", kind: "file", path: "note.md" },
      { name: "image.png", kind: "file", path: "image.png" },
    ]);
  });

  it("respects hidden-file visibility independently of markdown filtering", () => {
    expect(
      filterEntries(entries.slice(), {
        showHidden: true,
        showOnlyMarkdown: true,
      }),
    ).toEqual([
      { name: "docs", kind: "dir", path: "docs" },
      { name: "note.md", kind: "file", path: "note.md" },
      { name: ".secret.md", kind: "file", path: ".secret.md" },
    ]);
  });
});
