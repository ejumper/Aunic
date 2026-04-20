export function remoteLog(label: string, data?: Record<string, unknown>): void {
  try {
    const payload = data ? `${label} ${JSON.stringify(data)}` : label;
    const blob = new Blob([payload], { type: "text/plain" });
    if (navigator.sendBeacon && navigator.sendBeacon("/debug/log", blob)) {
      return;
    }
    void fetch("/debug/log", {
      method: "POST",
      body: payload,
      keepalive: true,
    });
  } catch {
    // swallow — debug logging must never break the app
  }
}
