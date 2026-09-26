import { h } from "../dom.js";

const UI_NAME = /^[a-z][a-z0-9-]{1,30}$/;

function defaultImporter(ui) {
  return import(`../units/${ui}/index.js`);
}

function defaultAttach(ui, href) {
  return new Promise((resolve) => {
    const done = () => resolve(undefined);
    document.head.append(
      h("link", { rel: "stylesheet", href, "data-ui": ui, onload: done, onerror: done }),
    );
  });
}

/**
 * @param {{
 *   importer?: (ui: string) => Promise<any>,
 *   attachStylesheet?: (ui: string, href: string) => Promise<unknown>,
 * }} [options]
 */
export function createLoader({
  importer = defaultImporter,
  attachStylesheet = defaultAttach,
} = {}) {
  const modules = new Map();
  const sheets = new Set();

  async function importUi(ui) {
    const mod = await importer(ui);
    if (typeof mod?.createUnitUi !== "function") {
      throw new Error(`интерфейс «${ui}» не отдаёт функцию createUnitUi`);
    }
    if (typeof mod.stylesheet === "string" && mod.stylesheet && !sheets.has(ui)) {
      sheets.add(ui);
      await attachStylesheet(ui, mod.stylesheet);
    }
    return mod;
  }

  /** @returns {Promise<{ createUnitUi: import("../units/contract").CreateUnitUi, stylesheet?: string }>} */
  function load(ui) {
    if (typeof ui !== "string" || !UI_NAME.test(ui)) {
      return Promise.reject(new Error(`недопустимое имя интерфейса «${String(ui)}»`));
    }
    if (!modules.has(ui)) {
      const pending = importUi(ui);
      modules.set(ui, pending);
      pending.catch(() => {
        if (modules.get(ui) === pending) modules.delete(ui);
      });
    }
    return modules.get(ui);
  }

  return { load };
}
