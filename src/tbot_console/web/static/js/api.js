const TOKEN_KEY = "console:token";

let unauthorizedHook = null;

export const store = {
  get(key) {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key, value) {
    try {
      if (value === null || value === undefined) localStorage.removeItem(key);
      else localStorage.setItem(key, String(value));
    } catch {}
  },
};

export const getToken = () => store.get(TOKEN_KEY) || "";

export const setToken = (value) => store.set(TOKEN_KEY, value || null);

export function onUnauthorized(hook) {
  unauthorizedHook = typeof hook === "function" ? hook : null;
}

export function queryString(query) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query || {})) {
    if (value === null || value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  return search.toString();
}

/**
 * @param {string} url
 * @param {{ method?: string, body?: unknown, signal?: AbortSignal }} [options]
 */
export async function request(url, { method = "GET", body, signal } = {}) {
  /** @type {Record<string, string>} */
  const headers = { "X-Tbot-Console": "1" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  /** @type {RequestInit} */
  const init = { method, headers };
  if (signal) init.signal = signal;
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const response = await fetch(url, init);
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload && payload.detail !== undefined ? payload.detail : response.statusText;
    const error = /** @type {import("./units/contract").ServiceError} */ (
      new Error(Array.isArray(detail) ? detail.join("; ") : String(detail))
    );
    error.status = response.status;
    error.detail = detail;
    if (response.status === 401 && unauthorizedHook) unauthorizedHook(error);
    throw error;
  }
  return payload;
}

function joinPath(base, path) {
  const tail = String(path ?? "").replace(/^\/+/, "");
  return tail ? `${base}/${tail}` : base;
}

/**
 * @param {string} base
 * @returns {import("./units/contract").Client}
 */
export function client(base) {
  const root = String(base).replace(/\/+$/, "");
  return {
    get(path, query, signal) {
      const search = queryString(query);
      return request(`${joinPath(root, path)}${search ? `?${search}` : ""}`, { signal });
    },
    send(method, path, body, signal) {
      return request(joinPath(root, path), { method, body, signal });
    },
  };
}
