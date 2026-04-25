import { type Extension } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags } from "@lezer/highlight";

const monospaceFont = 'var(--aunic-monospace-font, "MonoLisa", ui-monospace, monospace)';

const syntax = HighlightStyle.define([
  { tag: tags.heading, color: "#f4faf6", fontWeight: "700" },
  { tag: tags.strong, fontWeight: "700" },
  { tag: tags.emphasis, fontStyle: "italic"},
  { tag: tags.link, color: "#8bc7ff" },
  { tag: tags.url, color: "#b3e0c9" },
  { tag: tags.monospace, color: "hsl(155, 50%, 60%)", fontFamily: monospaceFont },
  { tag: tags.quote, color: "#b9c8c0" },
  { tag: tags.processingInstruction, color: "#8ab4a0" },
]);

export function aunicTheme(): Extension {
  return [
    EditorView.theme(
      {
        "&": {
          height: "100%",
          color: "var(--editor-fg)",
          backgroundColor: "var(--editor-bg)",
          fontSize: "0.95rem",
          "--aunic-line-padding-left": "1rem",
          "--aunic-line-padding-right": "1rem",
          "--aunic-monospace-font": '"MonoLisa", ui-monospace, monospace',
          "--aunic-tab-width": "1.5rem",
          "--aunic-inline-code-bg": "hsla(155, 15%, 15%, 0.7)",
          "--aunic-inline-code-pad-x": "0.28rem",
        },
        ".cm-scroller": {
          fontFamily: '"ProLisa", ui-sans-serif, system-ui, sans-serif',
          lineHeight: "1.6",
        },
        ".cm-content": {
          minHeight: "100%",
          padding: "0.85rem 0",
          caretColor: "var(--editor-caret)",
        },
        ".cm-line": {
          paddingRight: "var(--aunic-line-padding-right)",
          tabSize: "var(--aunic-tab-width)",
        },
        ".cm-gutters": {
          borderRight: "1px solid var(--editor-border)",
          backgroundColor: "var(--editor-gutter)",
          color: "var(--editor-muted)",
        },
        ".cm-lineNumbers .cm-gutterElement": {
          display: "flex",
          alignItems: "flex-end",
          padding: "0 5px 0.28rem 3px",
        },
        ".cm-activeLine": {
          backgroundColor: "var(--editor-active-line)",
        },
        ".cm-activeLineGutter": {
          backgroundColor: "var(--editor-active-line)",
          color: "var(--editor-fg)",
        },
        ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": {
          backgroundColor: "var(--editor-selection)",
        },
        "&.cm-focused": {
          outline: "none",
        },
        ".cm-aunic-hidden-fence": {
          visibility: "hidden",
        },
        ".cm-aunic-heading-1": {
          fontSize: "1.55em",
          fontWeight: "750",
        },
        ".cm-aunic-heading-2": {
          fontSize: "1.35em",
          fontWeight: "725",
        },
        ".cm-aunic-heading-3": {
          fontSize: "1.18em",
          fontWeight: "700",
        },
        ".cm-aunic-heading-4, .cm-aunic-heading-5, .cm-aunic-heading-6": {
          fontWeight: "700",
        },
        ".cm-aunic-marker": {
          borderRadius: "4px",
          padding: "0 0.2rem",
          fontWeight: "700",
        },
        ".cm-aunic-marker-write": {
          color: "var(--marker-write-fg)",
          backgroundColor: "var(--marker-write-bg)",
        },
        ".cm-aunic-marker-include": {
          color: "var(--marker-include-fg)",
          backgroundColor: "var(--marker-include-bg)",
        },
        ".cm-aunic-marker-exclude": {
          color: "var(--marker-exclude-fg)",
          backgroundColor: "var(--marker-exclude-bg)",
        },
        ".cm-aunic-marker-read-only": {
          color: "var(--marker-readonly-fg)",
          backgroundColor: "var(--marker-readonly-bg)",
        },
        ".cm-aunic-table-line": {
          color: "var(--editor-table)",
        },
        ".cm-aunic-page-break": {
          display: "block",
          width: "100%",
          height: "1px",
          margin: "0.78em 0",
          backgroundColor: "var(--editor-border)",
        },
        ".cm-aunic-inline-code": {
          borderRadius: "6px",
          padding: "0.08rem var(--aunic-inline-code-pad-x)",
          marginInline: "calc(-1 * var(--aunic-inline-code-pad-x))",
          backgroundColor: "var(--aunic-inline-code-bg)",
          boxDecorationBreak: "clone",
          WebkitBoxDecorationBreak: "clone",
          color: "hsl(155, 50%, 60%)",
          fontFamily: monospaceFont,
        },
        ".cm-aunic-code-block-line": {
          position: "relative",
          isolation: "isolate",
          fontFamily: monospaceFont,
        },
        ".cm-aunic-tab-measure": {
          position: "absolute",
          visibility: "hidden",
          pointerEvents: "none",
          whiteSpace: "pre",
          contain: "layout style paint",
          fontFamily: monospaceFont,
        },
        ".cm-searchMatch": {
          backgroundColor: "hsla(49, 95%, 64%, 0.2)",
          outline: "1px solid hsla(49, 95%, 64%, 0.32)",
        },
        ".cm-searchMatch.cm-searchMatch-selected": {
          backgroundColor: "hsla(142, 68%, 46%, 0.26)",
          outline: "1px solid hsla(142, 68%, 58%, 0.48)",
        },
        ".cm-aunic-table-wrap": {
          padding: "0.2rem 1rem",
          cursor: "text",
          lineHeight: "normal",
          maxWidth: "100%",
          overflowX: "auto",
        },
        ".cm-aunic-md-table": {
          borderCollapse: "collapse",
          fontSize: "0.9em",
          tableLayout: "auto",
          width: "auto",
          maxWidth: "100%",
        },
        ".cm-aunic-md-table th, .cm-aunic-md-table td": {
          border: "1px solid var(--editor-border)",
          padding: "0.2rem 0.6rem",
          textAlign: "left",
          verticalAlign: "top",
          overflowWrap: "break-word",
          whiteSpace: "normal",
          wordBreak: "normal",
        },
        ".cm-aunic-md-table th": {
          backgroundColor: "var(--editor-gutter)",
          fontWeight: "700",
          color: "var(--editor-fg)",
        },
        ".cm-aunic-md-table td": {
          color: "var(--editor-fg)",
        },
        ".cm-aunic-rule-line": {
          color: "var(--editor-muted)",
          letterSpacing: "0",
        },
        ".cm-foldGutter .cm-gutterElement": {
          display: "flex",
          alignItems: "flex-end",
          justifyContent: "center",
          padding: "0 0 0.28rem",
        },
        ".cm-foldGutter .cm-gutterElement span": {
          display: "block",
          width: "100%",
          textAlign: "center",
          lineHeight: "1",
          cursor: "pointer",
        },
        ".cm-foldPlaceholder": {
          border: "1px solid var(--editor-border)",
          borderRadius: "6px",
          backgroundColor: "var(--editor-fold-bg)",
          color: "var(--editor-muted)",
        },
      },
      { dark: true },
    ),
    syntaxHighlighting(syntax),
  ];
}
