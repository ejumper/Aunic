import { describe, expect, it } from "vitest";
import { isKnownPromptCommand, parsePromptCommand } from "./promptCommands";

describe("prompt command parsing", () => {
  it("recognizes active slash and at commands", () => {
    expect(parsePromptCommand("/note continue")).toMatchObject({
      command: "/note",
      remaining: "continue",
    });
    expect(parsePromptCommand("@web spanning tree")).toMatchObject({
      command: "@web",
      remaining: "spanning tree",
    });
  });

  it("ignores unknown slash and at strings", () => {
    expect(parsePromptCommand("/nonsense send this to the model")).toBeNull();
    expect(parsePromptCommand("@unknown send this to the model")).toBeNull();
    expect(isKnownPromptCommand("/nonsense")).toBe(false);
  });
});
