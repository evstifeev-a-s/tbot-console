# IBKR connector — Interactive Brokers market data and the paper-first trading client

Market-data client for **Interactive Brokers** over the TWS API, covering the
instrument classes IB is worth having for: US equities and ETFs, index and equity options
(with greeks), futures and future options, FX, and cash indices. Plus **market scanners** —
IB's server-side screener, the one call here that answers "which instruments match these
conditions" rather than "what is this instrument doing" — and the **order book and the tape**:
real-time depth, every print and quote change as it happens, and the tape's history. All of that
is `IBKRClient`, which reads and never places an order.

Order entry is a separate object, **`IBKRTrader`** (below): limit orders and brackets (entry,
stop and targets held at IB, so the protection works while the strategy's process is down),
locked to a paper account unless the console's live-trading switch is on for the unit. It is the
only code in the platform that sends, changes or cancels orders ([AGENTS.md](../../../../AGENTS.md)).

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
  refresh a series you already hold), `fetch_option_chain`, `fetch_executions` (the account's
  fills IB reports — the current trading day unless TWS keeps more — as `IBKRExecution`:
  `buy`/`sell`, shares, price, epoch time, order and perm ids, optionally for one `symbol` and
  from `since=`, oldest first; a read call that works on a read-only session and places
  nothing), `fetch_open_orders` (every working order of the session's login that IB reports —
  placed from TWS, the mobile app or any API client, `reqAllOpenOrders` — as `IBKROrder`:
  `buy`/`sell`, IB's order type, shares and the filled part, the limit price for a limit kind,
  the stop trigger for a stop kind (the current trail stop for a trailing one), status, parent
  and OCA group, and the time IB logged it; IB's unset price reads as `None`; optionally for one
  `symbol`, only the configured `account` when one is set, in placing order, each with its
  `ref` (`orderRef`); bounded by the config's `timeout`; a read call that changes nothing),
  `fetch_positions` (the account's non-zero positions as `IBKRPosition`, only the configured
  `account` when one is set), the scanner:
  `scan`, `scan_vocabulary`, `watch_scan`/`stop_scan`, and depth and tape (below):
  `watch_depth`/`stop_depth`, `depth_exchanges`, `watch_tape`/`stop_tape`, `fetch_ticks`,
  `active_streams`. Contract lookups are memoized per client
  under the **whole `IBKRInstrument`** — routing and trading class included, because both change
  which contract IB resolves — and dropped on `disconnect`; the scanner vocabulary is kept across
  reconnects.
- `IBKRInstrument` — the venue-neutral contract spec and the symbol layer; carries no `ib_async`
  types, so parsing and validation are testable without a gateway.
- `IBKRConfig.from_env()` — `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID`, `IBKR_ACCOUNT`,
  `IBKR_TIMEOUT`, `IBKR_WRITABLE`, `IBKR_MARKET_DATA_TYPE`, `IBKR_STREAM_LIMIT`;
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

## Depth and tape

Level 2 (the order book: every price someone bids or offers at, with its size), time and sales
(every trade, or every change of the best bid and ask), and the tape's history.

```python
from datetime import UTC, datetime, timedelta

from tbot_console.connectors.ibkr import IBKRClient, IBKRConfig, IBKRInstrument

async with IBKRClient(IBKRConfig.from_env()) as client:          # IBKR_MARKET_DATA_TYPE=1
    nasdaq = IBKRInstrument.parse("AAPL-USD-STK@ISLAND")        # ISLAND: Nasdaq TotalView
    book = await client.watch_depth(nasdaq, on_book, rows=10)    # on_book(DepthBook)
    tape = await client.watch_tape(nasdaq, on_print)             # on_print(TapePrint)
    last_hour = await client.fetch_ticks(nasdaq, datetime.now(UTC) - timedelta(hours=1))
    await client.stop_tape(tape)
```

- `watch_depth(instrument, callback, *, rows=10, smart=False, on_error=None)` → `DepthWatch`
  (`reqMktDepth(contract, rows, smart)`). After every update batch the callback gets a
  `DepthBook(symbol, bids, asks, time, smart)` of `DepthLevel(price, size, venue)`: bids best
  (highest) first, asks best (lowest) first, at most `rows` per side; a level without a price or
  a size is left out; `venue` is the market maker or exchange IB names (empty when it names
  none). `smart=True` is IB's book merged over every exchange that sends this account depth
  (`isSmartDepth`); without it the instrument must be routed to one exchange (`@ISLAND`,
  `@NYSE`, `@ARCA`, …) — a SMART-routed one is refused before IB is asked. IB error 317 (the
  book was reset) empties the book; the rows after it start a new one and nothing from before
  the reset survives, not even rows of the same batch.
- The client keeps each side as IB's rows itself, replaying `Ticker.domTicks` with the TWS API's
  row semantics: insert shifts the rows below it down, delete shifts them up, update overwrites.
  ib_async's own `domBids`/`domAsks` overwrite by row number on insert and delete, which leaves
  stale rows after a level appears or vanishes mid-book, so they are not read.
- `depth_exchanges()` → `list[DepthExchange]`: the exchanges that send depth
  (`reqMktDepthExchanges`), sorted by security type and exchange.
- `watch_tape(instrument, callback, *, kind="trades"|"quotes", on_error=None)` → `TapeWatch`
  (`reqTickByTickData(contract, "AllLast" | "BidAsk", 0, False)`). The callback gets each
  `TapePrint(symbol, time, price, size, venue, conditions, past_limit, unreported)` — every
  trade, odd lots and off-exchange prints included — or `TapeQuote(symbol, time, bid, ask,
  bid_size, ask_size)`, in arrival order. `time` is when the batch reached this process:
  ib_async replaces IB's whole-second stamp with it. A non-positive price reads as `None` (an
  absent side); a print without a price is dropped.
