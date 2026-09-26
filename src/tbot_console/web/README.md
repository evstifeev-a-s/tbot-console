# Web — the operator console and its unit shell

**The console** (`app:main`, console script `tbot-web`, built-in unit `console`, process `web`,
default `127.0.0.1:8420`) is one FastAPI process plus a bundler-free vanilla-JS frontend. It
serves `static/`, serves each unit's UI files from that unit's repo (`/js/units/<ui>/…`), checks
the token, answers the control plane `/api/control/*` (`control_api.py`) and forwards
`/api/<unit>/…` to the service of the unit that owns it (`proxy.py`). Cold path; it never places
orders.

**The console is the control plane, not a strategy host.** It learns its units from the registry
on every request ([`control/`](../control/README.md): home plus the roots in
`config/workspace.json`), so a new manifest or root appears within ~15 s and a removed one
disappears, with no restart. The home page lists every unit with its processes, their state and
the Start/Stop/Restart/Adopt buttons; every action is a detached
`python -m tbot_console.control.cli _op …` executor (202 + `op_id`), so a console restart never
touches a running process and a console that dies mid-action leaves the executor to finish. The
console imports no unit code: `tests/unit/web/test_app.py` pins the exact module set a fresh
`import tbot_console.web.app` loads, and `tests/unit/test_import_weight.py` pins that neither the
console nor `tbot` loads `ib_async` or `numpy`. What a page shows is computed by the unit's own
service, restarted with its unit (`uv run tbot restart <id>:api` restarts the service alone). The
static frontend and the unit UI files are read from disk per request and served with
`Cache-Control: no-cache`, so a page reload picks up JS/CSS changes without restarting anything.

**Unit UI files come from the unit's repo — repo layout = URL layout.** `<repo>/js/units/<ui>/<path>`
is served at `/js/units/<ui>/<path>` by `unit_asset(ui, path)`, a route registered before the
static mount. The repo is `Registry.ui_base(ui)` (the first repo that claimed that UI name); an
unknown UI, an empty or dot-prefixed path segment, or a path that resolves outside the resolved
UI directory answers 404. The UI directory itself is resolved first, so a repo whose `js` is a
symlink still works. Unit code imports the shared helpers relatively (`../../dom.js`,
`../../util.js`, `../../api.js`, `../../tooltip.js`) and its types from `../contract`: the browser
resolves those URLs to the platform's own static files, and in the unit repo `tbot ui-links`
makes the same relative paths resolve for node, tsc and biome.

**The proxy is a pass-through resolved per request.** The target of `/api/<id>/…` is
`http://127.0.0.1:<api.port>/api/<id>/…` from that unit's manifest, so adding or removing a unit
needs no code. Path, query, body, status and `content-type` pass through as bytes (a large JSON
answer is never re-parsed in the console), plus `cache-control`, `etag` and `last-modified`. It
is not an endpoint whitelist: the target hosts are loopback-only and come from the manifests,
and a whitelist in the shell would make every new endpoint a console release. Path segments are
re-quoted, `.`/`..`/empty segments answer 404, an unknown id or a unit without `api` answers 404,
and a method the service does not serve answers whatever the service says. A service that is
down answers 503 with a hint naming the home page button and the command
(`uv run tbot start <id>:<proc>`) — `ConnectError` and `RemoteProtocolError` (the latter is what a
service killed mid-request looks like) — a hung one 504 after 60 s.

