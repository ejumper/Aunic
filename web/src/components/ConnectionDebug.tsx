import { useEffect, useState } from "react";
import { wsUrl } from "../env";
import { useConnectionState } from "../ws/context";

interface ProbeResult {
  state: "pending" | "ok" | "error";
  status?: number;
  message?: string;
  at?: string;
}

export function ConnectionDebug() {
  const { state, diagnostics } = useConnectionState();
  const [rootProbe, setRootProbe] = useState<ProbeResult>({ state: "pending" });
  const [wsHttpProbe, setWsHttpProbe] = useState<ProbeResult>({ state: "pending" });

  useEffect(() => {
    if (state === "open") {
      return;
    }
    const abort = new AbortController();
    setRootProbe({ state: "pending" });
    setWsHttpProbe({ state: "pending" });
    void runProbe("/", abort.signal).then(setRootProbe);
    void runProbe("/ws", abort.signal).then(setWsHttpProbe);
    return () => abort.abort();
  }, [state, diagnostics?.attempts]);

  if (state === "open") {
    return null;
  }

  const standalone = isStandaloneMode();
  const lastClose = diagnostics?.lastClose;

  return (
    <section className="connection-debug" aria-label="Connection debug">
      <div className="connection-debug__header">
        <strong>WS debug</strong>
        <span>{state}</span>
      </div>
      <dl>
        <div>
          <dt>href</dt>
          <dd>{window.location.href}</dd>
        </div>
        <div>
          <dt>protocol</dt>
          <dd>{window.location.protocol}</dd>
        </div>
        <div>
          <dt>host</dt>
          <dd>{window.location.host}</dd>
        </div>
        <div>
          <dt>ws</dt>
          <dd>{diagnostics?.url ?? wsUrl}</dd>
        </div>
        <div>
          <dt>standalone</dt>
          <dd>{standalone ? "yes" : "no"}</dd>
        </div>
        <div>
          <dt>attempts</dt>
          <dd>{diagnostics?.attempts ?? 0}</dd>
        </div>
        <div>
          <dt>last attempt</dt>
          <dd>{formatTime(diagnostics?.lastAttemptAt)}</dd>
        </div>
        <div>
          <dt>last error</dt>
          <dd>{formatTime(diagnostics?.lastErrorAt)}</dd>
        </div>
        <div>
          <dt>last close</dt>
          <dd>
            {lastClose
              ? `${lastClose.code} ${lastClose.wasClean ? "clean" : "unclean"} ${
                  lastClose.reason || ""
                }`.trim()
              : "none"}
          </dd>
        </div>
        <div>
          <dt>fetch /</dt>
          <dd>{formatProbe(rootProbe)}</dd>
        </div>
        <div>
          <dt>fetch /ws</dt>
          <dd>{formatProbe(wsHttpProbe)}</dd>
        </div>
      </dl>
    </section>
  );
}

async function runProbe(path: string, signal: AbortSignal): Promise<ProbeResult> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 4_000);
  const abortFromParent = () => controller.abort();
  signal.addEventListener("abort", abortFromParent, { once: true });
  try {
    const response = await fetch(`${path}?probe=${Date.now()}`, {
      cache: "no-store",
      method: "GET",
      signal: controller.signal,
    });
    return {
      state: "ok",
      status: response.status,
      at: new Date().toISOString(),
    };
  } catch (error) {
    if (signal.aborted) {
      return { state: "pending" };
    }
    return {
      state: "error",
      message: error instanceof Error ? error.message : String(error),
      at: new Date().toISOString(),
    };
  } finally {
    window.clearTimeout(timeout);
    signal.removeEventListener("abort", abortFromParent);
  }
}

function isStandaloneMode(): boolean {
  const navigatorStandalone =
    "standalone" in window.navigator &&
    (window.navigator as Navigator & { standalone?: boolean }).standalone === true;
  return (
    navigatorStandalone ||
    window.matchMedia("(display-mode: standalone)").matches ||
    window.matchMedia("(display-mode: fullscreen)").matches
  );
}

function formatTime(value: string | null | undefined): string {
  if (!value) {
    return "none";
  }
  return new Date(value).toLocaleTimeString();
}

function formatProbe(result: ProbeResult): string {
  if (result.state === "pending") {
    return "pending";
  }
  const time = result.at ? ` at ${new Date(result.at).toLocaleTimeString()}` : "";
  if (result.state === "ok") {
    return `${result.status ?? "ok"}${time}`;
  }
  return `${result.message ?? "error"}${time}`;
}
