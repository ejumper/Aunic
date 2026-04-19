import { makeClientEnvelope, type ClientEnvelope, type ServerEnvelope } from "./envelope";
import type { EventPayload, ServerEventType } from "./events";
import type { ClientRequestType, RequestPayload, RequestResponse } from "./requests";
import type { ErrorPayload } from "./types";

export type ConnectionState = "idle" | "connecting" | "open" | "reconnecting" | "closed";

export interface WsClientOptions {
  url: string;
  requestTimeoutMs?: number;
  reconnect?: {
    initialDelayMs?: number;
    maxDelayMs?: number;
    factor?: number;
    jitter?: number;
  };
  autoHelloOnOpen?: boolean;
  onStateChange?: (state: ConnectionState) => void;
  onOutgoing?: (env: ClientEnvelope) => void;
  webSocketCtor?: WebSocketConstructor;
}

export interface WsRequestError extends Error {
  reason: string;
  details?: Record<string, unknown>;
}

type WebSocketLike = Pick<WebSocket, "send" | "close" | "readyState"> & {
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
};

type WebSocketConstructor = {
  new (url: string): WebSocketLike;
};

type PendingRequest = {
  resolve: (value: unknown) => void;
  reject: (error: WsRequestError) => void;
  timer: ReturnType<typeof setTimeout>;
};

type ReconnectConfig = Required<NonNullable<WsClientOptions["reconnect"]>>;

const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;
const NORMAL_CLOSE_CODE = 1000;
const SOCKET_OPEN = 1;
const SOCKET_CLOSED = 3;
const DEFAULT_RECONNECT: ReconnectConfig = {
  initialDelayMs: 250,
  maxDelayMs: 5_000,
  factor: 2,
  jitter: 0.25,
};

export class WsClient {
  private readonly url: string;
  private readonly requestTimeoutMs: number;
  private readonly reconnect: ReconnectConfig;
  private readonly autoHelloOnOpen: boolean;
  private readonly onStateChange?: (state: ConnectionState) => void;
  private readonly initialOutgoingHandler?: (env: ClientEnvelope) => void;
  private readonly webSocketCtor: WebSocketConstructor;
  private readonly pending = new Map<string, PendingRequest>();
  private readonly eventHandlers = new Map<ServerEventType, Set<(payload: unknown) => void>>();
  private readonly anyHandlers = new Set<(env: ServerEnvelope) => void>();
  private readonly outgoingHandlers = new Set<(env: ClientEnvelope) => void>();
  private socket: WebSocketLike | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectDelayMs: number;
  private manuallyClosed = false;
  private connectionState: ConnectionState = "idle";

  constructor(options: WsClientOptions) {
    this.url = options.url;
    this.requestTimeoutMs = options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
    this.reconnect = { ...DEFAULT_RECONNECT, ...options.reconnect };
    this.reconnectDelayMs = this.reconnect.initialDelayMs;
    this.autoHelloOnOpen = options.autoHelloOnOpen ?? true;
    this.onStateChange = options.onStateChange;
    this.initialOutgoingHandler = options.onOutgoing;
    if (options.onOutgoing) {
      this.outgoingHandlers.add(options.onOutgoing);
    }
    this.webSocketCtor = options.webSocketCtor ?? WebSocket;
  }

  get state(): ConnectionState {
    return this.connectionState;
  }

  start(): void {
    this.manuallyClosed = false;
    this.clearReconnectTimer();
    if (this.socket && this.connectionState !== "closed") {
      return;
    }
    this.connect("connecting");
  }

  stop(): void {
    this.manuallyClosed = true;
    this.clearReconnectTimer();
    this.rejectAllPending(makeRequestError("client_closed"));
    const socket = this.socket;
    this.socket = null;
    if (socket && socket.readyState !== SOCKET_CLOSED) {
      socket.close(NORMAL_CLOSE_CODE, "client closed");
    }
    this.setState("closed");
  }