**The frontend is a unit shell.** It learns its units from `GET /api/control/units`, lists them
in the sidebar grouped «Стратегии» / «Мониторы» / «Консоль» with status dots, and loads each
unit's UI `/js/units/<ui>/index.js` on demand (`createUnitUi(sdk)`, contract in
`units/contract.d.ts`). Routes are `#/` (home dashboard), `#/<unit>`, `#/<unit>/<page>/<arg…>`,
plus the shell page `#/<unit>/processes` for every unit. An old bookmark `#/<page>[/…]` redirects
(`replaceState`) to `#/<unit>/<page>/…` of the first unit, in registry order, whose manifest
lists `<page>` in `legacy_pages`; a unit id always wins over a legacy page. An unknown unit shows
a panel with a link home, and re-routes by itself once the registry knows the segment
(`knownSegment`), so a bookmark opened while the first registry load has failed, or while the
owning manifest is missing or refused, recovers once the registry lists the owning unit (a unit
whose service is down stays listed, so its bookmarks redirect at once); while the registry has
never loaded, that panel names the registry error instead. A registry error on first load (404
included) shows an empty home page with the error note; later errors keep the last good data.
The platform ships no unit UI; `tests/fixtures/unit_repo/js/units/demo/` is the template.

Operator usage — pages, buttons, `TBOT_WEB_TOKEN`/`TBOT_WEB_HOST`/`TBOT_WEB_PORT`/
`TBOT_WEB_ALLOWED_HOSTS`, connecting a repo, the security model — is documented in
[docs/CONSOLE.md](../../../docs/CONSOLE.md); this file is the architecture map.

Security model in code: the shell refuses a request whose `Host` is not a loopback name or
address (`control.service.is_loopback_host`: `localhost`, any `127.x`, `[::1]`), `TBOT_WEB_HOST` or
a name in `TBOT_WEB_ALLOWED_HOSTS` (400, DNS rebinding), and any `/api/*` request other than
GET/HEAD/OPTIONS without the header `X-Tbot-Console: 1` (403 — a custom header forces a CORS
preflight, so a foreign page cannot POST a restart); it does the bearer-token check
(`secrets.compare_digest` against `TBOT_WEB_TOKEN`) on `/api/*` only, before anything is
forwarded, and never forwards the `Authorization` header; the static frontend and the unit UI
files are served unauthenticated; security headers (CSP plus `control.service.SECURITY_HEADERS`:
nosniff, DENY, no-referrer) go on every response, proxied ones included; `app.main()` refuses a
non-loopback bind without a token. Under `tbot` the console takes no address from an env file
(`procs.FILE_DROPPED_KEYS`), so it listens on loopback; a hand start (`uv run tbot-web`) reads
`.env` whole. Unit services have no token of their own and `control.service.serve()` refuses any
non-loopback bind.

`__init__.py` is empty — consumers import submodules directly. FastAPI and uvicorn are core
dependencies of the platform.

## Public API

```python
from tbot_console.web.app import create_app, main, unit_asset
from tbot_console.web import control_api, proxy
```

- `app:main` — the console. Under `tbot` the built-in manifest runs it as
  `python -m tbot_console.web.app` with `TBOT_PORT` = `console_port` from home's
  `config/workspace.json` (default 8420); a hand start listens on `TBOT_PORT`, else
  `TBOT_WEB_PORT`, else 8420, and on `TBOT_WEB_HOST` (default `127.0.0.1`).
- `unit_asset(ui, path)` → the file to serve for `/js/units/<ui>/<path>`, or `None` (404).
- `control_api.router` — the `/api/control` router; `control_api.units_payload()` — the registry
  payload the home page polls.
- `proxy.forward()` — the per-request pass-through; `proxy.OFFLINE_ERRORS`, `offline_hint(unit)`.

## Modules

