import { type Extension } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags } from "@lezer/highlight";

const syntax = HighlightStyle.define([
  { tag: tags.heading, color: "#f4faf6", fontWeight: "700" },
  { tag: tags.strong, fontWeight: "700" },
  { tag: tags.emphasis, fontStyle: "italic" },
  { tag: tags.link, color: "#8bc7ff" },
  { tag: tags.url, color: "#b3e0c9" },
  { tag: tags.monospace, color: "#f6d58b" },
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
        },
        ".cm-scroller": {
          fontFamily:
            "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, \"Liberation Mono\", monospace",
          lineHeight: "1.6",
        },
        ".cm-content": {
          minHeight: "100%",
          padding: "0.85rem 0",
          caretColor: "var(--editor-caret)",
        },
        ".cm-line": {
          padding: "0 1rem",
        },
        ".cm-gutters": {
          borderRight: "1px solid var(--editor-border)",
          backgroundColor: "var(--editor-gutter)",
          color: "var(--editor-muted)",
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
        ".cm-aunic-hidden-markup": {
          display: "none",
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
          padding: "0.08rem 0.28rem",
          backgroundColor: "hsl(210, 13%, 18%)",
          color: "#f6d58b",
        },
        ".cm-aunic-code-block-line": {
          backgroundColor: "hsl(210, 13%, 13%)",
        },
        ".cm-aunic-code-block-line--start": {
          borderTopLeftRadius: "6px",
          borderTopRightRadius: "6px",
        },
        ".cm-aunic-code-block-line--end": {
          borderBottomLeftRadius: "6px",
          borderBottomRightRadius: "6px",
        },
        ".cm-aunic-table-wrap": {
          padding: "0.2rem 1rem",
          cursor: "text",
          lineHeight: "normal",
          maxWidth: "100%",
          overflowX: "hidden",
        },
        ".cm-aunic-md-table": {
          borderCollapse: "collapse",
          fontSize: "0.9em",
          tableLayout: "fixed",
          width: "100%",
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
          alignItems: "center",
          justifyContent: "center",
          padding: "0",
        },
        ".cm-foldGutter .cm-gutterElement span": {
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          width: "100%",
          height: "100%",
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
