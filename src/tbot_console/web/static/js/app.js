import { client, getToken, onUnauthorized, setToken, store } from "./api.js";
import { h } from "./dom.js";
import {
  createActionRunner,
  createDashboard,
  dot,
  groupUnits,
  unitTone,
} from "./shell/dashboard.js";
import { createLoader } from "./shell/loader.js";
import { createProcessesPage } from "./shell/processes.js";
import { createRegistry } from "./shell/registry.js";
import {
  displaySegment,
  knownSegment,
  PROCESSES_PAGE,
  parseHash,
  resolvePage,
  unitHref,
} from "./shell/route.js";
import { hide, initTooltips } from "./tooltip.js";

const TOAST_MS = 8_000;
const TOAST_ERROR_MS = 20_000;

const control = client("/api/control");
const loader = createLoader();
const instances = new Map();
const lastOps = new Map();

const dom = {
  nav: document.getElementById("unit-nav"),
  topbar: document.getElementById("topbar-slot"),
  banner: document.getElementById("service-banner"),
  view: document.getElementById("view"),
  toast: h("div", { class: "toast", role: "status", hidden: true }),
};

const shell = {
  locked: false,
  seq: 0,
  unitId: null,
  activation: null,
  page: null,
  navUnit: null,
  navPage: null,
  navSignature: "",
  toastTimer: null,
};

const registry = createRegistry({ get: (path) => control.get(path), onChange: onRegistryChange });

const runAction = createActionRunner({
  send: (path) => control.send("POST", path),
  notify,
  remember: (unitId, proc, opId) => lastOps.set(`${unitId}:${proc}`, opId),
  hurry: () => registry.hurry(),
});

function unitById(id) {
  return registry.current?.units.find((unit) => unit.id === id) || null;
}

function safely(fn) {
  if (typeof fn !== "function") return;
  try {
    fn();
  } catch (e) {
    console.error(e);
  }
}

