import { h, replaceChildrenIfChanged } from "../dom.js";
import { fmtTimeFull } from "../util.js";
import { ACTION_NOUNS, PHASE_LABELS, pick, processRows, refreshClock } from "./dashboard.js";

const OUTPUT_LINES = 200;

const linesOf = (data) => (Array.isArray(data?.lines) ? data.lines.map(String) : []);

function describeOp(record) {
  const phase = record?.phase;
  const finished = Number(record?.finished_at) > 0;
  return [
    ["Команда", pick(ACTION_NOUNS, record?.action) || String(record?.action ?? "—")],
    ["Этап", pick(PHASE_LABELS, phase) || String(phase ?? "—")],
    ["Начата", fmtTimeFull(record?.started_at)],
    ["Закончена", finished ? fmtTimeFull(record.finished_at) : "ещё идёт или оборвалась"],
    ["Ошибка", record?.error ? String(record.error) : "нет"],
  ];
}

function sizeText(size) {
  const bytes = Number(size);
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} байт`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
}

function detailPanel(unitId, get, lastOp, signal) {
  let proc = null;
  const heading = h("span");
  const outputButton = h(
    "button",
    { class: "btn ghost small", type: "button", onclick: () => loadOutput() },
    "Показать вывод",
  );
  const opButton = h(
    "button",
    { class: "btn ghost small", type: "button", onclick: () => loadOp() },
    "Последняя команда",
  );
  const meta = h("div", { class: "proc-output-meta muted", hidden: true });
  const output = h("pre", { class: "proc-output", hidden: true });
  const opBox = h("div", { class: "proc-op", hidden: true });
  const node = h(
    "section",
    { class: "panel proc-detail" },
    h(
      "div",
      { class: "panel-title" },
      heading,
      h("span", { class: "proc-detail-actions" }, outputButton, opButton),
    ),
    meta,
    output,
    opBox,
  );

  function opId() {
    return proc?.op?.op_id || lastOp(unitId, proc?.name) || null;
  }

  async function loadOutput() {
    if (!proc) return;
    const name = proc.name;
    outputButton.setAttribute("disabled", "");
    meta.hidden = false;
    meta.textContent = "Загрузка вывода…";
    try {
      const data = await get(
        `units/${encodeURIComponent(unitId)}/processes/${encodeURIComponent(name)}/output`,
        { lines: OUTPUT_LINES },
        signal,
      );
      if (signal.aborted) return;
      const lines = linesOf(data);
      const facts = [String(data?.path ?? ""), sizeText(data?.size)].filter(Boolean);
      facts.push(`последние ${lines.length} строк`);
      meta.textContent = facts.join(" · ");
      output.hidden = false;
      output.textContent = lines.length ? lines.join("\n") : "Вывод пуст.";
      output.scrollTop = output.scrollHeight;
      outputButton.textContent = "Обновить вывод";
    } catch (e) {
      if (signal.aborted) return;
      meta.textContent = `Не удалось загрузить вывод: ${e?.message || e}`;
    } finally {
      if (!signal.aborted) sync(proc);
    }
  }

  async function loadOp() {
    const id = opId();
    if (!id) return;
    opButton.setAttribute("disabled", "");
    opBox.hidden = false;
    opBox.replaceChildren(h("div", { class: "muted" }, "Загрузка…"));
    try {
      const response = await get(`ops/${encodeURIComponent(id)}`, undefined, signal);
      if (signal.aborted) return;
      const record = response?.op || null;
      const lines = linesOf(response);
      opBox.replaceChildren(
        record
          ? h(
              "div",
              { class: "proc-op-facts" },
              describeOp(record).map(([label, value]) =>
                h("div", {}, h("span", { class: "muted" }, `${label}: `), value),
              ),
            )
          : h(
              "div",
              { class: "muted" },
              "Запись команды не найдена: её уже сменила более новая команда. Ниже — её вывод.",
            ),
        lines.length ? h("pre", { class: "proc-output" }, lines.join("\n")) : null,
      );
    } catch (e) {
      if (signal.aborted) return;
      opBox.replaceChildren(
        h(
          "div",
          { class: "proc-note tone-bad" },
          `Не удалось загрузить команду: ${e?.message || e}`,
        ),
      );
    } finally {
      if (!signal.aborted) sync(proc);
    }
  }

  function sync(next) {
    proc = next;
    heading.textContent = `${proc?.title || proc?.name} · ${proc?.name}`;
    const hasOutput = proc?.output !== false;
    outputButton.toggleAttribute("disabled", !hasOutput);
    outputButton.setAttribute(
      "data-tip",
      hasOutput
        ? `Показать последние ${OUTPUT_LINES} строк того, что программа пишет в свой вывод.`
        : "У этой программы нет файла вывода, который видит консоль.",
    );
    const id = opId();
    opButton.toggleAttribute("disabled", !id);
    opButton.setAttribute(
      "data-tip",
      id
        ? "Показать, как прошла последняя команда запуска или остановки, и её журнал."
        : "Консоль ещё не выполняла команд для этой программы.",
    );
  }

  return { node, sync };
}

/**
 * @param {{
 *   unitId: string,
 *   get: (path: string, query?: any, signal?: AbortSignal) => Promise<any>,
 *   onAction: (unit: any, proc: any, action: string) => void,
 *   signal: AbortSignal,
 *   lastOp?: (unitId: string, proc: string) => string | null,
 *   now?: () => number,
 * }} options
 */
export function createProcessesPage({
  unitId,
  get,
  onAction,
  signal,
  lastOp = () => null,
  now = () => Date.now() / 1000,
}) {
  const title = h("h2", { class: "view-title" }, "Процессы");
  const note = h("div", { class: "panel empty", hidden: true });
  const tbody = h("tbody");
  const tablePanel = h(
    "div",
    { class: "panel proc-panel" },
    h(
      "table",
      { class: "grid proc-table" },
      h(
        "thead",
        {},
        h(
          "tr",
          {},
          h("th", {}, "Программа"),
          h("th", {}, "Состояние"),
          h("th", { class: "num" }, "Номер в системе"),
          h(
            "th",
            {
              class: "num",
              "data-tip":
                "Для работающей программы — сколько она уже работает, для остальных — когда она перешла в это состояние.",
            },
            "Время",
          ),
          h("th", {}, "Действия"),
        ),
      ),
      tbody,
    ),
  );
  const details = h("div", { class: "proc-details" });
  const root = h(
    "div",
    { class: "proc-page" },
    title,
    h(
      "p",
      { class: "view-sub" },
      "Из каких программ состоит служба, в каком они состоянии и что пишут в свой вывод. Таблица обновляется сама; вывод загружается по кнопке.",
    ),
    note,
    tablePanel,
    details,
  );
  const panels = new Map();
  let lastSignature = "";
  let clocks = [];

  function update(unit) {
    if (!unit) return;
    title.textContent = `Процессы · ${unit.title}`;
    const processes = unit.processes || [];
    if (!processes.length) {
      note.hidden = false;
      note.textContent =
        "Консоль не управляет процессами этой службы: либо у неё нет описания процессов, либо консоль запущена в старой версии.";
      tablePanel.hidden = true;
      details.replaceChildren();
      panels.clear();
      lastSignature = "";
      clocks = [];
      return;
    }
    note.hidden = true;
    tablePanel.hidden = false;
    const nowSec = now();
    const sig = JSON.stringify(processes);
    if (sig !== lastSignature) {
      lastSignature = sig;
      clocks = processes.map((proc) => processRows(unit, proc, { nowSec, onAction }));
      tbody.replaceChildren(...clocks.flatMap((entry) => entry.rows));
    } else {
      for (const entry of clocks) refreshClock(entry, nowSec);
    }
    const names = new Set(processes.map((p) => p.name));
    for (const name of [...panels.keys()]) {
      if (!names.has(name)) panels.delete(name);
    }
    const nodes = [];
    for (const proc of processes) {
      let panel = panels.get(proc.name);
      if (!panel) {
        panel = detailPanel(unitId, get, lastOp, signal);
        panels.set(proc.name, panel);
      }
      panel.sync(proc);
      nodes.push(panel.node);
    }
    replaceChildrenIfChanged(details, nodes);
  }

  return { root, update };
}