| File | Responsibility |
|---|---|
| `app.py` | the console: `create_app()` + `main()` (`control.procs.load_env_file()`; port and host as above), `host_allowed(name)`, the security middleware (see «Security model in code»; `SECURITY_HEADERS` = CSP + `control.service.SECURITY_HEADERS`), `control_api.router`, the proxy route `/api/{service}/{path:path}` (GET/POST/PUT/PATCH/DELETE) → `proxy.forward()`, `unit_asset()` + the route `/js/units/{ui}/{path:path}`, `static/` at `/` (`html=True`); no Swagger of its own |
| `proxy.py` | `forward()` looks the unit up in the registry per request (`UnitManifest.api_url()`), `upstream_path(unit_id, path)` re-quotes the path, `offline_hint(unit)`, `OFFLINE_ERRORS` (shared with `control_api`), `shared_client()` (the console's one `httpx.AsyncClient`); behaviour — see «The proxy is a pass-through» above |
| `control_api.py` | `/api/control`: `GET /units` → `{v, control, units: [UnitEntry], broken}` — every unit's manifest fields (`glyph` defaults to the id's first two letters, uppercased), `ProcessView`s (`control.state.process_view` with the unit's repo, in a thread, each with its Russian `label`) and its `/api/<id>/status` fetched concurrently (`STATUS_TIMEOUT`: 3.5 s read, 0.5 s connect; `api.reachable`/`hint` when down, `status_error` in plain Russian when it answers badly — for a non-200 answer with the service's JSON `detail` appended: «служба ответила кодом 500: …»), cached 3 s; `POST /units/{id}/processes/{proc}/{action}` (start/stop/restart/adopt) → 202 `{op_id}` after spawning the detached `_op` executor (console python, cwd and `TBOT_ROOT` = home) with stdout into the unit repo's `run/ops/<op_id>.log`, 409 when the action is not in the process's `can` (or on Windows), 404 for an unknown unit/process/action; `GET /units/{id}/processes/{proc}/output?lines=` → `{path, lines, size}` tail of the process output; `GET /ops/{op_id}` → `{op, lines}` (op record + op-log tail, searched in every repo's `run/`) |
| `static/` | frontend, see below |

### Routes

| Route | Backed by |
|---|---|
| `GET /api/control/units`, `POST /api/control/units/{id}/processes/{proc}/{action}`, `GET /api/control/units/{id}/processes/{proc}/output`, `GET /api/control/ops/{op_id}` | `control_api.py` |
| `/api/{service}/{path}` | `proxy.forward()` → the unit's service |
| `GET /js/units/{ui}/{path}` | `unit_asset()` → `<repo>/js/units/<ui>/<path>` |
| `/` and everything else | `static/` |

### Frontend (`static/`)

No bundler, no runtime deps; ES modules plus one vendored non-module script,
`vendor/lightweight-charts.standalone.production.js`, pinned by an SRI hash in `index.html` —
bumping the vendor file without updating the hash bricks every unit chart. It is loaded for unit
UIs that draw charts (the ambient `LightweightCharts` global); they turn the library's own logo
off (`attributionLogo: false`), so the sidebar carries the TradingView attribution link
(`a.attribution`) instead; keep it when touching the shell.

| File | Responsibility |
|---|---|
| `index.html` | generic shell: title «Торговая консоль», brand link to `#/`, `nav#unit-nav` (units grouped by kind with status dots, the active unit's pages plus «Процессы»), `header#topbar-slot` (the active unit's topbar), `#service-banner`, `main#view`, script loading, the sidebar footer link `a.attribution` (TradingView Lightweight Charts™) |
| `css/app.css` | the shell's styles and the shared classes unit UIs build on (`:root` tokens, cards, tables, `.pos`/`.neg`/`.muted`/`.faint`); a unit's own rules live in its `stylesheet` |
| `js/app.js` | the shell: registry polling, the router (`shell/route.js`), one `createUnitUi(sdk)` instance per unit per page lifetime (recreated when `ui` changes, dropped when the unit disappears), sidebar and dashboard/processes pages. Lifecycle: `activate(host)` on entering a unit; leaving runs the route cleanup, then the activation cleanup; every re-render aborts the route signal before calling the cleanup; everything runs in try/catch with a per-unit error panel, so a broken unit UI never blanks the others. Banner: `host.banner` first, otherwise `api.reachable === false` plus the unit's hint. Token gate on any 401 via `api.onUnauthorized`; a toast reports action results; the «Главная» link is always in the sidebar |
| `js/api.js` | `client(base)` → `{get(path, query, signal), send(method, path, body, signal)}`: drops null/undefined/"" query values, sends `X-Tbot-Console: 1` on every request and `Content-Type: application/json` only with a body; `onUnauthorized(hook)`; `request()`/`queryString()` exported; `store {get, set}` — `localStorage` that never throws (`null` removes the key); `getToken`/`setToken` on top of it under `console:token`. There is no per-endpoint `api` object — each unit UI builds its own on `sdk.api` |
| `js/dom.js` | `h(tag, props, ...children)` — DOM builder over `createElement`/`setAttribute`/text nodes, no HTML parsing (the shell adds no `innerHTML` sinks); `replaceChildrenIfChanged(parent, nodes)`; `showConfirm(dialog, cancel, apply, signal)` → `Promise<boolean>`, the modal-confirm lifecycle |
| `js/util.js` | DOM/format helpers for unit UIs: `el`, `esc`, `fmtTime`/`fmtTimeFull`, `fmtDuration`, `fmtNum`, `fmtPrice` (no thousands grouping — a dense row of prices read as twice the numbers), `fmtUsd`, `fmtCount` (≥1e6 → «N.N млн», ≥1e3 → «N тыс», else rounded; «—» for non-numbers), `pnlClass`, `debounce`, `downloadCsv`; unit-tested |
| `js/tooltip.js` | one delegated tooltip for the whole console: any element with `data-tip` gets a styled bubble whose text is selectable (it survives a short grace period so the cursor can reach it); `initTooltips()` is called once from `app.js`, `hide()` on every navigation, `tipPosition()` (pure, unit-tested) keeps the bubble on screen. Native `title` is reserved for one-word hints — it delays ~1s, self-dismisses and does not wrap multi-sentence Russian |
| `js/shell/route.js` | pure hash grammar over the registry units: `parseHash(hash, units)` → home / unit / redirect / unknown (unit ids win over legacy pages; a legacy page redirects to the first unit in registry order whose `legacy_pages` lists it — no unit id is written in the shell), `knownSegment(units, segment)`, `unitHref`, `resolvePage` (unknown page → first page, or `processes` for a unit without pages); valid and reserved ids are enforced only by `control.manifest`/`registry` |
| `js/shell/registry.js` | polls `/api/control/units` every 10 s, every 1 s while a process of a unit is busy or its op unfinished, `hurry()` = 5 s of fast polling after an action; the payload is used as sent (`units`/`broken` default to `[]`); an error on first load gives an empty registry plus the error, later errors keep the last good data; a 401 pauses polling |
| `js/shell/loader.js` | `import("../units/<ui>/index.js")` (served from the unit's repo) after a regex check on `ui`; one import per ui, the optional `stylesheet` export attached once as a `<link>` before the first render; a failed load is not cached, so the next visit retries |
| `js/shell/dashboard.js` | home page «Стратегии и мониторы»: a card per unit grouped «Стратегии» / «Мониторы» / «Консоль»; process rows with the server's state label, «(принят консолью)» added when adopted, pid and uptime; buttons only from `can` (whitelisted actions, disabled while busy); the unit's status headline, all metrics and at most 3 problems; `status_error` exactly as the console words it; broken manifests and roots as red cards in a group that persists across polls and is rebuilt only when the broken list changes; a failed registry poll shows an error-banner note; a card is rebuilt only when its data signature changes. `createActionRunner`: the `<dialog>` confirmation when `confirm` is set (cancel sends nothing), then the POST, the remembered `op_id`, a toast and `hurry()` |
| `js/shell/processes.js` | `#/<unit>/processes`: the process table with the same actions, and per process an on-demand output tail (`…/output?lines=200`: path, size, lines) and the last op record (`/api/control/ops/{op_id}`); takes the route's `AbortSignal` (`signal` option) and returns `{root, update}`, with no cleanup of its own |
| `js/units/contract.d.ts` | the shell ↔ unit UI contract: `createUnitUi(sdk) → {pages, activate(host), render(view, route)}`; sdk `{id, api, href, navigate, rerender, storage}` (`storage` is `api.js`'s `store` under a `<unit>.` key prefix); route `{page, arg, signal}`; host `{topbar, banner(text)}`; the registry shapes (`UnitEntry` with `legacy_pages`, `ProcessView` with `label`; `glyph` arrives filled) and `UnitStatus`; `Client` is `{get, send}` returning `Promise<Json>` (`Json = ReturnType<typeof JSON.parse>`); `.d.ts` files may use `any` through a biome override in `biome.json` |
| `js/globals.d.ts` | ambient `LightweightCharts` global for `tsc --checkJs` |
| `js/require-tests.mjs` | a `node --test` reporter that sets a failing exit code and prints "no tests ran" when no test passed or failed — a glob that matches nothing (or a symlinked folder node skips) otherwise exits 0 |
| `js/**/*.test.js` | `node:test` suites for `api.js`, `util.js`, `tooltip.js` and `shell/{route,registry,loader,dashboard}.js` on fictional units (the dashboard test runs on a fake DOM whose `innerHTML` setter throws) |

JS quality gates live at the repo root: `package.json` (`npm test` = `node --test` with the
`require-tests.mjs` reporter, `npm run test:coverage` with a 90% branch-coverage gate on
`util.js`, `npm run typecheck` = `tsc -p jsconfig.json`, `npm run lint` = biome
`--error-on-warnings`), the `innerHTML` baseline tripwire (`scripts/check_innerhtml_baseline.py`,
an exact count), pre-commit hooks, CI jobs `web-unit` (`.github/workflows/tests.yml`) and
`web-smoke` (`.github/workflows/web-smoke.yml`, `playwright.config.js`: `uv run tbot-web` with
`TBOT_ROOT=tests/fixtures/unit_repo` on a spare port; the sidebar lists the demo unit, `#/demo`
renders the fixture UI with its stylesheet applied, and no page error appears except the expected
503 of the demo service that is not running). Python tests: `tests/unit/web/` (`test_app` — the
shell, its guards, its proxy and its import isolation; `test_control_api`); the asset route and
operations on a unit from another repo are tested in `tests/unit/control/test_workspace.py`.

## Extension points

- **A new unit UI** lives in the unit repo, never here: `js/units/<ui>/index.js` exporting
  `createUnitUi(sdk)` (contract `js/units/contract.d.ts`) and optionally
  `export const stylesheet = new URL("./style.css", import.meta.url).href`; the manifest names it
  in `ui`. No console change or restart.
- **A unit stylesheet** names the unit's own class prefix in every selector, must not redefine
  `:root` tokens, and must not add `innerHTML` sinks — build with `h()`/`el()` and `esc()`.
- **A new shared helper for unit UIs:** add the file to `static/js/` and its path to
  `control.cli.UI_LINKS`, in a minor release; renaming, removing or changing the signature of an
  exported helper is a major one (unit UIs import these files by path).
- **A new control endpoint:** on `control_api.router`; the proxy route never shadows it because
  `/api/control` is registered first and `control` is a reserved unit id.
- **A new explanatory hint:** put the text in `data-tip="…"` on the element (escaped like any
  other interpolation) — `tooltip.js` picks it up by delegation.
- Never import a unit's service into `app.py`: the isolation test in `tests/unit/web/test_app.py`
  fails.

## What to reuse

- `api.js` `client(base)` for every call a unit UI makes (`sdk.api` is one, rooted at
  `/api/<id>`); `dom.js` `h()` for DOM without HTML parsing; `util.js` formatters and `esc()`;
  `tooltip.js` through `data-tip`.
- `proxy.OFFLINE_ERRORS` and `offline_hint()` wherever the console has to tell "service down"
  from "service answered badly".
- `control.service` (in [control/](../control/README.md)) for a unit's own service: the same
  loopback guard and security headers as the console.
