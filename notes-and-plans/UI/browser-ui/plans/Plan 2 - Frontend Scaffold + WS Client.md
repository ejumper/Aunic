# Plan 2 — Frontend Scaffold + WS Client

## Context

Plan 1 shipped the backend WebSocket server at `ws://<host>:<port>/ws` with a known envelope shape and typed client/server messages (see [Plan 1](../../UI/browser-ui/plans/Plan%201-%20Backend%20WS%20Server.md) and [src/aunic/browser/messages.py](../../../src/aunic/browser/messages.py)). Plan 2 establishes the frontend tree that all later plans build on: a Vite + React + TypeScript project with a typed WebSocket client and a tiny diagnostic UI. No editor, no file tree, no transcript rendering — just "can connect, exchange typed messages, and observe what the server pushes." Plans 3–6 each layer real UI on top of this foundation; Plan 7 adds PWA polish.

Per [browserUI-overview.md](../../UI/browser-ui/browserUI-overview.md): TypeScript + React + Vite, WS-only with `id`-based correlation, wire types mirror backend dataclasses 1:1 (no parallel vocabulary), editor state stays local, backend owns run state.

---

## Scope

### In Plan 2
- Top-level `web/` project: Vite + React 18 + TypeScript, ES2022 target, mobile-friendly defaults.
- Typed `WsClient` with request/response correlation, broadcast subscriptions, reconnect with backoff, per-request timeout.
- TS wire types in `web/src/ws/types.ts` mirroring [messages.py](../../../src/aunic/browser/messages.py) serializers.
- React context + `useWs()` hook exposing the client + connection state.
- Minimal diagnostic shell: connection-state badge, "send hello" button rendering the `session_state` JSON, `read_file` input, scrollable last-N raw message log.
- Vite dev server proxies `/ws` → backend so `pnpm dev` (or `npm run dev`) talks to a running `aunic serve` without CORS fuss.
- Vitest unit tests for `WsClient` using a fake WebSocket (no network).

### Deferred to later plans
- React Aria Tree file explorer → Plan 3.
- CodeMirror 6 note editor, markdown rendering, save flow → Plan 4.
- Transcript view + row controls → Plan 5.
- Prompt composer, run submission UX, model/mode pickers, `run_active` spinner → Plan 6.
- PWA manifest, iOS tweaks, Playwright → Plan 7.
- Any visual design pass beyond "unstyled functional" — styles now would be thrown away when real UI lands.
- Authentication, CSRF, TLS — all per `security/security-overview.md`, later.

---

## Defaults chosen (adjust on approval if desired)

| Decision | Default | Reason |
|---|---|---|
| Frontend dir | `/home/ejumps/HalfaCloud/Aunic/web/` | Sibling to `src/` and `tests/`; matches how `src/aunic/browser/` was added as a separate tree. |
| Package manager | `pnpm` | Fast, disk-efficient, good lockfile semantics; npm is fine too — trivial to switch. |
| State store | `zustand` | Tiny (~1kb), React-first, no provider boilerplate; avoids overbuilding a Redux shell for a scaffolding PR. |
| Test runner | `vitest` | Shares Vite config + TS setup, runs in Node w/ jsdom. |
| Styling | Plain CSS + CSS modules for the dev shell | No Tailwind/styled-components yet — real styling belongs to the plan that owns the component. |

---

## Repo layout

```
web/
  package.json
  pnpm-lock.yaml            # (or package-lock.json if npm)
  tsconfig.json
  tsconfig.node.json        # for vite.config.ts
  vite.config.ts
  index.html
  .eslintrc.cjs             # minimal; strict TS via tsc, lint rules kept tight but small
  .gitignore
  src/
    main.tsx                # React entry; mounts <App/>
    App.tsx                 # diagnostic shell
    env.ts                  # VITE_AUNIC_WS_URL default + resolution
    ws/
      envelope.ts           # MessageEnvelope type, id generator (crypto.randomUUID)
      types.ts              # TS mirrors of messages.py serializers (FileSnapshot, TranscriptRow, ProgressEvent, PermissionRequest, SessionState, FileChanged)
      requests.ts           # ClientRequestType union + per-type payload/response TS interfaces
      events.ts             # ServerEventType union + per-type payload TS interfaces
      client.ts             # WsClient class (the core of Plan 2)
      client.test.ts        # vitest unit tests using a fake WebSocket
      context.tsx           # <WsProvider/> + useWs() hook + useConnectionState()
    state/
      session.ts            # zustand store seeded from session_state events
    components/
      ConnectionBadge.tsx
      HelloPanel.tsx        # button → request("hello") → pretty JSON
      ReadFilePanel.tsx     # input + button → request("read_file", {path})
      RawLog.tsx            # ring buffer of last N raw envelopes (both directions)
    styles/
      app.css               # bare dev styles; replaced in later plans
tests/
  # no Python tests added in Plan 2; frontend tests live in web/
pyproject.toml              # unchanged
```

