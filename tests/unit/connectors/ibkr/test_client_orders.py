from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ib_async import Contract, LimitOrder, Order, StopOrder
from ib_async.objects import TradeLogEntry
from ib_async.order import OrderStatus, Trade
from ib_async.wrapper import RequestError

from tbot_console.connectors.ibkr import IBKRClient, IBKRConfig, IBKROrder, SecType
from tbot_console.connectors.ibkr.client import UNSET_PRICE, order_from_trade
from tbot_console.core.exceptions import ExchangeConnectionError, ExchangeRequestError

from .conftest import FakeIB


def trade(order: Order, symbol: str = "DEMO", minute: int = 0, filled: float = 0.0) -> Trade:
    contract = Contract(secType="STK", symbol=symbol, currency="USD", exchange="SMART")
    moment = datetime(2026, 10, 7, 14, minute, tzinfo=UTC)
    order.account = order.account or "DU0000001"
    status = OrderStatus(orderId=order.orderId, status="Submitted", filled=filled)
    return Trade(contract, order, status, [], [TradeLogEntry(moment, "Submitted")])


def numbered(order: Order, order_id: int, parent_id: int = 0) -> Order:
    order.orderId, order.permId, order.parentId = order_id, order_id * 10, parent_id
    return order


@pytest.fixture
def ib() -> FakeIB:
    fake = FakeIB()
    fake.connected = True
    return fake


async def test_open_orders_read_as_entry_stop_and_target_in_placing_order(ib: FakeIB):
    entry = numbered(LimitOrder("BUY", 500, 2.5), 1)
    stop = numbered(StopOrder("SELL", 500, 2.3), 2, parent_id=1)
    target = numbered(LimitOrder("SELL", 200, 3.1), 3, parent_id=1)
    ib.open_trades = [trade(target, minute=2), trade(entry, minute=0), trade(stop, minute=1)]
    found = await IBKRClient(IBKRConfig(), ib=ib).fetch_open_orders("demo")
    assert [(o.order_id, o.side, o.order_type) for o in found] == [
        (1, "buy", "LMT"),
        (2, "sell", "STP"),
        (3, "sell", "LMT"),
    ]
    first = found[0]
    assert isinstance(first, IBKROrder)
    assert (first.limit_price, first.stop_price, first.shares, first.status) == (
        2.5,
        None,
        500.0,
        "Submitted",
    )
    assert (found[1].limit_price, found[1].stop_price, found[1].parent_id) == (None, 2.3, 1)
    assert first.instrument is not None and first.instrument.sec_type is SecType.STK
    assert first.placed_at == datetime(2026, 10, 7, 14, 0, tzinfo=UTC).timestamp()
    assert ib.open_order_calls == 1


async def test_orders_of_other_symbols_and_accounts_are_left_out(ib: FakeIB):
    mine = numbered(LimitOrder("BUY", 100, 1.0), 1)
    other = numbered(LimitOrder("BUY", 100, 1.0), 2)
    foreign = numbered(LimitOrder("BUY", 100, 1.0), 3)
    foreign.account = "DU0000002"
    ib.open_trades = [trade(mine), trade(other, symbol="ELSE"), trade(foreign)]
    client = IBKRClient(IBKRConfig(account="DU0000001"), ib=ib)
    assert [o.order_id for o in await client.fetch_open_orders("DEMO")] == [1]
    assert [o.order_id for o in await client.fetch_open_orders()] == [1, 2]


def test_unset_prices_and_unreadable_actions_read_as_nothing():
    market = numbered(Order(action="SELL", totalQuantity=100, orderType="MKT"), 4)
    market.lmtPrice = market.auxPrice = UNSET_PRICE * 10
    read = order_from_trade(trade(market))
    assert read is not None and (read.limit_price, read.stop_price) == (None, None)
    trail = numbered(Order(action="SELL", totalQuantity=100, orderType="TRAIL"), 5)
    trail.auxPrice, trail.trailStopPrice = 0.1, 2.2
    read = order_from_trade(trade(trail))
    assert read is not None and read.stop_price == 2.2
    stop_limit = numbered(Order(action="SELL", totalQuantity=100, orderType="STP LMT"), 6)
    stop_limit.auxPrice, stop_limit.lmtPrice = 2.0, 1.95
    read = order_from_trade(trade(stop_limit))
    assert read is not None and (read.stop_price, read.limit_price) == (2.0, 1.95)
    odd = numbered(Order(action="SSHORT", totalQuantity=100, orderType="LMT", lmtPrice=1.0), 7)
    assert order_from_trade(trade(odd)) is None
    unlogged = trade(numbered(LimitOrder("BUY", 1, 1.0), 8))
    unlogged.log.clear()
    read = order_from_trade(unlogged)
    assert read is not None and read.placed_at is None


async def test_a_refused_or_silent_open_orders_request_is_an_exchange_error(ib: FakeIB):
    ib.raise_on["orders"] = RequestError(-1, 321, "Error validating request")
    with pytest.raises(ExchangeRequestError) as caught:
        await IBKRClient(IBKRConfig(), ib=ib).fetch_open_orders()
    assert caught.value.code == 321
    ib.raise_on["orders"] = TimeoutError()
    with pytest.raises(ExchangeConnectionError):
        await IBKRClient(IBKRConfig(), ib=ib).fetch_open_orders()
