import { EditorView, ViewPlugin, type ViewUpdate } from "@codemirror/view";

const MEASURE_TEXT = "000";

export function monospaceTabWidth() {
  return ViewPlugin.fromClass(
    class {
      private readonly measureElement: HTMLSpanElement;
      private resizeObserver: ResizeObserver | null = null;
      private destroyed = false;

      constructor(private readonly view: EditorView) {
        this.measureElement = document.createElement("span");
        this.measureElement.className = "cm-aunic-tab-measure";
        this.measureElement.textContent = MEASURE_TEXT;
        view.dom.appendChild(this.measureElement);
        this.measure();
        this.measureAfterFontsLoad();
        this.observeMeasureElement();
      }

      update(update: ViewUpdate) {
        if (update.geometryChanged) {
          this.measure();
        }
      }

      destroy() {
        this.destroyed = true;
        this.resizeObserver?.disconnect();
        this.measureElement.remove();
      }

      private observeMeasureElement() {
        if (typeof ResizeObserver === "undefined") {
          return;
        }
        this.resizeObserver = new ResizeObserver(() => this.measure());
        this.resizeObserver.observe(this.measureElement);
      }

      private measureAfterFontsLoad() {
        const fonts = document.fonts;
        if (!fonts) {
          return;
        }
        void fonts.ready.then(() => {
          if (!this.destroyed) {
            this.measure();
          }
        });
      }

      private measure() {
        const width = this.measureElement.getBoundingClientRect().width;
        if (width > 0) {
          this.view.dom.style.setProperty("--aunic-tab-width", `${width}px`);
        }
      }
    },
  );
}
