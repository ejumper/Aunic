import type { ClientRequestType, RequestPayload } from "./requests";
import type { EventPayload, ServerEventType } from "./events";

export interface ClientEnvelope<T extends ClientRequestType = ClientRequestType> {
  id: string;
  type: T;
  payload: RequestPayload<T>;
}

export interface ServerEnvelope<T extends ServerEventType = ServerEventType> {
  id: string;
  type: T;
  payload: EventPayload<T>;
}

export function createMessageId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `msg-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export function makeClientEnvelope<T extends ClientRequestType>(
  type: T,
  payload: RequestPayload<T>,
): ClientEnvelope<T> {
  return {
    id: createMessageId(),
    type,
    payload,
  };
}