function go(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

function createSdk(id) {
  return {
    id,
    api: client(`/api/${id}`),
    href: (page, arg) => unitHref(id, page, arg),
    navigate: (page, arg) => go(unitHref(id, page, arg)),
    rerender() {
      if (
        shell.unitId === id &&
        shell.page?.kind === "unit" &&
        shell.page.page !== PROCESSES_PAGE
      ) {
        renderUnitPage(shell.page.page, shell.page.arg);
      }
    },
    storage: {
      get: (name) => store.get(`${id}.${name}`),
      set: (name, value) => store.set(`${id}.${name}`, value),
    },
  };
}

function isCompleteUi(value) {
  return (
    value &&
    Array.isArray(value.pages) &&
    typeof value.activate === "function" &&
    typeof value.render === "function"
  );
}

function instanceFor(unit) {
  let inst = instances.get(unit.id);
  if (inst && inst.ui !== unit.ui) {
    dropInstance(unit.id);
    inst = null;
  }
  if (inst) return inst;
  const created = {
    ui: unit.ui,
    sdk: createSdk(unit.id),
    value: null,
    error: null,
    settled: false,
  };
  const loading = unit.ui
    ? loader.load(unit.ui).then((mod) => {
        const value = mod.createUnitUi(created.sdk);
        if (!isCompleteUi(value)) throw new Error("интерфейс вернул неполное описание страниц");
        created.value = value;
      })
    : Promise.resolve();
  created.ready = loading
    .catch((e) => {
      console.error(e);
      created.error = e;
    })
    .finally(() => {
      created.settled = true;
    });
  instances.set(unit.id, created);
  return created;
}

function dropInstance(id) {
  if (shell.unitId === id) deactivate();
  instances.delete(id);
}

function teardownPage() {
  const page = shell.page;
  shell.page = null;
  hide();
  if (page) {
    page.controller?.abort();
    safely(page.cleanup);
  }
  dom.view.replaceChildren();
}

function deactivate() {
  teardownPage();
  const activation = shell.activation;
  shell.activation = null;
  shell.unitId = null;
  safely(activation?.cleanup);
  dom.topbar.replaceChildren();
  syncBanner();
}

function activate(unit, inst) {
  const box = h("div", { class: "unit-topbar", "data-unit": unit.id });
  dom.topbar.replaceChildren(box);
  const activation = { cleanup: null, bannerText: null };
  shell.activation = activation;
  shell.unitId = unit.id;
  if (!inst.value) return;
  const host = {
    topbar: box,
    banner(text) {
      if (shell.activation !== activation) return;
      activation.bannerText = text ? String(text) : null;
      syncBanner();
    },
  };
  try {
    activation.cleanup = inst.value.activate(host);
  } catch (e) {
    console.error(e);
    inst.error = e;
    inst.value = null;
  }
}

function syncBanner() {
  const unit = shell.unitId ? unitById(shell.unitId) : null;
  let text = shell.activation?.bannerText || null;
  if (!text && unit?.api && unit.api.reachable === false) {
    text = unit.api.hint || `Служба «${unit.title}» не отвечает. Консоль ждёт её и продолжит сама.`;
  }
  dom.banner.hidden = !text;
  dom.banner.textContent = text || "";
}

function notify(text, tone) {
  dom.toast.className = `toast tone-${tone === "bad" ? "bad" : "ok"}`;
  dom.toast.textContent = text;
  dom.toast.hidden = false;
  clearTimeout(shell.toastTimer);
  shell.toastTimer = setTimeout(
    () => {
      dom.toast.hidden = true;
    },
    tone === "bad" ? TOAST_ERROR_MS : TOAST_MS,
  );
}

function navLink(href, glyph, label, active) {
  return h(
    "a",
    { href, class: active ? "active" : null, "aria-current": active ? "page" : null },
    h("span", { class: "nav-glyph" }, glyph),
    label,
  );
}

function pagesNav(unit) {
  const inst = instances.get(unit.id);
  const pages = inst?.value ? inst.value.pages : [];
  const links = pages.map((page) =>
    navLink(unitHref(unit.id, page.id), page.glyph, page.label, page.id === shell.navPage),
  );
  links.push(
    navLink(unitHref(unit.id, PROCESSES_PAGE), "⚙", "Процессы", shell.navPage === PROCESSES_PAGE),
  );
  return h("div", { class: "nav-pages" }, links);
}

function navSignature(data) {
  const inst = instances.get(shell.navUnit);
  return JSON.stringify([
    shell.page?.kind === "home",
    shell.navUnit,
    shell.navPage,
    inst?.value ? inst.value.pages : null,
    data.units.map((unit) => [
      unit.id,
      unit.kind,
      unit.title,
      unit.glyph,
      unit.sub,
      unitTone(unit),
    ]),
  ]);
}

function renderNav() {
  const data = registry.current;
  if (!data || shell.locked) return;
  const signature = navSignature(data);
  if (signature === shell.navSignature) return;
  shell.navSignature = signature;
  const nodes = [navLink("#/", "⌂", "Главная", shell.page?.kind === "home")];
  for (const group of groupUnits(data.units)) {
    const items = [];
    for (const unit of group.units) {
      const active = unit.id === shell.navUnit;
      items.push(
        h(
          "a",
          {
            href: unitHref(unit.id),
            class: `nav-unit${active ? " current" : ""}`,
            "data-unit": unit.id,
            title: unit.sub || null,
          },
          dot(unitTone(unit)),
          h("span", { class: "nav-glyph" }, unit.glyph),
          h("span", { class: "nav-label" }, unit.title),
        ),
      );
      if (active) items.push(pagesNav(unit));
    }
    nodes.push(
      h("div", { class: "nav-group" }, h("div", { class: "nav-group-title" }, group.title), items),
    );
  }
  dom.nav.replaceChildren(...nodes);
}

function setNav(unitId, page) {
  shell.navUnit = unitId;
  shell.navPage = page;
  renderNav();
}

function showHome() {
  teardownPage();
  const controller = new AbortController();
  const dashboard = createDashboard({
    onAction: (unit, proc, action) => runAction(unit, proc, action, controller.signal),
  });
  shell.page = { kind: "home", controller, cleanup: null, dashboard };
  dom.view.append(dashboard.root);
  dashboard.update(registry.current, registry.error);
}

function showUnknown(segment) {
  teardownPage();
  const failed = Boolean(registry.error);
  shell.page = { kind: "unknown", segment, failed, controller: null, cleanup: null };
  dom.view.append(
    h(
      "div",
      { class: "panel empty" },
      h("span", { class: "glyph" }, failed ? "!" : "?"),
      failed
        ? `Список стратегий и мониторов не загрузился: ${registry.error?.message || registry.error}. Консоль повторит запрос сама.`
        : `Консоль не знает службы «${displaySegment(segment)}». Возможно, её описание ещё не добавлено или в нём ошибка — список служб обновляется сам.`,
      h("br"),
      h("a", { href: "#/" }, "На главную"),
    ),
  );
}

function uiErrorPanel(unit, error) {
  return h(
    "div",
    { class: "panel empty" },
    h("span", { class: "glyph" }, "!"),
    `Не удалось открыть страницы «${unit.title}»: ${error?.message || error || "неизвестная ошибка"}. Остальные стратегии и мониторы работают как обычно. Если консоль только что перезапускалась, обновите страницу.`,
    h("br"),
    h(
      "button",
      { class: "btn small", type: "button", onclick: () => location.reload() },
      "Обновить страницу",
    ),
    " ",
    h("a", { href: unitHref(unit.id, PROCESSES_PAGE) }, "Процессы этой службы"),
  );
}

function renderUnitPage(page, arg) {
  const unit = unitById(shell.unitId);
  if (!unit) {
    route();
    return;
  }
  const inst = instances.get(unit.id);
  teardownPage();
  const controller = new AbortController();
  const current = { kind: "unit", unitId: unit.id, page, arg, controller, cleanup: null };
  shell.page = current;
  if (page === PROCESSES_PAGE) {
    const processes = createProcessesPage({
      unitId: unit.id,
      get: (path, query, signal) => control.get(path, query, signal),
      onAction: (target, proc, action) => runAction(target, proc, action, controller.signal),
      signal: controller.signal,
      lastOp: (unitId, proc) => lastOps.get(`${unitId}:${proc}`) || null,
    });
    current.processes = processes;
    dom.view.append(processes.root);
    processes.update(unit);
    return;
  }
  if (!inst?.value) {
    dom.view.append(uiErrorPanel(unit, inst?.error));
    return;
  }
  try {
    current.cleanup = inst.value.render(dom.view, { page, arg, signal: controller.signal });
  } catch (e) {
    console.error(e);
    dom.view.replaceChildren(uiErrorPanel(unit, e));
  }
}

async function route() {
  const seq = ++shell.seq;
  await registry.ready;
  if (seq !== shell.seq || shell.locked) return;
  const data = registry.current;
  const parsed = parseHash(location.hash, data.units);
  if (parsed.kind === "redirect") {
    history.replaceState(null, "", parsed.to);
    route();
    return;
  }
  if (parsed.kind === "home" || parsed.kind === "unknown") {
    deactivate();
    if (parsed.kind === "home") showHome();
    else showUnknown(parsed.segment);
    setNav(null, null);
    return;
  }
  const unit = unitById(parsed.unit);
  const inst = instanceFor(unit);
  if (shell.unitId !== unit.id) deactivate();
  if (!inst.settled) {
    teardownPage();
    dom.view.append(h("div", { class: "panel empty" }, `Загрузка страниц «${unit.title}»…`));
    setNav(unit.id, parsed.page);
    await inst.ready;
    if (seq !== shell.seq || shell.locked) return;
  }
  let page = parsed.page;
  let arg = parsed.arg;
  if (!inst.error || !page || page === PROCESSES_PAGE) {
    const resolved = resolvePage(inst.value ? inst.value.pages : [], page);
    if (resolved !== page) {
      page = resolved;
      arg = "";
      history.replaceState(null, "", unitHref(unit.id, page));
    }
  }
  if (shell.unitId !== unit.id) activate(unit, inst);
  setNav(unit.id, page);
  syncBanner();
  renderUnitPage(page, arg);
}

function onRegistryChange(data, error) {
  if (shell.locked) return;
  let reroute = false;
  for (const [id, inst] of [...instances]) {
    const unit = data.units.find((u) => u.id === id);
    if (unit && unit.ui === inst.ui) continue;
    if (shell.unitId === id) reroute = true;
    dropInstance(id);
  }
  const page = shell.page;
  if (
    page?.kind === "unknown" &&
    (page.failed !== Boolean(error) || knownSegment(data.units, page.segment))
  ) {
    reroute = true;
  }
  if (reroute) {
    route();
    return;
  }
  renderNav();
  syncBanner();
  if (page?.kind === "home") page.dashboard.update(data, error);
  if (page?.kind === "unit" && page.processes) page.processes.update(unitById(page.unitId));
}

function showTokenGate() {
  if (shell.locked) return;
  deactivate();
  shell.locked = true;
  shell.seq += 1;
  shell.navSignature = "";
  dom.nav.replaceChildren();
  const input = /** @type {HTMLInputElement} */ (
    h("input", {
      type: "password",
      autocomplete: "current-password",
      "aria-label": "Токен доступа",
      placeholder: "токен",
      required: true,
    })
  );
  const form = h(
    "form",
    { class: "token-form", "data-role": "token-form" },
    input,
    h("button", { class: "btn primary", type: "submit" }, "Войти"),
  );
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    setToken(input.value.trim());
    unlock();
  });
  dom.view.replaceChildren(
    h(
      "div",
      { class: "panel token-gate" },
      h("h2", { class: "view-title" }, "Доступ по токену"),
      h(
        "p",
        { class: "view-sub" },
        "Консоль защищена токеном (переменная окружения TBOT_WEB_TOKEN). Введите его, чтобы продолжить.",
      ),
      getToken() ? h("div", { class: "error-banner" }, "Токен не подошёл.") : null,
      form,
    ),
  );
  input.focus();
}

async function unlock() {
  shell.locked = false;
  dom.view.replaceChildren();
  await registry.refresh();
  if (!shell.locked) route();
}

dom.toast.addEventListener("click", () => {
  dom.toast.hidden = true;
});
document.body.append(dom.toast);
onUnauthorized(showTokenGate);
initTooltips();
window.addEventListener("hashchange", () => route());
registry.start();
route();
