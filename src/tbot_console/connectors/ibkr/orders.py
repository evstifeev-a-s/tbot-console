from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ib_async import Order, Trade
from ib_async.order import OrderStatus
from ib_async.util import UNSET_DOUBLE

from tbot_console.connectors.ibkr.client import STOP_ORDER_TYPES, _order_price, _to_float
from tbot_console.connectors.ibkr.models import ContractSpec, OrderRole, PlacedOrder, Side
from tbot_console.core.exceptions import ConfigurationError, ExchangeRequestError

AccountKind = Literal["paper", "live"]

PAPER_PREFIX = "D"
TIFS = ("DAY", "GTC")
MAX_REF_LENGTH = 64
CENT = 0.01
SUB_CENT = 0.0001
TICK_TOLERANCE = 1e-6
REJECTED_CODE = 201
CANCELLED_CODE = 202
VALIDATION_ERROR_CODE = 321
ANSWERED = frozenset(
    {
        OrderStatus.PreSubmitted,
        OrderStatus.Submitted,
        OrderStatus.Filled,
        OrderStatus.Cancelled,
        OrderStatus.ApiCancelled,
        OrderStatus.Inactive,
        OrderStatus.ValidationError,
    }
)
FAILED = frozenset({OrderStatus.Cancelled, OrderStatus.ApiCancelled, OrderStatus.Inactive})
LIVE_ORDER = frozenset({OrderStatus.PreSubmitted, OrderStatus.Submitted})


class LiveAccountError(ConfigurationError):
    pass


class OrderCheckError(ValueError):
    pass


class OrderRejectedError(ExchangeRequestError):
    pass


def mask_account(account: str) -> str:
    if len(account) <= 5:
        return f"{account[:1]}****"
    return f"{account[:2]}****{account[-3:]}"


def account_kind(accounts: Iterable[str]) -> AccountKind:
    names = [name for name in accounts if name]
    paper = bool(names) and all(name.upper().startswith(PAPER_PREFIX) for name in names)
    return "paper" if paper else "live"


def price_step(price: float, min_tick: float, currency: str = "USD") -> float:
    rule = CENT if price >= 1 else SUB_CENT
    if not math.isfinite(min_tick) or min_tick <= 0:
        return rule
    return max(min_tick, rule) if currency.upper() == "USD" else min_tick


def on_step(price: float, step: float) -> bool:
    units = price / step
    return abs(units - round(units)) <= TICK_TOLERANCE


def snap(price: float, step: float) -> float:
    return round(round(price / step) * step, 10)


def role_of(order: Order) -> OrderRole:
    if not order.parentId:
        return "entry"
    return "stop" if order.orderType.upper() in STOP_ORDER_TYPES else "target"


def checked_side(side: object) -> Side:
    if side == "buy":
        return "buy"
    if side == "sell":
        return "sell"
    raise OrderCheckError(f"side must be 'buy' or 'sell', got {side!r}")


