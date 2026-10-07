from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import math
from collections.abc import Awaitable, Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from types import TracebackType
from typing import Any, Literal, TypeVar

from ib_async import IB, Contract, ContractDetails, ExecutionFilter, Fill, ScanData, Ticker, Trade
from ib_async.ib import StartupFetch
from ib_async.objects import BarData, BarDataList, OptionComputation, ScanDataList
from ib_async.wrapper import RequestError

from tbot_console.analysis.models import TIMEFRAME_SECONDS, Candle, Timeframe
from tbot_console.connectors.ibkr.config import IBKRConfig, MarketDataType
from tbot_console.connectors.ibkr.instruments import (
    DATED,
    STRIKED,
    IBKRInstrument,
    InstrumentSyntaxError,
    SecType,
)
from tbot_console.connectors.ibkr.models import (
    ContractSpec,
    IBKRExecution,
    IBKROrder,
    IBKRQuote,
    OptionChainSpec,
    OptionGreeks,
)
from tbot_console.connectors.ibkr.scanner import (
    MAX_ACTIVE_SCANS,
    ScanHit,
    ScannerVocabulary,
    ScanRequest,
    ScanRequestError,
    build_subscription,
    filter_options,
    parse_scanner_parameters,
)
from tbot_console.core.exceptions import ExchangeConnectionError, ExchangeRequestError

LOG = logging.getLogger(__name__)

T = TypeVar("T")

BAR_SIZE: dict[Timeframe, str] = {
    Timeframe.M1: "1 min",
    Timeframe.M5: "5 mins",
    Timeframe.M15: "15 mins",
    Timeframe.H1: "1 hour",
    Timeframe.H4: "4 hours",
    Timeframe.D1: "1 day",
    Timeframe.W1: "1 week",
}

WHAT_TO_SHOW: dict[SecType, str] = {
    SecType.STK: "TRADES",
    SecType.IND: "TRADES",
    SecType.CASH: "MIDPOINT",
    SecType.FUT: "TRADES",
    SecType.CONTFUT: "TRADES",
    SecType.OPT: "TRADES",
    SecType.FOP: "TRADES",
}

MAX_DURATION_DAYS: dict[Timeframe, int] = {
    Timeframe.M1: 7,
    Timeframe.M5: 30,
    Timeframe.M15: 60,
    Timeframe.H1: 365,
    Timeframe.H4: 365,
    Timeframe.D1: 365 * 15,
    Timeframe.W1: 365 * 15,
}

TRADING_SECONDS_PER_DAY = 6.5 * 3600
CALENDAR_STRETCH = 1.45
SECONDS_PER_DAY = 86400
DELAYED_FEEDS = frozenset({MarketDataType.DELAYED, MarketDataType.DELAYED_FROZEN})
HISTORY_TIMEOUT_FACTOR = 8.0

NO_SECURITY_DEFINITION = 200
HISTORY_SERVICE_ERROR = 162
NOTICE_CODES = frozenset({165, 10090, 10167})
NO_DATA_MARKER = "no data"
UNSET_PRICE = 1e300
STOP_ORDER_TYPES = frozenset({"STP", "STP LMT", "STP PRT"})
TRAIL_ORDER_TYPES = frozenset({"TRAIL", "TRAIL LIMIT", "TRAIL LIT", "TRAIL MIT"})


def is_notice(code: int) -> bool:
    return code in NOTICE_CODES or 2100 <= code < 2200


def is_no_data(code: int, message: str) -> bool:
    return code == HISTORY_SERVICE_ERROR and NO_DATA_MARKER in message.lower()


@dataclass(slots=True)
class RequestErrors:
    by_request: dict[int, tuple[int, str]] = field(default_factory=dict)
    by_contract: dict[int, tuple[int, str]] = field(default_factory=dict)

    def __call__(self, req_id: int, code: int, message: str, contract: Contract | None) -> None:
        if is_notice(code):
            return
        self.by_request.setdefault(req_id, (code, message))
        if contract is not None and contract.conId:
            self.by_contract.setdefault(contract.conId, (code, message))


