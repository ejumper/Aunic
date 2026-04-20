interface SendCancelControlsProps {
  runActive: boolean;
  submitting: boolean;
  canSubmit: boolean;
  onSubmit: () => void;
  onCancel: () => void;
}

export function SendCancelControls({
  runActive,
  submitting,
  canSubmit,
  onSubmit,
  onCancel,
}: SendCancelControlsProps) {
  if (runActive) {
    return (
      <button
      type="button"
      className="prompt-cancel-button"
      aria-label="Cancel run"
      disabled={submitting}
      onClick={onCancel}
      >
        {submitting ? "Cancelling..." : "Cancel"}
      </button>
    );
  }

  return (
    <button
      type="button"
      className="prompt-send-button"
      aria-label="Send"
      disabled={submitting || !canSubmit}
      onClick={onSubmit}
    >
      {submitting ? (
        "Sending..."
      ) : (
        <>
          <span className="prompt-control-label">Send</span>
          <img
            className="prompt-control-icon"
            src="/icons/send.svg"
            alt=""
            aria-hidden="true"
          />
        </>
      )}
    </button>
  );
}
