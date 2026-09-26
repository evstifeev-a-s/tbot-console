import { h } from "../../dom.js";

export const stylesheet = new URL("./style.css", import.meta.url).href;

const PAGES = Object.freeze([{ id: "main", glyph: "Д", label: "Главная" }]);

/**
 * @param {import("../contract").UnitSdk} sdk
 * @returns {import("../contract").UnitUi}
 */
export function createUnitUi(sdk) {
  return {
    pages: PAGES,
    activate: () => undefined,
    render(view, route) {
      const answer = h("p", { class: "demo-answer" }, "Спрашиваю службу…");
      view.append(
        h(
          "section",
          { class: "panel demo-panel" },
          h("h2", { class: "panel-title" }, "Демо"),
          answer,
        ),
      );
      sdk.api.get("hello", {}, route.signal).then(
        (reply) => {
          answer.textContent = reply.text;
        },
        (error) => {
          if (!route.signal.aborted) answer.textContent = `Служба не ответила: ${error.message}`;
        },
      );
      return undefined;
    },
  };
}