def _days_duration(days: int) -> str:
    if days >= 365:
        return f"{math.ceil(days / 365)} Y"
    return f"{days} D"


def duration_since(timeframe: Timeframe, since: datetime, now: datetime | None = None) -> str:
    moment = bar_timestamp(now if now is not None else datetime.now(UTC))
    bar_seconds = TIMEFRAME_SECONDS[timeframe]
    span = max(0.0, moment - bar_timestamp(since)) + bar_seconds
    if bar_seconds < SECONDS_PER_DAY and span <= SECONDS_PER_DAY:
        return f"{math.ceil(span)} S"
    days = min(max(math.ceil(span / SECONDS_PER_DAY), 1), MAX_DURATION_DAYS[timeframe])
    return _days_duration(days)


def duration_for(timeframe: Timeframe, count: int) -> str:
    bar_seconds = TIMEFRAME_SECONDS[timeframe]
    if bar_seconds >= SECONDS_PER_DAY:
        bars_per_day = SECONDS_PER_DAY / bar_seconds
    else:
        bars_per_day = max(1.0, TRADING_SECONDS_PER_DAY / bar_seconds)
    days = math.ceil(max(count, 1) / bars_per_day * CALENDAR_STRETCH)
    return _days_duration(min(max(days, 1), MAX_DURATION_DAYS[timeframe]))


def build_contract(instrument: IBKRInstrument) -> Contract:
    return Contract(
        secType=instrument.sec_type.value,
        symbol=instrument.symbol,
        currency=instrument.currency,
        exchange=instrument.effective_exchange,
        lastTradeDateOrContractMonth=instrument.expiry,
        strike=instrument.strike,
        right=instrument.right,
        multiplier=instrument.multiplier,
        tradingClass=instrument.trading_class,
    )


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _price(value: float | None) -> float | None:
    if value is None or math.isnan(value) or value < 0:
        return None
    return float(value)


def _finite(value: float | None) -> float | None:
    if value is None or math.isnan(value):
        return None
    return float(value)


def _size(value: float | None) -> float:
    if value is None or math.isnan(value) or value < 0:
        return 0.0
    return float(value)


def bar_timestamp(value: date | datetime) -> float:
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return moment.timestamp()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC).timestamp()
    LOG.warning("IBKR bar carries an unreadable date %r; timestamping it as epoch 0", value)
    return 0.0


def candle_from_bar(bar: BarData) -> Candle:
    return Candle(
        timestamp=bar_timestamp(bar.date),
        open=_to_float(bar.open),
        high=_to_float(bar.high),
        low=_to_float(bar.low),
        close=_to_float(bar.close),
        volume=max(_to_float(bar.volume), 0.0),
    )


def greeks_from(computation: OptionComputation | None) -> OptionGreeks | None:
    if computation is None:
        return None
    return OptionGreeks(
        implied_vol=_finite(computation.impliedVol),
        delta=_finite(computation.delta),
        gamma=_finite(computation.gamma),
        vega=_finite(computation.vega),
        theta=_finite(computation.theta),
        option_price=_price(computation.optPrice),
        underlying_price=_price(computation.undPrice),
    )


def quote_from_ticker(symbol: str, ticker: Ticker) -> IBKRQuote:
    return IBKRQuote(
        symbol=symbol,
        bid=_price(ticker.bid),
        ask=_price(ticker.ask),
        last=_price(ticker.last),
        close=_price(ticker.close),
        bid_size=_size(ticker.bidSize),
        ask_size=_size(ticker.askSize),
        timestamp=ticker.time.timestamp() if ticker.time is not None else 0.0,
        delayed=ticker.marketDataType in DELAYED_FEEDS,
        greeks=greeks_from(ticker.modelGreeks),
    )


def spec_from_details(
    instrument: IBKRInstrument, details: ContractDetails, contract: Contract
) -> ContractSpec:
    return ContractSpec(
        instrument=instrument,
        con_id=contract.conId,
        local_symbol=contract.localSymbol,
        exchange=contract.exchange or instrument.effective_exchange,
        trading_class=contract.tradingClass,
        min_tick=_to_float(details.minTick),
        multiplier=_to_float(contract.multiplier),
    )


