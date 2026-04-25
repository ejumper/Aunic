import { describe, expect, it } from "vitest";
import { codeBlockVisualColumns } from "./activeLineRawMarkdown";

describe("activeLineRawMarkdown", () => {
  it("counts tabs as the editor's visual tab width when sizing code blocks", () => {
    expect(codeBlockVisualColumns("- Root")).toBe(6);
    expect(codeBlockVisualColumns("\t- Child")).toBe(10);
    expect(codeBlockVisualColumns("\t\t- Grandchild")).toBe(18);
  });
});
