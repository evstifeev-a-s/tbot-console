export const POLL_MS = 10_000;
export const FAST_POLL_MS = 1_000;

const BUSY_STATES = new Set(["starting", "stopping", "checking"]);

/** @typedef {import("../units/contract").Registry} Registry */

export function processBusy(proc) {
  return Boolean(proc && BUSY_STATES.has(proc.state));
}

export function hasOpInFlight(registry) {
  for (const unit of registry?.units || []) {
    for (const proc of unit.processes || []) {
      if (processBusy(proc)) return true;
    }
  }
  return false;
}

/**
 * @param {{
 *   get: (path: string) => Promise<any>,
 *   onChange?: (registry: Registry, error: any) => void,
 *   schedule?: (fn: () => void, ms: number) => any,
 *   cancel?: (handle: any) => void,
 *   now?: () => number,
 * }} options
 */
export function createRegistry({
  get,
  onChange = () => {},
  schedule = (fn, ms) => setTimeout(fn, ms),
  cancel = (handle) => clearTimeout(handle),
  now = () => Date.now(),
}) {
  /** @type {Registry | null} */
  let current = null;
  let error = null;
  let timer = null;
  let running = false;
  let fastUntil = 0;
  let pending = null;
  /** @type {(value?: unknown) => void} */
  let markReady = () => {};
  const ready = new Promise((resolve) => {
    markReady = resolve;
  });

  async function load() {
    try {
      const raw = await get("units");
      current = { ...raw, units: raw?.units ?? [], broken: raw?.broken ?? [] };
      error = null;
    } catch (e) {
      error = e;
      if (!current) current = { v: 1, control: false, units: [], broken: [] };
    }
    markReady();
    try {
      onChange(current, error);
    } catch (e) {
      console.error(e);
    }
  }

  function nextDelay() {
    return hasOpInFlight(current) || now() < fastUntil ? FAST_POLL_MS : POLL_MS;
  }

  function plan() {
    if (timer !== null) cancel(timer);
    timer = null;
    if (!running || error?.status === 401) return;
    timer = schedule(tick, nextDelay());
  }

  function tick() {
    timer = null;
    refresh();
  }

  function refresh() {
    if (!pending) {
      pending = load().finally(() => {
        pending = null;
        plan();
      });
    }
    return pending;
  }

  return {
    get current() {
      return current;
    },
    get error() {
      return error;
    },
    ready,
    refresh,
    start() {
      running = true;
      return refresh();
    },
    hurry(ms = 5_000) {
      fastUntil = Math.max(fastUntil, now() + ms);
      return refresh();
    },
  };
}
