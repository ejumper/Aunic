export type BrowserMode = "note" | "chat";
export type WorkMode = "off" | "read" | "work" | string;
export type TranscriptRole = "system" | "user" | "assistant" | "tool" | string;
export type TranscriptRowType = "message" | "tool_call" | "tool_result" | "tool_error" | string;
export type FileChangedKind = "created" | "modified" | "deleted";
export type PermissionResolution = "once" | "always" | "reject";
export type ImageTransport =
  | "claude_sdk_multimodal"
  | "openai_chat_vision"
  | "unsupported";
export type ProgressEventKind =
  | "status"
  | "error"
  | "sleep_started"
  | "sleep_ended"
  | "file_written"
  | "tool_call"
  | "tool_result"
  | "tool_error"
  | (string & {});

export interface ModelOptionPayload {
  label: string;
  provider_name: string;
  model: string;
  profile_id: string | null;
  context_window: number | null;
  supports_images?: boolean;
  image_transport?: ImageTransport;
}

export interface PromptImageAttachmentPayload {
  name: string;
  data_base64: string;
  size_bytes: number | null;
}

export interface TranscriptRowPayload {
  row_number: number;
  role: TranscriptRole;
  type: TranscriptRowType;
  tool_name: string | null;
  tool_id: string | null;
  content: unknown;
}

export interface FileSnapshotPayload {
  path: string;
  revision_id: string;
  content_hash: string;
  mtime_ns: number;
  size_bytes: number;
  captured_at: string;
  note_content: string;
  transcript_rows: TranscriptRowPayload[];
  has_transcript: boolean;
}

export interface ProgressEventPayload {
  kind: ProgressEventKind;
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
  key: string | null;
  details: unknown;
}

export interface PendingPermissionPayload {
  permission_id: string;
  request: PermissionRequestPayload;
}

export interface EditorSettingsPayload {
  save_mode: "manual" | "auto";
}

export interface SessionStatePayload {
  instance_id: string;
  run_active: boolean;
  run_id: string | null;
  workspace_root: string;
  default_mode: BrowserMode;
  mode: BrowserMode;
  work_mode: WorkMode;
  models: ModelOptionPayload[];
  selected_model_index: number;
  selected_model: ModelOptionPayload;
  pending_permission: PendingPermissionPayload | null;
  research_state?: ResearchStatePayload;
  context_usage?: ContextUsagePayload;
  editor_settings?: EditorSettingsPayload;
  capabilities?: {
    prompt_commands?: boolean;
    research_flow?: boolean;
    plan_flow?: boolean;
  };
}

export interface ContextUsagePayload {
  tokens_used: number | null;
  context_window: number | null;
  fraction: number | null;
  last_note_chars: number | null;
}

export type ResearchMode = "idle" | "results" | "chunks";
export type ResearchSource = "web" | "rag";
export type ResearchBusy = "searching" | "fetching" | "inserting";

export interface ResearchResultPayload {
  title: string;
  url: string | null;
  snippet: string;
  source: string | null;
  result_id: string | null;
  local_path: string | null;
  score: number | null;
  heading_path: string[];
}

export interface ResearchChunkPayload {
  title: string;
  url: string;
  text: string;
  score: number;
  heading_path: string[];
  chunk_id: string;
  chunk_order: number | null;
  is_match: boolean;
}

export interface ResearchPacketPayload {
  title: string;
  url: string | null;
  full_text_available: boolean;
  source: string | null;
  result_id: string | null;
  total_chunks: number | null;
  truncated: boolean;
  chunks: ResearchChunkPayload[];
}

export interface ResearchStatePayload {
  mode: ResearchMode;
  source: ResearchSource | null;
  query: string;
  scope: string | null;
  busy: ResearchBusy | null;
  results: ResearchResultPayload[];
  packet: ResearchPacketPayload | null;
}

export interface FileChangedPayload {
  path: string;
  revision_id: string | null;
  kind: FileChangedKind;
  exists: boolean;
  captured_at: string;
}

export interface TranscriptRowEventPayload {
  path: string;
  row: TranscriptRowPayload;
}

export interface NoteToolResultEventPayload {
  path: string;
  tool_name: "note_edit" | "note_write";
  content: Record<string, unknown>;
  snapshot: FileSnapshotPayload;
}

export interface ErrorPayload {
  reason: string;
  details?: Record<string, unknown>;
}

export interface FileEntryPayload {
  name: string;
  kind: "dir" | "file";
  path: string;
}

export interface ListFilesPayload {
  path: string;
  entries: FileEntryPayload[];
}

export interface ProjectNodePayload {
  id: string;
  path: string;
  name: string;
  kind: "dir" | "file";
  scope: "entry" | "child";
  active: boolean;
  effective_active: boolean;
  checkable: boolean;
  removable: boolean;
  exists: boolean;
  openable: boolean;
  recursive: boolean;
  children: ProjectNodePayload[];
}

export interface ProjectPlanPayload {
  id: string;
  plan_id: string;
  path: string;
  name: string;
  title: string;
  status: string;
  active: boolean;
  exists: boolean;
  openable: boolean;
}

export interface ProjectStatePayload {
  source_file: string;
  entries: ProjectNodePayload[];
  plans: ProjectPlanPayload[];
  active_plan_id: string | null;
}
