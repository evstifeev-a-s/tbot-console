# Control — unit registry and process control for the console

One place to run and watch every strategy and monitor, whichever repository it lives in. A
**unit** is one strategy or monitor instance; it has one or more **processes** (for example a
service `api` and a trading `bot`). A unit is a JSON manifest `config/units/<id>.json` in its
**unit repo**; the platform ships no manifest except the built-in system unit `console`
(`registry.console_manifest(port)`). The operator-facing Russian UI says «стратегии и мониторы»
and «процессы», never «юнит».

**Home and roots.** The registry reads the **home** folder (`TBOT_ROOT`, else the cwd — the unit
repo `tbot` runs in) and the folders listed in home's git-ignored `config/workspace.json`
(`registry.Workspace`: `{"roots": ["../other-repo", …], "console_port": 8420}`, extra keys
refused; a relative root is resolved against home). There is no parent-folder scanning. Claim
order: the built-in console → home → roots in file order. The first claimant keeps a unit id or
legacy page (one namespace), a listen port and a UI name; a later claimant is listed broken with
the reason («имя «x» уже занято: …», «порт … уже занят», «интерфейс «x» уже отдаёт другая
папка»), so a repo's own `console.json` is always refused. Inside one repo a port clash still
refuses every unit involved. A root is refused as a whole (one broken entry naming the
`workspace.json` line) when it equals home or an earlier root, has no `config/units`, has no
`.venv` python, has no `tbot-console` in that `.venv`, or holds one of another major version
(`root_problem(base)`; the last three read «… выполните там `uv sync`»). A home `.venv` with the
same problem refuses home's units; a malformed `workspace.json` is one broken entry and home
still loads. Broken `file` names are relative to home. `load_registry()` memoizes for 3 s per
resolved home (`forget()` drops the memo — tests) and the console polls every 10 s, so a root
added to or removed from `workspace.json` shows up within ~15 s with no restart.

**Each unit runs from its repo.** `Registry.base(unit)` is the repo that claimed it: the child's
cwd, its python (`paths.python`: `.venv/bin/python`, then `.venv/Scripts/python.exe`, never
`resolve()`d so the venv's site-packages load; home falls back to the console's own interpreter),
its env files (relative to that repo, no absolute path, no `..` segment — the manifest refuses
the rest), `TBOT_ROOT` = that repo, its state and op files under that repo's `run/`, its output
under that repo's `logs/`. The env drop list is the union over that repo's manifests. Console
scripts (for `adopt` and the foreign-process search) are read from that repo's `.venv`
(`paths.site_dirs`). Executors (`tbot _op`) run with the console's python, cwd = home,
`TBOT_ROOT` = home.

Cold path. The only control module a trading process imports is `logs.py` (stdlib logging
setup), through `control.logs.configure_logging()`. Process control is POSIX-only (macOS, Linux);
on Windows the package imports and type-checks, every process view reports `can: []` with a
problem text, and every action refuses — there the console and unit services are started by hand.
The console's HTTP side of this package is `web/control_api.py` (see
[web/README.md](../web/README.md)); the operator guide is
[docs/CONSOLE.md](../../../docs/CONSOLE.md).

## Public API

```python
from tbot_console.control import ops, state
from tbot_console.control.registry import load_registry

registry = load_registry()                     # home = TBOT_ROOT or cwd, plus workspace roots
unit = registry.get("alpha")
base = registry.base(unit)                     # the repo that claimed the unit
view = state.process_view(base, unit, "bot")   # {"state": "running", "label": "работает", "pid": ..., "can": [...], ...}
ops.Operation(base, unit, "bot", "restart", ops.new_op_id()).execute()
```

