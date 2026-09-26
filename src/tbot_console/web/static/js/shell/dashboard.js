import { h, replaceChildrenIfChanged, showConfirm } from "../dom.js";
import { fmtDuration, fmtTime } from "../util.js";
import { processBusy } from "./registry.js";
import { unitHref } from "./route.js";

const TONES = new Set(["ok", "warn", "bad", "idle"]);

const KIND_GROUPS = [
  { kind: "strategy", title: "Стратегии" },
  { kind: "monitor", title: "Мониторы" },
  { kind: "system", title: "Консоль" },
];

const STATE_TIPS = {
  running: "Программа запущена и работает.",
  starting: "Консоль запускает программу и ждёт, пока та будет готова к работе.",
  stopping: "Программе отправлен сигнал остановиться; консоль ждёт, пока она аккуратно завершится.",
  checking:
    "Консоль проверяет настройки перед запуском. Если настройки плохие, программа не запустится, а работающая не будет остановлена.",
  stopped: "Программа не запущена.",
  exited:
    "Программа работала, но завершилась сама, без команды остановки. Причину ищите в её выводе на странице «Процессы».",
  stuck:
    "Программе велели остановиться, но она не остановилась за отведённое время и продолжает работать. Нужно разобраться вручную.",
  interrupted:
    "Команда запуска или остановки оборвалась на середине. Проверьте состояние программы и повторите команду.",
  foreign:
    "Программа работает, но запущена не из консоли (например, вручную в терминале). Консоль не управляет ею, пока вы не нажмёте «Принять».",
};

const STATE_TONES = {
  running: "ok",
  starting: "warn",
  stopping: "warn",
  checking: "warn",
  stopped: "idle",
  exited: "bad",
  stuck: "bad",
  interrupted: "bad",
  foreign: "warn",
};

const ACTION_LABELS = {
  start: "Запустить",
  stop: "Остановить",
  restart: "Перезапустить",
  adopt: "Принять",
};

export const ACTION_NOUNS = {
  start: "запуск",
  stop: "остановка",
  restart: "перезапуск",
  adopt: "приём под управление",
};

const ACTION_TIPS = {
  start: "Запустить программу. Перед запуском консоль проверит настройки.",
  stop: "Остановить программу мягким сигналом: она сама завершит работу и выйдет.",
  restart:
    "Проверить настройки, затем остановить и снова запустить программу. Если настройки плохие, работающая программа останется как есть.",
  adopt:
    "Взять под управление консоли программу, запущенную вне её. Программа при этом не перезапускается и не останавливается.",
};

export const PHASE_LABELS = {
  checking: "проверяю настройки",
  stopping: "жду остановки",
  starting: "запускаю",
  waiting_ready: "жду готовности",
  done: "готово",
  failed: "не удалось",
};

const TRADES_TIP =
  "Эта стратегия сама отправляет заявки на биржу: запуск и остановка её бота меняют то, как ведутся открытые позиции.";
const PID_TIP = "Номер процесса в операционной системе — по нему программу видно в команде ps.";
const LIVE_STATES = new Set(["running", "foreign", "stuck"]);
const BAD_STATES = new Set(["exited", "stuck", "interrupted"]);
const MAX_PROBLEMS = 3;

export function pick(table, key) {
  return typeof key === "string" && Object.hasOwn(table, key) ? table[key] : null;
}

function toneOf(value) {
  return TONES.has(value) ? value : "idle";
}

export function stateLabel(proc) {
  const label = proc?.label ? String(proc.label) : String(proc?.state ?? "неизвестно");
  return proc?.state === "running" && proc.adopted ? `${label} (принят консолью)` : label;
}

export function unitTone(unit) {
  const processes = unit?.processes || [];
  if (processes.some((p) => BAD_STATES.has(p.state))) return "bad";
  if (unit?.status_error) return "bad";
  if (unit?.status && TONES.has(unit.status.tone)) return unit.status.tone;
  if (unit?.api && unit.api.reachable === false) return "bad";
  if (processes.some((p) => pick(STATE_TONES, p.state) === "warn")) return "warn";
  if (processes.length && processes.every((p) => p.state === "running")) return "ok";
  return "idle";
}

export function groupUnits(units) {
  return KIND_GROUPS.map((group) => ({
    ...group,
    units: (units || []).filter((unit) => unit.kind === group.kind),
  })).filter((group) => group.units.length);
}

export function fmtUptime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 2 * 86_400) return fmtDuration(seconds);
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  return `${days}д ${hours}ч`;
}

