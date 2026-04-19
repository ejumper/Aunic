import { useEffect, useState } from "react";
import { useSessionStore } from "../state/session";
import { useWs } from "../ws/context";
import type { SessionStatePayload } from "../ws/types";
import { isWsRequestError } from "./errors";

export function HelloPanel() {
  const { client } = useWs();
  const session = useSessionStore((store) => store.session);
  const [response, setResponse] = useState<SessionStatePayload | null>(session);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setResponse(session);
  }, [session]);

  async function sendHello() {
    setError(null);
    try {
      const nextSession = await client.request("hello", {});
      setResponse(nextSession);
    } catch (err) {
      setError(formatRequestError(err));
    }
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Handshake</p>
          <h2>Session State</h2>
        </div>
        <button type="button" onClick={sendHello}>
          Send hello
        </button>
      </div>
      {error ? <p className="error-text">{error}</p> : null}
      <pre className="json-output">{JSON.stringify(response, null, 2)}</pre>
    </section>
  );
}

function formatRequestError(error: unknown): string {
  if (isWsRequestError(error)) {
    return `${error.reason}${error.details ? ` ${JSON.stringify(error.details)}` : ""}`;
  }
  return String(error);
}