CLI (`[project.scripts] tbot`, run as `uv run tbot …` from a unit repo): `ps`, `start|stop|restart
<unit>[:<proc>]` (a whole unit = every process in manifest order, stop in reverse), `check
<unit>[:<proc>]`, `adopt <unit>:<proc> [--pid N]`, `up` (every `autostart` process that is not
running; trading bots never autostart; ends with `консоль: http://127.0.0.1:<port>` when the
console's start did not fail; refuses with exit 2 when home has no `config/units`), `output
<unit>:<proc> [-n N]`, `json [target]`, `ui-links`, and the hidden `_op <action> <unit>:<proc>
--op-id ID` the console spawns. Broken entries print as `ОШИБКА в <file>: <reason>`. Exit codes:
0 done (a start or adopt of a process the console already runs is a no-op that keeps the last op
record), 1 failed (an unreadable manifest refusing a start, restart or adopt included), 2 bad
arguments, a refused `ui-links` or `tbot check` of a `check: true` process while a manifest is
unreadable, 3 another operation holds the process.

`tbot ui-links` (run in a unit repo; home = cwd) makes the platform's UI helpers resolvable for
node, tsc and biome at the same relative paths the browser uses: for each of `UI_LINKS`
(`js/api.js`, `js/dom.js`, `js/util.js`, `js/tooltip.js`, `js/globals.d.ts`,
`js/units/contract.d.ts`, `js/require-tests.mjs`) it replaces `<home>/<name>` with a symlink to
the installed package's `web/static/<name>` (located through `importlib.resources`), copies the
file instead when `symlink_to` raises `OSError` (Windows without developer mode), and refuses
before touching anything when git tracks a file of that name. A unit repo git-ignores exactly
those seven paths.

Guarantees an operation gives, whatever starts it (console button or CLI):

- one operation per process at a time (`run/<unit>.<proc>.lock`, `flock`); a second one is
  refused while the first holds it;
- the child runs in its own session (`Popen(start_new_session=True)`, never an asyncio
  subprocess — its transport SIGKILLs the child on close), so restarting the console or `tbot`
  never touches it; the console runs each action as a detached `tbot _op` executor whose stdout
  is the op log `run/ops/<op_id>.log` of the unit's repo;
- identity is pid plus psutil create-time plus a cwd in the unit's repo (`procs.owned()`), so a
  reused pid is never signalled and a copied folder's `run/` state never signals the original;
- `check: true` processes run `<repo python> -m <module> --check` first (one JSON object: `ok`,
  `summary`, `checks`, `identity` with lock, descriptor and `lock_holder`); a failed check
  refuses a start, and on restart it runs before the stop, so a bad config leaves a running bot
  alone; a lock already held by another pid means "adopt it", never a second bot;
- stop is SIGTERM and a wait of `stop_timeout_s`; a trading process (a `places_orders` unit's
  `check: true` process) is never killed — the op fails with `stop_timeout` and the process shows
  `stuck`; every other process, the trading unit's `api` service included, is SIGKILLed after the
  timeout;
- readiness after a spawn: `http` (the health path answers 200), `descriptor` (the bot's runtime
  descriptor names the spawned pid) or `alive` (still running after 3 s); a child that dies while
  waiting fails the op with its last 20 output lines; a live child past `timeout_s` ends the op
  as done, and the unconfirmed readiness is only its final message;
- output goes to `logs/units/<unit>.<proc>.out` (an adopted process records the file behind its
  stdout instead), rotated at 20 MB keeping three older files;
- the child environment is an allowlist of the OS variables (PATH, HOME, USER, LOGNAME, SHELL,
  locale, TMPDIR, TZ), then `env_files` in order minus the dropped keys (files carry credentials,
  the config file carries settings; the dropped names are echoed in the op log), then the manifest
  `env` (only wiring keys are allowed there), then `TBOT_PORT` (the process's listen port,
  `ready.port` or `api.port`, when it has one), `TBOT_UNIT`, `TBOT_ROOT`, `PYTHONUNBUFFERED`,
  `PYTHONFAULTHANDLER`; the console's own environment is not inherited. The dropped keys
  (`procs.env_file_policy()`) are the union of `env_files_ignore` (`prefixes`, `keys`) over every
  non-hidden `config/units/*.json` file of the unit's repo, read raw so a manifest the registry
  refuses still counts, plus the platform constant `FILE_DROPPED_KEYS` (`IBKR_ALLOW_LIVE_TRADING`,
  `IBKR_WRITABLE` — the switches of the platform's own IBKR connector — and
  `TBOT_WEB_HOST`/`TBOT_WEB_ALLOWED_HOSTS`, so an env file cannot open the console to the
  network; `TBOT_WEB_TOKEN` is a credential and passes). A unit with `env_files_only` (`prefixes`,
  `keys`) then keeps only the admitted keys of what is left; the rest is skipped silently, never
  listed as dropped, so the drop list is the same with or without the filter (the console admits
  only `TBOT_WEB_TOKEN`). `procs.file_env(base, unit, *files)` applies both steps and is shared by
  `child_env()` and `load_env_file(unit)`. The policy fails closed: a manifest file that is not a
  JSON object or has a bad `env_files_ignore` raises `procs.EnvPolicyError` naming the file; until
  it is fixed every start and restart, and the adopt of a `check` process, fails with that message
  before anything is stopped (a failed op: exit 1, `error_code: env_policy`), `tbot check` of a
  `check: true` process prints it and exits 2, and stop never reads the policy. A supervised
  process (`TBOT_UNIT` set, `procs.supervised()`) never rereads `.env` itself:
  `procs.load_env_file(unit=None)`, which the console and `service.serve()` (with its `unit`) call
  and any unit process may call, loads `<paths.root()>/.env` (`override=False`; never a `.env`
  found by walking up parent folders) only for a hand start, so the dropped keys stay dropped.
  Without a unit it loads the whole file (the console); with one it validates
  `config/units/<unit>.json` (`registry.parse_manifest`: its id must match the file name) and
  loads only what `file_env()` passes for that unit; a missing or invalid manifest, or an
  `EnvPolicyError`, is a `SystemExit` «…; .env не загружен» before anything is loaded;
- `keep_awake` prefixes `/usr/bin/caffeinate -i` (`idle`) or `-is` (`system`) on macOS;
  caffeinate execs the command in the same pid and asserts on its behalf, so the pid is still the
  bot's;
- `adopt` never signals: it takes the lock holder (`check` processes), the port listener (api
  processes) or `--pid`, verifies that its command line runs `-m <module>` or one of the module's
  console scripts in the unit repo's `.venv`, that its cwd is the unit's repo and that the bot
  descriptor names the same pid, then writes the state file with `adopted: true` and the file
  behind the process's stdout as its output.

Process states (`state.process_view`, computed per call, never cached): `running`,
`starting`/`stopping`/`checking` (an operation holds the lock), `stopped`, `exited` (died without
a recorded stop), `stuck` (stop timed out), `interrupted` (the executor died mid-operation),
`foreign` (the port or the bot lock is held by a process the registry did not start — `adopt`
takes it over without a restart). Every view carries `label`, the state in Russian
(`STATE_NAMES`), which `tbot ps` and the console print as is. `can`: stopped/exited/interrupted →
`start`; running → `stop`, `restart`; stuck → `stop`; foreign → `adopt`. `confirm` is the
process's manifest `confirm` when set, else the neutral `TRADING_CONFIRM` for trading processes
(`places_orders` + `check`) and `CONSOLE_CONFIRM` for the system unit; the console asks before any
action on a process that has one.

Files under a repo's `run/`: the state file `<unit>.<proc>.json` (`pid`, `create_time`,
`started_at`, `output`, `adopted`, `identity`), the last operation `<unit>.<proc>.op.json`
(`op_id`, `action`, `phase`, `started_at`, `error`, `error_code`, `finished_at`), the transition
lock `<unit>.<proc>.lock` and the op logs `ops/<op_id>.log`.

## Modules

| File | Responsibility |
|---|---|
| `manifest.py` | pydantic models of `config/units/<id>.json` (frozen, `extra="forbid"`): `UNIT_ID_PATTERN` and `RESERVED_IDS` (the console's own path segments: `api`, `js`, `css`, `vendor`, `static`, `processes`, …); process `env` keys are `TBOT_*` or wiring (`WIRING_KEY_RE`: `<PREFIX>_…_HOST`, `_ALLOWED_HOSTS`, `_CONFIG_PATH`, `_CONFIG_DIR`, `_DATA_DIR`; no ports — tbot passes `TBOT_PORT`) and never secret-looking (`SECRET_KEY_RE`: ends in `TOKEN`/`SECRET`/`PASSWORD`/`PASSWD`/`KEY`, `TBOT_*` included); `env_files` relative to the repo with no `..`; `EnvFilesIgnore` (`prefixes` like `XY_`, `keys`; never `TBOT_*`); `EnvFilesOnly` (`prefixes`, `keys`, at least one of them; `TBOT_*` allowed; `admits(key)`) for the optional `env_files_only`; `legacy_pages` (old top-level page names the console redirects to this unit; never its own id); a process `confirm` text (1–400 chars); in a `places_orders` unit every process except `api.process` has `check: true`, a trading process can never autostart, and every non-`TBOT_` `env` key has its first prefix in `env_files_ignore.prefixes`, so env files cannot supply the unit's other settings under that prefix; ports 1024–65535 except Postgres 5432/5433; the api process's `ready.port`, if set, equals `api.port`; `trades(proc)` (`places_orders` and `check`), `api_url()` (always `http://127.0.0.1:<api.port>`), `output_path()`, `ready_endpoint()`, `listen_port()` |
| `registry.py` | `load_registry(home)` → `Registry(units, broken, bases)` with the claim rules above, memoized 3 s (`MEMO_TTL_S`, `forget()`); `Registry.get(id)`, `base(unit)`, `ui_base(ui)`; `console_manifest(port)` (the built-in `console` unit, process `web`, `ready.port` = `console_port`); `Workspace` + `workspace(home)`; `root_problem(base)` (python present, `tbot-console` installed in the root's `.venv`, same major via `importlib.metadata.distributions(name=…, path=site_dirs)`); `parse_manifest(path)` (one file → `UnitManifest` or `Broken`; the file name must equal the id); `load_root(base)` (one repo, clashing ports refuse every unit involved); `claim(home, roots, console)` (the cross-repo claim; also what `testing.manifest_problems` runs) |
| `paths.py` | home (`TBOT_ROOT` or cwd), `units_dir`, `manifest_files()` (sorted `config/units/[!.]*.json`: hidden files such as `._x.json` / `.#x.json` are skipped), `workspace_file`, `ui_dir(base, ui)` = `<base>/js/units/<ui>`, `python(base)`, `site_dirs(base)`, `run/` state, op and lock files, op logs `run/ops/<op_id>.log` |
| `procs.py` | `child_env(base, unit, proc)` (`file_env()`: `env_file_policy()` + `FILE_DROPPED_KEYS`, then `env_files_only`; `EnvPolicyError`, `TBOT_PORT` from `unit.listen_port()`), `supervised()`/`load_env_file(unit=None)`, `interpreter(base)` (the repo's python or `EnvPolicyError` «… выполните там `uv sync`»), `build_argv(spec, python)` (caffeinate), detached spawn, output rotation, pid identity (`is_alive`: psutil, a zombie counts as gone; `owned(base, pid, started)`: alive and its cwd is `base` or unreadable), SIGTERM/SIGKILL, port and lock holders, `find_instances()`, `console_scripts(module, base)` and `runs_module(argv, module, base=…)` (`-m <module>` or a console script from the repo's `.venv` whose entry point lives in `<module>` or `<module>.__main__`) |
| `state.py` | transition `flock`, `process_view` (state, `label`, `can`, confirm text, problem), `STATE_NAMES` in Russian, `instances()` (the foreign-process search: the port holder, or `procs.find_instances()` for a process without a port), `output_file()`; state and op files are read and written with `files.py` |
| `ops.py` | the executor: `Operation(base, unit, proc, action, op_id).execute()` for start / stop / restart / adopt with phases `checking → stopping → starting → waiting_ready → done`/`failed`; `run_check()` (builds the `--check` argv with the repo's python); `tail_lines()` |
| `cli.py` | `tbot`, incl. `ui-links` (`UI_LINKS`) |
| `service.py` | `unit_app(unit, title)`: FastAPI with docs, openapi and oauth2-redirect under `/api/<unit>/` and no `/redoc`; a Host allowlist (loopback only, `is_loopback_host()`) and security-header middleware (`SECURITY_HEADERS`); a JSON 500 handler (HTTP errors keep FastAPI's own handler and their own headers); async `GET /api/<unit>/health` `{status, unit, pid, started_at}`. `setup_logging()` = `logs.configure_logging()` + httpx at WARNING. Loopback-only `serve(factory, name=…, host_env=…, port_env=…, default_port=…, unit=None)`: refuses a non-loopback host from the process environment before reading `.env`, then `procs.load_env_file(unit)` (`.env` only for a hand start, filtered for that unit) and checks the host again, port from `TBOT_PORT`, else `port_env`, else `default_port` |
| `status.py` | UnitStatus builders shared by every unit service and `tbot ps`: `TONE_RANK` idle < ok < warn < bad, `metric(label, value, hint=None, tone=None)`, `problem(text, tone)`, `status_payload(tone, headline, metrics, problems, now=None)` (the `v: 1` envelope, tone raised to the worst problem), `duration(seconds)` → «N с» / «N мин» / «H ч M мин» or «H ч» / «N дн» (callers add «назад») |
| `logs.py` | `LOG_FORMAT` and `configure_logging(log_path=None)`: `basicConfig` at INFO, plus an optional rotating file (20 000 000 bytes × 5) attached once per resolved path, its folder created; used by unit processes and `service.setup_logging()` |
| `files.py` | `read_object(path)` → dict or `None` (missing, broken or not an object all give `None`), `write_json_atomic(path, data)` (mkdir, indent 2, sorted keys, trailing newline, `<name>.tmp` then replace), `flock_held(path)` → `True`/`False`/`None` (non-blocking `LOCK_SH` probe; `None` when the file is missing or on Windows) |

Tests: `tests/unit/control/` on fictional units only (`fixtures/units/{alpha,beta}.json` through
`helpers.fixture_manifest()`): manifest rules, full op cycles against throwaway child processes,
`test_workspace.py` (roots, claim order, root problems, `console_port`, hot-add, the asset route,
`ui-links`, the node zero-test guard), `test_env_golden.py` (the environment of the console and
of a fictional unit against `fixtures/env_golden.json`: a change there is a change to what live
processes receive), `test_env_least_privilege.py` (`load_env_file(unit)` on a hand start),
`test_cli.py`, `test_service.py` (the unit-service contract on a demo app), `test_status.py`,
`test_logs.py`, `test_files.py`; plus `tests/unit/web/test_control_api.py`.

## Extension points

- **New strategy or monitor:** a unit repo, never a change here. It holds the manifest
  `config/units/<id>.json`; a service module with `GET /api/<id>/health` (cheap, never touches
  data) and `GET /api/<id>/status` (`{"v":1,"tone","headline","metrics","problems","updated_at"}`,
  plain Russian, server-computed, under 200 ms) built on `service.unit_app`/`service.serve`, the
  `/status` body from `control.status`; an optional UI `js/units/<ui>/index.js` exporting
  `createUnitUi(sdk)` (contract in `web/static/js/units/contract.d.ts`; a unit without `ui` gets
  only the processes page). The console picks it up from home or a `workspace.json` root. The
  fixture `tests/fixtures/unit_repo/` is the template; the operator steps are in
  [docs/CONSOLE.md](../../../docs/CONSOLE.md).
- **A second instance of an existing strategy:** another manifest with its own `id`, `api.port`
  and config-path wiring key in `env` (`<PREFIX>_CONFIG_PATH`); tbot passes `TBOT_UNIT` and
  `TBOT_PORT`, so the same service and UI serve any number of instances.
- **A trading process:** `places_orders: true`, `check: true` (the module answers `--check`),
  `autostart: false`, readiness `descriptor`, a generous `stop_timeout_s`.
- **A new manifest env key:** only wiring (an address or a path) that matches `WIRING_KEY_RE`, or
  `TBOT_*`; ports come from the manifest through `TBOT_PORT`, secrets stay in env files, trading
  settings in the strategy config. A trading unit also lists the key's prefix in
  `env_files_ignore.prefixes`.
- **Settings that must never come from an env file:** the unit's `env_files_ignore` (a prefix or
  exact keys); it applies to every process of every unit of that repo, because the drop list is
  one union per repo.
- **A unit that needs only a few keys from `.env`:** `env_files_only` (`prefixes`, `keys`); it
  applies to that unit's processes only, under `tbot` and for a hand start that calls
  `procs.load_env_file("<id>")` (directly or through `service.serve(…, unit="<id>")`).
- **A new workspace setting:** a field on `registry.Workspace` (it forbids unknown keys, so an
  old console refuses a file written for a newer one instead of ignoring it).

## What to reuse

- `service.unit_app(unit, title)` + `service.serve(factory, …)` for any local HTTP service;
  `control.status` for the UnitStatus body; `control.files` for JSON state files;
  `control.logs.configure_logging()` for a process's log setup.
- `tbot_console.testing` in a unit repo's contract tests: `manifest_problems(root)` (every
  manifest valid and claimable next to the console), `manifest_modules(root)`,
  `startup_imports(module, src)` + `imported_modules(code, cwd)` (what a process loads at
  startup, run in a fresh interpreter), `loopback_client(app)` (a `TestClient` whose Host passes
  the loopback allowlist).
- `procs.runs_module(argv, module, base=…)` to recognise a process by `-m <module>` or its
  console script.
- `ops.tail_lines(path, n)` for the end of a large log without reading it whole.
- `state.process_view()` is the single source of what a process is doing; the console, `tbot ps`
  and `tbot up` all read it rather than probing on their own.