Rationale for `web/` at repo root (vs `src/web/` or `frontend/`): keeps JS tooling (node_modules, lockfile, vite cache) cleanly separated from the Python package in `src/aunic/`, and leaves room for Plan 7 to add a production build step that a future CLI can serve.

---

## `WsClient` design (core of this plan)

File: `web/src/ws/client.ts`.

### Public surface

```ts
export type ConnectionState =
  | "idle"
  | "connecting"
  | "open"
  | "reconnecting"
  | "closed";

export interface WsClientOptions {
  url: string;                    // e.g. ws://127.0.0.1:8765/ws (or same-origin /ws in prod)
  requestTimeoutMs?: number;      // default 15_000
  reconnect?: {
    initialDelayMs?: number;      // default 250
    maxDelayMs?: number;          // default 5_000
    factor?: number;              // default 2
    jitter?: number;              // default 0.25
  };
  autoHelloOnOpen?: boolean;      // default true
  onStateChange?: (s: ConnectionState) => void;
}

export interface WsRequestError extends Error {
  reason: string;                 // matches server error.reason
  details?: Record<string, unknown>;
}

export class WsClient {
  constructor(opts: WsClientOptions);
  start(): void;
  stop(): void;                   // graceful close, reject pending w/ "client_closed"
  request<T = unknown>(type: ClientRequestType, payload: object): Promise<T>;
  on<E extends ServerEventType>(type: E, handler: (payload: EventPayload<E>) => void): () => void;
  onAny(handler: (env: ServerEnvelope) => void): () => void;  // used by RawLog
  get state(): ConnectionState;
}
```

### Correlation rules (important subtlety from Plan 1)

