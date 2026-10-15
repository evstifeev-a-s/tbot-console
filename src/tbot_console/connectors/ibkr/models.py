from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NamedTuple

from tbot_console.connectors.ibkr.instruments import IBKRInstrument


@dataclass(frozen=True, slots=True)
class OptionGreeks:
    implied_vol: float | None
    delta: float | None
    gamma: float | None
    vega: float | None
    theta: float | None
    option_price: float | None
    underlying_price: float | None


@dataclass(frozen=True, slots=True)
class IBKRQuote:
    symbol: str
    bid: float | None
    ask: float | None
    last: float | None
    close: float | None
    bid_size: float
    ask_size: float
    timestamp: float
    delayed: bool
    greeks: OptionGreeks | None = None

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None:
            return self.last if self.last is not None else self.close
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid


@dataclass(frozen=True, slots=True)
class IBKRExecution:
    exec_id: str
    timestamp: float
    symbol: str
    instrument: IBKRInstrument | None
    side: Literal["buy", "sell"]
    shares: float
    price: float
    account: str
    order_id: int
    perm_id: int


@dataclass(frozen=True, slots=True)
class IBKROrder:
    order_id: int
    perm_id: int
    parent_id: int
    symbol: str
    instrument: IBKRInstrument | None
    side: Literal["buy", "sell"]
    order_type: str
    shares: float
    filled: float
    limit_price: float | None
    stop_price: float | None
    status: str
    account: str
    oca_group: str
    placed_at: float | None
    ref: str = ""


Side = Literal["buy", "sell"]
OrderRole = Literal["entry", "stop", "target"]
OrderEventKind = Literal["accepted", "partial", "filled", "cancelled", "rejected", "notice"]


class Target(NamedTuple):
    price: float
    shares: int


@dataclass(frozen=True, slots=True)
class PlacedOrder:
    order_id: int
    perm_id: int
    parent_id: int
    ref: str
    role: OrderRole
    side: Side
    order_type: str
    shares: float
    limit_price: float | None
    stop_price: float | None
    status: str


@dataclass(frozen=True, slots=True)
class PlacedChain:
    parent_id: int
    legs: tuple[PlacedOrder, ...]
    ref: str

    def of_role(self, role: OrderRole) -> tuple[PlacedOrder, ...]:
        return tuple(leg for leg in self.legs if leg.role == role)


@dataclass(frozen=True, slots=True)
class OrderEvent:
    kind: OrderEventKind
    order_id: int
    perm_id: int
    parent_id: int
    ref: str
    role: OrderRole
    symbol: str
    side: Side
    shares: float
    filled: float
    remaining: float
    avg_price: float | None
    status: str
    code: int | None
    reason: str
    time: float


@dataclass(frozen=True, slots=True)
class IBKRPosition:
    account: str
    symbol: str
    instrument: IBKRInstrument | None
    shares: float
    avg_price: float


@dataclass(frozen=True, slots=True)
class MarginImpact:
    init_margin_change: float | None
    maint_margin_change: float | None
    equity_with_loan_change: float | None
    commission: float | None
    warning: str


@dataclass(frozen=True, slots=True)
class DepthLevel:
    price: float
    size: float
    venue: str


@dataclass(frozen=True, slots=True)
class DepthBook:
    symbol: str
    bids: tuple[DepthLevel, ...]
    asks: tuple[DepthLevel, ...]
    time: float
    smart: bool


@dataclass(frozen=True, slots=True)
class DepthExchange:
    exchange: str
    sec_type: str
    listing_exchange: str
    service_data_type: str
    agg_group: int | None


@dataclass(frozen=True, slots=True)
class TapePrint:
    symbol: str
    time: float
    price: float
    size: float
    venue: str
    conditions: str
    past_limit: bool
    unreported: bool


@dataclass(frozen=True, slots=True)
class TapeQuote:
    symbol: str
    time: float
    bid: float | None
    ask: float | None
    bid_size: float
    ask_size: float


@dataclass(frozen=True, slots=True)
class ContractSpec:
    instrument: IBKRInstrument
    con_id: int
    local_symbol: str
    exchange: str
    trading_class: str
    min_tick: float
    multiplier: float


@dataclass(frozen=True, slots=True)
class OptionChainSpec:
    exchange: str
    underlying_con_id: int
    trading_class: str
    multiplier: float
    expirations: tuple[str, ...]
    strikes: tuple[float, ...]

    def nearest_strikes(self, reference: float, count: int) -> tuple[float, ...]:
        if not self.strikes or count <= 0 or reference <= 0:
            return ()
        ranked = sorted(self.strikes, key=lambda strike: abs(strike - reference))
        return tuple(sorted(ranked[:count]))
