import type { TranscriptRowPayload } from "../../../ws/types";
import { MarkdownText } from "../../MarkdownText";
import { contentToText } from "./rowUtils";

interface ChatRowProps {
  row: TranscriptRowPayload;
  onDelete: (rowNumber: number) => void;
}

export function ChatRow({ row, onDelete }: ChatRowProps) {
  const isUser = row.role === "user";
  const text = contentToText(row.content);

  return (
    <div
      className={`chat-bubble chat-bubble--${isUser ? "user" : "assistant"}`}
      data-row-number={row.row_number}
    >
      <MarkdownText className="chat-bubble__text" text={text} />
      <button
        type="button"
        className="chat-bubble__del"
        aria-label={`Delete transcript row ${row.row_number}`}
        onClick={() => onDelete(row.row_number)}
      >
        ✕
      </button>
    </div>
  );
}
