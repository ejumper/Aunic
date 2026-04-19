import type { WsRequestError } from "../ws/client";

export function isWsRequestError(error: unknown): error is WsRequestError {
  return (
    error instanceof Error &&
    "reason" in error &&
    typeof (error as WsRequestError).reason === "string"
  );
}