function processTime(proc, nowSec) {
  const since = Number(proc?.since);
  if (!Number.isFinite(since) || since <= 0) return { text: "—", tip: "" };
  if (LIVE_STATES.has(proc.state)) {
    return {
      text: fmtUptime(nowSec - since),
      tip: `Сколько времени программа уже работает: запущена ${fmtTime(since)}.`,
    };
  }
  return { text: `с ${fmtTime(since)}`, tip: "Когда программа перешла в это состояние." };
}

function processNotes(proc) {
  const notes = [];
  if (proc?.problem) notes.push({ text: String(proc.problem), tone: "bad" });
  const op = proc?.op;
  if (op) {
    const action = pick(ACTION_NOUNS, op.action) || String(op.action ?? "");
    const phase = pick(PHASE_LABELS, op.phase) || String(op.phase ?? "");
    const finished = op.phase === "done" || op.phase === "failed";
    const interrupted = op.phase === "interrupted";
    notes.push({
      text: interrupted
        ? `Команда «${action}» прервалась, не дойдя до конца`
        : `${finished ? "Последняя команда" : "Идёт команда"}: ${action} — ${phase}`,
      tone: op.phase === "failed" ? "bad" : finished ? "idle" : "warn",
    });
    if (op.error) notes.push({ text: `Ошибка: ${op.error}`, tone: "bad" });
  }
  return notes;
}

export function dot(tone) {
  return h("span", { class: `tone-dot tone-${toneOf(tone)}`, "aria-hidden": "true" });
}

function actionButtons(unit, proc, onAction) {
  const busy = processBusy(proc);
  const buttons = [];
  for (const action of proc?.can || []) {
    if (!Object.hasOwn(ACTION_LABELS, action)) continue;
    buttons.push(
      h(
        "button",
        {
          class: `btn small${action === "start" ? " primary" : ""}`,
          type: "button",
          "data-action": action,
          "data-tip": ACTION_TIPS[action],
          disabled: busy,
          onclick: () => onAction(unit, proc, action),
        },
        ACTION_LABELS[action],
      ),
    );
  }
  return h("div", { class: "proc-actions" }, buttons);
}

export function processRows(unit, proc, { nowSec, onAction, compact = false }) {
  const time = processTime(proc, nowSec);
  const pid = proc?.pid ? `pid ${proc.pid}` : "";
  const stateCell = h(
    "td",
    { class: `proc-state tone-${toneOf(pick(STATE_TONES, proc?.state))}` },
    h(
      "span",
      { "data-tip": pick(STATE_TIPS, proc?.state) },
      dot(pick(STATE_TONES, proc?.state)),
      stateLabel(proc),
    ),
  );
  const cells = [
    h(
      "td",
      {},
      h("div", { class: "proc-title" }, String(proc?.title || proc?.name || "")),
      h("div", { class: "proc-name mono muted" }, String(proc?.name ?? "")),
    ),
    stateCell,
  ];
  let clock;
  if (compact) {
    clock = h("span", { "data-tip": time.tip || null }, time.text);
    stateCell.append(
      h(
        "div",
        { class: "proc-meta mono muted" },
        pid ? h("span", { "data-tip": PID_TIP }, pid) : null,
        pid ? " · " : null,
        clock,
      ),
    );
  } else {
    clock = h("td", { class: "num mono", "data-tip": time.tip || null }, time.text);
    cells.push(h("td", { class: "num mono", "data-tip": pid ? PID_TIP : null }, pid || "—"), clock);
  }
  cells.push(h("td", {}, actionButtons(unit, proc, onAction)));
  const rows = [h("tr", { "data-proc": String(proc?.name ?? "") }, cells)];
  const notes = processNotes(proc);
  if (notes.length) {
    rows.push(
      h(
        "tr",
        { class: "proc-notes" },
        h(
          "td",
          { colspan: String(cells.length) },
          notes.map((note) =>
            h("div", { class: `proc-note tone-${toneOf(note.tone)}` }, note.text),
          ),
        ),
      ),
    );
  }
  return { rows, clock, proc };
}

export function refreshClock(entry, nowSec) {
  const time = processTime(entry.proc, nowSec);
  entry.clock.textContent = time.text;
}

function metricNode(metric) {
  const tone = metric?.tone ? ` tone-${toneOf(metric.tone)}` : "";
  return h(
    "div",
    { class: `dash-metric${tone}`, "data-tip": metric?.hint ? String(metric.hint) : null },
    h("span", { class: "label" }, String(metric?.label ?? "")),
    h("span", { class: "value" }, String(metric?.value ?? "")),
  );
}

