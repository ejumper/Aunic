import { describe, expect, it } from "vitest";
import {
  detectTrigger,
  normalizeLeadingSpacesToTabs,
} from "./fourSpaceIndent";

describe("fourSpaceIndent", () => {
  it("converts four leading spaces to a tab", () => {
    expect(normalizeLeadingSpacesToTabs("    ")).toBe("\t");
  });

  it("converts repeated four-space groups in leading indentation", () => {
    expect(normalizeLeadingSpacesToTabs("\t        ")).toBe("\t\t\t");
  });

  it("rewrites leading spaces before text when the caret is still in the indent", () => {
    expect(detectTrigger(10, "    item", [14])).toEqual({
      from: 10,
      to: 14,
      insert: "\t",
    });
  });

  it("does not rewrite when the caret is outside the leading whitespace", () => {
    expect(detectTrigger(10, "    item", [18])).toBeNull();
  });

  it("does not react to the old sentinel trigger text", () => {
    expect(detectTrigger(0, ">|", [2])).toBeNull();
  });
});