def instrument_from_contract(contract: Contract) -> IBKRInstrument | None:
    try:
        sec_type = SecType(contract.secType.upper())
    except ValueError:
        LOG.debug("IBKR security type %r has no IBKRInstrument shape", contract.secType)
        return None

    try:
        return IBKRInstrument(
            sec_type=sec_type,
            symbol=contract.symbol,
            currency=contract.currency or "USD",
            exchange=contract.exchange or contract.primaryExchange,
            expiry=contract.lastTradeDateOrContractMonth if sec_type in DATED else "",
            strike=contract.strike if sec_type in STRIKED else 0.0,
            right=contract.right.upper()[:1] if sec_type in STRIKED else "",
            multiplier=contract.multiplier,
            trading_class=contract.tradingClass,
        )
    except InstrumentSyntaxError as exc:
        LOG.debug("IBKR contract %s is not expressible as an instrument: %s", contract, exc)
        return None


def execution_from_fill(fill: Fill) -> IBKRExecution | None:
    execution, contract = fill.execution, fill.contract
    side = execution.side.upper()
    if side not in ("BOT", "SLD"):
        LOG.debug("IBKR execution %s has no readable side %r", execution.execId, execution.side)
        return None
    direction: Literal["buy", "sell"] = "buy" if side == "BOT" else "sell"
    return IBKRExecution(
        exec_id=execution.execId,
        timestamp=bar_timestamp(execution.time),
        symbol=contract.symbol,
        instrument=instrument_from_contract(contract),
        side=direction,
        shares=_size(execution.shares),
        price=_to_float(execution.price),
        account=execution.acctNumber,
        order_id=execution.orderId,
        perm_id=execution.permId,
    )


def _order_price(value: Any) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(price) or price <= 0 or price >= UNSET_PRICE:
        return None
    return price


def order_from_trade(trade: Trade) -> IBKROrder | None:
    order, contract, status = trade.order, trade.contract, trade.orderStatus
    action = order.action.upper()
    if action not in ("BUY", "SELL"):
        LOG.debug("IBKR order %s has no readable action %r", order.orderId, order.action)
        return None
    kind = order.orderType.upper()
    if kind in STOP_ORDER_TYPES:
        stop = _order_price(order.auxPrice)
    elif kind in TRAIL_ORDER_TYPES:
        stop = _order_price(order.trailStopPrice)
    else:
        stop = None
    placed = trade.log[0].time if trade.log else None
    return IBKROrder(
        order_id=order.orderId,
        perm_id=order.permId,
        parent_id=order.parentId,
        symbol=contract.symbol,
        instrument=instrument_from_contract(contract),
        side="buy" if action == "BUY" else "sell",
        order_type=kind,
        shares=_size(_order_price(order.totalQuantity)),
        filled=_size(status.filled),
        limit_price=_order_price(order.lmtPrice) if "LMT" in kind or "LIMIT" in kind else None,
        stop_price=stop,
        status=status.status,
        account=order.account,
        oca_group=order.ocaGroup,
        placed_at=bar_timestamp(placed) if placed is not None else None,
    )


def hits_from_scan_data(rows: Iterable[ScanData]) -> list[ScanHit]:
    hits: list[ScanHit] = []
    for row in rows:
        details = row.contractDetails
        contract = details.contract if details is not None else None
        hits.append(
            ScanHit(
                rank=row.rank,
                con_id=contract.conId if contract is not None else 0,
                local_symbol=contract.localSymbol if contract is not None else "",
                exchange=(contract.exchange or contract.primaryExchange)
                if contract is not None
                else "",
                instrument=instrument_from_contract(contract) if contract is not None else None,
                distance=row.distance,
                benchmark=row.benchmark,
                projection=row.projection,
                legs=row.legsStr,
            )
        )
    return sorted(hits, key=lambda hit: hit.rank)


ScanCallback = Callable[[list[ScanHit]], Any]


@dataclass(slots=True, eq=False)
class ScanWatch:
    request: ScanRequest
    rows: ScanDataList
    handler: Callable[[ScanDataList], None]