def checked_shares(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise OrderCheckError(f"{name} must be a whole number of shares above zero, got {value!r}")
    return value


def checked_price(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise OrderCheckError(f"{name} must be a price, got {value!r}")
    if not math.isfinite(value) or value <= 0:
        raise OrderCheckError(f"{name} must be a finite price above zero, got {value!r}")
    return float(value)


def checked_tif(tif: object) -> str:
    if tif not in TIFS:
        raise OrderCheckError(
            f"tif must be one of {', '.join(TIFS)}, got {tif!r}: DAY orders expire at the close "
            "(the stop and targets of a bracket too), GTC ones stay until filled or cancelled"
        )
    return str(tif)


def checked_ref(ref: object) -> str:
    if not isinstance(ref, str) or not ref.strip() or len(ref) > MAX_REF_LENGTH:
        raise OrderCheckError(
            f"ref must be a non-empty label of at most {MAX_REF_LENGTH} characters, got {ref!r}"
        )
    return ref


def ib_action(side: Side) -> str:
    return "BUY" if side == "buy" else "SELL"


def check_value(shares: int, price: float, limit: float) -> None:
    value = shares * price
    if value > limit:
        raise OrderCheckError(
            f"order value {shares} × {price:g} = {value:,.2f} is above the {limit:,.2f} limit "
            "this trader was built with"
        )


@dataclass(frozen=True, slots=True)
class Portion:
    shares: int
    target: float | None


@dataclass(frozen=True, slots=True)
class BracketPlan:
    side: Side
    shares: int
    entry: float
    stop: float
    stop_limit: float | None
    portions: tuple[Portion, ...]

    def prices(self) -> list[tuple[str, float]]:
        named = [("entry", self.entry), ("stop", self.stop)]
        if self.stop_limit is not None:
            named.append(("stop_limit", self.stop_limit))
        named += [
            (f"target {index}", portion.target)
            for index, portion in enumerate(self.portions, 1)
            if portion.target is not None
        ]
        return named


def plan_bracket(
    side: object,
    shares: object,
    entry: object,
    stop: object,
    targets: Sequence[tuple[float, int]],
    *,
    stop_limit: object = None,
    outside_rth: bool = False,
    max_order_value: float,
) -> BracketPlan:
    direction = checked_side(side)
    total = checked_shares("shares", shares)
    entry_price = checked_price("entry", entry)
    stop_price = checked_price("stop", stop)
    limit = None if stop_limit is None else checked_price("stop_limit", stop_limit)
    legs = [
        (
            checked_price(f"target {index} price", price),
            checked_shares(f"target {index} shares", count),
        )
        for index, (price, count) in enumerate(targets, 1)
    ]
    aimed = sum(count for _, count in legs)
    if aimed > total:
        raise OrderCheckError(
            f"the targets add up to {aimed} shares, more than the {total} of the entry"
        )
    buying = direction == "buy"
    if buying and not stop_price < entry_price:
        raise OrderCheckError(
            f"a buy's stop must be below its entry: stop {stop_price:g}, entry {entry_price:g}"
        )
    if not buying and not stop_price > entry_price:
        raise OrderCheckError(
            f"a sell's stop must be above its entry: stop {stop_price:g}, entry {entry_price:g}"
        )
    for index, (price, _) in enumerate(legs, 1):
        if buying and not price > entry_price:
            raise OrderCheckError(
                f"a buy's target must be above its entry: target {index} {price:g}, "
                f"entry {entry_price:g}"
            )
        if not buying and not price < entry_price:
            raise OrderCheckError(
                f"a sell's target must be below its entry: target {index} {price:g}, "
                f"entry {entry_price:g}"
            )
    if limit is not None and buying and limit > stop_price:
        raise OrderCheckError(
            f"stop_limit is the worst price the stop may sell at, so for a buy it must be at "
            f"or below the stop: stop_limit {limit:g}, stop {stop_price:g}"
        )
    if limit is not None and not buying and limit < stop_price:
        raise OrderCheckError(
            f"stop_limit is the worst price the stop may buy back at, so for a sell it must be "
            f"at or above the stop: stop_limit {limit:g}, stop {stop_price:g}"
        )
    if outside_rth and limit is None:
        raise OrderCheckError(
            "outside regular hours IBKR does not trigger a market stop on a US stock: pass "
            "stop_limit so the stop goes out as a stop-limit order (STP LMT)"
        )
    check_value(total, entry_price, max_order_value)
    portions = [Portion(count, price) for price, count in legs]
    if aimed < total:
        portions.append(Portion(total - aimed, None))
    return BracketPlan(direction, total, entry_price, stop_price, limit, tuple(portions))


def check_ticks(spec: ContractSpec, currency: str, prices: Iterable[tuple[str, float]]) -> None:
    for name, price in prices:
        step = price_step(price, spec.min_tick, currency)
        if not on_step(price, step):
            raise OrderCheckError(
                f"{name} {price:g} is off the price step of {spec.instrument.symbol}: at this "
                f"price it moves in steps of {step:g}"
            )


def parse_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number == UNSET_DOUBLE:
        return None
    return number


def failure_of(trade: Trade) -> tuple[int | None, str]:
    for entry in reversed(trade.log):
        if entry.errorCode:
            return entry.errorCode, entry.message
    return None, trade.orderStatus.whyHeld or trade.orderStatus.status


def is_answered(trade: Trade) -> bool:
    return trade.orderStatus.status in ANSWERED or bool(trade.log and trade.log[-1].errorCode)


def placed_from(trade: Trade) -> PlacedOrder:
    order, kind = trade.order, trade.order.orderType.upper()
    return PlacedOrder(
        order_id=order.orderId,
        perm_id=order.permId or trade.orderStatus.permId,
        parent_id=order.parentId,
        ref=order.orderRef,
        role=role_of(order),
        side="buy" if order.action.upper() == "BUY" else "sell",
        order_type=kind,
        shares=_to_float(order.totalQuantity),
        limit_price=_order_price(order.lmtPrice) if "LMT" in kind else None,
        stop_price=_order_price(order.auxPrice) if kind in STOP_ORDER_TYPES else None,
        status=trade.orderStatus.status,
    )


def described(order: Order) -> dict[str, Any]:
    kind = order.orderType.upper()
    return {
        "order_id": order.orderId,
        "parent_id": order.parentId,
        "role": role_of(order),
        "action": order.action,
        "type": kind,
        "shares": _to_float(order.totalQuantity),
        "limit": _order_price(order.lmtPrice) if "LMT" in kind else None,
        "stop": _order_price(order.auxPrice) if kind in STOP_ORDER_TYPES else None,
        "tif": order.tif,
        "outside_rth": order.outsideRth,
        "transmit": order.transmit,
    }
