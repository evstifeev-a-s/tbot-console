import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createRegistry, FAST_POLL_MS, hasOpInFlight, POLL_MS } from "./registry.js";

const failure = (status, message = "boom") => Object.assign(new Error(message), { status });

function harness(answers, { now = () => 0 } = {}) {
  const queue = [...answers];
  const timers = [];
  const changes = [];
  const registry = createRegistry({
    get: async (path) => {
      assert.equal(path, "units");
      const next = queue.length > 1 ? queue.shift() : queue[0];
      if (next instanceof Error) throw next;
      return next;
    },
    onChange: (data, error) => changes.push({ data, error }),
    schedule: (fn, ms) => {
      timers.push({ fn, ms });
      return timers.length;
    },
    cancel: () => {},
    now,
  });
  return { registry, timers, changes };
}

function unit(id, extra = {}) {
  return { id, kind: "strategy", title: id, glyph: "X", sub: "", order: 10, ui: null, ...extra };
}

describe("hasOpInFlight", () => {
  it("trusts the busy states and never a stale op record", () => {
    const withProc = (proc) => ({ units: [{ processes: [proc] }] });
    assert.equal(hasOpInFlight(withProc({ state: "running", op: null })), false);
    assert.equal(hasOpInFlight(withProc({ state: "stopping", op: null })), true);
    assert.equal(
      hasOpInFlight(withProc({ state: "running", op: { phase: "waiting_ready" } })),
      false,
    );
    assert.equal(hasOpInFlight(withProc({ state: "foreign", op: { phase: "checking" } })), false);
    assert.equal(hasOpInFlight(withProc({ state: "running", op: { phase: "done" } })), false);
    assert.equal(
      hasOpInFlight(withProc({ state: "interrupted", op: { phase: "starting" } })),
      false,
    );
    assert.equal(hasOpInFlight(null), false);
  });
});

describe("createRegistry", () => {
  it("passes the server answer through and fills missing lists", async () => {
    const answer = { v: 1, control: true, units: [unit("beta"), unit("alpha")] };
    const { registry, changes } = harness([answer]);
    await registry.start();
    assert.deepEqual(registry.current, { ...answer, broken: [] });
    assert.equal(changes.length, 1);
  });

  it("starts empty with the error when the first answer fails, then recovers", async () => {
    const { registry, changes } = harness([
      failure(404, "консоль не знает службы «control»"),
      { units: [unit("alpha")] },
    ]);
    await registry.start();
    await registry.ready;
    assert.equal(registry.error.status, 404);
    assert.deepEqual(registry.current, { v: 1, control: false, units: [], broken: [] });
    assert.deepEqual(changes[0], { data: registry.current, error: registry.error });
    await registry.refresh();
    assert.equal(registry.error, null);
    assert.deepEqual(
      registry.current.units.map((u) => u.id),
      ["alpha"],
    );
  });

  it("keeps the last good answer when a later poll fails", async () => {
    const { registry } = harness([{ units: [unit("alpha"), unit("beta")] }, failure(502)]);
    await registry.start();
    await registry.refresh();
    assert.deepEqual(
      registry.current.units.map((u) => u.id),
      ["alpha", "beta"],
    );
    assert.equal(registry.error.status, 502);
  });

  it("polls every 10 s at rest and every second while an op is in flight", async () => {
    const busy = {
      units: [unit("alpha", { processes: [{ name: "bot", state: "starting", op: null }] })],
    };
    const { registry, timers } = harness([
      { units: [unit("alpha")] },
      busy,
      { units: [unit("alpha")] },
    ]);
    await registry.start();
    assert.equal(timers.at(-1).ms, POLL_MS);
    timers.at(-1).fn();
    await registry.refresh();
    assert.equal(timers.at(-1).ms, FAST_POLL_MS);
    await registry.refresh();
    assert.equal(timers.at(-1).ms, POLL_MS);
  });

  it("hurries for a while after an action", async () => {
    let clock = 1_000;
    const { registry, timers } = harness([{ units: [unit("alpha")] }], { now: () => clock });
    await registry.start();
    await registry.hurry(5_000);
    assert.equal(timers.at(-1).ms, FAST_POLL_MS);
    clock += 6_000;
    await registry.refresh();
    assert.equal(timers.at(-1).ms, POLL_MS);
  });

  it("stops polling on 401 until refreshed again", async () => {
    const { registry, timers } = harness([failure(401), { units: [unit("alpha")] }]);
    await registry.start();
    assert.equal(timers.length, 0);
    await registry.refresh();
    assert.equal(timers.length, 1);
  });

  it("shares one request between overlapping refreshes", async () => {
    let requests = 0;
    const registry = createRegistry({
      get: async () => {
        requests += 1;
        return { units: [] };
      },
      schedule: () => 0,
      cancel: () => {},
    });
    await Promise.all([registry.start(), registry.refresh(), registry.refresh()]);
    assert.equal(requests, 1);
  });
});