function statusBlock(unit) {
  const parts = [];
  if (unit.status_error) {
    parts.push(
      h("div", { class: "dash-headline tone-bad" }, dot("bad"), String(unit.status_error)),
    );
  } else if (unit.status) {
    const status = unit.status;
    const metrics = Array.isArray(status.metrics) ? status.metrics : [];
    const problems = Array.isArray(status.problems) ? status.problems : [];
    parts.push(
      h(
        "div",
        { class: `dash-headline tone-${toneOf(status.tone)}` },
        dot(status.tone),
        String(status.headline ?? ""),
      ),
    );
    if (metrics.length) parts.push(h("div", { class: "dash-metrics" }, metrics.map(metricNode)));
    if (problems.length) {
      parts.push(
        h(
          "ul",
          { class: "dash-problems" },
          problems
            .slice(0, MAX_PROBLEMS)
            .map((p) =>
              h("li", { class: `tone-${toneOf(p?.tone)}` }, dot(p?.tone), String(p?.text ?? "")),
            ),
          problems.length > MAX_PROBLEMS
            ? h("li", { class: "muted" }, `…и ещё ${problems.length - MAX_PROBLEMS}`)
            : null,
        ),
      );
    }
  }
  if (unit.api && unit.api.reachable === false) {
    parts.push(
      h(
        "div",
        { class: "dash-hint" },
        String(unit.api.hint || `Служба «${unit.title}» не отвечает.`),
      ),
    );
  }
  return parts.length ? h("div", { class: "dash-status" }, parts) : null;
}

function unitCard(unit, { nowSec, onAction }) {
  const clocks = unit.processes.map((proc) =>
    processRows(unit, proc, { nowSec, onAction, compact: true }),
  );
  const rows = clocks.flatMap((entry) => entry.rows);
  const body = rows.length
    ? h("table", { class: "dash-procs" }, h("tbody", {}, rows))
    : h("p", { class: "dash-noprocs muted" }, "Консоль не управляет процессами этой службы.");
  const node = h(
    "section",
    { class: `dash-card tone-${unitTone(unit)}`, "data-unit": unit.id },
    h(
      "header",
      { class: "dash-card-head" },
      h("span", { class: "dash-glyph" }, unit.glyph),
      h(
        "div",
        { class: "dash-card-title" },
        h("a", { href: unitHref(unit.id) }, unit.title),
        unit.sub ? h("span", { class: "dash-sub" }, unit.sub) : null,
      ),
      unit.places_orders
        ? h("span", { class: "dash-badge trades", "data-tip": TRADES_TIP }, "ставит заявки")
        : null,
    ),
    statusBlock(unit),
    body,
    h(
      "footer",
      { class: "dash-card-foot" },
      h("a", { href: unitHref(unit.id, "processes") }, "Процессы и их вывод →"),
    ),
  );
  return { node, clocks };
}

function brokenCard(item) {
  return h(
    "section",
    { class: "dash-card broken tone-bad" },
    h(
      "header",
      { class: "dash-card-head" },
      h("span", { class: "dash-glyph" }, "!"),
      h(
        "div",
        { class: "dash-card-title" },
        h("strong", {}, "Описание службы не прочитано"),
        h("span", { class: "dash-sub mono" }, String(item?.file ?? "?")),
      ),
    ),
    h("p", { class: "dash-broken-error" }, String(item?.error ?? "")),
    h(
      "p",
      { class: "muted" },
      "Остальные службы работают как обычно. Исправьте файл — консоль перечитает его сама.",
    ),
  );
}

function signature(unit) {
  return JSON.stringify(unit, (key, value) => (key === "updated_at" ? undefined : value));
}

/**
 * @param {{ onAction: (unit: any, proc: any, action: string) => void, now?: () => number }} options
 */
