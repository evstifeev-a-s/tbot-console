import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, it } from "node:test";

import {
  confirmAction,
  createActionRunner,
  createDashboard,
  fmtUptime,
  groupUnits,
  stateLabel,
  unitTone,
} from "./dashboard.js";
import { createProcessesPage } from "./processes.js";

class FakeText {
  constructor(text) {
    this.nodeType = 3;
    this.data = String(text);
    this.parentNode = null;
  }

  get textContent() {
    return this.data;
  }
}

class FakeElement {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = tag.toUpperCase();
    this.childNodes = [];
    this.attrs = new Map();
    this.listeners = new Map();
    this.parentNode = null;
    this.hidden = false;
    this.open = false;
    this.scrollTop = 0;
    this.scrollHeight = 0;
  }

  get children() {
    return this.childNodes.filter((node) => node.nodeType === 1);
  }

  get className() {
    return this.attrs.get("class") || "";
  }

  set className(value) {
    this.attrs.set("class", String(value));
  }

  get disabled() {
    return this.attrs.has("disabled");
  }

  set disabled(value) {
    if (value) this.attrs.set("disabled", "");
    else this.attrs.delete("disabled");
  }

  get textContent() {
    return this.childNodes.map((node) => node.textContent).join("");
  }

  set textContent(value) {
    this.replaceChildren(String(value));
  }

  set innerHTML(_value) {
    throw new Error("innerHTML must not be used by the shell");
  }

  setAttribute(name, value) {
    this.attrs.set(name, String(value));
  }

  getAttribute(name) {
    return this.attrs.has(name) ? this.attrs.get(name) : null;
  }

  hasAttribute(name) {
    return this.attrs.has(name);
  }

  removeAttribute(name) {
    this.attrs.delete(name);
  }

  toggleAttribute(name, force) {
    if (force) this.attrs.set(name, "");
    else this.attrs.delete(name);
  }

  append(...nodes) {
    for (const item of nodes) {
      const node = typeof item === "string" ? new FakeText(item) : item;
      if (node.parentNode) node.parentNode.removeChild(node);
      node.parentNode = this;
      this.childNodes.push(node);
    }
  }

  removeChild(node) {
    this.childNodes = this.childNodes.filter((child) => child !== node);
    node.parentNode = null;
  }

  replaceChildren(...nodes) {
    for (const node of this.childNodes) node.parentNode = null;
    this.childNodes = [];
    this.append(...nodes);
  }

  remove() {
    this.parentNode?.removeChild(this);
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  dispatch(type) {
    const event = { type, target: this, preventDefault() {} };
    for (const listener of this.listeners.get(type) || []) listener(event);
  }

  click() {
    this.dispatch("click");
  }

  showModal() {
    this.open = true;
  }

  close() {
    this.open = false;
  }
}

function* walk(node) {
  if (node.nodeType !== 1) return;
  yield node;
  for (const child of node.childNodes) yield* walk(child);
}

function all(root, predicate) {
  return [...walk(root)].filter(predicate);
}

function byClass(root, cls) {
  return all(root, (node) => node.className.split(/\s+/).includes(cls));
}

