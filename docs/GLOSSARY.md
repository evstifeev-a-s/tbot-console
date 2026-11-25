# Glossary

Words the operator uses in chat, mapped to identifiers. The console UI never says «юнит»: it says
«стратегии и мониторы» and «процессы».

| Operator's word (Russian) | Identifier(s) | Meaning |
|---|---|---|
| юнит, юниты | `UnitManifest`, `config/units/<id>.json`, unit repo | one strategy or monitor instance declared in a unit repo |
| тегай | `git tag vX.Y.Z` + `git push origin vX.Y.Z` | cut the release tag and push it, right after `main` |
| тег консоли | `tag` of `tbot-console` in a unit repo's `[tool.uv.sources]` | the platform release a unit repo runs |
| вкладка Консоль-Процессы | `#/console/processes`, `web/static/js/shell/processes.js` | the processes page of the built-in `console` unit |
| торговый процесс | `unit.trades(proc)`, `places_orders` | a process that sends orders itself |
| переключатель «живая торговля» | `control/live.py`, `config/live_trading.json`, `tbot live`, `IBKR_ALLOW_LIVE_TRADING` | per-unit permission for orders to reach a live account |
| бумажный счёт / живой счёт | `account_kind` `"paper"` / `"live"` | IBKR paper account (number starts with `D`) / real-money account |
| шлюз, бумажный шлюз | TWS or IB Gateway, `IBKR_HOST`, `IBKR_PORT` | the IBKR program the TWS API connects through |
| торговый клиент | `IBKRTrader` | the only code that places, changes or cancels orders |
| читающий клиент | `IBKRClient` | the read-only IBKR client |
| стакан | `watch_depth`, `DepthBook` | level 2 order book |
| лента сделок | `watch_tape`, `fetch_ticks`, `TapePrint` | time and sales: every trade as it prints |
| цепочка «вход + стоп + цели» | `place_bracket`, `PlacedChain` | bracket: entry with its stop and targets held at IBKR |
