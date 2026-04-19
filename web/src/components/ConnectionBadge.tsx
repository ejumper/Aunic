import { useConnectionState } from "../ws/context";

const labels: Record<string, string> = {
  idle: "Idle",
  connecting: "Connecting",
  open: "Connected",
  reconnecting: "Reconnecting",
  closed: "Closed",
};

export function ConnectionBadge() {
  const { state, lastConnectedAt } = useConnectionState();
  const label = labels[state] ?? state;
  const lastConnectedLabel = lastConnectedAt
    ? `Last connected ${lastConnectedAt.toLocaleTimeString()}`
    : "Waiting for first connection.";
  const healthy = state === "open";

  return (
    <div
      className={`connection-indicator ${
        healthy ? "connection-indicator--connected" : "connection-indicator--disconnected"
      }`}
      role="status"
      aria-label={`Browser WebSocket ${label}. ${lastConnectedLabel}`}
      title={lastConnectedLabel}
    />
  );
}
