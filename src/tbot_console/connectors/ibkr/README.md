# IBKR connector — read-only Interactive Brokers client

Read-only market-data client for **Interactive Brokers** over the TWS API, covering the
instrument classes IB is worth having for: US equities and ETFs, index and equity options
(with greeks), futures and future options, FX, and cash indices. Plus **market scanners** —
IB's server-side screener, the one call here that answers "which instruments match these
conditions" rather than "what is this instrument doing".

**No WebSocket, no API key.** The TWS API is a length-prefixed binary stream over a plain TCP
socket, reachable only through a **running TWS or IB Gateway** (`IBKR_HOST`, normally this
machine; there is no key-based programmatic endpoint), so this client shares nothing with a
WebSocket exchange connector. The protocol is handled by [`ib_async`](https://github.com/ib-api-reloaded/ib_async), an
asyncio-native successor to the archived `ib_insync`; this package is the thin, typed layer over
it. Cold path throughout: `float` results, frozen dataclasses, no tick-path role.

`ib-async` comes with the `ibkr` extra: a unit repo depends on `tbot-console[ibkr]`, and here
`uv sync --all-groups --all-extras`. Without it `tbot_console.connectors.ibkr` will not import;
the unit tests and `mypy src/` import it unconditionally, so the platform's checks need the
extra.

## Public API

```python
from tbot_console.connectors.ibkr import IBKRClient, IBKRConfig, IBKRInstrument
from tbot_console.analysis.models import Timeframe

async with IBKRClient(IBKRConfig.from_env()) as client:        # paper TWS on 127.0.0.1:7497
    spx = IBKRInstrument.parse("SPX-USD-IND@CBOE")
    chains = await client.fetch_option_chain(spx)
    bars = await client.fetch_bars(spx, Timeframe.D1, count=250)
    quote = await client.fetch_quote(IBKRInstrument.parse("SPX-USD-OPT-20260320-5000-C"))
    greeks = quote.greeks if quote else None
```

- `IBKRClient` — `connect`/`disconnect` (also an async context manager), `qualify`,
  `fetch_quote`/`fetch_quotes`, `fetch_bars` (the last `count` bars, or with `since=` only the
  bars from that moment on, asked in seconds while the span is under a day — the cheap way to
  refresh a series you already hold), `fetch_option_chain`, and the scanner:
  `scan`, `scan_vocabulary`, `watch_scan`/`stop_scan`. Contract lookups are memoized per client
  under the **whole `IBKRInstrument`** — routing and trading class included, because both change
  which contract IB resolves — and dropped on `disconnect`; the scanner vocabulary is kept across
  reconnects.
- `IBKRInstrument` — the venue-neutral contract spec and the symbol layer; carries no `ib_async`
  types, so parsing and validation are testable without a gateway.
- `IBKRConfig.from_env()` — `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID`, `IBKR_ACCOUNT`,
  `IBKR_TIMEOUT`, `IBKR_WRITABLE`, `IBKR_MARKET_DATA_TYPE`;
  `from_env(force_readonly=True)` ignores `IBKR_WRITABLE`, so a read-only caller never trips the
  live-trading guard.
- Every venue failure surfaces as `tbot_console.core.exceptions.ExchangeRequestError` (carrying
  IB's numeric error code) or `ExchangeConnectionError` — never a raw `ib_async` exception and
  never a silent empty answer. `fetch_bars` and `scan` raise for an error IB reports on their own
  request; `fetch_bars` returns `[]` only for IB's "HMDS query returned no data" (code 162) and
  raises `ExchangeConnectionError` for a history IB never answered within the timeout;
  `fetch_quotes` leaves out (and logs with its code) an instrument whose snapshot IB refused, so
  a refused quote is never read as a live one. Notices are not failures: 165, 10090 (partly
  subscribed), 10167 (delayed data shown) and the 2100–2199 farm-status messages.

## Scanners

A scan is a query, not an instrument: `ScanRequest` in, `list[ScanHit]` out, ranked by IB.

```python
from tbot_console.connectors.ibkr import IBKRClient, IBKRConfig, ScanRequest

async with IBKRClient(IBKRConfig.from_env()) as client:
    hits = await client.scan(
        ScanRequest(
            scan_code="TOP_PERC_GAIN",
            instrument="STK",
            location="STK.US.MAJOR",
            limit=25,
            above_price=10.0,
            above_volume=1_000_000,
            extra_filters=(("impliedVolAbove", "40"),),
        ),
        validate=True,
    )
    for hit in hits:
        if hit.instrument is not None:
            bars = await client.fetch_bars(hit.instrument, Timeframe.D1, 30)
```

Every `ScanHit` carries an `IBKRInstrument` when the row is expressible in the grammar above, so a
scan feeds `fetch_bars` / `fetch_quote` / `fetch_option_chain` without hand-building anything.
Rows IB returns that have no `SecType` of ours (bonds, funds, warrants) still come back as hits,
with `instrument=None` and the `con_id` intact.

- **Named bounds vs `extra_filters`.** The common screens are typed fields (`above_price`,
  `below_price`, `above_volume`, `above_option_volume`, `market_cap_above`/`_below`,
  `exclude_convertible`, `stock_type`). Everything else in IB's filter vocabulary — hundreds of
  tags like `impliedVolAbove`, `avgVolumeAbove`, `dividendYieldAbove` — goes through
  `extra_filters` as `(tag, value)` pairs, so nothing is locked out. Bond-only bounds (coupon,
  maturity, Moody's/S&P ratings) have no named field because there is no `BOND` `SecType` here;
  reach them through `extra_filters` if they are ever wanted.
- **`scan_vocabulary()`** parses `reqScannerParameters` — IB's several-megabyte XML catalogue of
  valid instruments, locations, scan codes and filter tags — into `ScannerVocabulary`, cached per
  client. `scan(..., validate=True)` checks a request against it *before* hitting IB, because
  IB's own rejection for a bad scan code is the opaque "error validating request". Validation is
  opt-in precisely because the first call pays for that XML.
- **Streaming**: `watch_scan(request, callback)` returns a `ScanWatch`; the callback gets a fresh
  `list[ScanHit]` on every IB update and may be sync or async. A callback that raises is logged
  and the feed survives. `stop_scan(watch)` unsubscribes,
  and `disconnect()` cancels everything still running.
- **Limits**: IB serves at most **50 rows** per scan (`build_subscription` clamps and warns) and
  tolerates roughly **10 concurrent** scanner subscriptions (`watch_scan` warns past that).

## Symbol grammar

`SYMBOL-CURRENCY-SECTYPE[-EXPIRY[-STRIKE-RIGHT]][@EXCHANGE]`, an IB-shaped `BASE-QUOTE-KIND[…]`.
There is **no symbol normalizer**: an IB instrument is identified by a tuple (secType, symbol,
currency, expiry, strike, right), not by a single exchange string, and IB's own `localSymbol` is
not derivable offline — a normalizer would have to invent a synthetic key with no consumer.

| Class | Example | Notes |
|---|---|---|
| Stock / ETF | `AAPL-USD-STK`, `SPY-USD-STK` | IB has no separate ETF secType |
| Cash index | `SPX-USD-IND@CBOE` | exchange required — IB will not SMART-route an index |
| FX | `EUR-USD-CASH` | symbol is the base, currency the quote; `IDEALPRO` by default |
| Future | `ES-USD-FUT-20260320@CME` | expiry `YYYYMMDD` or `YYYYMM` |
| Continuous future | `ES-USD-CONTFUT@CME` | history only, never tradable |
| Option | `SPX-USD-OPT-20260320-5000-C` | `C`/`P`; `SMART` by default |
| Future option | `ES-USD-FOP-20260320-5000-C@CME` | |

`@EXCHANGE` is **routing, not identity** *for naming*: `to_normalized()` omits it, `to_routed()`
includes it. It is still part of the contract cache key, which is the instrument object itself —
`ES-…@CME` and `ES-…@NYMEX` are two different contracts and get two lookups. `STK`/`OPT` default to `SMART`, `CASH` to `IDEALPRO`; `FUT`, `FOP`,
`IND` and `CONTFUT` have no sane default, so they must be routed or qualification fails with
IB error 200.

## Modules

| File | Responsibility |
|---|---|
| `instruments.py` | `SecType`, `IBKRInstrument` (frozen, validating), `parse`/`to_normalized`/`to_routed`, `DEFAULT_EXCHANGE`. No `ib_async` import |
| `config.py` | `IBKRConfig` (+`from_env`), `Gateway`, `MarketDataType`, the `PORTS` table, the live-trading guard. No `ib_async` import |
| `models.py` | Cold-path results: `IBKRQuote` (+`mid`/`spread`), `OptionGreeks`, `ContractSpec`, `OptionChainSpec` (+`nearest_strikes`) |
| `scanner.py` | The scanner as pure logic: `ScanRequest`, `ScanHit`, `ScanType`/`ScanLocation`/`ScannerVocabulary`, `build_subscription` (the sentinel handling), `filter_options`, `parse_scanner_parameters` (the XML catalogue), `MAX_ROWS`/`MAX_ACTIVE_SCANS` |
| `client.py` | `IBKRClient`; the ib_async↔ours row translation — `build_contract`, `instrument_from_contract`, `spec_from_details`, `candle_from_bar` (→ `analysis.models.Candle`), `quote_from_ticker`, `hits_from_scan_data`; `duration_for`, `duration_since`, `bar_timestamp`; `RequestErrors` (the per-call `errorEvent` listener), `is_notice`, `is_no_data`; `BAR_SIZE`, `WHAT_TO_SHOW`, `MAX_DURATION_DAYS`; `ScanWatch` |

Unit tests: `tests/unit/connectors/ibkr/` (a `FakeIB` stands in for the gateway; scanner streaming
is driven through `ScanDataList.updateEvent.emit`).
Integration: `tests/integration/connectors/ibkr/` — reads `IBKR_*` from the environment (paper
defaults), probes the paper port and skips when nothing is listening; refuses to run against a
live port at all. The scanner-catalogue XML shape is taken
from IB's documented schema and is only confirmed against a live gateway by
`test_scanner_vocabulary_parses`, so run the integration suite once before trusting
`scan_vocabulary` output.

## Ports and safety

| | TWS | IB Gateway |
|---|---|---|
| Paper | 7497 | 4002 |
| Live | 7496 | 4001 |

`IBKRConfig` defaults to **paper TWS, `readonly=True`** — IB itself then rejects order entry, so
the guarantee is enforced at the venue, not just by our code. A writable session is allowed
without a flag **only when the port is a known paper port and `server_type` is not
`PRODUCTION`**; anything else — a live port, or a non-standard port we cannot classify — raises
`ConfigurationError` unless `IBKR_ALLOW_LIVE_TRADING=true`. The port is checked because it, not
`server_type`, is what actually decides which TWS the socket reaches. The platform itself never
opens a writable session and never places orders ([AGENTS.md](../../../../AGENTS.md)); under
`tbot`, `IBKR_ALLOW_LIVE_TRADING` and `IBKR_WRITABLE` never reach a process from an env file
(`control.procs.FILE_DROPPED_KEYS`).

## Gotchas

- **A price of `0.00` is a price; `-1` and `NaN` are not.** IB marks an absent bid/ask with `-1`
  (ib_async's `emptyPrice`) and an unreceived tick with `NaN`; both become `None`. A genuine zero
  — an expired option's `last`/`close`, a worthless bid — is kept as `0.0`, so a screener can tell
  "worth nothing" from "no data". Never test an `IBKRQuote` price with `if price:`.
- **Market data defaults to delayed.** Without a data subscription IB returns nothing at all on a
  live-feed request, so `market_data_type` defaults to `DELAYED_FROZEN` (IB type 4) and every
  `IBKRQuote` reports `delayed`. Set `IBKR_MARKET_DATA_TYPE=1` once subscriptions are in place.
- **`whatToShow` is per security type.** FX rejects `TRADES` and must use `MIDPOINT`; everything
  else uses `TRADES`. Encoded in `WHAT_TO_SHOW` — extend it when adding a class.
- **History is capped by bar size.** `duration_for` converts a bar count into an IB duration
  string, stretching for nights and weekends (a 6.5 h session, ×1.45 for calendar days), then
  clamps to `MAX_DURATION_DAYS`. A request for more 1-minute bars than IB will serve in one call
  returns what fits rather than failing — check `len()` before assuming depth. The smallest day
  unit is a whole day: asking for the last 120 one-minute bars still downloads the full day, so a
  refresh should pass `since=` instead (`duration_since`: calendar seconds back from now plus one
  bar, up to 86 400 s; beyond that whole days within the same cap).
- **ib_async swallows request errors.** With its default `RaiseRequestErrors = False` a failed
  request ends with an empty result and the error goes only to the log and `IB.errorEvent`. The
  client listens to `errorEvent` for the duration of each call (`RequestErrors`, keyed by request
  id and by contract) instead of flipping that switch, because the switch is global to the `IB`
  object and would make one refused contract fail a whole multi-contract snapshot. ib_async also
  counts 321 (request validation) as a warning and keeps waiting for the answer; the client
  still reports it once the request ends.
- **One client id per connection.** A second client reusing `IBKR_CLIENT_ID` is refused by the
  gateway; give parallel processes distinct ids.
- **Ambiguous contracts.** `reqContractDetails` can return several matches (same ticker on
  several exchanges); `qualify` logs a warning and takes the first. Route explicitly with
  `@EXCHANGE` when that matters.
- **`ScannerSubscription` bounds use sentinels, not `None`.** IB marks an unset numeric bound with
  `sys.float_info.max` / `INT_MAX` (`ib_async.util.UNSET_DOUBLE` / `UNSET_INTEGER`), so passing
  `0` for "no minimum" would be read as a real bound of zero. `build_subscription` only sets the
  fields the request actually names and leaves the rest at ib_async's defaults — never fill them
  in by hand.
- **The scanner-parameters XML is parsed with stdlib `ElementTree`,** which does not resolve
  external entities, and `parse_scanner_parameters` refuses a payload carrying a DTD or entity
  declaration outright. The source is the operator's own loopback gateway, so `defusedxml` would
  be a project-wide dependency for no reachable threat.

## Extension points

- **Order entry never lands here.** A unit repo that trades through IB builds it in its own
  code: a trading client that composes `IBKRClient` (not a subclass — the read-only guarantee
  stays a separate object), constructs `IBKRConfig(readonly=False)`, which already enforces the
  live-account gate, and connects with the default `fetchFields` instead of `StartupFetch(0)` so
  positions, open orders and executions are synced at startup.
- **Streaming quotes.** `IB.reqMktData` returns a `Ticker` that updates in place with an
  `updateEvent`; bridge it to a plain callback the way `watch_scan` does. Add it when a second
  repository needs it (the platform's growth rule).
- **New instrument class.** Add a `SecType` member, a `WHAT_TO_SHOW` entry, and a
  `DEFAULT_EXCHANGE` entry if IB can route it without help. `instrument_from_contract` picks it up
  automatically, so scanner rows of that class stop coming back with `instrument=None`.
- **New named scan bound.** Add the field to `ScanRequest` and one line to the `optional` tuple in
  `build_subscription`; the sentinel handling then applies for free. Only worth it for a bound
  used repeatedly — `extra_filters` already reaches every tag IB has.
- **Candle depth beyond one request.** Page `reqHistoricalDataAsync` backwards by passing
  `endDateTime` instead of `""`; `fetch_bars` deliberately makes a single call.

## What to reuse

- `IBKRInstrument.parse` is the only place that turns a string into an IB contract — unit code
  and tests go through it; never hand-build a `Contract`.
- `duration_for` / `duration_since` / `BAR_SIZE` encode IB's historical-data limits; reuse them
  instead of re-deriving durations.
- `build_subscription` is the only correct way to fill a `ScannerSubscription` — it is where the
  unset-sentinel rule lives.
- `instrument_from_contract` is the exact inverse of `build_contract` — trading class and
  multiplier included, which is what keeps a weekly option (`SPXW`) from re-qualifying as the
  monthly. Use it for any IB response that hands back a contract, not just scanner rows.
- `IBKRClient.qualify` memoizes `conId` lookups — call it rather than caching contracts yourself.
