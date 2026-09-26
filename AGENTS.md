# AGENTS.md

`tbot-console` is the platform that strategies and monitors from other repositories run on: the
operator console (`tbot-web`), process control (`tbot`), and the few libraries more than one of
those repositories needs. It knows nothing about any concrete strategy or monitor. A repository
that holds one (a **unit repo**) depends on this package by a git tag and is picked up by the
console without any change here.

This file is the canonical, vendor-neutral instruction set for every AI coding agent working in
this repository. `CLAUDE.md` is the only adapter (Claude Code does not read `AGENTS.md`); never
fork content into it. `scripts/check_docs.py` enforces that. Keep this file short: add a rule
only if it is non-obvious, decision-changing and has no mechanical enforcement.

## Working with the operator

- **Reply in Russian.** Narrative prose for the operator (`README.md`, `docs/`) is Russian;
  everything read next to code (identifiers, module `README.md`, commit messages, PR titles and
  bodies, this file) is English.
- **Be terse.** Lead with the answer or result. No filler, no emoji, no restating the request.
- **Plain language, always.** Write for a smart person outside the field: gloss a term the first
  time it appears, never send a bare formula or symbol, give every number its unit and meaning.
- **No sycophancy.** If something is wrong or won't work, say so plainly.

## Non-negotiable rules

- **Unit-agnostic.** No name, id, env prefix, port, module or page of a real unit repo appears
  anywhere here — code, tests, fixtures, docs. Tests and examples use fictional units (`alpha`,
  `beta`, `demo`). If a change only makes sense for one unit, it belongs in that unit's repo.
  Before finishing, search the diff for real unit names; every hit must go.
- **Growth rule.** A module joins the platform only when a second repository needs it: the whole
  module with its tests, in one platform release, with the consumer's import rename in the same
  bump PR. Nothing is added for a hypothetical consumer.
- **Compatibility within a major.** Unit repos rely on: the manifest schema
  (`control/manifest.py`), the unit service protocol (`/api/<id>/health`, `/api/<id>/status`
  UnitStatus `v: 1`), the UI contract (`web/static/js/units/contract.d.ts`), the helper files a
  unit UI imports and `tbot ui-links` links (`js/{api,dom,util,tooltip}.js`, `globals.d.ts`,
  `require-tests.mjs`), the `tbot` commands, `tbot_console.testing`, and the public API of
  `core`, `analysis` and `connectors.ibkr`. Removing or changing any of them is a new major
  version; adding is a minor; a fix is a patch. The console lists a repo whose `.venv` holds
  another major as broken.
- **No order-placing code.** The platform never places, edits or cancels orders. The IBKR
  connector stays read-only; `procs.FILE_DROPPED_KEYS` keeps its live-trading switches out of
  every process.
- **Process-control guarantees are a contract with live trading processes** that unit repos run
  under `tbot`: a trading process is never SIGKILLed, one operation per process at a time, pid +
  create-time identity, detached sessions, an allowlisted child environment, loopback-only
  services. They are listed in [control/README.md](src/tbot_console/control/README.md); never
  weaken one.
- **Never run the console against a real unit repo on this machine.** A folder next to this one
  may be a unit repo with a live trading process. Run and test against
  `tests/fixtures/unit_repo` or throwaway copies (`TBOT_ROOT=tests/fixtures/unit_repo`), on spare
  ports (`TBOT_WEB_PORT`), never on the default 8420.
- **No code comments.** Write clean code that reads itself.

## Repository map

| Package (`src/tbot_console/`) | Docs | Role |
|---|---|---|
| `control/` | [README](src/tbot_console/control/README.md) | unit registry (home + `config/workspace.json` roots), process control run as detached executors, `tbot` CLI incl. `ui-links`; `service.py`, `status.py`, `logs.py`, `files.py` helpers for unit services |
| `web/` | [README](src/tbot_console/web/README.md) | the console (FastAPI + vanilla JS): home page, `/api/control/*`, proxy to unit services, unit UI files served from each unit's repo |
| `connectors/ibkr/` | [README](src/tbot_console/connectors/ibkr/README.md) | read-only Interactive Brokers client: quotes, bars, option chains, scanners |
| `core/` | — | `exceptions.py` (the error hierarchy the connector raises), `types.py` (`ServerType` and shared enums) |
| `analysis/` | — | `models.py` (`Candle`, `Timeframe`, analysis result types), `robust.py` (`atr`, `theil_sen`, `mad_sigma`, `spearman_rho`) |
| `testing.py` | — | helpers for unit repos' contract tests: `loopback_client`, `manifest_problems`, `manifest_modules`, `startup_imports`, `imported_modules` |

`tests/fixtures/unit_repo/` is a fictional unit repo (monitor `demo`) used by the tests and the
Playwright smoke; it is also the template a new unit repo starts from. Operator guide (Russian):
[docs/CONSOLE.md](docs/CONSOLE.md). Human landing page: [README.md](README.md).

## Releasing and co-developing

- **Release:** bump `version` in `pyproject.toml` and the tag in
  `tests/fixtures/unit_repo/pyproject.toml` (a test pins them together), `uv lock`, commit,
  `git tag vX.Y.Z`. Push `main` and the tag only when the operator asks.
- **Say what reaches trading processes.** A release that changes `__init__.py`,
  `control/__init__.py`, `control/logs.py`, `core/` or `analysis/` changes code a running
  trading process imports; name those files in the release PR so unit repos can run their own
  pre-deploy checks.
- **Co-developing with a unit repo:** in the unit repo, `uv run --with-editable ../tbot-console
  pytest|mypy|tbot …` overlays this checkout for that one command; plain `uv run` stays on the
  pinned tag. For JS, `uv run --with-editable ../tbot-console tbot ui-links`, then plain
  `uv run tbot ui-links` to return. Before the unit PR merges, release here and bump the tag
  there.

## Documentation

Every fact lives in one place: module detail in the module `README.md` (English, house format:
`# <Module> — <role>`, intro, `## Public API`, `## Modules`, `## Extension points`,
`## What to reuse`), operator procedure in `docs/CONSOLE.md` (Russian). Update docs in the same
commit when a change alters behaviour, a public contract, a command or a procedure; otherwise
leave them alone. Do not edit this file, `scripts/check_docs.py`, hooks or
`.github/workflows/*` as a side effect of another task — propose the change to the operator.

`scripts/check_docs.py` is the gate: every module README is linked from the map above, relative
links resolve, `CLAUDE.md` stays an adapter (pre-commit and `tests/unit/docs`). `--drift` warns
about changed code in a documented package whose README was not touched; `--report` shows
README staleness from git history.

## Definition of done

1. `uv run pytest tests/unit`, `uv run mypy src/`, `uv run ruff check src/ tests/`,
   `uv run ruff format --check src/ tests/` — all green.
2. If `static/` changed: `npm test`, `npm run test:coverage`, `npm run typecheck`,
   `npm run lint`, `python3 scripts/check_innerhtml_baseline.py`; if the shell, a route or the
   registry changed, also `npx playwright test`.
3. `python3 scripts/check_docs.py --drift` clean, or the change consciously judged invisible to
   the docs.
4. Commit to the task's feature branch (off `main`). Push and open PRs only when asked.

## Commands

```bash
uv sync --locked --all-groups --all-extras
uv run pytest tests/unit
uv run mypy src/
uv run ruff check --fix src/ tests/ && uv run ruff format src/ tests/
npm ci && npm test && npm run typecheck && npm run lint
npx playwright test
python3 scripts/check_docs.py
python3 scripts/check_innerhtml_baseline.py
uv build
```
