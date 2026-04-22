import type {
  ErrorPayload,
  FileChangedPayload,
  NoteToolResultEventPayload,
  PendingPermissionPayload,
  ProgressEventPayload,
  SessionStatePayload,
  TranscriptRowEventPayload,
} from "./types";

export interface ServerEventMap {
  session_state: SessionStatePayload;
  progress_event: ProgressEventPayload;
  note_tool_result: NoteToolResultEventPayload;
  transcript_row: TranscriptRowEventPayload;
  file_changed: FileChangedPayload;
  permission_request: PendingPermissionPayload;
  response: unknown;
  error: ErrorPayload;
}

export type ServerEventType = keyof ServerEventMap;
export type EventPayload<T extends ServerEventType> = ServerEventMap[T];
