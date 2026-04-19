import { useState } from "react";
import type { PendingPermissionPayload, PermissionResolution } from "../../ws/types";

interface PermissionPromptProps {
  permission: PendingPermissionPayload;
  onResolve: (resolution: PermissionResolution) => Promise<void>;
}

export function PermissionPrompt({ permission, onResolve }: PermissionPromptProps) {
  const [resolving, setResolving] = useState<PermissionResolution | null>(null);
  const request = permission.request;

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
        <p className="eyebrow">Permission</p>
        <strong>{request.message}</strong>
        <p>
          {request.tool_name} · {request.action}
          {request.target ? ` · ${request.target}` : ""}
        </p>
      </div>
      <div className="permission-prompt__actions">
        <button type="button" disabled={resolving !== null} onClick={() => void resolve("once")}>
          {resolving === "once" ? "Allowing..." : "Once"}
        </button>
        <button type="button" disabled={resolving !== null} onClick={() => void resolve("always")}>
          {resolving === "always" ? "Allowing..." : "Always"}
        </button>
        <button
          type="button"
          className="secondary-button"
          disabled={resolving !== null}
          onClick={() => void resolve("reject")}
        >
          {resolving === "reject" ? "Rejecting..." : "Reject"}
        </button>
      </div>
    </div>
  );
}
