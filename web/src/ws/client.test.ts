import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WsClient } from "./client";
import type { ClientEnvelope } from "./envelope";
import type { SessionStatePayload } from "./types";

const SOCKET_CONNECTING = 0;
const SOCKET_OPEN = 1;
const SOCKET_CLOSED = 3;

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  readyState = SOCKET_CONNECTING;
  sent: ClientEnvelope[] = [];
  closeCalls: Array<{ code?: number; reason?: string }> = [];

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(JSON.parse(data) as ClientEnvelope);
  }

  close(code?: number, reason?: string) {
    this.readyState = SOCKET_CLOSED;
    this.closeCalls.push({ code, reason });
    this.onclose?.(new CloseEvent("close"));
  }

  open() {
    this.readyState = SOCKET_OPEN;
    this.onopen?.(new Event("open"));
  }

  receive(envelope: object) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(envelope) }));
  }

  serverClose() {
    this.readyState = SOCKET_CLOSED;
    this.onclose?.(new CloseEvent("close"));
  }
}

const sessionPayload: SessionStatePayload = {
  instance_id: "instance-1",
  run_active: false,
  run_id: null,
  workspace_root: "/home/ejumps",
  default_mode: "note",
  mode: "note",
  work_mode: "off",
  models: [
    {
      label: "Codex",
      provider_name: "codex",
      model: "gpt-5.4",
      profile_id: null,
      context_window: null,
    },
  ],
  selected_model_index: 0,
  selected_model: {
    label: "Codex",
    provider_name: "codex",
    model: "gpt-5.4",
    profile_id: null,
    context_window: null,
  },
  pending_permission: null,
};

describe("WsClient", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("resolves a request from a matching response envelope", async () => {
    const client = makeClient({ autoHelloOnOpen: false });
    client.start();
    const socket = openLatestSocket();

    const promise = client.request("read_file", { path: "note.md" });
    const sent = socket.sent[0];
    socket.receive({
      id: sent.id,
      type: "response",
      payload: {
        path: "note.md",
        revision_id: "rev-1",
        content_hash: "hash",
        mtime_ns: 1,
        size_bytes: 4,
        captured_at: "2026-04-17T00:00:00Z",
        note_content: "body",
        transcript_rows: [],
        has_transcript: false,
      },
    });

    await expect(promise).resolves.toMatchObject({ path: "note.md", revision_id: "rev-1" });
  });

  it("resolves hello from a matching session_state envelope", async () => {
    const client = makeClient({ autoHelloOnOpen: false });
    client.start();
    const socket = openLatestSocket();

    const promise = client.request("hello", { instance_id: "instance-1", page_id: "page-1" });
    const sent = socket.sent[0];
    socket.receive({ id: sent.id, type: "session_state", payload: sessionPayload });

    await expect(promise).resolves.toMatchObject({ workspace_root: "/home/ejumps" });
  });

  it("rejects a request from a matching error envelope", async () => {
    const client = makeClient({ autoHelloOnOpen: false });
    client.start();
    const socket = openLatestSocket();

    const promise = client.request("read_file", { path: "../escape.md" });
    const sent = socket.sent[0];
    socket.receive({
      id: sent.id,
      type: "error",
      payload: { reason: "invalid_path", details: { path: "../escape.md" } },
    });

    await expect(promise).rejects.toMatchObject({
      reason: "invalid_path",
      details: { path: "../escape.md" },
    });
  });

  it("times out requests that never receive a matching envelope", async () => {
    const client = makeClient({ autoHelloOnOpen: false, requestTimeoutMs: 10 });
    client.start();
    openLatestSocket();

    const promise = client.request("hello", { instance_id: "instance-1", page_id: "page-1" });
    vi.advanceTimersByTime(10);

    await expect(promise).rejects.toMatchObject({ reason: "request_timeout" });
  });

  it("rejects requests while not connected", async () => {
    const client = makeClient({ autoHelloOnOpen: false });

    await expect(
      client.request("hello", { instance_id: "instance-1", page_id: "page-1" }),
    ).rejects.toMatchObject({
      reason: "not_connected",
    });
  });

  it("dispatches correlated responses to event subscribers", async () => {
    const client = makeClient({ autoHelloOnOpen: false });
    const handler = vi.fn();
    client.on("session_state", handler);
    client.start();
    const socket = openLatestSocket();

    const promise = client.request("hello", { instance_id: "instance-1", page_id: "page-1" });
    const sent = socket.sent[0];
    socket.receive({ id: sent.id, type: "session_state", payload: sessionPayload });
    await promise;

    expect(handler).toHaveBeenCalledWith(sessionPayload);
  });

  it("reconnects with backoff and sends hello after reconnect", async () => {
    const states: string[] = [];
    const client = makeClient({
      autoHelloOnOpen: true,
      helloPayload: { instance_id: "instance-1", page_id: "page-1" },
      reconnect: { initialDelayMs: 25, maxDelayMs: 25, factor: 1, jitter: 0 },
      onStateChange: (state) => states.push(state),
    });
    client.start();
    const firstSocket = openLatestSocket();
    expect(firstSocket.sent[0].type).toBe("hello");

    firstSocket.serverClose();
    expect(client.state).toBe("reconnecting");

    vi.advanceTimersByTime(25);
    expect(FakeWebSocket.instances).toHaveLength(2);
    const secondSocket = openLatestSocket();

    expect(secondSocket.sent[0].type).toBe("hello");
    expect(states).toEqual([
      "connecting",
      "open",
      "reconnecting",
      "connecting",
      "open",
    ]);
  });

  it("stop rejects all pending requests with client_closed", async () => {
    const client = makeClient({ autoHelloOnOpen: false });
    client.start();
    openLatestSocket();

    const promise = client.request("hello", { instance_id: "instance-1", page_id: "page-1" });
    client.stop();

    await expect(promise).rejects.toMatchObject({ reason: "client_closed" });
    expect(client.state).toBe("closed");
  });
});

function makeClient(options: Partial<ConstructorParameters<typeof WsClient>[0]> = {}) {
  return new WsClient({
    url: "ws://test/ws",
    webSocketCtor: FakeWebSocket,
    requestTimeoutMs: 1_000,
    reconnect: { jitter: 0 },
    helloPayload: { instance_id: "instance-1", page_id: "page-1" },
    ...options,
  });
}

function openLatestSocket(): FakeWebSocket {
  const socket = FakeWebSocket.instances.at(-1);
  if (!socket) {
    throw new Error("No fake WebSocket instance was created.");
  }
  socket.open();
  return socket;
}
