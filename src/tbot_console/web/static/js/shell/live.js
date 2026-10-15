import { h, showConfirm } from "../dom.js";
import { fmtTimeFull } from "../util.js";

export const LIVE_RESTART_NOTE =
  "Переключатель живой торговли изменён — перезапустите процесс, чтобы применить.";

const PAPER_TIP =
  "Заявки этой стратегии уходят только на бумажный (учебный) счёт IBKR: деньги на нём не настоящие. Разрешить живой счёт можно на странице «Процессы».";
const LIVE_TIP =
  "Торговым процессам этой стратегии разрешено отправлять заявки на живой счёт IBKR — на реальные деньги. Выключается на странице «Процессы».";
const PAPER_STATE =
  "Сейчас: бумажный счёт. Торговые процессы этой стратегии отправляют заявки только на бумажный (учебный) счёт IBKR, деньги на нём не настоящие.";
const LIVE_STATE =
  "Сейчас: ЖИВОЙ СЧЁТ. Торговые процессы этой стратегии могут отправлять заявки на живой счёт IBKR — на реальные деньги.";
const SWITCH_HINT =
  "Переключатель начинает действовать, когда торговый процесс запускается или перезапускается: работающий процесс остаётся на том счёте, с которым стартовал. Стоп и цели каждой заявки лежат у брокера и срабатывают, даже если процесс остановлен.";
const VIA_NAMES = { console: "в консоли", cli: "командой tbot live" };

export function liveEnabled(unit) {
  return unit?.live_trading?.enabled === true;
}

export function liveBadge(unit) {
  if (!unit?.places_orders) return null;
  return liveEnabled(unit)
    ? h("span", { class: "dash-badge live", "data-tip": LIVE_TIP }, "ЖИВОЙ СЧЁТ")
    : h("span", { class: "dash-badge paper", "data-tip": PAPER_TIP }, "бумажный счёт");
}

function changedText(state) {
  const changed = state?.changed_at ? Date.parse(String(state.changed_at)) : Number.NaN;
  if (!Number.isFinite(changed)) return "";
  const via = typeof state.via === "string" && Object.hasOwn(VIA_NAMES, state.via);
  return `Изменено ${fmtTimeFull(changed / 1000)}${via ? ` ${VIA_NAMES[state.via]}` : ""}.`;
}

/**
 * @param {{ onToggle: (unit: any, enabled: boolean) => Promise<unknown> | unknown }} options
 */
export function createLivePanel({ onToggle }) {
  let current = null;
  let busy = false;
  const state = h("p", { class: "live-state" });
  const changed = h("p", { class: "live-changed muted" });
  const toggle = h(
    "button",
    {
      type: "button",
      class: "live-switch",
      role: "switch",
      "aria-checked": "false",
      onclick: () => flip(),
    },
    h(
      "span",
      { class: "live-switch-track", "aria-hidden": "true" },
      h("span", { class: "live-switch-knob" }),
    ),
    h("span", { class: "live-switch-label" }, "Разрешить заявки на живом счёте"),
  );
  const node = h(
    "section",
    { class: "panel live-panel", hidden: true },
    h("div", { class: "panel-title" }, "Счёт для заявок"),
    state,
    toggle,
    changed,
    h("p", { class: "live-hint muted" }, SWITCH_HINT),
  );

  async function flip() {
    if (!current || busy) return;
    busy = true;
    toggle.setAttribute("disabled", "");
    try {
      await onToggle(current, !liveEnabled(current));
    } finally {
      busy = false;
      toggle.removeAttribute("disabled");
    }
  }

  function update(unit) {
    current = unit;
    node.hidden = !unit?.places_orders;
    if (node.hidden) return;
    const on = liveEnabled(unit);
    node.className = `panel live-panel${on ? " on" : ""}`;
    toggle.setAttribute("aria-checked", String(on));
    state.textContent = on ? LIVE_STATE : PAPER_STATE;
    changed.textContent = changedText(unit.live_trading);
    changed.hidden = !changed.textContent;
  }

  return { node, update };
}

/**
 * @param {{ unit: any, signal?: AbortSignal }} request
 * @returns {Promise<boolean>}
 */
export function confirmLiveTrading({ unit, signal }) {
  const cancel = h(
    "button",
    { class: "btn ghost", type: "button", "data-role": "cancel" },
    "Отмена",
  );
  const apply = h(
    "button",
    { class: "btn danger", type: "button", "data-role": "apply" },
    "Разрешить живой счёт",
  );
  const dialog = h(
    "dialog",
    { class: "confirm-dialog live-confirm" },
    h("h3", {}, `Разрешить заявки на живом счёте — «${unit?.title}»?`),
    h(
      "div",
      { class: "confirm-body" },
      h(
        "p",
        { class: "confirm-note danger" },
        "Заявки пойдут на РЕАЛЬНЫЕ ДЕНЬГИ: после запуска или перезапуска торговый процесс этой стратегии будет отправлять заявки на живой счёт IBKR, а не на бумажный.",
      ),
      h(
        "p",
        { class: "confirm-note" },
        "Выключить переключатель можно в любой момент, без подтверждения; работающий процесс узнает об этом после перезапуска.",
      ),
    ),
    h("div", { class: "confirm-foot" }, cancel, apply),
  );
  return showConfirm(dialog, cancel, apply, signal);
}

/**
 * @param {{
 *   send: (path: string, body: { enabled: boolean }) => Promise<any>,
 *   confirm?: typeof confirmLiveTrading,
 *   notify?: (text: string, tone: string) => void,
 *   hurry?: () => void,
 * }} options
 */
export function createLiveSwitch({
  send,
  confirm = confirmLiveTrading,
  notify = () => {},
  hurry = () => {},
}) {
  return async function setLiveTrading(unit, enabled, signal) {
    if (!unit?.places_orders) return false;
    const on = enabled === true;
    if (on && !(await confirm({ unit, signal }))) return false;
    try {
      await send(`units/${encodeURIComponent(unit.id)}/live-trading`, { enabled: on });
      notify(
        on
          ? `«${unit.title}»: разрешены заявки на ЖИВОМ счёте. Действует после запуска или перезапуска торгового процесса.`
          : `«${unit.title}»: заявки только на бумажный счёт. Работающий торговый процесс узнает об этом после перезапуска.`,
        on ? "bad" : "ok",
      );
      return true;
    } catch (e) {
      notify(`Переключатель не изменён: ${e?.message || e}`, "bad");
      return false;
    } finally {
      hurry();
    }
  };
}