- `fetch_ticks(instrument, start, end=None, *, kind="trades"|"quotes", limit=1000,
  rth_only=False)` → the tape from `start` (a naive time is UTC), oldest first
  (`reqHistoricalTicksAsync`). **IB serves at most 1000 ticks per request** (`TICK_PAGE`) and
  stamps them to the whole second, so a longer stretch is paged: each next request starts at
  the second of the last tick and skips the ticks of that second it already returned. A second
  with more ticks than one request holds cannot be paged inside; the rest of that second is
  skipped with a warning. Paging stops at `end`, at `limit` or when IB has nothing more, and
  every page counts against IB's historical-data pacing (about 60 requests per 10 minutes).
  `fetch_ticks` is history: it needs IB's data permissions, not the real-time feed setting.
- Callbacks behave like `watch_scan`'s: sync or async; one that raises is logged and the stream
  goes on.
- **A refusal is an error, never silence.** `watch_*` waits up to `STREAM_CONFIRM_S` (1 s, or
  the config's `timeout` if shorter) for IB to refuse the request and then raises
  `ExchangeRequestError` with IB's code and a plain reading of it (`STREAM_REFUSALS`): 309 and
  10190 (too many depth or tick-by-tick streams), 10092 (no depth for this security type on this
  exchange), 354, 10089, 10168, 10186 (no market data subscription), 10189 (no tick-by-tick
  subscription), 316 (depth halted), 10197 (the login is busy in a competing session). A
  refusal after that window ends the stream: it is logged, `watch.error` is set, its slot is
  freed and `on_error(error)` is called. 10090 (part of the data is not subscribed) and the
  2100–2199 farm notices only warn. A lost connection ends every stream the same way with
  `ExchangeConnectionError`; subscribe again after `connect()`. The readings come from IB's
  error reference; only the integration suite against a gateway confirms them.
- **Real time only.** Delayed data has no depth and no tape: with `IBKR_MARKET_DATA_TYPE` 3 or 4
  both `watch_*` raise `ConfigurationError` saying a real-time subscription is needed, before
  IB is asked.
- **Three streams.** IB serves 3 depth and tick-by-tick streams at once by default (quote
  boosters add more). The client counts its depth and tape streams together against
  `IBKRConfig.stream_limit` (`IBKR_STREAM_LIMIT`, default 3) and refuses one more at once with
  `StreamRefusedError` (an `ExchangeRequestError` without a code) naming the open ones. It
  cannot see other clients' streams on the same login, and IB counts those too. One stream per
  contract and kind per client: ib_async keeps one `Ticker` per contract, so a second depth, or
  a second tape of the same kind, on the same contract is refused as well.
- `stop_depth(watch)` / `stop_tape(watch)` cancel at IB once (a stream IB refused sends
  nothing); `disconnect()` stops every depth, tape and scan stream; `active_streams` lists the
  open depth and tape streams.

## Trading client

```python
from tbot_console.connectors.ibkr import IBKRConfig, IBKRInstrument, IBKRTrader, Target

trader = IBKRTrader(IBKRConfig.from_env(), max_order_value=2_000, journal="state/orders.jsonl")
async with trader:                                  # refuses a live account without the switch
    print(trader.account_kind, trader.account)      # paper DU****567
    trader.watch_orders(on_order)                   # on_order(OrderEvent)
    chain = await trader.place_bracket(
        IBKRInstrument.parse("DEMO-USD-STK"), "buy", 300, 2.50, 2.30,
        [Target(2.80, 180), Target(3.10, 120)], ref="signal-42",
    )
    await trader.modify(chain.of_role("stop")[1].order_id, stop=2.50)   # second part to breakeven
    await trader.cancel_chain(chain.parent_id)
```

- **Composition, not a subclass.** `IBKRTrader(config, *, max_order_value, journal, ib=None)`
  holds an `IBKRClient` on `replace(config, readonly=False)`, so `IBKRConfig`'s port gate still
  applies; `trader.client` reads through the same connection (quotes, depth, open orders).
  `connect()` uses ib_async's default `fetchFields` (positions, open and completed orders,
  executions synced at startup); the read-only client keeps `StartupFetch(0)`. `IBKRClient`
  itself has no order call.
