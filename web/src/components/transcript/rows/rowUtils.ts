import type { TranscriptRowPayload } from "../../../ws/types";

export function contentToText(content: unknown): string {
  if (typeof content === "string") {
    return content;
  }
  return JSON.stringify(content, null, 2) ?? String(content);
}

export function flattenToolResult(
  toolName: string | null,
  type: string,
  content: unknown,
): string {
  if (typeof content === "string") {
    return content;
  }
  const rec = asRecord(content);
  if ("message" in rec && type === "tool_error") {
    return String(rec.message || "Tool failed.");
  }
  if (toolName === "rag_search" && Array.isArray(content)) {
    return flattenRagSearchResults(content);
  }
  if (toolName === "rag_fetch" && !Array.isArray(content) && content && typeof content === "object") {
    return flattenRagFetchResult(rec);
  }
  if (toolName === "read" && !Array.isArray(content) && content && typeof content === "object") {
    return flattenReadResult(rec);
  }
  if (
    toolName &&
    ["edit", "write", "note_edit", "note_write"].includes(toolName) &&
    !Array.isArray(content) &&
    content &&
    typeof content === "object"
  ) {
    return flattenEditLikeResult(rec);
  }
  if (toolName === "stop_process" && !Array.isArray(content) && content && typeof content === "object") {
    return flattenProcessStopResult(rec);
  }
  if (toolName?.startsWith("mcp__") && !Array.isArray(content) && content && typeof content === "object") {
    return flattenMcpResult(rec);
  }
  return JSON.stringify(content, null, 2) ?? String(content);
}

function flattenRagSearchResults(results: unknown[]): string {
  const lines: string[] = [];
  for (const item of results) {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      lines.push(JSON.stringify(item));
      continue;
    }
    const r = item as Record<string, unknown>;
    const title = String(r.title ?? "").trim() || "(untitled)";
    const source = String(r.source ?? "").trim();
    const resultId = String(r.result_id ?? "").trim();
    const docId = String(r.doc_id ?? "").trim();
    const snippet = String(r.snippet ?? "").trim();
    const identifier = resultId || docId;
    const ref = source ? `[${source}] ${identifier}` : identifier;
    let line = `${title} | ${ref}`;
    if (snippet) line += ` | ${snippet}`;
    lines.push(line);
  }
  return lines.length > 0 ? lines.join("\n") : "(no RAG results)";
}

function flattenRagFetchResult(result: Record<string, unknown>): string {
  const fullText = String(result.full_text ?? "").trim();
  if (fullText) {
    const title = String(result.title ?? "").trim();
    return title ? `# ${title}\n\n${fullText}` : fullText;
  }
  const lines: string[] = [];
  const title = String(result.title ?? "").trim();
  const resultId = String(result.result_id ?? "").trim();
  const docId = String(result.doc_id ?? "").trim();
  const source = String(result.source ?? "").trim();
  if (title) lines.push(`Title: ${title}`);
  if (resultId) lines.push(`result_id: ${resultId}`);
  if (docId) lines.push(`doc_id: ${docId}`);
  if (source) lines.push(`Source: ${source}`);
  return lines.length > 0 ? lines.join("\n") : JSON.stringify(result);
}

function flattenReadResult(result: Record<string, unknown>): string {
  const type = String(result.type ?? "").trim();
  if (type === "text_file") return String(result.content ?? "");
  if (type === "file_unchanged") return String(result.message ?? "Earlier read result is still current.");
  if (type === "pdf" || type === "notebook") return String(result.content ?? "");
  if (type === "image") {
    return `Image: ${result.file_path} (${result.width}x${result.height})`;
  }
  return JSON.stringify(result, null, 2);
}

function flattenEditLikeResult(result: Record<string, unknown>): string {
  const type = String(result.type ?? "").trim();
  const path = String(result.file_path ?? "(active note)");
  if (type === "file_edit" || type === "note_content_edit") return `Edit applied to ${path}`;
  if (type === "file_write" || type === "note_content_write") return `Write applied to ${path}`;
  return JSON.stringify(result, null, 2);
}

function flattenProcessStopResult(result: Record<string, unknown>): string {
  const bgId = String(result.background_id ?? "").trim() || "(unknown)";
  const command = String(result.command ?? "").trim();
  const cmdSuffix = command ? ` (${command.length > 80 ? command.slice(0, 77) + "..." : command})` : "";
  const exitCode = result.exit_code;
  const reason = String(result.reason ?? "").trim();
  const reasonSuffix = reason ? `; reason: ${reason}` : "";
  const status = String(result.status ?? "").trim();
  if (status === "already_exited") {
    return `Background command ${bgId}${cmdSuffix} had already exited (exit ${exitCode})${reasonSuffix}`;
  }
  const signals = Array.isArray(result.signals_sent) ? result.signals_sent.map(String).join(" then ") : "no signal sent";
  const elapsedMs = typeof result.elapsed_ms === "number" ? `${(result.elapsed_ms / 1000).toFixed(1)}s` : "?s";
  if (result.forced) {
    return `Force-stopped background command ${bgId}${cmdSuffix} - ${signals} after ${elapsedMs}${reasonSuffix}`;
  }
  return `Stopped background command ${bgId}${cmdSuffix} - ${signals}, exit ${exitCode} in ${elapsedMs}${reasonSuffix}`;
}

function flattenMcpResult(result: Record<string, unknown>): string {
  const content = result.content;
  if (typeof content === "string" && content.trim()) return content;
  const structured = result.structured_content;
  if (structured !== undefined) return JSON.stringify(structured);
  return JSON.stringify(result, null, 2);
}

export function asRecord(content: unknown): Record<string, unknown> {
  return content && typeof content === "object" && !Array.isArray(content)
    ? (content as Record<string, unknown>)
    : {};
}

export function commandFromRows(
  row: TranscriptRowPayload,
  toolCall: TranscriptRowPayload | undefined,
): string {
  const toolCallContent = asRecord(toolCall?.content);
  const rowContent = asRecord(row.content);
  return stringValue(toolCallContent.command) || stringValue(rowContent.command);
}

export function queryFromRows(
  row: TranscriptRowPayload,
  toolCall: TranscriptRowPayload | undefined,
): string {
  const toolCallContent = asRecord(toolCall?.content);
  const rowContent = asRecord(row.content);
  return (
    firstString(toolCallContent.queries) ||
    stringValue(toolCallContent.query) ||
    firstString(rowContent.queries) ||
    stringValue(rowContent.query)
  );
}

export function normalizedHost(url: string): string {
  if (!url) {
    return "unknown";
  }
  try {
    return new URL(url).hostname || "unknown";
  } catch {
    return "unknown";
  }
}

export function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function firstString(value: unknown): string {
  return Array.isArray(value) && typeof value[0] === "string" ? value[0] : "";
}
