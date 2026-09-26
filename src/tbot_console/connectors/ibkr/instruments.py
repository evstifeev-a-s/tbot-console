from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SecType(str, Enum):
    STK = "STK"
    IND = "IND"
    CASH = "CASH"
    FUT = "FUT"
    CONTFUT = "CONTFUT"
    OPT = "OPT"
    FOP = "FOP"


DATED = frozenset({SecType.FUT, SecType.OPT, SecType.FOP})
STRIKED = frozenset({SecType.OPT, SecType.FOP})
RIGHTS = frozenset({"C", "P"})

DEFAULT_EXCHANGE: dict[SecType, str] = {
    SecType.STK: "SMART",
    SecType.OPT: "SMART",
    SecType.CASH: "IDEALPRO",
}

ROUTE_SEPARATOR = "@"
GRAMMAR = "SYMBOL-CURRENCY-SECTYPE[-EXPIRY[-STRIKE-RIGHT]][@EXCHANGE]"


class InstrumentSyntaxError(ValueError):
    pass


def format_strike(strike: float) -> str:
    return f"{strike:.4f}".rstrip("0").rstrip(".") or "0"


def _parse_strike(text: str, normalized: str) -> float:
    try:
        strike = float(text)
    except ValueError:
        raise InstrumentSyntaxError(f"{normalized!r}: strike {text!r} is not a number") from None
    if strike <= 0:
        raise InstrumentSyntaxError(f"{normalized!r}: strike must be positive, got {strike}")
    return strike


def _validate_expiry(expiry: str, owner: str) -> None:
    if not expiry.isdigit() or len(expiry) not in (6, 8):
        raise InstrumentSyntaxError(f"{owner}: expiry {expiry!r} must be YYYYMM or YYYYMMDD")


@dataclass(frozen=True, slots=True)
class IBKRInstrument:
    sec_type: SecType
    symbol: str
    currency: str = "USD"
    exchange: str = ""
    expiry: str = ""
    strike: float = 0.0
    right: str = ""
    multiplier: str = ""
    trading_class: str = ""

    def __post_init__(self) -> None:
        if not self.symbol:
            raise InstrumentSyntaxError("symbol must not be empty")
        if not self.currency:
            raise InstrumentSyntaxError(f"{self.symbol}: currency must not be empty")

        if self.sec_type in DATED:
            _validate_expiry(self.expiry, self.symbol)
        elif self.expiry:
            raise InstrumentSyntaxError(
                f"{self.symbol}: {self.sec_type.value} carries no expiry, got {self.expiry!r}"
            )

        if self.sec_type in STRIKED:
            if self.strike <= 0:
                raise InstrumentSyntaxError(f"{self.symbol}: option needs a positive strike")
            if self.right not in RIGHTS:
                raise InstrumentSyntaxError(
                    f"{self.symbol}: option right must be C or P, got {self.right!r}"
                )
        elif self.strike or self.right:
            raise InstrumentSyntaxError(
                f"{self.symbol}: {self.sec_type.value} carries no strike or right"
            )

    @property
    def is_option(self) -> bool:
        return self.sec_type in STRIKED

    @property
    def is_dated(self) -> bool:
        return self.sec_type in DATED

    @property
    def effective_exchange(self) -> str:
        return self.exchange or DEFAULT_EXCHANGE.get(self.sec_type, "")

    def to_normalized(self) -> str:
        head = f"{self.symbol}-{self.currency}-{self.sec_type.value}"
        if self.sec_type in STRIKED:
            return f"{head}-{self.expiry}-{format_strike(self.strike)}-{self.right}"
        if self.sec_type in DATED:
            return f"{head}-{self.expiry}"
        return head

    def to_routed(self) -> str:
        normalized = self.to_normalized()
        return f"{normalized}@{self.exchange}" if self.exchange else normalized

    @classmethod
    def parse(cls, normalized: str, exchange: str = "") -> IBKRInstrument:
        text = normalized.strip().upper()
        routed = exchange
        if ROUTE_SEPARATOR in text:
            text, _, suffix = text.partition(ROUTE_SEPARATOR)
            routed = suffix.strip() or exchange

        parts = text.split("-")
        if len(parts) < 3:
            raise InstrumentSyntaxError(f"{normalized!r}: expected {GRAMMAR}")

        symbol, currency, sec_type_text = parts[0], parts[1], parts[2]
        try:
            sec_type = SecType(sec_type_text)
        except ValueError:
            known = ", ".join(s.value for s in SecType)
            raise InstrumentSyntaxError(
                f"{normalized!r}: unknown security type {sec_type_text!r}; expected one of {known}"
            ) from None

        tail = parts[3:]
        expiry, strike, right = "", 0.0, ""
        if sec_type in STRIKED:
            if len(tail) != 3:
                raise InstrumentSyntaxError(
                    f"{normalized!r}: {sec_type.value} needs EXPIRY-STRIKE-RIGHT"
                )
            expiry, right = tail[0], tail[2]
            strike = _parse_strike(tail[1], normalized)
        elif sec_type in DATED:
            if len(tail) != 1:
                raise InstrumentSyntaxError(f"{normalized!r}: {sec_type.value} needs EXPIRY")
            expiry = tail[0]
        elif tail:
            raise InstrumentSyntaxError(
                f"{normalized!r}: {sec_type.value} takes no trailing fields"
            )

        return cls(
            sec_type=sec_type,
            symbol=symbol,
            currency=currency,
            exchange=routed,
            expiry=expiry,
            strike=strike,
            right=right,
        )
