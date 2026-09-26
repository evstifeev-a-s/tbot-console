from dataclasses import dataclass
from enum import Enum


class Timeframe(str, Enum):
    M1 = "1"
    M5 = "5"
    M15 = "15"
    H1 = "60"
    H4 = "240"
    D1 = "1D"
    W1 = "1W"


TIMEFRAME_SECONDS: dict[Timeframe, int] = {
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
    Timeframe.D1: 86400,
    Timeframe.W1: 604800,
}


class TrendType(str, Enum):
    UPTREND = "uptrend"
    DOWNTREND = "downtrend"
    SIDEWAYS = "sideways"


@dataclass(slots=True)
class Candle:
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(slots=True)
class TrendAnalysis:
    trend_type: TrendType
    strength: float
    slope: float
    r_squared: float
    adx: float


@dataclass(slots=True)
class Channel:
    upper: float
    lower: float
    middle: float
    slope: float
    width_pct: float
    middle_start: float = 0.0
    start_ts: float = 0.0
    end_ts: float = 0.0
    bars: int = 0
    bar_seconds: float = 0.0


@dataclass(slots=True)
class PriceLevel:
    """Уровень как зона: core — граница тел (торгуемая), outer — экстремум
    теней (инвалидация/стопы), по рекомендациям S/R-практики: тела задают
    уровень, «проколы» — только хвост зоны."""

    price: float
    outer: float
    touches: int
    strength: float
    kind: str = ""
    last_touch_ts: float = 0.0


@dataclass(slots=True)
class SupportResistance:
    support: PriceLevel | None
    resistance: PriceLevel | None


@dataclass(slots=True)
class BreakoutSignal:
    level: float
    direction: str
    confirmed: bool
    close_confirmed: bool
    volume_confirmed: bool
    retest_confirmed: bool


@dataclass(slots=True)
class TimeframeAnalysis:
    timeframe: Timeframe
    trend: TrendAnalysis
    channel: Channel
    breakout: BreakoutSignal | None
    candles: list[Candle]
    levels: SupportResistance | None = None