- **The lock: paper unless switched.**
  - The switch is `IBKR_ALLOW_LIVE_TRADING=true`, read when the trader is built. Under `tbot`
    only the console sets it, and only for the trading processes (`unit.trades(proc)`) of a unit
    whose live-trading switch is on (`control.live`); an operator may set it in a terminal for a
    hand start. An env file never supplies it (`control.procs.FILE_DROPPED_KEYS`) and a manifest
    `env` cannot name it.
  - The port check of `IBKRConfig` stays: without the switch a writable session is allowed only
    on the paper ports 7497 and 4002.
  - **The account is the main check.** Right after every connect the trader reads
    `managedAccounts()`. The login is paper only when **every** account starts with `D`
    (`DU…` paper accounts, `DF…` paper advisor accounts); one that does not makes it live. A live
    account without the switch disconnects and raises `LiveAccountError` (a
    `ConfigurationError`) saying the account is live and how to turn the switch on. Several
    accounts without `IBKR_ACCOUNT` are refused (where would orders go?), and `IBKR_ACCOUNT` must
    be one of them. Every order names its account explicitly.
  - With the switch and a live account the connect logs a loud warning. `account_kind`
    (`"paper"`, `"live"`, `None` while disconnected) and the masked `account` are for the unit's
    `--check` and status page. A dropped connection clears the check; the next `connect()` runs
    it again, and no order goes out before it passes.
  - An account number never appears whole in a message or in the journal: `mask_account`
    keeps `DU****567`.
- `place_limit(instrument, side, shares, limit, *, tif="DAY", outside_rth=False, ref)` →
  `PlacedOrder(order_id, perm_id, parent_id, ref, role, side, order_type, shares, limit_price,
  stop_price, status)`.
- `place_bracket(instrument, side, shares, entry, stop, targets=(), *, stop_limit=None,
  tif="DAY", outside_rth=False, ref)` → `PlacedChain(parent_id, legs, ref)` (`of_role("stop")`
  and so on). `targets` are `Target(price, shares)` pairs or plain tuples; the entry is a limit
  order, each target a limit order, the stop a `STP`, or a `STP LMT` when `stop_limit` is given.