function buttons(root) {
  return all(root, (node) => node.tagName === "BUTTON");
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

const EVIL = `<img src=x onerror="alert(1)">`;
const EVIL2 = `"><script>alert(2)</script>`;

function proc(extra = {}) {
  return {
    name: "bot",
    title: "Торговый бот",
    state: "running",
    label: "работает",
    pid: 4242,
    since: 1_000,
    adopted: false,
    can: ["stop", "restart"],
    confirm: null,
    op: null,
    problem: null,
    output: true,
    ...extra,
  };
}

function unit(id, extra = {}) {
  return {
    id,
    kind: "strategy",
    title: id,
    glyph: "A",
    sub: "",
    order: 10,
    ui: null,
    places_orders: false,
    api: null,
    processes: [],
    status: null,
    status_error: null,
    ...extra,
  };
}

beforeEach(() => {
  globalThis.document = {
    createElement: (tag) => new FakeElement(tag),
    body: new FakeElement("body"),
  };
});

afterEach(() => {
  delete globalThis.document;
});

describe("pure helpers", () => {
  it("shows the server's state label and marks an adopted process", () => {
    assert.equal(stateLabel(proc({ state: "exited", label: "упал" })), "упал");
    assert.equal(
      stateLabel(proc({ state: "stuck", label: "завис при остановке" })),
      "завис при остановке",
    );
    assert.equal(stateLabel(proc({ adopted: true })), "работает (принят консолью)");
    assert.equal(stateLabel(proc({ state: "exited", label: "упал", adopted: true })), "упал");
    assert.equal(stateLabel(proc({ state: "constructor", label: undefined })), "constructor");
    assert.equal(stateLabel(null), "неизвестно");
  });

  it("shows long uptimes in days", () => {
    assert.equal(fmtUptime(90), "1м 30с");
    assert.equal(fmtUptime(3 * 86_400 + 5 * 3_600 + 60), "3д 5ч");
  });

  it("derives a unit tone, worst first", () => {
    assert.equal(unitTone(unit("alpha", { processes: [proc()] })), "ok");
    assert.equal(
      unitTone(unit("alpha", { processes: [proc(), proc({ state: "exited" })] })),
      "bad",
    );
    assert.equal(unitTone(unit("alpha", { processes: [proc({ state: "starting" })] })), "warn");
    assert.equal(unitTone(unit("alpha", { status_error: "timeout" })), "bad");
    assert.equal(
      unitTone(unit("alpha", { processes: [proc()], status: { tone: "warn", headline: "" } })),
      "warn",
    );
    assert.equal(unitTone(unit("alpha", { processes: [proc({ state: "stopped" })] })), "idle");
    assert.equal(unitTone(unit("alpha")), "idle");
  });

  it("groups by kind in a fixed order and drops empty groups", () => {
    const groups = groupUnits([
      unit("console", { kind: "system" }),
      unit("beta", { kind: "monitor" }),
      unit("alpha"),
    ]);
    assert.deepEqual(
      groups.map((g) => [g.title, g.units.map((u) => u.id)]),
      [
        ["Стратегии", ["alpha"]],
        ["Мониторы", ["beta"]],
        ["Консоль", ["console"]],
      ],
    );
  });
});

describe("dashboard rendering", () => {
  it("renders hostile server strings as text only", () => {
    const hostile = unit("alpha", {
      title: EVIL,
      glyph: EVIL2,
      sub: EVIL,
      places_orders: true,
      api: { url: "http://127.0.0.1:18601", reachable: false, hint: EVIL },
      status: {
        v: 1,
        tone: "ok evil",
        headline: EVIL2,
        metrics: Array.from({ length: 8 }, (_, i) => ({
          label: `${EVIL} ${i}`,
          value: EVIL2,
          hint: EVIL,
          tone: "</style>",
        })),
        problems: Array.from({ length: 5 }, () => ({ text: EVIL, tone: "bad" })),
        updated_at: 1,
      },
      processes: [
        proc({
          title: EVIL,
          problem: EVIL2,
          op: { op_id: "x", action: EVIL, phase: EVIL2, error: EVIL },
        }),
      ],
    });
    const dashboard = createDashboard({ onAction: () => {}, now: () => 2_000 });
    dashboard.update(
      {
        v: 1,
        control: true,
        units: [hostile],
        broken: [{ file: EVIL, error: EVIL2 }],
      },
      null,
    );
    const root = dashboard.root;
    assert.equal(all(root, (n) => n.tagName === "IMG" || n.tagName === "SCRIPT").length, 0);
    const text = root.textContent;
    assert.ok(text.includes(EVIL));
    assert.ok(text.includes(EVIL2));
    const metrics = byClass(root, "dash-metric");
    assert.equal(metrics.length, 8);
    assert.equal(metrics[0].getAttribute("data-tip"), EVIL);
    assert.ok(metrics.every((m) => m.className === "dash-metric tone-idle"));
    const headline = byClass(root, "dash-headline")[0];
    assert.equal(headline.className, "dash-headline tone-idle");
    const problems = byClass(root, "dash-problems")[0];
    assert.equal(problems.children.length, 4);
    assert.equal(problems.children[3].textContent, "…и ещё 2");
    assert.equal(byClass(root, "dash-hint")[0].textContent, EVIL);
    const link = all(root, (n) => n.tagName === "A")[0];
    assert.equal(link.getAttribute("href"), "#/alpha");
    assert.equal(link.textContent, EVIL);
    assert.equal(byClass(root, "broken").length, 1);
  });

  it("shows the console's status error as sent", () => {
    const dashboard = createDashboard({ onAction: () => {} });
    const text = "служба ответила кодом 500: внутренняя ошибка службы: OSError: disk gone";
    dashboard.update({ units: [unit("beta", { status_error: text })], broken: [] }, null);
    const headline = byClass(dashboard.root, "dash-headline")[0];
    assert.equal(headline.textContent, text);
  });

  it("offers only whitelisted actions and disables them while busy", () => {
    const calls = [];
    const target = unit("alpha", {
      processes: [
        proc({ can: ["stop", "restart", "rm -rf", "__proto__"] }),
        proc({ name: "api", title: "Служба", state: "starting", can: ["stop"] }),
      ],
    });
    const dashboard = createDashboard({ onAction: (u, p, a) => calls.push([u.id, p.name, a]) });
    dashboard.update({ units: [target], broken: [] }, null);
    const all = buttons(dashboard.root);
    assert.deepEqual(
      all.map((b) => [b.getAttribute("data-action"), b.textContent, b.disabled]),
      [
        ["stop", "Остановить", false],
        ["restart", "Перезапустить", false],
        ["stop", "Остановить", true],
      ],
    );
    all[1].click();
    assert.deepEqual(calls, [["alpha", "bot", "restart"]]);
  });

  it("groups cards under Russian headings and reuses unchanged cards", () => {
    const registry = {
      units: [
        unit("alpha", { processes: [proc()] }),
        unit("beta", { kind: "monitor" }),
        unit("console", { kind: "system" }),
      ],
      broken: [],
    };
    let clock = 2_000;
    const dashboard = createDashboard({ onAction: () => {}, now: () => clock });
    dashboard.update(registry, null);
    assert.deepEqual(
      byClass(dashboard.root, "dash-group-title").map((n) => n.textContent),
      ["Стратегии", "Мониторы", "Консоль"],
    );
    const card = byClass(dashboard.root, "dash-card")[0];
    const uptime = () => byClass(card, "proc-meta")[0].textContent;
    assert.equal(uptime(), "pid 4242 · 16м 40с");
    clock = 1_000 + 3_700;
    dashboard.update(structuredClone(registry), null);
    assert.equal(byClass(dashboard.root, "dash-card")[0], card);
    assert.equal(uptime(), "pid 4242 · 1ч 1м");
    registry.units[0].processes[0].state = "exited";
    dashboard.update(registry, null);
    assert.notEqual(byClass(dashboard.root, "dash-card")[0], card);
  });

  it("explains a failed registry answer and hides the note once it recovers", () => {
    const dashboard = createDashboard({ onAction: () => {} });
    const note = byClass(dashboard.root, "error-banner")[0];
    assert.equal(note.hidden, true);
    dashboard.update({ units: [unit("alpha")], broken: [] }, new Error("502"));
    assert.equal(note.hidden, false);
    assert.match(note.textContent, /не отдала свежий список служб: 502/);
    dashboard.update({ units: [unit("alpha")], broken: [] }, null);
    assert.equal(note.hidden, true);
  });

  it("keeps cards and the broken group in place across polls while a manifest is broken", () => {
    const registry = {
      units: [unit("alpha", { processes: [proc()] })],
      broken: [{ file: "config/units/x.json", error: "Expecting value: line 1 column 1" }],
    };
    const dashboard = createDashboard({ onAction: () => {}, now: () => 2_000 });
    dashboard.update(registry, null);
    const groupsBox = byClass(dashboard.root, "dash-groups")[0];
    const card = byClass(dashboard.root, "dash-card")[0];
    const brokenGroup = all(dashboard.root, (n) => n.getAttribute("data-kind") === "broken")[0];
    let rebuilds = 0;
    const replace = groupsBox.replaceChildren.bind(groupsBox);
    groupsBox.replaceChildren = (...nodes) => {
      rebuilds += 1;
      replace(...nodes);
    };
    dashboard.update(structuredClone(registry), null);
    assert.equal(rebuilds, 0);
    assert.equal(byClass(dashboard.root, "dash-card")[0], card);
    assert.equal(
      all(dashboard.root, (n) => n.getAttribute("data-kind") === "broken")[0],
      brokenGroup,
    );
    dashboard.update(
      { ...registry, broken: [{ file: "config/units/y.json", error: "нет id" }] },
      null,
    );
    assert.equal(rebuilds, 0);
    assert.match(brokenGroup.textContent, /config\/units\/y\.json/);
    assert.doesNotMatch(brokenGroup.textContent, /config\/units\/x\.json/);
  });
});

describe("confirmation and actions", () => {
  it("resolves false on cancel and removes the dialog", async () => {
    const pending = confirmAction({
      unit: unit("alpha", { title: EVIL }),
      proc: proc({ confirm: EVIL2 }),
      action: "stop",
    });
    const dialog = document.body.children[0];
    assert.equal(dialog.tagName, "DIALOG");
    assert.equal(dialog.open, true);
    assert.ok(dialog.textContent.includes(EVIL2));
    assert.equal(all(dialog, (n) => n.tagName === "SCRIPT").length, 0);
    all(dialog, (n) => n.getAttribute("data-role") === "cancel")[0].click();
    assert.equal(await pending, false);
    assert.equal(document.body.children.length, 0);
  });

  it("resolves true on apply and false when the page goes away", async () => {
    const applied = confirmAction({
      unit: unit("alpha"),
      proc: proc({ confirm: "x" }),
      action: "restart",
    });
    all(document.body, (n) => n.getAttribute("data-role") === "apply")[0].click();
    assert.equal(await applied, true);
    const controller = new AbortController();
    const aborted = confirmAction({
      unit: unit("alpha"),
      proc: proc({ confirm: "x" }),
      action: "stop",
      signal: controller.signal,
    });
    controller.abort();
    assert.equal(await aborted, false);
    assert.equal(document.body.children.length, 0);
  });

  it("sends nothing when the operator cancels", async () => {
    const sent = [];
    const run = createActionRunner({
      send: async (path) => sent.push(path),
      confirm: async () => false,
    });
    assert.equal(await run(unit("alpha"), proc({ confirm: "Бот торгует" }), "stop"), false);
    assert.deepEqual(sent, []);
  });

  it("posts the action, remembers the op and hurries the registry", async () => {
    const sent = [];
    const remembered = [];
    const notes = [];
    let hurried = 0;
    const run = createActionRunner({
      send: async (path) => {
        sent.push(path);
        return { op_id: "op-1" };
      },
      confirm: async () => true,
      notify: (_text, tone) => notes.push(tone),
      remember: (...args) => remembered.push(args),
      hurry: () => {
        hurried += 1;
      },
    });
    assert.equal(await run(unit("alpha"), proc({ confirm: "Бот торгует" }), "restart"), true);
    assert.equal(await run(unit("beta"), proc({ name: "api" }), "start"), true);
    assert.deepEqual(sent, ["units/alpha/processes/bot/restart", "units/beta/processes/api/start"]);
    assert.deepEqual(remembered[0], ["alpha", "bot", "op-1"]);
    assert.deepEqual(notes, ["ok", "ok"]);
    assert.equal(hurried, 2);
  });

  it("reports a refused action and ignores unknown ones", async () => {
    const notes = [];
    const run = createActionRunner({
      send: async () => {
        throw new Error("уже идёт другая команда");
      },
      notify: (text, tone) => notes.push([tone, text]),
    });
    assert.equal(await run(unit("alpha"), proc(), "stop"), false);
    assert.equal(notes[0][0], "bad");
    assert.match(notes[0][1], /уже идёт другая команда/);
    assert.equal(await run(unit("alpha"), proc(), "kill"), false);
    assert.equal(notes.length, 1);
  });
});

describe("processes page", () => {
  const openPage = (options) =>
    createProcessesPage({
      unitId: "alpha",
      onAction: () => {},
      signal: new AbortController().signal,
      ...options,
    });

  it("loads the output tail on demand and shows it as text", async () => {
    const requests = [];
    const page = openPage({
      get: async (path, query) => {
        requests.push([path, query]);
        return { path: "logs/units/alpha.bot.out", lines: [EVIL, "вторая строка"], size: 2048 };
      },
      now: () => 2_000,
    });
    page.update(
      unit("alpha", { title: "Альфа", processes: [proc(), proc({ name: "api", output: false })] }),
    );
    assert.equal(requests.length, 0);
    const outputButtons = all(
      page.root,
      (n) => n.tagName === "BUTTON" && n.textContent === "Показать вывод",
    );
    assert.equal(outputButtons.length, 2);
    assert.equal(outputButtons[1].disabled, true);
    outputButtons[0].click();
    await flush();
    assert.deepEqual(requests, [["units/alpha/processes/bot/output", { lines: 200 }]]);
    const pre = byClass(page.root, "proc-output")[0];
    assert.equal(pre.hidden, false);
    assert.equal(pre.textContent, `${EVIL}\nвторая строка`);
    assert.equal(all(page.root, (n) => n.tagName === "IMG").length, 0);
    assert.match(byClass(page.root, "proc-output-meta")[0].textContent, /2 КБ/);
  });

  it("shows the last command record for a process", async () => {
    const page = openPage({
      get: async (path) => {
        assert.equal(path, "ops/op-7");
        return {
          op: {
            op_id: "op-7",
            action: "restart",
            phase: "failed",
            started_at: 100,
            finished_at: 160,
            error: "не остановился за 120 с",
          },
          lines: ["строка 1", "строка 2"],
        };
      },
      lastOp: () => "op-7",
    });
    page.update(unit("alpha", { processes: [proc()] }));
    const opButton = all(
      page.root,
      (n) => n.tagName === "BUTTON" && n.textContent === "Последняя команда",
    )[0];
    assert.equal(opButton.disabled, false);
    opButton.click();
    await flush();
    const text = byClass(page.root, "proc-op")[0].textContent;
    assert.match(text, /перезапуск/);
    assert.match(text, /не удалось/);
    assert.match(text, /не остановился за 120 с/);
    assert.match(text, /строка 1\nстрока 2/);
  });

  it("explains a unit the console does not manage", () => {
    const page = openPage({ unitId: "beta", get: async () => ({}) });
    page.update(unit("beta"));
    assert.match(page.root.textContent, /не управляет процессами/);
  });
});
