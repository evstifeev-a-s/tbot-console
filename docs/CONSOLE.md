# Console and `tbot` — procedures and trust model

Steps an agent follows when it runs the console and `tbot` for the operator, and the trust model
of connected repos. How things work lives in the module READMEs:

- process control, `tbot` commands and exit codes, manifest rules, the child environment, the
  live-trading switch: [control/README.md](../src/tbot_console/control/README.md);
- the console's routes, proxy, security model and frontend:
  [web/README.md](../src/tbot_console/web/README.md);
- the IBKR clients and the account-side setup:
  [connectors/ibkr/README.md](../src/tbot_console/connectors/ibkr/README.md);
- why things are decided the way they are: [DECISIONS.md](DECISIONS.md).

**Home** is the unit repo that the `tbot` command and the console are run from: it holds
`config/units/` and the git-ignored `config/workspace.json` and `config/live_trading.json`. Run every `tbot` command
from home; `tbot up` refuses a folder without `config/units`.

## Procedures

### After a reboot

Long-running processes (the console, bots) must be started by launchd. A process started from a
Claude desktop-app session stays in the app's coalition and is killed when the app quits, even a
`nohup` or `setsid` one reparented to ppid 1 (verified on macOS on 2026-11-25: every unit
started that way died when the app quit). What survives:

- a one-off LaunchAgent plist with `RunAtLoad` and `AbandonProcessGroup` set to true, with home
  as its working directory, that runs the command;
- a process started by a console that launchd started.

launchd jobs cannot read `~/Documents` (TCC), which is why the repos live in `~/Projects`.

1. From home, as a launchd job: `uv run --no-sync tbot up` (`--no-sync`: run the installed
   `.venv` as is, without letting uv update it first). It starts every `autostart` process that
   is not running, in home and every root, the console first, and prints the console's address
   unless its start failed.
2. Trading processes never autostart. Look at each one's state, then start each one explicitly
   through the launchd-started console, or as a launchd job running
   `uv run tbot start <id>:<proc>`.

Only the console: a launchd job running `uv run --no-sync tbot start console` from home; `nohup`
from an app session does not survive the app quitting. A stopped console cannot be started from
its own page.

### Connect a unit repo (a root)

1. Clone it next to home and run `uv sync --locked` in it. If it has `.env.example`, copy it to
   `.env`; the keys in it are the operator's to fill in.
2. Add it to home's `config/workspace.json`: `{"roots": ["../<folder>"], "console_port": 8420}`.
   Roots are relative to home; `console_port` is optional.
3. Within ~15 s its units appear on the home page and in `uv run tbot ps`, with no console
   restart. A refused root is one broken entry with the reason
   ([control/README.md](../src/tbot_console/control/README.md), "Home and roots").

`tbot ui-links` is not needed to run a root; it only serves UI development in that repo.

### Update a root

1. Pull only reviewed code: a branch whose changes the operator accepted.
2. If the pull changed the root's dependencies, run `uv sync` in it. The console runs a root's
   processes with that root's own `.venv` python and never syncs it.
3. Keep every root on the platform major of home. A root a major behind is listed broken until it
   is bumped.

### Disconnect a root

1. Stop its processes first: `uv run tbot stop <id>`, plus `uv run tbot stop <id>:<proc>` for a
   process with its own `places_orders`, which a whole-unit command leaves alone.
2. Remove the root from `roots`. Its units disappear within ~15 s; removing the root stops no
   process.

### Expose the console beyond loopback

Under `tbot` the console always listens on loopback, on `console_port`, and takes no address from
`.env`. To reach it from another machine:

1. In home's `.env` set `TBOT_WEB_HOST` (for example `0.0.0.0`), `TBOT_WEB_ALLOWED_HOSTS` (extra
   host names, comma-separated, for example the machine's LAN name), `TBOT_WEB_PORT` and
   `TBOT_WEB_TOKEN`. The token is required: the console refuses a non-loopback bind without it.
2. Start the console by hand from home: `uv run tbot-web`. A hand start reads `.env` whole, except
   the trading switches.

The page asks for the token once and keeps it in the browser.

### Start a new unit repo

1. Copy `tests/fixtures/unit_repo/` (the fictional monitor `demo`): manifest, service, UI,
   `pyproject.toml` with the platform tag, `.gitignore`.
2. Do not name a console script `tbot` or `tbot-web`, and put no files under `tbot_console/`:
   those names belong to the platform. No check enforces this.
3. One contract test that runs the `tbot_console.testing` helpers over the repo is enough.
4. For UI checks in the repo (`node --test`, `tsc`, `biome`) run `uv run tbot ui-links`.
5. A trading process follows "Extension points" in
   [control/README.md](../src/tbot_console/control/README.md); the live-trading switch needs no
   manifest change.

## Trust model

- A root's processes run as the operator's OS user. They can read any of his files, other repos'
  `.env` included, and can connect to the broker themselves. The console only withholds other
  repos' keys: a process gets env files from its own repo only. Connect and update only reviewed
  code.
- For a unit that only reads from IBKR, turn on "Read-Only API" in IB Gateway or TWS.