- **Several targets are several independent chains.** IBKR documents a bracket as one parent
  with two children (target and stop) that form one OCA group: one fills, the other is cancelled;
  a partial fill shrinks the other. What it does with more children under one parent is not
  documented, and the likely reading — every child of a parent in one group, each fill shrinking
  the others — would let the first target cancel or shrink the other parts' target and stop; one
  shared OCA group of type 2 ("reduce") would shrink the second target too. So each part of the
  position is its own bracket: an entry on that part's shares, its target and its stop on the same
  shares. When the first target fills, IBKR lifts only that part's stop; the rest keeps its own
  stop and target, and shares without a target get a chain of entry and stop. The cost: one entry
  order per part at the same price (more orders, a minimum commission each, parts may fill in
  different order). `FakeIB` models the pessimistic rule, and
  `test_the_first_target_lifts_only_its_own_stop` holds the layout to it. **Not yet verified on
  a paper gateway** (none was running when this was built): if IBKR keeps per-pair OCA groups
  under one entry apart, one entry per bracket would do.
- Each chain goes out in one transmission: `transmit=False` on its entry and target and `True`
  on its stop, the last child (ib_async's `bracketOrder` shape); `ref` is the `orderRef` of every
  order of every chain of the call. The call waits up to the config's `timeout` for IB to confirm
  each order. If IB rejects one, the call's unfilled entries are cancelled (their children go with
  them), orders guarding shares already filled stay, and `OrderRejectedError` carries IB's code
  and reason. An order IB has not confirmed in time comes back `PendingSubmit` with a warning;
  its fate arrives through `watch_orders`.
- `modify(order_id, *, price=None, stop=None, shares=None)` — e.g. the stop to breakeven after
  the first target; it waits for IB's answer and raises `ExchangeRequestError` with IB's code
  when the change is refused. `cancel(order_id)`. `cancel_chain(parent_id)` cancels everything
  of the chain (and of the other chains sent under the same `ref`) that still works: an unfilled
  entry takes its children down with it; after a fill only the remaining children go.
  `reqGlobalCancel` is never exposed — it cancels every order of the account, other clients'
  and manual ones included.
- `watch_orders(callback)` → `OrderWatch` (`stop_orders(watch)`): `OrderEvent(kind, order_id,
  perm_id, parent_id, ref, role, symbol, side, shares, filled, remaining, avg_price, status,
  code, reason, time)` for the orders of this connection's client id, from `orderStatusEvent`,
  `execDetailsEvent` and `errorEvent`. Kinds: `accepted`, `partial`, `filled`, `cancelled`,
  `rejected` (IB 201, `Inactive`, an error on an order never accepted), `notice` (warnings such as
  110, 399, 2109 — the order stays live). 202 reads as `cancelled` with IB's reason. `accepted`
  and each terminal kind come once per order, `partial` once per new fill. Callbacks as
  `watch_scan`'s.
- `fetch_positions()` → `IBKRPosition(account, symbol, instrument, shares, avg_price)` of the
  traded account (`avg_price` per share or per unit of a derivative: IB's `avgCost` divided by the
  multiplier); `IBKRClient.fetch_positions()` reads them on a read-only session too.
  `what_if(instrument, side, shares, limit, …)` → `MarginImpact` — IB's margin and commission
  answer for a limit order that is never sent.
- **Checks before sending**, each with its own `OrderCheckError` (journaled as `refused`) and
  its own test, none of them asking IB for anything but the contract:
  - stocks only (`SecType.STK`), other classes refused with the reason;
  - shares a whole number above zero; prices finite and above zero;
  - prices on the price step: `ContractSpec.min_tick` from `qualify`, never finer than the US
    rule for a USD stock ($0.01 from $1, $0.0001 below $1), and that rule alone when IB names no
    tick (`price_step`);
  - a buy needs stop < entry < every target, a sell the mirror; `stop_limit` sits on the stop's
    side and is not better than the stop;
  - the targets' shares add up to no more than the entry's;
  - the order's value (shares × entry or limit) stays within `max_order_value`, a required
    constructor argument — no trader exists without it;
  - outside regular hours (`outside_rth=True`) only limit and stop-limit orders: IBKR does not
    trigger a market stop on a US stock outside regular hours (its Outside RTH rules; IB answers
    such an order with warning 2109 "Outside Regular Trading Hours is ignored"). Taken from
    IBKR's documentation; `test_ib_ignores_outside_rth_on_a_stop_market_order` confirms it on a
    paper gateway once order tests are allowed;
  - `tif` `DAY` or `GTC` — a `DAY` bracket's stop and targets expire at the close too;
  - `ref` is a label of 1–64 characters that no working order carries: the open orders IB
    reports (`reqAllOpenOrders`, so the check survives a reconnect and a restart) plus the ones
    this session just sent.
