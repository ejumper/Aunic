import { beforeEach, describe, expect, it } from "vitest";
import { useSessionStore } from "./session";
import type { ProgressEventPayload, SessionStatePayload } from "../ws/types";

describe("useSessionStore", () => {
  beforeEach(() => {
    useSessionStore.getState().clearSession();
  });

  it("hydrates run and permission state from session_state", () => {
    useSessionStore.getState().setSession({
      ...sessionPayload(),
      instance_id: "instance-1",
      run_active: true,
      run_id: "run-1",
      pending_permission: {
        permission_id: "perm-1",
        request: {
          tool_name: "bash",
          action: "run",
          target: "pwd",
          message: "Run command?",
          policy: "ask",
          key: null,
          details: null,
        },
      },
    });

    expect(useSessionStore.getState()).toMatchObject({
      runActive: true,
      currentRunId: "run-1",
      pendingPermission: { permission_id: "perm-1" },
    });
  });

  it("uses progress events as the indicator message", () => {
    useSessionStore.getState().applyProgressEvent(progress("status", "Thinking"));

    expect(useSessionStore.getState().indicatorMessage).toMatchObject({
      text: "Thinking",
      kind: "status",
    });
  });

  it("ignores noisy progress event kinds", () => {
    useSessionStore.getState().applyProgressEvent(progress("status", "First"));
    useSessionStore.getState().applyProgressEvent(progress("file_written", "Wrote row"));
    useSessionStore.getState().applyProgressEvent(progress("tool_call", "Calling tool"));

    expect(useSessionStore.getState().indicatorMessage?.text).toBe("First");
  });

  it("formats provider requests like the TUI indicator", () => {
    useSessionStore.getState().applyProgressEvent(progress("loop_event", "Sent tool-loop turn to provider.", {
      loop_kind: "provider_request",
    }));

    expect(useSessionStore.getState().indicatorMessage).toMatchObject({
      text: "Pontificating...",
      kind: "status",
    });
  });

  it("shows the active task label on provider_request when provided", () => {
    useSessionStore.getState().applyProgressEvent(
      progress("loop_event", "Sent tool-loop turn to provider.", {
        loop_kind: "provider_request",
        active_task_label: "Running tests",
      }),
    );

    expect(useSessionStore.getState().indicatorMessage).toMatchObject({
      text: "Running tests...",
      kind: "status",
    });
  });

  it("uses task tool verbs for task_update calls", () => {
    useSessionStore.getState().applyProgressEvent(
      progress("loop_event", "Tool loop provider response: usage.", {
        loop_kind: "provider_response",
        tool_calls: ["task_update"],
      }),
    );

    expect(useSessionStore.getState().indicatorMessage).toMatchObject({
      text: "Updating task...",
      kind: "status",
    });
  });

  it("formats provider tool calls with tool verbs", () => {
    useSessionStore.getState().applyProgressEvent(
      progress("loop_event", "Tool loop provider response: usage.", {
        loop_kind: "provider_response",
        tool_calls: ["web_fetch"],
      }),
    );

    expect(useSessionStore.getState().indicatorMessage).toMatchObject({
      text: "Fetching...",
      kind: "status",
    });
  });

  it("formats failed tool results as indicator errors", () => {
    useSessionStore.getState().applyProgressEvent(
      progress("loop_event", "bash finished with status tool_error.", {
        loop_kind: "tool_result",
        tool_name: "bash",
        status: "tool_error",
      }),
    );

    expect(useSessionStore.getState().indicatorMessage).toMatchObject({
      text: "bash failed.",
      kind: "error",
    });
  });

  it("sets indicator messages directly for UI-originated errors", () => {
    useSessionStore.getState().setIndicatorMessage("Nope", "error");

    expect(useSessionStore.getState().indicatorMessage).toMatchObject({
      text: "Nope",
      kind: "error",
    });
  });
});

function progress(
  kind: string,
  message: string,
  details: Record<string, unknown> = {},
): ProgressEventPayload {
  return {
    kind,
    message,
    path: null,
    details,
  };
}

function sessionPayload(): SessionStatePayload {
  return {
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
}