  request<T extends ClientRequestType>(
    type: T,
    payload: RequestPayload<T>,
  ): Promise<RequestResponse<T>> {
    if (this.connectionState !== "open" || !this.socket || this.socket.readyState !== SOCKET_OPEN) {
      return Promise.reject(makeRequestError("not_connected"));
    }

    const envelope = makeClientEnvelope(type, payload);
    return new Promise<RequestResponse<T>>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(envelope.id);
        reject(makeRequestError("request_timeout"));
      }, this.requestTimeoutMs);

      this.pending.set(envelope.id, {
        resolve: (value: unknown) => resolve(value as RequestResponse<T>),
        reject,
        timer,
      });

      try {
        this.socket?.send(JSON.stringify(envelope));
        this.dispatchOutgoing(envelope);
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(envelope.id);
        reject(makeRequestError("send_failed", { message: String(error) }));
      }
    });
  }

  on<E extends ServerEventType>(
    type: E,
    handler: (payload: EventPayload<E>) => void,
  ): () => void {
    const handlers = this.eventHandlers.get(type) ?? new Set<(payload: unknown) => void>();
    const wrapped = handler as (payload: unknown) => void;
    handlers.add(wrapped);
    this.eventHandlers.set(type, handlers);
    return () => {
      handlers.delete(wrapped);
      if (handlers.size === 0) {
        this.eventHandlers.delete(type);
      }
    };
  }

  onAny(handler: (env: ServerEnvelope) => void): () => void {
    this.anyHandlers.add(handler);
    return () => {
      this.anyHandlers.delete(handler);
    };
  }

  onOutgoing(handler: (env: ClientEnvelope) => void): () => void {
    this.outgoingHandlers.add(handler);
    return () => {
      if (handler !== this.initialOutgoingHandler) {
        this.outgoingHandlers.delete(handler);
      }
    };
  }

  private connect(nextState: ConnectionState): void {
    this.setState(nextState);
    const socket = new this.webSocketCtor(this.url);
    this.socket = socket;
    socket.onopen = () => this.handleOpen(socket);
    socket.onmessage = (event: MessageEvent) => this.handleMessage(event);
    socket.onerror = () => {
      if (this.connectionState === "connecting") {
        this.setState("reconnecting");
      }
    };
    socket.onclose = () => this.handleClose(socket);
  }

  private handleOpen(socket: WebSocketLike): void {
    if (this.socket !== socket || this.manuallyClosed) {
      return;
    }
    this.reconnectDelayMs = this.reconnect.initialDelayMs;
    this.setState("open");
    if (this.autoHelloOnOpen) {
      void this.request("hello", {}).catch(() => {
        // The connection state already reflects transport health; the UI can send hello manually.
      });
    }
  }

  private handleMessage(event: MessageEvent): void {
    const envelope = parseServerEnvelope(event.data);
    if (!envelope) {
      return;
    }

    const pending = this.pending.get(envelope.id);
    if (pending) {
      this.pending.delete(envelope.id);
      clearTimeout(pending.timer);
      if (envelope.type === "error") {
        pending.reject(toRequestError(envelope.payload as ErrorPayload));
      } else {
        pending.resolve(envelope.payload);
      }
    }

    this.dispatchEvent(envelope);
  }

  private handleClose(socket: WebSocketLike): void {
    if (this.socket !== socket) {
      return;
    }
    this.socket = null;
    if (this.manuallyClosed) {
      this.setState("closed");
      return;
    }
    this.rejectAllPending(makeRequestError("connection_closed"));
    this.scheduleReconnect();
  }

  private scheduleReconnect(): void {
    this.setState("reconnecting");
    const delay = withJitter(this.reconnectDelayMs, this.reconnect.jitter);
    this.reconnectDelayMs = Math.min(
      this.reconnect.maxDelayMs,
      Math.max(1, this.reconnectDelayMs * this.reconnect.factor),
    );
    this.clearReconnectTimer();
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.manuallyClosed) {
        this.connect("connecting");
      }
    }, delay);
  }

  private dispatchEvent(envelope: ServerEnvelope): void {
    for (const handler of this.anyHandlers) {
      handler(envelope);
    }
    const handlers = this.eventHandlers.get(envelope.type);
    if (!handlers) {
      return;
    }
    for (const handler of handlers) {
      handler(envelope.payload);
    }
  }

  private dispatchOutgoing(envelope: ClientEnvelope): void {
    for (const handler of this.outgoingHandlers) {
      handler(envelope);
    }
  }

  private rejectAllPending(error: WsRequestError): void {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private setState(state: ConnectionState): void {
    if (this.connectionState === state) {
      return;
    }
    this.connectionState = state;
    this.onStateChange?.(state);
  }
}

export function toRequestError(payload: ErrorPayload): WsRequestError {
  return makeRequestError(payload.reason, payload.details);
}

export function makeRequestError(
  reason: string,
  details?: Record<string, unknown>,
): WsRequestError {
  const error = new Error(reason) as WsRequestError;
  error.name = "WsRequestError";
  error.reason = reason;
  error.details = details;
  return error;
}

function parseServerEnvelope(data: unknown): ServerEnvelope | null {
  if (typeof data !== "string") {
    return null;
  }
  try {
    const parsed = JSON.parse(data) as Partial<ServerEnvelope>;
    if (
      typeof parsed.id !== "string" ||
      typeof parsed.type !== "string" ||
      parsed.payload === null ||
      typeof parsed.payload !== "object"
    ) {
      return null;
    }
    return parsed as ServerEnvelope;
  } catch {
    return null;
  }
}

function withJitter(delayMs: number, jitter: number): number {
  if (jitter <= 0) {
    return delayMs;
  }
  const spread = delayMs * jitter;
  const min = delayMs - spread;
  return Math.round(min + Math.random() * spread * 2);
}