- **The order journal**, at the path the unit passes: JSON Lines, one object per line with `at`
  (epoch seconds) and `event` — `connect`, `disconnect`, `refused`, `request`, `status`, `fill`,
  `error`, `withdraw`, `unconfirmed`, `what_if` — plus `ref`, order ids and the masked account.
  Every write appends one line and `fsync`s; `read_journal` skips a line a crash tore, and the
  next write starts on a fresh line. A new order or a change is not sent, and `connect()` fails,
  when the journal cannot be written; a cancel still goes out, and a status the journal cannot
  record is logged and still delivered.
- **Client id.** IB lets only the API client id that placed an order change or cancel it. Give
  the trading process its own constant `IBKR_CLIENT_ID`, distinct from the unit's read-only
  processes and from `IBKR_TEST_CLIENT_ID`; after a restart under the same id ib_async syncs its
  open orders and `modify`/`cancel`/`cancel_chain` work on them again.

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
| `config.py` | `IBKRConfig` (+`from_env`, `stream_limit`), `Gateway`, `MarketDataType`, the `PORTS` table, the live-trading guard and `live_trading_allowed()`. No `ib_async` import |
| `models.py` | Cold-path results: `IBKRQuote` (+`mid`/`spread`), `OptionGreeks`, `ContractSpec`, `OptionChainSpec` (+`nearest_strikes`), `IBKRExecution`, `IBKROrder` (+`ref`), `IBKRPosition`, `DepthLevel`/`DepthBook`/`DepthExchange`, `TapePrint`/`TapeQuote`, `Target`, `PlacedOrder`/`PlacedChain`, `OrderEvent`, `MarginImpact` |
| `scanner.py` | The scanner as pure logic: `ScanRequest`, `ScanHit`, `ScanType`/`ScanLocation`/`ScannerVocabulary`, `build_subscription` (the sentinel handling), `filter_options`, `parse_scanner_parameters` (the XML catalogue), `MAX_ROWS`/`MAX_ACTIVE_SCANS` |
| `streams.py` | Depth and tape as pure logic: `apply_depth`/`BookState` (IB's row semantics), `depth_book`, `exchange_from`, `print_from_tick`/`quote_from_tick`/`quote_from_history`, `tape_from`/`history_from`, `fresh_ticks` (the paging dedupe), `STREAM_REFUSALS`/`refusal_message`, `StreamRefusedError`, `DepthWatch`/`TapeWatch`, `TICK_PAGE`, `STREAM_CONFIRM_S` |
| `orders.py` | Orders as pure logic: `plan_bracket` (`BracketPlan`/`Portion`: the checks and the split into parts), `check_ticks`/`price_step`/`snap`, `check_value`, the `checked_*` validators, `account_kind`/`mask_account`, `role_of`, `placed_from`/`described`, `LiveAccountError`, `OrderCheckError`, `OrderRejectedError` |
| `trader.py` | `IBKRTrader`: the account lock, placement, changes, cancels, `OrderWatch` and the event mapping, positions, `what_if` |
| `journal.py` | `OrderJournal` (append + `fsync`, a torn last line mended) and `read_journal` |
| `client.py` | `IBKRClient`; the ib_async↔ours row translation — `build_contract`, `instrument_from_contract`, `spec_from_details`, `candle_from_bar` (→ `analysis.models.Candle`), `quote_from_ticker`, `hits_from_scan_data`, `execution_from_fill`, `order_from_trade`, `position_from`; `duration_for`, `duration_since`, `bar_timestamp`; `RequestErrors` (the per-call `errorEvent` listener), `is_notice`, `is_no_data`; `BAR_SIZE`, `WHAT_TO_SHOW`, `MAX_DURATION_DAYS`; `ScanWatch` |

Unit tests: `tests/unit/connectors/ibkr/` (a `FakeIB` stands in for the gateway; scanner streaming
is driven through `ScanDataList.updateEvent.emit`, depth and tape through `push(ticker, …)`,
which fills `domTicks`/`tickByTicks` and emits the ticker's `updateEvent` as one batch).
Integration: `tests/integration/connectors/ibkr/` — reads `IBKR_*` from the environment (paper
defaults), probes the paper port and skips when nothing is listening; refuses to run against a
live port at all. It connects as client id `IBKR_TEST_CLIENT_ID` (default 9917), never as a
unit's `IBKR_CLIENT_ID`: the gateway lets one connection hold an id, so sharing it would lock out
whichever of the test and a running unit process connects second. Depth and tape tests skip, or
assert the `ConfigurationError`, when the feed is delayed, and fail on a refusal whose IB code
has no reading in `STREAM_REFUSALS`. The scanner-catalogue XML shape is taken
from IB's documented schema and is only confirmed against a live gateway by
`test_scanner_vocabulary_parses`, so run the integration suite once before trusting
`scan_vocabulary` output.

## Ports and safety

| | TWS | IB Gateway |
|---|---|---|
| Paper | 7497 | 4002 |
| Live | 7496 | 4001 |

`IBKRConfig` defaults to **paper TWS, `readonly=True`** — IB itself then rejects order entry, so
for every reader the guarantee is enforced at the venue, not just by our code. A writable
session — only `IBKRTrader` opens one — is allowed without `IBKR_ALLOW_LIVE_TRADING=true` **only
when the port is a known paper port and `server_type` is not `PRODUCTION`**; anything else — a
live port, or a non-standard port we cannot classify — raises `ConfigurationError`. The port is
checked because it, not `server_type`, decides which TWS the socket reaches; the trader then
checks the account itself, which is what decides whose money an order moves (above). Under
`tbot`, `IBKR_WRITABLE` never reaches a process from an env file, and `IBKR_ALLOW_LIVE_TRADING`
reaches only the trading processes of a unit whose console switch is on
(`control.procs.FILE_DROPPED_KEYS`, `control.live`, [control/README.md](../../control/README.md)).

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
- **ib_async marks a refused change as cancelled.** For an error (not a warning) on an existing
  order ib_async sets the trade's status to `Cancelled`, even when IB refused only the
  modification and the order still works with its old values. `modify` raises IB's code and says
  so; `fetch_open_orders()` shows what IB really holds.
- **Children wait as `PreSubmitted`.** A bracket's target and stop are held at IB until their
  entry fills; they become `Submitted` then.
- **One `Ticker` per contract.** ib_async keys tickers by `conId`, so a contract's quotes, depth
  and tape all update the same object and fire the same `updateEvent`. Each watch reads only its
  own part of the batch (`domTicks` or its kind of `tickByTicks`), and errors are matched to a
  stream by the request id ib_async keeps in `wrapper.ticker2ReqId`.
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
- **More order types or classes** (market, trailing stop, options): add them to `orders.py` and
  `trader.py` with their own pre-send checks and a `FakeIB` model of how IBKR answers; every
  order goes through `IBKRTrader`, where the account lock, the journal and the `ref` check live,
  and `IBKRClient` stays order-free.
- **Streaming quotes.** `IB.reqMktData` returns a `Ticker` that updates in place with an
  `updateEvent`; bridge it to a plain callback the way `watch_tape` does (own the batch, read
  only the ticks of your kind — a contract's `Ticker` is shared with its depth and tape). It
  lands here even for one unit: everything IBKR is platform code ([AGENTS.md](../../../../AGENTS.md)).
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
- `BookState`/`apply_depth` for any depth feed (IB's row semantics and the reset), and
  `fresh_ticks` for any whole-second paging of IB history.
- `plan_bracket`, `check_ticks` and `price_step` for the pre-send checks of a new order kind;
  `mask_account` for any account number in a message; `read_journal` to read the order journal
  back (a unit's status page, a post-mortem).
