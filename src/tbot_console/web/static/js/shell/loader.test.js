import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createLoader } from "./loader.js";

function factory() {
  return { createUnitUi: () => ({ pages: [], activate() {}, render() {} }) };
}

describe("createLoader", () => {
  it("refuses a malformed ui name without importing anything", async () => {
    const imported = [];
    const loader = createLoader({
      importer: async (ui) => {
        imported.push(ui);
        return factory();
      },
    });
    for (const bad of ["../x", "Alpha", "a", "alpha/index", "", null, "alpha?v=2"]) {
      await assert.rejects(loader.load(bad), /недопустимое имя/);
    }
    assert.deepEqual(imported, []);
  });

  it("imports each ui once and attaches its stylesheet once", async () => {
    const imported = [];
    const sheets = [];
    const loader = createLoader({
      importer: async (ui) => {
        imported.push(ui);
        return { ...factory(), stylesheet: `/js/units/${ui}/style.css` };
      },
      attachStylesheet: async (ui, href) => {
        sheets.push([ui, href]);
      },
    });
    const [a, b] = await Promise.all([loader.load("beta"), loader.load("beta")]);
    await loader.load("beta");
    assert.equal(a, b);
    assert.deepEqual(imported, ["beta"]);
    assert.deepEqual(sheets, [["beta", "/js/units/beta/style.css"]]);
  });

  it("skips the stylesheet step for a package without one", async () => {
    const sheets = [];
    const loader = createLoader({
      importer: async () => factory(),
      attachStylesheet: async (ui) => {
        sheets.push(ui);
      },
    });
    await loader.load("alpha");
    assert.deepEqual(sheets, []);
  });

  it("rejects a package without the factory and retries it next time", async () => {
    let attempts = 0;
    const loader = createLoader({
      importer: async () => {
        attempts += 1;
        return attempts === 1 ? {} : factory();
      },
    });
    await assert.rejects(loader.load("beta"), /createUnitUi/);
    const mod = await loader.load("beta");
    assert.equal(typeof mod.createUnitUi, "function");
    assert.equal(attempts, 2);
  });

  it("keeps a broken package from affecting another one", async () => {
    const loader = createLoader({
      importer: async (ui) => {
        if (ui === "broken") throw new SyntaxError("Unexpected token");
        return factory();
      },
    });
    await assert.rejects(loader.load("broken"), SyntaxError);
    const mod = await loader.load("alpha");
    assert.equal(typeof mod.createUnitUi, "function");
  });
});
