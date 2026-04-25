import { useNoteEditorStore } from "../state/noteEditor";

export function ConnectionBadge() {
  const dirty = useNoteEditorStore((store) => store.dirty);
  const path = useNoteEditorStore((store) => store.path);

  const label = !path ? "No file open" : dirty ? "Unsaved changes" : "File saved";

  return (
    <div
      className={`connection-indicator ${
        dirty ? "connection-indicator--disconnected" : "connection-indicator--connected"
      }`}
      role="status"
      aria-label={label}
      title={label}
    />
  );
}
