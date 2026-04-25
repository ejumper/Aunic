import { useEffect, useRef } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import {
  closeBrowserFind,
  findNextBrowserMatch,
  findPreviousBrowserMatch,
  replaceAllBrowserMatches,
  replaceCurrentBrowserMatch,
  setBrowserFindReplaceMode,
  setBrowserFindText,
  setBrowserReplaceText,
  toggleBrowserFindCaseSensitive,
} from "../../browserFind";
import { useFindStore } from "../../state/find";

export function PromptFind() {
  const active = useFindStore((store) => store.active);
  const replaceMode = useFindStore((store) => store.replaceMode);
  const findText = useFindStore((store) => store.findText);
  const replaceText = useFindStore((store) => store.replaceText);
  const caseSensitive = useFindStore((store) => store.caseSensitive);
  const matchCount = useFindStore((store) => store.matchCount);
  const currentMatchIndex = useFindStore((store) => store.currentMatchIndex);
  const findInputRef = useRef<HTMLInputElement | null>(null);
  const lastButtonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!active) {
      return;
    }
    requestAnimationFrame(() => {
      findInputRef.current?.focus();
      findInputRef.current?.select();
    });
  }, [active]);

  const matchLabel = findText
    ? matchCount <= 0
      ? "No matches"
      : currentMatchIndex === null
        ? `${matchCount} matches`
        : `${currentMatchIndex + 1} / ${matchCount} matches`
    : "";

  return (
    <>
      <div className="prompt-find" aria-label="Find and replace">
        <label className="prompt-find__row">
          <span className="prompt-find__label">Find</span>
          <input
            ref={findInputRef}
            className="prompt-find__input"
            type="text"
            value={findText}
            onChange={(event) => setBrowserFindText(event.target.value)}
            onKeyDown={(event) => handleFindInputKeyDown(event, lastButtonRef.current)}
          />
        </label>

        {replaceMode ? (
          <label className="prompt-find__row">
            <span className="prompt-find__label">Replace</span>
            <input
              className="prompt-find__input"
              type="text"
              value={replaceText}
              onChange={(event) => setBrowserReplaceText(event.target.value)}
              onKeyDown={handleReplaceInputKeyDown}
            />
          </label>
        ) : null}

        <p className="prompt-find__status" aria-live="polite">
          {matchLabel}
        </p>
      </div>

      <div className="context-meter" aria-hidden="true" />

      <div className="prompt-composer__footer prompt-composer__footer--find">
        <div className="prompt-composer__controls prompt-composer__controls--find">
          <button
            type="button"
            className="mode-pill"
            onClick={() => closeBrowserFind({ restoreFocus: "prompt" })}
          >
            Close
          </button>
          <button
            type="button"
            className={`mode-pill ${caseSensitive ? "mode-pill--active" : ""}`}
            onClick={() => toggleBrowserFindCaseSensitive()}
          >
            Aa: {caseSensitive ? "On" : "Off"}
          </button>
          <button type="button" className="mode-pill" onClick={() => findNextBrowserMatch()}>
            Next
          </button>
          <button type="button" className="mode-pill" onClick={() => findPreviousBrowserMatch()}>
            Prev
          </button>
          {replaceMode ? (
            <>
              <button
                type="button"
                className="mode-pill"
                onClick={() => replaceCurrentBrowserMatch()}
              >
                Replace
              </button>
              <button
                type="button"
                className="mode-pill"
                onClick={() => replaceAllBrowserMatches()}
              >
                Replace All
              </button>
            </>
          ) : null}
        </div>

        <button
          ref={lastButtonRef}
          type="button"
          className="mode-pill"
          onClick={() => setBrowserFindReplaceMode(!replaceMode)}
          onKeyDown={(event) => {
            if (event.key === "Tab" && !event.shiftKey) {
              event.preventDefault();
              findInputRef.current?.focus();
            }
          }}
        >
          {replaceMode ? "Find" : "Replace"}
        </button>
      </div>
    </>
  );
}

function handleFindInputKeyDown(
  event: ReactKeyboardEvent<HTMLInputElement>,
  lastButton: HTMLButtonElement | null,
): void {
  if (event.key === "Tab" && event.shiftKey) {
    event.preventDefault();
    lastButton?.focus();
    return;
  }
  if (event.key === "Enter") {
    event.preventDefault();
    findNextBrowserMatch();
    return;
  }
  if (event.key === "ArrowUp") {
    event.preventDefault();
    findPreviousBrowserMatch();
    return;
  }
  if (event.key === "ArrowDown") {
    event.preventDefault();
    findNextBrowserMatch();
    return;
  }
  if (event.key === "Escape") {
    event.preventDefault();
    closeBrowserFind({ restoreFocus: "prompt" });
  }
}

function handleReplaceInputKeyDown(event: ReactKeyboardEvent<HTMLInputElement>): void {
  if (event.key === "Enter") {
    event.preventDefault();
    replaceCurrentBrowserMatch();
    return;
  }
  if (event.key === "Escape") {
    event.preventDefault();
    closeBrowserFind({ restoreFocus: "prompt" });
  }
}
