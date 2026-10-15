from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, TypeVar

from ib_async import Contract, Ticker
from ib_async.objects import (
    DepthMktDataDescription,
    HistoricalTickBidAsk,
    HistoricalTickLast,
    MktDepthData,
    TickByTickAllLast,
    TickByTickBidAsk,
)
from ib_async.util import UNSET_INTEGER

from tbot_console.connectors.ibkr.instruments import IBKRInstrument
from tbot_console.connectors.ibkr.models import (
    DepthBook,
    DepthExchange,
    DepthLevel,
    TapePrint,
    TapeQuote,
)
from tbot_console.core.exceptions import ExchangeConnectionError, ExchangeRequestError

T = TypeVar("T")

TapeKind = Literal["trades", "quotes"]
StreamCallback = Callable[[Any], Any]
ErrorCallback = Callable[[ExchangeConnectionError], Any]

DEFAULT_DEPTH_ROWS = 10
TICK_PAGE = 1000
STREAM_CONFIRM_S = 1.0

DEPTH_TICK_TYPE = "mktDepth"
TAPE_TICK_TYPES: dict[TapeKind, str] = {"trades": "AllLast", "quotes": "BidAsk"}
TAPE_HISTORY: dict[TapeKind, str] = {"trades": "TRADES", "quotes": "BID_ASK"}

DEPTH_RESET = 317
PARTLY_SUBSCRIBED = 10090
STREAM_REFUSALS: dict[int, str] = {
    309: "IBKR allows only a few depth streams at once (3 by default, more with quote boosters)",
    310: "IBKR has no depth stream under this request id",
    316: "IBKR halted this depth stream; subscribe again",
    354: "the account has no market data subscription for this instrument",
    10089: "this data needs an extra market data subscription for API use "
    "(Market Data Connections in TWS names it)",
    10092: "IBKR serves no depth for this security type on this exchange; route the instrument "
    "to an exchange that has depth or pass smart=True",
    10168: "the account has no market data subscription for this instrument and delayed data "
    "is off",
    10186: "the account has no market data subscription for this instrument and delayed data "
    "is off",
    10189: "IBKR refused tick-by-tick data: the account has no real-time subscription for this "
    "instrument",
    10190: "IBKR allows only a few tick-by-tick streams at once (3 by default, more with quote "
    "boosters)",
    10197: "no market data during a competing session: the same IBKR login is open elsewhere",
}


class StreamRefusedError(ExchangeRequestError):
    pass


def refusal_message(what: str, code: int, message: str) -> str:
    meaning = STREAM_REFUSALS.get(code)
    detail = f"{meaning} (IB: {message})" if meaning else message
    return f"IBKR refused {what}: error {code}: {detail}"


def utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def epoch(moment: datetime) -> float:
    return utc(moment).timestamp()


