import { useEffect, useState } from "react";
import { useWs } from "../ws/context";
import type { ClientEnvelope, ServerEnvelope } from "../ws/envelope";

type RawLogEntry =
  | { id: string; direction: "out"; at: string; envelope: ClientEnvelope }
  | { id: string; direction: "in"; at: string; envelope: ServerEnvelope };

const MAX_LOG_ENTRIES = 50;

export function RawLog() {
  const { client } = useWs();
  const [entries, setEntries] = useState<RawLogEntry[]>([]);

  useEffect(() => {
    const append = (entry: RawLogEntry) => {
      setEntries((current) => [entry, ...current].slice(0, MAX_LOG_ENTRIES));
    };
    const unsubscribeIncoming = client.onAny((envelope) => {
      append({ id: envelope.id, direction: "in", at: new Date().toISOString(), envelope });
    });
    const unsubscribeOutgoing = client.onOutgoing((envelope) => {
      append({ id: envelope.id, direction: "out", at: new Date().toISOString(), envelope });
    });
    return () => {
      unsubscribeIncoming();
      unsubscribeOutgoing();
    };
  }, [client]);

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Wire Log</p>
          <h2>Raw Envelopes</h2>
        </div>
        <span className="muted">{entries.length} / {MAX_LOG_ENTRIES}</span>
      </div>
      <div className="raw-log">
        {entries.length === 0 ? (
          <p className="muted">No envelopes yet.</p>
        ) : (
          entries.map((entry) => (
            <article className="log-entry" key={`${entry.direction}-${entry.id}-${entry.at}`}>
              <header>
                <span className={`direction direction-${entry.direction}`}>
                  {entry.direction === "in" ? "recv" : "send"}
                </span>
                <time>{new Date(entry.at).toLocaleTimeString()}</time>
                <strong>{entry.envelope.type}</strong>
              </header>
              <pre>{JSON.stringify(entry.envelope, null, 2)}</pre>
            </article>
          ))
        )}
      </div>
    </section>
  );
}
