import { useState } from "react";
import type { PendingPermissionPayload, PermissionResolution } from "../../ws/types";

interface PermissionPromptProps {
  permission: PendingPermissionPayload;
  onResolve: (resolution: PermissionResolution) => Promise<void>;
}

export function PermissionPrompt({ permission, onResolve }: PermissionPromptProps) {
  const [resolving, setResolving] = useState<PermissionResolution | null>(null);
  const request = permission.request;
  const details = isRecord(request.details) ? request.details : {};
  const isPlanApproval = details.kind === "plan_approval";
  const planMarkdown = typeof details.plan_markdown === "string" ? details.plan_markdown : "";
  const previewLines = planMarkdown.split(/\r?\n/).slice(0, 12);

  const resolve = async (resolution: PermissionResolution) => {
    setResolving(resolution);
    try {
      await onResolve(resolution);
    } finally {
      setResolving(null);
    }
  };

  return (
    <div className="permission-prompt" role="alert">
      <div className="permission-prompt__copy">
        <p className="eyebrow">{isPlanApproval ? "Plan approval" : "Permission"}</p>
        <strong>{request.message}</strong>
        <p>
          {request.tool_name} · {request.action}
          {request.target ? ` · ${request.target}` : ""}
        </p>
        {isPlanApproval && previewLines.length > 0 ? (
          <pre className="permission-prompt__preview">
            {previewLines.join("\n")}
            {planMarkdown.split(/\r?\n/).length > previewLines.length ? "\n..." : ""}
          </pre>
        ) : null}
      </div>
      <div className="permission-prompt__actions">
        <button type="button" disabled={resolving !== null} onClick={() => void resolve("once")}>
          {resolving === "once" ? (isPlanApproval ? "Approving..." : "Allowing...") : isPlanApproval ? "Approve & implement" : "Once"}
        </button>
        {!isPlanApproval ? (
          <button type="button" disabled={resolving !== null} onClick={() => void resolve("always")}>
            {resolving === "always" ? "Allowing..." : "Always"}
          </button>
        ) : null}
        <button
          type="button"
          className="secondary-button"
          disabled={resolving !== null}
          onClick={() => void resolve("reject")}
        >
          {resolving === "reject" ? (isPlanApproval ? "Keeping..." : "Rejecting...") : isPlanApproval ? "Keep planning" : "Reject"}
        </button>
      </div>
    </div>
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