export function createDashboard({ onAction, now = () => Date.now() / 1000 }) {
  const note = h("div", { class: "error-banner", role: "status", hidden: true });
  const groupsBox = h("div", { class: "dash-groups" });
  const root = h(
    "div",
    { class: "dash" },
    h("h2", { class: "view-title" }, "Стратегии и мониторы"),
    h(
      "p",
      { class: "view-sub" },
      "Все службы торговой системы на одной странице: что запущено, как идут дела и кнопки запуска и остановки. Страница обновляется сама раз в 10 секунд, во время команды — каждую секунду.",
    ),
    note,
    groupsBox,
  );
  const cards = new Map();
  const sections = new Map();
  const brokenList = h("div", { class: "dash-cards" });
  const brokenSection = h(
    "section",
    { class: "dash-group", "data-kind": "broken" },
    h("h3", { class: "dash-group-title" }, "С ошибками в описании"),
    brokenList,
  );
  const empty = h(
    "div",
    { class: "panel empty" },
    "Консоль пока не знает ни одной стратегии или монитора.",
  );
  let brokenSig = "";

  function sectionFor(group) {
    let section = sections.get(group.kind);
    if (!section) {
      const list = h("div", { class: "dash-cards" });
      section = {
        list,
        node: h(
          "section",
          { class: "dash-group", "data-kind": group.kind },
          h("h3", { class: "dash-group-title" }, group.title),
          list,
        ),
      };
      sections.set(group.kind, section);
    }
    return section;
  }

  function update(registry, error) {
    const nowSec = now();
    note.hidden = !error;
    note.textContent = error
      ? `Консоль не отдала свежий список служб: ${error.message || error}. Ниже — последние известные данные.`
      : "";
    const units = registry?.units || [];
    const seen = new Set();
    const groupNodes = [];
    for (const group of groupUnits(units)) {
      const section = sectionFor(group);
      const nodes = [];
      for (const unit of group.units) {
        seen.add(unit.id);
        const sig = signature(unit);
        let card = cards.get(unit.id);
        if (!card || card.sig !== sig) {
          card = { sig, ...unitCard(unit, { nowSec, onAction }) };
          cards.set(unit.id, card);
        } else {
          for (const clock of card.clocks) refreshClock(clock, nowSec);
        }
        nodes.push(card.node);
      }
      replaceChildrenIfChanged(section.list, nodes);
      groupNodes.push(section.node);
    }
    for (const id of [...cards.keys()]) {
      if (!seen.has(id)) cards.delete(id);
    }
    const broken = registry?.broken || [];
    if (broken.length) {
      const sig = JSON.stringify(broken);
      if (sig !== brokenSig) {
        brokenSig = sig;
        brokenList.replaceChildren(...broken.map(brokenCard));
      }
      groupNodes.push(brokenSection);
    }
    if (!units.length && !broken.length) groupNodes.push(empty);
    replaceChildrenIfChanged(groupsBox, groupNodes);
  }

  return { root, update };
}

/**
 * @param {{ unit: any, proc: any, action: string, signal?: AbortSignal }} request
 * @returns {Promise<boolean>}
 */
export function confirmAction({ unit, proc, action, signal }) {
  const label = ACTION_LABELS[action] || String(action);
  const cancel = h(
    "button",
    { class: "btn ghost", type: "button", "data-role": "cancel" },
    "Отмена",
  );
  const apply = h("button", { class: "btn primary", type: "button", "data-role": "apply" }, label);
  const dialog = h(
    "dialog",
    { class: "confirm-dialog proc-confirm" },
    h("h3", {}, `${label} «${proc?.title || proc?.name}» — ${unit?.title}?`),
    h(
      "div",
      { class: "confirm-body" },
      h("p", { class: "confirm-note warn" }, String(proc?.confirm ?? "")),
      ACTION_TIPS[action] ? h("p", { class: "confirm-note" }, ACTION_TIPS[action]) : null,
    ),
    h("div", { class: "confirm-foot" }, cancel, apply),
  );
  return showConfirm(dialog, cancel, apply, signal);
}

/**
 * @param {{
 *   send: (path: string) => Promise<any>,
 *   confirm?: typeof confirmAction,
 *   notify?: (text: string, tone: string) => void,
 *   remember?: (unitId: string, proc: string, opId: string) => void,
 *   hurry?: () => void,
 * }} options
 */
export function createActionRunner({
  send,
  confirm = confirmAction,
  notify = () => {},
  remember = () => {},
  hurry = () => {},
}) {
  return async function runAction(unit, proc, action, signal) {
    if (!Object.hasOwn(ACTION_LABELS, action)) return false;
    const what = `«${ACTION_LABELS[action]}» для «${proc?.title || proc?.name}» (${unit?.title})`;
    const needsConfirm = Boolean(proc?.confirm) && action !== "adopt";
    if (needsConfirm && !(await confirm({ unit, proc, action, signal }))) return false;
    try {
      const result = await send(
        `units/${encodeURIComponent(unit.id)}/processes/${encodeURIComponent(proc.name)}/${action}`,
      );
      if (result?.op_id) remember(unit.id, proc.name, String(result.op_id));
      notify(`Команда ${what} принята — ход виден в строке процесса.`, "ok");
      return true;
    } catch (e) {
      notify(`Команда ${what} не выполнена: ${e?.message || e}`, "bad");
      return false;
    } finally {
      hurry();
    }
  };
}
