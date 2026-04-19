import type {
  ErrorPayload,
  FileChangedPayload,
  PendingPermissionPayload,
  ProgressEventPayload,
  SessionStatePayload,
  TranscriptRowEventPayload,
} from "./types";

export interface ServerEventMap {
  session_state: SessionStatePayload;
  progress_event: ProgressEventPayload;
  transcript_row: TranscriptRowEventPayload;
  file_changed: FileChangedPayload;
  permission_request: PendingPermissionPayload;
  response: unknown;
  error: ErrorPayload;
}

export type ServerEventType = keyof ServerEventMap;
export type EventPayload<T extends ServerEventType> = ServerEventMap[T];
