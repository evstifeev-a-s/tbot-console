import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { displaySegment, knownSegment, parseHash, resolvePage, unitHref } from "./route.js";

const UNITS = [
  { id: "alpha", legacy_pages: ["old-page"] },
  { id: "beta", legacy_pages: [] },
  { id: "console", legacy_pages: [] },
];

describe("parseHash", () => {
  it("treats an empty hash and a bare slash as home", () => {
    for (const hash of ["", "#", "#/", "#//"]) {
      assert.deepEqual(parseHash(hash, UNITS), { kind: "home" });
    }
  });

  it("reads unit, page and the undecoded rest as arg", () => {
    assert.deepEqual(parseHash("#/alpha/history/parts", UNITS), {
      kind: "unit",
      unit: "alpha",
      page: "history",
      arg: "parts",
    });
    assert.deepEqual(parseHash("#/beta/history/a/b%20c", UNITS), {
      kind: "unit",
      unit: "beta",
      page: "history",
      arg: "a/b%20c",
    });
    assert.deepEqual(parseHash("#/beta", UNITS), {
      kind: "unit",
      unit: "beta",
      page: "",
      arg: "",
    });
    assert.deepEqual(parseHash("#/alpha/", UNITS), {
      kind: "unit",
      unit: "alpha",
      page: "",
      arg: "",
    });
  });

  it("redirects a legacy page to the unit that lists it, with its tail", () => {
    assert.deepEqual(parseHash("#/old-page", UNITS), {
      kind: "redirect",
      to: "#/alpha/old-page",
    });
    assert.deepEqual(parseHash("#/old-page/a/b", UNITS), {
      kind: "redirect",
      to: "#/alpha/old-page/a/b",
    });
  });

  it("gives a legacy page to the first unit in registry order that lists it", () => {
    const units = [
      { id: "beta", legacy_pages: ["old-page"] },
      { id: "alpha", legacy_pages: ["old-page", "old-list"] },
    ];
    assert.deepEqual(parseHash("#/old-page", units), { kind: "redirect", to: "#/beta/old-page" });
    assert.deepEqual(parseHash("#/old-list", units), { kind: "redirect", to: "#/alpha/old-list" });
  });

  it("lets a unit id win over another unit's legacy page", () => {
    const units = [
      { id: "alpha", legacy_pages: ["old-page"] },
      { id: "old-page", legacy_pages: [] },
    ];
    assert.deepEqual(parseHash("#/old-page", units), {
      kind: "unit",
      unit: "old-page",
      page: "",
      arg: "",
    });
  });

  it("reports a legacy page as unknown while no unit lists it", () => {
    const withoutAlpha = UNITS.filter((unit) => unit.id !== "alpha");
    assert.deepEqual(parseHash("#/old-page", withoutAlpha), {
      kind: "unknown",
      segment: "old-page",
    });
    assert.deepEqual(parseHash("#/old-page", [{ id: "beta" }]), {
      kind: "unknown",
      segment: "old-page",
    });
  });

  it("reports an unknown unit without redirecting", () => {
    assert.deepEqual(parseHash("#/ghost/old-page", UNITS), { kind: "unknown", segment: "ghost" });
    assert.deepEqual(parseHash("#/beta", [{ id: "alpha", legacy_pages: ["old-page"] }]), {
      kind: "unknown",
      segment: "beta",
    });
  });
});

describe("knownSegment", () => {
  it("knows unit ids and the legacy pages some unit lists", () => {
    assert.equal(knownSegment(UNITS, "beta"), true);
    assert.equal(knownSegment(UNITS, "old-page"), true);
    assert.equal(knownSegment(UNITS, "ghost"), false);
    assert.equal(
      knownSegment(
        UNITS.filter((unit) => unit.id !== "alpha"),
        "old-page",
      ),
      false,
    );
    assert.equal(knownSegment([], "beta"), false);
  });
});

describe("unitHref", () => {
  it("builds canonical hashes", () => {
    assert.equal(unitHref("alpha"), "#/alpha");
    assert.equal(unitHref("alpha", "history"), "#/alpha/history");
    assert.equal(unitHref("alpha", "history", "parts"), "#/alpha/history/parts");
    assert.equal(unitHref("alpha", "", "parts"), "#/alpha");
  });

  it("round-trips through parseHash", () => {
    const parsed = parseHash(unitHref("beta", "history", "parts"), UNITS);
    assert.deepEqual(parsed, { kind: "unit", unit: "beta", page: "history", arg: "parts" });
  });
});

describe("resolvePage", () => {
  const pages = [
    { id: "summary", glyph: "С", label: "Сводка" },
    { id: "settings", glyph: "Н", label: "Настройки" },
  ];

  it("keeps a known page and the shell processes page", () => {
    assert.equal(resolvePage(pages, "settings"), "settings");
    assert.equal(resolvePage(pages, "processes"), "processes");
    assert.equal(resolvePage([], "processes"), "processes");
  });

  it("falls back to the first page, or processes for a unit without pages", () => {
    assert.equal(resolvePage(pages, ""), "summary");
    assert.equal(resolvePage(pages, "nope"), "summary");
    assert.equal(resolvePage([], ""), "processes");
    assert.equal(resolvePage(undefined, "summary"), "processes");
  });
});

describe("displaySegment", () => {
  it("decodes when it can and keeps the raw text otherwise", () => {
    assert.equal(displaySegment("a%20b"), "a b");
    assert.equal(displaySegment("%E0%A4%A"), "%E0%A4%A");
  });
});
