# Decisions

The operator's decisions for this repo, newest first. Each entry: what was decided, why, his own
words, what it supersedes, and where it is enforced.

## 2026-10-15 — The console page shows the running console version

- Decision: the console unit's processes page (`#/console/processes`) shows the `tbot-console`
  version that is running; `UnitEntry.version` is set for the `console` unit only.
- Why: not stated. Context: that day the live console turned out to run an old tag from a unit
  repo's `.venv` while fixes were being released.
- Operator's words: «Добавь чтобы на вкладке Консоль-Процессы отображалась версия консоли»
- Supersedes: —
- Enforced in: `src/tbot_console/web/control_api.py` (`CONSOLE_VERSION`),
  `src/tbot_console/web/static/js/shell/processes.js`.

## 2026-10-15 — Tests that send orders run only with the operator's permission

- Decision: integration tests that send orders run only with the operator's explicit permission
  in chat and only against a paper gateway: a liquid stock, limits far from the market so nothing
  fills, everything cancelled afterwards. They use their own client id, never a unit's.
- Why: they send real orders to a broker account, and a shared client id would knock a running
  unit process off the gateway.
- Operator's words: «Тесты, которые отправляют заявки, запускать только с моего явного
  разрешения в чате и только на бумажном счёте»
- Supersedes: —
- Enforced in: `tests/integration/connectors/ibkr/test_ibkr_paper_orders.py` (skips without
  `IBKR_PAPER_ORDER_TESTS=1`, asserts a paper account), `IBKR_TEST_CLIENT_ID` in the
  integration conftest, `AGENTS.md`.

## 2026-10-15 — Several targets are several independent brackets

- Decision: each part of a position that has its own target is its own bracket: an entry on that
  part's shares, its target and its stop. When the first target fills, only that part's stop is
  lifted. One shared OCA group of type 2 ("reduce") is rejected. Per-pair OCA groups under one
  entry were allowed if a paper gateway shows they stay apart; that is not verified yet.
- Why: IBKR documents a bracket as one entry with one target and one stop; what it does with
  more children under one entry is not documented, and the first fill could cancel or shrink the
  other parts' target and stop.
- Operator's words: «Одна общая группа OCA «с уменьшением объёма» (`ocaType` 2) не годится»
- Supersedes: —
- Enforced in: `src/tbot_console/connectors/ibkr/orders.py` (`plan_bracket`),
  `tests/unit/connectors/ibkr/test_trader.py::test_the_first_target_lifts_only_its_own_stop`;
  detail in [connectors/ibkr/README.md](../src/tbot_console/connectors/ibkr/README.md).

## 2026-10-15 — A per-unit live-trading switch, set only by the console

- Decision: whether a trading unit's orders may reach a live account is a switch per unit id,
  off by default, kept in home's git-ignored `config/live_trading.json` (never in a unit repo or a
  manifest), changed only from the console page or `tbot live`, each change logged. It reaches
  only that unit's trading processes, as `IBKR_ALLOW_LIVE_TRADING=true`, at start, restart and
  `--check`; a running process keeps the value it started with.
- Why: orders go to real money only when the operator turns it on himself.
- Operator's words: «По умолчанию заявки идут только на бумажный счёт; на живой — только
  когда я сам включу переключатель в консоли.»
- Supersedes: —
- Enforced in: `src/tbot_console/control/live.py`, `control/procs.py` (`child_env`,
  `FILE_DROPPED_KEYS`), `control/manifest.py` (a manifest `env` cannot name the key),
  `tests/unit/control/test_live_trading.py`.

## 2026-10-15 — Order code is allowed, only in the IBKR trading client

- Decision: the platform may place orders, but only through `IBKRTrader` in `connectors/ibkr/`.
  It reaches a paper account unless the unit's live-trading switch is on; the account check after
  every connect (every account starts with `D`) is the main gate, and the port gate stays.
  `IBKRClient` stays read-only.
- Why: small-cap strategies need to place entries and brackets themselves, with the stop and
  targets held at the broker so they work while the strategy process is down or the machine
  sleeps.
- Operator's words: «Код заявок в платформе разрешён.»
- Supersedes: the `AGENTS.md` rule "No order-placing code".
- Enforced in: `src/tbot_console/connectors/ibkr/trader.py`, `config.py`, `orders.py`,
  `tests/unit/connectors/ibkr/`, `AGENTS.md`.

## 2026-10-15 — Everything IBKR is platform code

- Decision: all code that talks to Interactive Brokers lives in `connectors/ibkr/`, even while a
  single unit needs it.
- Why: not stated beyond calling it shared («общая часть»).
- Operator's words: «Всё, что касается IBKR, — общая часть и живёт в платформе»
- Supersedes: the growth rule (a module joins only when a second repo needs it), for
  `connectors/ibkr/` only.
- Enforced in: `AGENTS.md` (growth rule).
