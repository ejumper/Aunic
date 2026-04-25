import type {
  FileSnapshotPayload,
  ListFilesPayload,
  PermissionResolution,
  BrowserMode,
  ModelOptionPayload,
  PromptImageAttachmentPayload,
  WorkMode,
  SessionStatePayload,
  ProjectStatePayload,
} from "./types";

export interface ClientRequestMap {
  hello: {
    payload: {
      instance_id: string;
      page_id: string;
    };
    response: SessionStatePayload;
  };
  list_files: {
    payload: { subpath?: string };
    response: ListFilesPayload;
  };
  read_file: {
    payload: { path: string };
    response: FileSnapshotPayload;
  };
  create_file: {
    payload: { path: string };
    response: FileSnapshotPayload;
  };
  create_directory: {
    payload: { path: string };
    response: { path: string; kind: "dir" };
  };
  rename_entry: {
    payload: { path: string; new_name: string };
    response: { path: string; old_path: string; kind: "file" | "dir" };
  };
  delete_entry: {
    payload: { path: string };
    response: { path: string; kind: "file" | "dir" };
  };
  get_project_state: {
    payload: { source_file: string };
    response: ProjectStatePayload;
  };
  add_include: {
    payload: {
      source_file: string;
      target_path: string;
      recursive?: boolean;
    };
    response: ProjectStatePayload;
  };
  create_plan: {
    payload: {
      source_file: string;
      title: string;
    };
    response: ProjectStatePayload;
  };
  delete_plan: {
    payload: {
      source_file: string;
      plan_id: string;
    };
    response: ProjectStatePayload;
  };
  remove_include_entry: {
    payload: {
      source_file: string;
      include_path: string;
    };
    response: ProjectStatePayload;
  };
  set_active_plan: {
    payload: {
      source_file: string;
      plan_id: string | null;
    };
    response: ProjectStatePayload;
  };
  set_include_entry_active: {
    payload: {
      source_file: string;
      include_path: string;
      active: boolean;
    };
    response: ProjectStatePayload;
  };
  set_project_child_active: {
    payload: {
      source_file: string;
      child_path: string;
      active: boolean;
    };
    response: ProjectStatePayload;
  };
  delete_transcript_row: {
    payload: {
      path: string;
      row_number: number;
      expected_revision: string | null;
    };
    response: FileSnapshotPayload;
  };
  delete_search_result: {
    payload: {
      path: string;
      row_number: number;
      result_index: number;
      expected_revision: string | null;
    };
    response: FileSnapshotPayload;
  };
  write_file: {
    payload: {
      path: string;
      text: string;
      expected_revision: string | null;
    };
    response: FileSnapshotPayload;
  };
  set_mode: {
    payload: { mode: BrowserMode };
    response: { mode: BrowserMode };
  };
  set_work_mode: {
    payload: { work_mode: WorkMode };
    response: { work_mode: WorkMode };
  };
  select_model: {
    payload: { index: number };
    response: {
      selected_model_index: number;
      selected_model: ModelOptionPayload;
    };
  };
  submit_prompt: {
    payload: {
      active_file: string;
      included_files: string[];
      text: string;
      image_attachments: PromptImageAttachmentPayload[];
    };
    response: { run_id: string };
  };
  run_prompt_command: {
    payload: {
      active_file: string;
      text: string;
    };
    response: {
      handled: boolean;
      draft: string;
      message: string;
      run_id: string | null;
      snapshot: FileSnapshotPayload | null;
    };
  };
  research_fetch_result: {
    payload: {
      active_file: string;
      result_index: number;
    };
    response: FileSnapshotPayload;
  };
  research_insert_chunks: {
    payload: {
      active_file: string;
      mode: "selected_chunks" | "full_page";
      chunk_indices?: number[];
    };
    response: FileSnapshotPayload;
  };
  research_back: {
    payload: Record<string, never>;
    response: { ok: boolean };
  };
  research_cancel: {
    payload: Record<string, never>;
    response: { ok: boolean };
  };
  cancel_run: {
    payload: { run_id: string | null };
    response: { cancelled: boolean };
  };
  resolve_permission: {
    payload: {
      permission_id: string;
      resolution: PermissionResolution;
    };
    response: { ok: boolean };
  };
}

export type ClientRequestType = keyof ClientRequestMap;
export type RequestPayload<T extends ClientRequestType> = ClientRequestMap[T]["payload"];
export type RequestResponse<T extends ClientRequestType> = ClientRequestMap[T]["response"];
