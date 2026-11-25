# tbot-console

The platform that trading strategies and monitors kept in their own repositories (unit repos)
run on:

- `tbot-web`: a local web console (default `http://127.0.0.1:8420`) that lists every unit and its
  processes, starts and stops them, and serves each unit's own pages;
- `tbot`: the same process control from the command line;
- shared libraries: the Interactive Brokers connector (market data and a paper-first trading
  client), `core`, `analysis`, and contract-test helpers for unit repos.

The package knows no concrete strategy. A unit repo declares its units in `config/units/*.json`,
ships its pages in `js/units/<ui>/` and depends on this package by git tag.

## Install

In a unit repo's `pyproject.toml` (use `tbot-console[ibkr]` for the IBKR connector):

```toml
[project]
dependencies = ["tbot-console"]

[tool.uv.sources]
tbot-console = { git = "https://github.com/evstifeev-a-s/tbot-console.git", tag = "vX.Y.Z" }
```

Then run `uv sync` and `uv run tbot ps` in that repo. Python 3.15; process control runs on macOS
and Linux.

To try the console on the bundled fictional unit repo:

```bash
uv sync --locked --all-groups --all-extras
TBOT_ROOT=tests/fixtures/unit_repo TBOT_WEB_PORT=18420 uv run tbot-web   # http://127.0.0.1:18420
```

## More

Rules, repository map and checks: [AGENTS.md](AGENTS.md). Procedures:
[docs/CONSOLE.md](docs/CONSOLE.md).
