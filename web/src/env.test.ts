import { describe, expect, it } from "vitest";
import { resolveWsUrl, sameOriginWsUrl } from "./env";

describe("env", () => {
  const httpsLocation = { protocol: "https:", host: "notes.example.com" } as const;

  it("uses same-origin ws when no override is configured", () => {
    expect(resolveWsUrl({ DEV: false }, httpsLocation)).toBe("wss://notes.example.com/ws");
  });

  it("uses the override in dev builds", () => {
    expect(
      resolveWsUrl(
        { DEV: true, VITE_AUNIC_WS_URL: "ws://127.0.0.1:8766/ws" },
        httpsLocation,
      ),
    ).toBe("ws://127.0.0.1:8766/ws");
  });

  it("ignores the override in production unless explicitly allowed", () => {
    expect(
      resolveWsUrl(
        { DEV: false, VITE_AUNIC_WS_URL: "wss://192.168.48.193:8767/ws" },
        httpsLocation,
      ),
    ).toBe("wss://notes.example.com/ws");
  });

  it("allows a production override when explicitly opted in", () => {
    expect(
      resolveWsUrl(
        {
          DEV: false,
          VITE_AUNIC_WS_URL: "ws://127.0.0.1:8766/ws",
          VITE_AUNIC_ALLOW_PROD_WS_URL: "true",
        },
        httpsLocation,
      ),
    ).toBe("ws://127.0.0.1:8766/ws");
  });

  it("builds ws URLs for non-https origins", () => {
    expect(sameOriginWsUrl({ protocol: "http:", host: "localhost:4173" })).toBe(
      "ws://localhost:4173/ws",
    );
  });
});