class IBKRClient:
    def __init__(self, config: IBKRConfig | None = None, ib: IB | None = None) -> None:
        self._config = config or IBKRConfig()
        self._ib = ib if ib is not None else IB()
        self._specs: dict[IBKRInstrument, ContractSpec] = {}
        self._contracts: dict[IBKRInstrument, Contract] = {}
        self._vocabulary: ScannerVocabulary | None = None
        self._watches: list[ScanWatch] = []
        self._callback_tasks: set[asyncio.Task[Any]] = set()

    @property
    def config(self) -> IBKRConfig:
        return self._config

    @property
    def ib(self) -> IB:
        return self._ib

    @property
    def is_connected(self) -> bool:
        return bool(self._ib.isConnected())

    async def connect(self) -> None:
        if self.is_connected:
            return
        cfg = self._config
        try:
            await self._ib.connectAsync(
                host=cfg.host,
                port=cfg.port,
                clientId=cfg.client_id,
                timeout=cfg.timeout,
                readonly=cfg.readonly,
                account=cfg.account,
                fetchFields=StartupFetch(0),
            )
        except (TimeoutError, OSError, RequestError) as exc:
            raise ExchangeConnectionError(
                f"IBKR gateway unreachable at {cfg.host}:{cfg.port} "
                f"(clientId={cfg.client_id}): {exc!r}. Is TWS or IB Gateway running "
                "with the API enabled for this client id?"
            ) from exc
        self._ib.reqMarketDataType(int(cfg.market_data_type))

    async def disconnect(self) -> None:
        for watch in list(self._watches):
            await self.stop_scan(watch)
        if self.is_connected:
            self._ib.disconnect()
        self._specs.clear()
        self._contracts.clear()

    async def __aenter__(self) -> IBKRClient:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.disconnect()

    @contextlib.contextmanager
    def _errors(self) -> Iterator[RequestErrors]:
        errors = RequestErrors()
        self._ib.errorEvent += errors
        try:
            yield errors
        finally:
            self._ib.errorEvent -= errors

    async def _request(self, awaitable: Awaitable[T], what: str) -> T:
        try:
            return await awaitable
        except RequestError as exc:
            raise ExchangeRequestError(
                f"IBKR rejected {what}: {exc.message}", code=exc.code, reason=exc.message
            ) from exc
        except (TimeoutError, OSError) as exc:
            raise ExchangeConnectionError(f"IBKR request failed for {what}: {exc!r}") from exc

    async def qualify(self, instrument: IBKRInstrument) -> ContractSpec:
        cached = self._specs.get(instrument)
        if cached is not None:
            return cached

        routed = instrument.to_routed()
        details = await self._request(
            self._ib.reqContractDetailsAsync(build_contract(instrument)), f"contract {routed}"
        )
        if not details:
            raise ExchangeRequestError(
                f"IBKR knows no contract for {instrument.to_normalized()} on exchange "
                f"{instrument.effective_exchange or '(unset)'}",
                code=NO_SECURITY_DEFINITION,
                reason="no security definition found",
            )
        if len(details) > 1:
            LOG.warning(
                "IBKR returned %d contracts for %s; using conId=%s",
                len(details),
                routed,
                details[0].contract.conId if details[0].contract else "?",
            )

        contract = details[0].contract
        if contract is None:
            raise ExchangeRequestError(
                f"IBKR returned contract details without a contract for {routed}",
                code=NO_SECURITY_DEFINITION,
                reason="empty contract in details",
            )

        spec = spec_from_details(instrument, details[0], contract)
        self._specs[instrument] = spec
        self._contracts[instrument] = contract
        return spec

    async def _qualified_contract(self, instrument: IBKRInstrument) -> Contract:
        cached = self._contracts.get(instrument)
        if cached is not None:
            return cached
        await self.qualify(instrument)
        return self._contracts[instrument]

    async def fetch_quote(self, instrument: IBKRInstrument) -> IBKRQuote | None:
        quotes = await self.fetch_quotes([instrument])
        return quotes.get(instrument.to_normalized())

    async def fetch_quotes(self, instruments: Sequence[IBKRInstrument]) -> dict[str, IBKRQuote]:
        if not instruments:
            return {}

        pairs: list[tuple[str, Contract]] = []
        for instrument in instruments:
            key = instrument.to_normalized()
            try:
                pairs.append((key, await self._qualified_contract(instrument)))
            except ExchangeRequestError as exc:
                LOG.warning("IBKR quote skipped for %s: %s", key, exc)

        if not pairs:
            return {}

        with self._errors() as errors:
            tickers = await self._request(
                self._ib.reqTickersAsync(*[contract for _, contract in pairs]), "tickers"
            )
        by_con_id = {t.contract.conId: t for t in tickers if t.contract is not None}

        quotes: dict[str, IBKRQuote] = {}
        for key, contract in pairs:
            failure = errors.by_contract.get(contract.conId)
            if failure is not None:
                LOG.warning("IBKR quote skipped for %s: error %d: %s", key, *failure)
                continue
            ticker = by_con_id.get(contract.conId)
            if ticker is not None:
                quotes[key] = quote_from_ticker(key, ticker)
        return quotes

    async def fetch_bars(
        self,
        instrument: IBKRInstrument,
        timeframe: Timeframe,
        count: int = 100,
        use_rth: bool = False,
        since: datetime | None = None,
    ) -> list[Candle]:
        bar_size = BAR_SIZE.get(timeframe)
        if bar_size is None:
            raise ExchangeRequestError(f"IBKR has no bar size for timeframe {timeframe.value}")

        key = instrument.to_normalized()
        contract = await self._qualified_contract(instrument)
        duration = (
            duration_for(timeframe, count) if since is None else duration_since(timeframe, since)
        )
        timeout = self._config.timeout * HISTORY_TIMEOUT_FACTOR
        loop = asyncio.get_running_loop()
        started = loop.time()
        with self._errors() as errors:
            bars = await self._request(
                self._ib.reqHistoricalDataAsync(
                    contract,
                    endDateTime="",
                    durationStr=duration,
                    barSizeSetting=bar_size,
                    whatToShow=WHAT_TO_SHOW[instrument.sec_type],
                    useRTH=use_rth,
                    formatDate=2,
                    timeout=timeout,
                ),
                f"history of {key}",
            )
        if not bars:
            self._raise_for_empty_history(key, errors, bars, timeout, loop.time() - started)

        candles = [candle_from_bar(bar) for bar in bars]
        if since is not None:
            start = bar_timestamp(since)
            candles = [candle for candle in candles if candle.timestamp >= start]
        return candles[-count:] if count > 0 else candles

    def _raise_for_empty_history(
        self, key: str, errors: RequestErrors, bars: BarDataList, timeout: float, waited: float
    ) -> None:
        failure = errors.by_request.get(bars.reqId)
        if failure is not None:
            code, message = failure
            if is_no_data(code, message):
                return
            raise ExchangeRequestError(
                f"IBKR rejected history of {key}: {message}", code=code, reason=message
            )
        if timeout and waited >= timeout:
            raise ExchangeConnectionError(
                f"IBKR sent no history of {key} within {timeout:g} s; the gateway may be "
                "overloaded or pacing the requests"
            )

    async def fetch_executions(
        self, symbol: str = "", since: datetime | None = None
    ) -> list[IBKRExecution]:
        flt = ExecutionFilter(acctCode=self._config.account or "", symbol=symbol.upper())
        fills = await self._request(self._ib.reqExecutionsAsync(flt), "executions")
        out = [e for e in map(execution_from_fill, fills) if e is not None]
        if since is not None:
            start = bar_timestamp(since)
            out = [e for e in out if e.timestamp >= start]
        return sorted(out, key=lambda e: (e.timestamp, e.exec_id))

    async def fetch_open_orders(self, symbol: str = "") -> list[IBKROrder]:
        asked = asyncio.wait_for(self._ib.reqAllOpenOrdersAsync(), self._config.timeout or None)
        trades = await self._request(asked, "open orders")
        wanted, account = symbol.upper(), self._config.account or ""
        out = [
            o
            for o in map(order_from_trade, trades)
            if o is not None
            and (not wanted or o.symbol.upper() == wanted)
            and (not account or o.account == account)
        ]
        return sorted(out, key=lambda o: (o.placed_at or 0.0, o.order_id))

    async def fetch_option_chain(self, underlying: IBKRInstrument) -> list[OptionChainSpec]:
        spec = await self.qualify(underlying)
        fut_fop_exchange = (
            underlying.effective_exchange
            if underlying.sec_type in (SecType.FUT, SecType.CONTFUT)
            else ""
        )
        chains = await self._request(
            self._ib.reqSecDefOptParamsAsync(
                underlying.symbol,
                fut_fop_exchange,
                underlying.sec_type.value,
                spec.con_id,
            ),
            f"option chain of {underlying.to_normalized()}",
        )
        return [
            OptionChainSpec(
                exchange=chain.exchange,
                underlying_con_id=chain.underlyingConId,
                trading_class=chain.tradingClass,
                multiplier=_to_float(chain.multiplier),
                expirations=tuple(sorted(chain.expirations)),
                strikes=tuple(sorted(chain.strikes)),
            )
            for chain in chains
        ]

    async def scan_vocabulary(self, *, refresh: bool = False) -> ScannerVocabulary:
        if self._vocabulary is not None and not refresh:
            return self._vocabulary
        xml = await self._request(self._ib.reqScannerParametersAsync(), "scanner parameters")
        self._vocabulary = parse_scanner_parameters(xml)
        return self._vocabulary

    async def scan(self, request: ScanRequest, *, validate: bool = False) -> list[ScanHit]:
        if validate:
            complaints = (await self.scan_vocabulary()).unknowns(request)
            if complaints:
                raise ScanRequestError(
                    f"scan {request.scan_code} does not match what IBKR offers: "
                    + "; ".join(complaints)
                )

        with self._errors() as errors:
            rows = await self._request(
                self._ib.reqScannerDataAsync(
                    build_subscription(request), [], filter_options(request)
                ),
                f"scan {request.scan_code}",
            )
        failure = errors.by_request.get(rows.reqId)
        if not rows and failure is not None:
            code, message = failure
            raise ExchangeRequestError(
                f"IBKR rejected scan {request.scan_code}: {message}", code=code, reason=message
            )
        return hits_from_scan_data(rows)

    async def watch_scan(self, request: ScanRequest, callback: ScanCallback) -> ScanWatch:
        if len(self._watches) >= MAX_ACTIVE_SCANS:
            LOG.warning(
                "IBKR allows about %d concurrent scanner subscriptions and %d are already "
                "active; this one may be rejected",
                MAX_ACTIVE_SCANS,
                len(self._watches),
            )

        rows = self._ib.reqScannerSubscription(
            build_subscription(request), [], filter_options(request)
        )

        def on_update(data: ScanDataList) -> None:
            self._deliver_scan(request, callback, data)

        rows.updateEvent += on_update
        watch = ScanWatch(request=request, rows=rows, handler=on_update)
        self._watches.append(watch)
        return watch

    async def stop_scan(self, watch: ScanWatch) -> None:
        watch.rows.updateEvent -= watch.handler
        if self.is_connected:
            self._ib.cancelScannerSubscription(watch.rows)
        if watch in self._watches:
            self._watches.remove(watch)

    @property
    def active_scans(self) -> tuple[ScanWatch, ...]:
        return tuple(self._watches)

    def _deliver_scan(
        self, request: ScanRequest, callback: ScanCallback, data: ScanDataList
    ) -> None:
        try:
            result = callback(hits_from_scan_data(data))
        except Exception:
            LOG.exception("IBKR scan callback failed for %s", request.scan_code)
            return
        if inspect.isawaitable(result):
            task = asyncio.ensure_future(result)
            self._callback_tasks.add(task)
            task.add_done_callback(self._finish_scan_task)

    def _finish_scan_task(self, task: asyncio.Task[Any]) -> None:
        self._callback_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            LOG.error("IBKR scan callback raised: %r", task.exception())
