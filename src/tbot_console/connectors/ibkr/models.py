from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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