Looking at [connection.py:107-118](../../../src/aunic/browser/connection.py#L107): every client request gets a response with the same envelope `id`, but the response **type varies**:
- Most requests → `type: "response"` with matching id.
- `hello` → `type: "session_state"` with matching id (not "response").
- Any request that fails → `type: "error"` with matching id.

The client must therefore key correlation on **envelope id, not envelope type**. Routing logic:
```ts
onMessage(env: ServerEnvelope) {
  const pending = this.pending.get(env.id);
  if (pending) {
    this.pending.delete(env.id);
    clearTimeout(pending.timer);
    if (env.type === "error") pending.reject(toRequestError(env.payload));
    else pending.resolve(env.payload);
    // Also dispatch to any subscribers so session_state replay works uniformly:
    this.dispatchEvent(env);
    return;
  }
  this.dispatchEvent(env);
}
```
Dispatching the envelope to subscribers even on correlated responses means a single `session_state` handler can seed the store whether the event came from the initial `hello` response or a later broadcast.

### Reconnect + hydration

- Exponential backoff with jitter between connection attempts.
- On (re)connect open: if `autoHelloOnOpen`, send `hello` so the server replays `session_state` (including `pending_permission`) — this matches the overview's "reconnect = refetch + subscribe live" model.
- Pending requests issued while `!== "open"` are queued and flushed on open, OR rejected immediately — choose **reject immediately** with `reason: "not_connected"` so UI code stays explicit about transitions. Queuing silently would mask the disconnect.
- `onStateChange` drives the connection badge and any "reconnecting…" banners later plans add.

### Request timeout

- Per request: `setTimeout(..., opts.requestTimeoutMs)` that rejects with `reason: "request_timeout"` and evicts the entry. Prevents leaks when server loses a message or spec drifts.

---

## Wire types (`web/src/ws/types.ts`)

Manual TS interfaces mirroring the shapes in [messages.py](../../../src/aunic/browser/messages.py). Naming mirrors the backend. Example:

```ts
export interface FileSnapshotPayload {
  path: string;
  revision_id: string;
  content_hash: string;
  mtime_ns: number;
  size_bytes: number;
  captured_at: string;              // ISO-8601 UTC
  note_content: string;
  transcript_rows: TranscriptRowPayload[];
  has_transcript: boolean;
}

export interface TranscriptRowPayload {
  row_number: number;
  role: string;
  type: string;
  tool_name: string | null;
  tool_id: string | null;
  content: unknown;                 // _jsonable() output; structure varies by row type
}

export interface ProgressEventPayload {
  kind: string;
  message: string;
  path: string | null;
  details: Record<string, unknown>;
}

export interface PermissionRequestPayload {
  tool_name: string;
  action: string;
  target: string;
  message: string;
  policy: string;
  key: string;
  details: unknown;
}

export interface SessionStatePayload {
  run_active: boolean;
  run_id: string | null;
  workspace_root: string;
  default_mode: "note" | "chat";
  mode: "note" | "chat";
  work_mode: string;
  models: ModelOptionPayload[];
  selected_model_index: number;
  selected_model: ModelOptionPayload;
  pending_permission: { permission_id: string; request: PermissionRequestPayload } | null;
}

export interface FileChangedPayload {
  path: string;
  revision_id: string | null;
  kind: "created" | "modified" | "deleted";
  exists: boolean;
  captured_at: string;
}

export interface ErrorPayload { reason: string; details?: Record<string, unknown>; }
```

Discriminated unions in `requests.ts` / `events.ts` tie type strings to payload shapes so `client.request("read_file", {path})` infers response as `FileSnapshotPayload`.

**Drift control**: add a single Python test `tests/test_browser_wire_parity.py` that asserts the names of fields produced by each serializer match a fixed list. If someone renames a field in `messages.py`, that test fails, reminding them to update `web/src/ws/types.ts`. (Cheap, no real schema sync needed for v1.)

---

## Vite config

`web/vite.config.ts`:
```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/ws": { target: "ws://127.0.0.1:8765", ws: true, changeOrigin: true },
    },
  },
  build: { target: "es2022", sourcemap: true },
});
```

During `pnpm dev`, browser connects to `ws://localhost:5173/ws` → Vite proxies to Aunic's `ws://127.0.0.1:8765/ws`. The WS URL resolves through `env.ts`:
```ts
export const wsUrl = import.meta.env.VITE_AUNIC_WS_URL
  ?? `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`;
```
This way, dev + prod use the same URL; only `.env.local` overrides it when you want to skip the proxy.

---

## App shell (Plan 2 UI)

Three panels stacked vertically with minimal CSS:

1. **ConnectionBadge** — shows `state`, last error if any, last successful connect time. Pulls from `useConnectionState()`.
2. **HelloPanel** — `[Send hello]` button → `client.request("hello", {})` → renders returned `SessionStatePayload` as pretty JSON. Also wires `client.on("session_state", ...)` to re-render on broadcast updates so the panel reacts to run state transitions.
3. **ReadFilePanel** — text input for relative path → `[Read]` calls `client.request("read_file", {path})`. Renders path, revision_id, note_content length, transcript_rows length. On error: shows `reason` + `details`.
4. **RawLog** — ring buffer of the last 50 envelopes (both directions). Each entry: timestamp, direction, pretty JSON. Subscribes via `client.onAny(...)` and a `beforeSend` hook on `WsClient`.

No routing, no layout system, no theme. These components are throwaway scaffolding; Plan 3+ replaces them.

---

## State management

`web/src/state/session.ts` holds a zustand store:
```ts
interface SessionSlice {
  session: SessionStatePayload | null;
  setSession: (s: SessionStatePayload) => void;
}
```
`WsProvider` on mount calls `client.on("session_state", setSession)` once, so the store is always current. Later plans add stores for open file, transcript rows, permissions — Plan 2 seeds the pattern with this one slice.

---

## Dependencies (web/package.json)

Runtime:
- `react@^18.3`, `react-dom@^18.3`
- `zustand@^4.5`

Dev:
- `typescript@^5.5`
- `vite@^5.4`
- `@vitejs/plugin-react@^4.3`
- `@types/react`, `@types/react-dom`
- `vitest@^2`, `jsdom@^24`
- `mock-socket@^9` — fake WebSocket for `client.test.ts`
- `eslint@^9`, `@typescript-eslint/*`, `eslint-plugin-react-hooks` (keep config small)

No Pydantic-equivalent runtime validator (zod / valibot). Wire types are hand-maintained; see drift-control note above.

---

## Critical files to read / reuse / modify

**Read before implementing:**
- [src/aunic/browser/messages.py](../../../src/aunic/browser/messages.py) — the authoritative serializer shapes; every TS interface must match.
- [src/aunic/browser/connection.py](../../../src/aunic/browser/connection.py) — request dispatch + the `hello` → `session_state` special case.
- [src/aunic/browser/session.py](../../../src/aunic/browser/session.py) — understand what `session_state` carries (pending_permission, run_id, models) so the TS shape is right.
- [browserUI-overview.md](../../UI/browser-ui/browserUI-overview.md) § "Browser Server / Transport" and § "Run-state hydration".

**Create:**
- Everything under `web/`.

**Modify:**
- `.gitignore` at repo root — add `web/node_modules`, `web/dist`, `web/.vite`.
- Optionally `tests/test_browser_wire_parity.py` — field-name parity test described in Wire types.

**Do not modify in Plan 2:**
- Any file under `src/aunic/` — the backend is the contract; this plan only consumes it.

---

## Verification

### Unit tests (`web/src/ws/client.test.ts`, vitest)
- `request` resolves with `response` payload when envelope id matches.
- `request` resolves with `session_state` payload when server replies to `hello` (special-case response type).
- `request` rejects with `WsRequestError{reason, details}` on `type: "error"` envelope.
- `request` rejects with `reason: "request_timeout"` when no reply before `requestTimeoutMs`.
- `request` rejects with `reason: "not_connected"` when called while state !== "open".
- `on(event, handler)` receives server-pushed events and correlated responses alike.
- Reconnect: fake socket closes → client transitions to `reconnecting` → reopens → emits `connecting → open` and auto-sends `hello` when `autoHelloOnOpen`.
- `stop()` rejects all pending requests with `reason: "client_closed"`.

Use `mock-socket` to stub WebSocket without touching the network.

### Wire parity test (`tests/test_browser_wire_parity.py`)
For each serializer in `aunic.browser.messages`, build a minimal input and assert the resulting dict has exactly the expected set of keys. Fails loudly if a field is renamed/added without updating TS.

### Manual verification
```bash
# terminal 1
aunic serve --workspace-root /home/ejumps

# terminal 2
cd web
pnpm install
pnpm dev
# open http://localhost:5173
```
Expected:
- Connection badge flips to "Connected" within ~1s.
- Clicking `[Send hello]` renders a `session_state` JSON blob containing `run_active: false`, the workspace root, and a non-empty `models` list.
- Entering `notes-and-plans/UI/browser-ui/browserUI-overview.md` into ReadFilePanel returns a `FileSnapshot` with matching revision_id, non-empty `note_content`, and `has_transcript: false`.
- Entering a path with `..` (e.g. `../etc/passwd`) returns an `error` envelope with `reason` indicating path rejection (validates Plan 1 + Plan 2 end-to-end).
- Killing the Aunic server → badge flips to `reconnecting`. Restarting it → badge returns to `open` and `[Send hello]` works again without page reload.

### Done criteria
- `pnpm install && pnpm build && pnpm test` all pass inside `web/`.
- Dev workflow (`aunic serve` + `pnpm dev`) yields a live connection and working `hello` / `read_file` round-trips against the real backend.
- `WsClient` is the sole WebSocket entry point; no component opens a raw socket.
- TS types for every serializer in `messages.py` exist in `ws/types.ts`.
- Wire parity test green.
- Plan 3 can start: file tree components import `useWs()` and call `client.request("list_files", {subpath})` with no scaffolding work left.
