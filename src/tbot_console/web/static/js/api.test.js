import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, it } from "node:test";

import { client, getToken, onUnauthorized, queryString, request, setToken, store } from "./api.js";

let calls;
let reply;

function fakeStorage(entries = []) {
  const data = new Map(entries);
  globalThis.localStorage = {
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => data.set(key, value),
    removeItem: (key) => data.delete(key),
  };
  return data;
}

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: `status ${status}`,
    json: async () => {
      if (body === undefined) throw new Error("no body");
      return body;
    },
  };
}

beforeEach(() => {
  calls = [];
  reply = () => jsonResponse(200, { ok: true });
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return reply(url, init);
  };
});

afterEach(() => {
  delete globalThis.fetch;
  delete globalThis.localStorage;
  onUnauthorized(null);
});

describe("queryString", () => {
  it("drops null, undefined and empty strings but keeps zero and false", () => {
    assert.equal(
      queryString({ a: 1, b: null, c: undefined, d: "", e: 0, f: false, g: "x y" }),
      "a=1&e=0&f=false&g=x+y",
    );
    assert.equal(queryString(undefined), "");
  });
});

describe("client", () => {
  it("joins the base and path and appends only real query values", async () => {
    const api = client("/api/alpha/");
    await api.get("rows", { symbol: "abc", end: null, since: undefined });
    await api.get("/history/runs");
    await api.get("");
    assert.deepEqual(
      calls.map((c) => c.url),
      ["/api/alpha/rows?symbol=abc", "/api/alpha/history/runs", "/api/alpha"],
    );
  });

  it("marks every request as coming from the console", async () => {
    const api = client("/api/control");
    await api.get("units");
    await api.send("POST", "units/alpha/processes/bot/stop");
    for (const call of calls) assert.equal(call.init.headers["X-Tbot-Console"], "1");
    assert.equal(calls[1].init.method, "POST");
    assert.equal(calls[1].init.body, undefined);
    assert.equal(calls[1].init.headers["Content-Type"], undefined);
  });

  it("sends a JSON body with its content type", async () => {
    await client("/api/alpha").send("PUT", "config", { values: { size: 2 } });
    assert.equal(calls[0].init.method, "PUT");
    assert.equal(calls[0].init.body, JSON.stringify({ values: { size: 2 } }));
    assert.equal(calls[0].init.headers["Content-Type"], "application/json");
  });

  it("passes the abort signal through", async () => {
    const controller = new AbortController();
    await client("/api/beta").get("items", {}, controller.signal);
    assert.equal(calls[0].init.signal, controller.signal);
  });

  it("adds the stored bearer token", async () => {
    fakeStorage([["console:token", "secret"]]);
    await client("/api/alpha").get("config");
    assert.equal(calls[0].init.headers.Authorization, "Bearer secret");
  });
});

describe("store", () => {
  it("stores strings, removes on null and keeps the token under its key", () => {
    const data = fakeStorage();
    store.set("alpha.run", 7);
    assert.equal(store.get("alpha.run"), "7");
    store.set("alpha.run", null);
    assert.equal(data.has("alpha.run"), false);
    setToken("secret");
    assert.equal(getToken(), "secret");
    setToken("");
    assert.equal(data.has("console:token"), false);
    assert.equal(getToken(), "");
  });

  it("degrades to no storage when the browser refuses it", () => {
    const refuse = () => {
      throw new Error("SecurityError");
    };
    globalThis.localStorage = { getItem: refuse, setItem: refuse, removeItem: refuse };
    assert.equal(store.get("alpha.run"), null);
    assert.doesNotThrow(() => store.set("alpha.run", "1"));
    assert.equal(getToken(), "");
    assert.doesNotThrow(() => setToken("secret"));
    delete globalThis.localStorage;
    assert.equal(store.get("alpha.run"), null);
    assert.doesNotThrow(() => store.set("alpha.run", null));
  });
});

describe("errors", () => {
  it("carries status and detail, joining a detail list", async () => {
    reply = () => jsonResponse(422, { detail: ["первое", "второе"] });
    await assert.rejects(request("/api/alpha/config"), (error) => {
      assert.equal(error.status, 422);
      assert.equal(error.message, "первое; второе");
      return true;
    });
  });

  it("falls back to the status text when the body is not JSON", async () => {
    reply = () => jsonResponse(502, undefined);
    await assert.rejects(request("/api/alpha/config"), /status 502/);
  });

  it("calls the injected hook on 401 and still rejects", async () => {
    const seen = [];
    onUnauthorized((error) => seen.push(error.status));
    reply = () => jsonResponse(401, { detail: "missing or invalid token" });
    await assert.rejects(client("/api/control").get("units"), (error) => error.status === 401);
    assert.deepEqual(seen, [401]);
  });

  it("does not call the hook for other failures", async () => {
    const seen = [];
    onUnauthorized((error) => seen.push(error.status));
    reply = () => jsonResponse(503, { detail: "служба не отвечает" });
    await assert.rejects(client("/api/beta").get("history/runs"));
    assert.deepEqual(seen, []);
  });
});