def _positive(value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    if math.isnan(number) or math.isinf(number) or number <= 0:
        return None
    return number


def _amount(value: float | None) -> float:
    number = _positive(value)
    return 0.0 if number is None else number


def apply_depth(levels: list[DepthLevel], position: int, operation: int, level: DepthLevel) -> None:
    if operation == 0:
        levels.insert(position, level)
    elif operation == 1:
        if 0 <= position < len(levels):
            levels[position] = level
        else:
            levels.append(level)
    elif operation == 2 and 0 <= position < len(levels):
        del levels[position]


@dataclass(slots=True)
class BookState:
    bids: list[DepthLevel] = field(default_factory=list)
    asks: list[DepthLevel] = field(default_factory=list)

    def clear(self) -> None:
        self.bids.clear()
        self.asks.clear()

    def apply(self, ticks: Iterable[MktDepthData]) -> None:
        for tick in ticks:
            side = self.bids if tick.side == 1 else self.asks
            level = DepthLevel(
                price=float(tick.price), size=float(tick.size), venue=tick.marketMaker
            )
            apply_depth(side, tick.position, tick.operation, level)


def _shown(levels: Iterable[DepthLevel]) -> list[DepthLevel]:
    return [
        level
        for level in levels
        if _positive(level.price) is not None and _positive(level.size) is not None
    ]


def depth_book(symbol: str, state: BookState, rows: int, moment: float, smart: bool) -> DepthBook:
    bids = sorted(_shown(state.bids), key=lambda level: -level.price)
    asks = sorted(_shown(state.asks), key=lambda level: level.price)
    return DepthBook(
        symbol=symbol, bids=tuple(bids[:rows]), asks=tuple(asks[:rows]), time=moment, smart=smart
    )


def exchange_from(row: DepthMktDataDescription) -> DepthExchange:
    group = row.aggGroup
    return DepthExchange(
        exchange=row.exchange,
        sec_type=row.secType,
        listing_exchange=row.listingExch,
        service_data_type=row.serviceDataType,
        agg_group=None if group in (None, UNSET_INTEGER) else int(group),
    )


def print_from_tick(symbol: str, tick: TickByTickAllLast | HistoricalTickLast) -> TapePrint | None:
    price = _positive(tick.price)
    if price is None:
        return None
    attrib = tick.tickAttribLast
    return TapePrint(
        symbol=symbol,
        time=epoch(tick.time),
        price=price,
        size=_amount(tick.size),
        venue=tick.exchange or "",
        conditions=(tick.specialConditions or "").strip(),
        past_limit=bool(attrib and attrib.pastLimit),
        unreported=bool(attrib and attrib.unreported),
    )


def quote_from_tick(symbol: str, tick: TickByTickBidAsk) -> TapeQuote:
    return TapeQuote(
        symbol=symbol,
        time=epoch(tick.time),
        bid=_positive(tick.bidPrice),
        ask=_positive(tick.askPrice),
        bid_size=_amount(tick.bidSize),
        ask_size=_amount(tick.askSize),
    )


def quote_from_history(symbol: str, tick: HistoricalTickBidAsk) -> TapeQuote:
    return TapeQuote(
        symbol=symbol,
        time=epoch(tick.time),
        bid=_positive(tick.priceBid),
        ask=_positive(tick.priceAsk),
        bid_size=_amount(tick.sizeBid),
        ask_size=_amount(tick.sizeAsk),
    )


def tape_from(kind: TapeKind, symbol: str, ticks: Iterable[Any]) -> list[TapePrint | TapeQuote]:
    if kind == "quotes":
        return [
            quote_from_tick(symbol, tick) for tick in ticks if isinstance(tick, TickByTickBidAsk)
        ]
    prints = (
        print_from_tick(symbol, tick) for tick in ticks if isinstance(tick, TickByTickAllLast)
    )
    return [item for item in prints if item is not None]


def history_from(kind: TapeKind, symbol: str, tick: Any) -> TapePrint | TapeQuote | None:
    if kind == "quotes":
        return quote_from_history(symbol, tick) if isinstance(tick, HistoricalTickBidAsk) else None
    return print_from_tick(symbol, tick) if isinstance(tick, HistoricalTickLast) else None


def second_of(moment: float) -> int:
    return math.floor(moment)


def fresh_ticks(
    page: Sequence[T], stamp: Callable[[T], float], boundary: int, seen: int
) -> list[T]:
    skipped, fresh = 0, []
    for tick in page:
        if skipped < seen and second_of(stamp(tick)) == boundary:
            skipped += 1
            continue
        fresh.append(tick)
    return fresh


@dataclass(slots=True, eq=False)
class DepthWatch:
    instrument: IBKRInstrument
    contract: Contract
    ticker: Ticker
    rows: int
    smart: bool
    req_id: int
    callback: StreamCallback
    on_error: ErrorCallback | None
    handler: Callable[[Ticker], None] = field(repr=False)
    settled: asyncio.Future[None] = field(repr=False)
    book: BookState = field(default_factory=BookState, repr=False)
    reset_at: int | None = None
    error: ExchangeConnectionError | None = None

    @property
    def label(self) -> str:
        return "depth"

    @property
    def family(self) -> str:
        return DEPTH_TICK_TYPE

    @property
    def what(self) -> str:
        return f"{self.label} of {self.instrument.to_routed()}"


@dataclass(slots=True, eq=False)
class TapeWatch:
    instrument: IBKRInstrument
    contract: Contract
    ticker: Ticker
    kind: TapeKind
    req_id: int
    callback: StreamCallback
    on_error: ErrorCallback | None
    handler: Callable[[Ticker], None] = field(repr=False)
    settled: asyncio.Future[None] = field(repr=False)
    error: ExchangeConnectionError | None = None

    @property
    def label(self) -> str:
        return f"{self.kind} tape"

    @property
    def family(self) -> str:
        return TAPE_TICK_TYPES[self.kind]

    @property
    def what(self) -> str:
        return f"{self.label} of {self.instrument.to_routed()}"


StreamWatch = DepthWatch | TapeWatch
