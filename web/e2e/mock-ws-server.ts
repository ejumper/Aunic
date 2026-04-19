import { WebSocketServer, type WebSocket } from "ws";

export const MOCK_WS_PORT = 8766;

interface ClientEnvelope {
  id: string;
  type: string;
  payload: Record<string, unknown>;
}

interface MockWsServer {
  close: () => Promise<void>;
}

const mockModel = {
  label: "Mock model",
  provider_name: "mock",
  model: "mock-model",
  profile_id: null,
  context_window: 128_000,
};

export async function startMockWsServer(port = MOCK_WS_PORT): Promise<MockWsServer> {
  const server = new WebSocketServer({ host: "127.0.0.1", port, path: "/ws" });

  server.on("connection", (socket) => {
    socket.on("message", (data) => {
      const envelope = parseEnvelope(data.toString());
      if (!envelope) {
        return;
      }
      handleEnvelope(socket, envelope);
    });
  });

  await new Promise<void>((resolve, reject) => {
    server.once("listening", resolve);
    server.once("error", reject);
  });

  return {
    close: () =>
      new Promise<void>((resolve, reject) => {
        for (const client of server.clients) {
          client.close();
        }
        server.close((error) => {
          if (error) {
            reject(error);
            return;
          }
          resolve();
        });
      }),
  };
}

function handleEnvelope(socket: WebSocket, envelope: ClientEnvelope): void {
  switch (envelope.type) {
    case "hello":
      send(socket, envelope.id, "session_state", {
        run_active: false,
        run_id: null,
        workspace_root: "/mock/workspace",
        default_mode: "note",
        mode: "note",
        work_mode: "off",
        models: [mockModel],
        selected_model_index: 0,
        selected_model: mockModel,
        pending_permission: null,
      });
      return;
    case "list_files":
      send(socket, envelope.id, "response", mockListFiles(envelope.payload.subpath));
      return;
    case "read_file":
      send(socket, envelope.id, "response", mockFileSnapshot(String(envelope.payload.path)));
      return;
    default:
      send(socket, envelope.id, "response", {});
  }
}

function mockListFiles(subpath: unknown) {
  const path = typeof subpath === "string" ? subpath : "";
  if (path === "notes") {
    return {
      path,
      entries: [
        {
          name: "project.md",
          kind: "file",
          path: "notes/project.md",
        },
      ],
    };
  }
  return {
    path: "",
    entries: [
      {
        name: "notes",
        kind: "dir",
        path: "notes",
      },
      {
        name: "README.md",
        kind: "file",
        path: "README.md",
      },
    ],
  };
}

function mockFileSnapshot(path: string) {
  const capturedAt = new Date("2026-04-18T00:00:00.000Z").toISOString();
  const noteContent = mockNoteContent(path);
  return {
    path,
    revision_id: `mock:${path}:1`,
    content_hash: "mock-content-hash",
    mtime_ns: 1,
    size_bytes: noteContent.length,
    captured_at: capturedAt,
    note_content: noteContent,
    transcript_rows: [],
    has_transcript: false,
  };
}

function mockNoteContent(path: string): string {
  const paragraphs = Array.from(
    { length: 80 },
    (_, index) =>
      `Line ${index + 1}: Mock note content for ${path} that is long enough to exercise the editor scroll container.`,
  );
  return [`# ${path}`, "", ...paragraphs].join("\n");
}

function send(socket: WebSocket, id: string, type: string, payload: unknown): void {
  socket.send(JSON.stringify({ id, type, payload }));
}

function parseEnvelope(raw: string): ClientEnvelope | null {
  try {
    const parsed = JSON.parse(raw) as Partial<ClientEnvelope>;
    if (
      typeof parsed.id !== "string" ||
      typeof parsed.type !== "string" ||
      typeof parsed.payload !== "object" ||
      parsed.payload === null
    ) {
      return null;
    }
    return parsed as ClientEnvelope;
  } catch {
    return null;
  }
}
