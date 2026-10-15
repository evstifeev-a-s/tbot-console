from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from ib_async import StopOrder

from tbot_console.analysis.models import Timeframe
from tbot_console.connectors.ibkr.instruments import IBKRInstrument
from tbot_console.connectors.ibkr.models import OrderEvent
from tbot_console.connectors.ibkr.trader import IBKRTrader

from .conftest import paper_config

pytestmark = [pytest.mark.integration, pytest.mark.private]

PERMISSION_ENV = "IBKR_PAPER_ORDER_TESTS"
AAPL = IBKRInstrument.parse("AAPL-USD-STK")
ANSWER_TIMEOUT = 15.0
OUTSIDE_RTH_IGNORED = 2109


@pytest.fixture
async def trader(tmp_path: Path) -> AsyncIterator[IBKRTrader]:
    if os.getenv(PERMISSION_ENV) != "1":
        pytest.skip(
            f"sends orders to the paper account: run only with the operator's explicit "
            f"permission, {PERMISSION_ENV}=1"
        )
    connected = IBKRTrader(
        await paper_config(), max_order_value=1_000, journal=tmp_path / "journal.jsonl"
    )
    await connected.connect()
    assert connected.account_kind == "paper"
    try:
        yield connected
    finally:
        for trade in connected.client.ib.openTrades():
            if trade.order.clientId == connected.config.client_id:
                connected.client.ib.cancelOrder(trade.order)
        await asyncio.sleep(1.0)
        await connected.disconnect()


async def far_below(trader: IBKRTrader, share: float) -> float:
    bars = await trader.client.fetch_bars(AAPL, Timeframe.D1, count=1)
    if not bars:
        pytest.skip("IBKR sent no daily bar of AAPL to price the test orders from")
    return round(bars[-1].close * share, 2)


class Seen:
    def __init__(self) -> None:
        self.items: list[Any] = []
        self.changed = asyncio.Event()

    def __call__(self, item: Any) -> None:
        self.items.append(item)
        self.changed.set()

    async def until(self, wanted: Callable[[list[Any]], bool]) -> None:
        try:
            async with asyncio.timeout(ANSWER_TIMEOUT):
                while not wanted(self.items):
                    self.changed.clear()
                    await self.changed.wait()
        except TimeoutError:
            pytest.fail(f"IBKR did not answer within {ANSWER_TIMEOUT:g} s: {self.items}")


def cancelled(order_ids: set[int]) -> Callable[[list[OrderEvent]], bool]:
    return lambda events: {e.order_id for e in events if e.kind == "cancelled"} >= order_ids


async def test_a_limit_far_below_the_market_is_accepted_moved_and_cancelled(
    trader: IBKRTrader,
):
    seen = Seen()
    trader.watch_orders(seen)
    entry = await far_below(trader, 0.5)
    placed = await trader.place_limit(AAPL, "buy", 1, entry, ref=f"it-limit-{time.time():.0f}")
    assert placed.status in ("PreSubmitted", "Submitted")

    moved = await trader.modify(placed.order_id, price=round(entry - 0.01, 2))
    assert moved.limit_price == round(entry - 0.01, 2)

    await trader.cancel(placed.order_id)
    await seen.until(cancelled({placed.order_id}))


async def test_a_two_target_bracket_goes_out_as_two_chains(trader: IBKRTrader):
    seen = Seen()
    trader.watch_orders(seen)
    entry = await far_below(trader, 0.5)
    ref = f"it-bracket-{time.time():.0f}"
    chain = await trader.place_bracket(
        AAPL,
        "buy",
        3,
        entry,
        round(entry * 0.9, 2),
        [(round(entry * 1.1, 2), 2), (round(entry * 1.2, 2), 1)],
        stop_limit=round(entry * 0.89, 2),
        ref=ref,
    )
    assert [leg.role for leg in chain.legs] == ["entry", "target", "stop"] * 2
    assert all(leg.status in ("PreSubmitted", "Submitted") for leg in chain.legs)

    listed = {o.order_id: o for o in await trader.client.fetch_open_orders("AAPL")}
    for leg in chain.legs:
        assert listed[leg.order_id].ref == ref
        assert listed[leg.order_id].parent_id == leg.parent_id

    await trader.cancel_chain(chain.parent_id)
    await seen.until(cancelled({leg.order_id for leg in chain.legs}))


async def test_ib_ignores_outside_rth_on_a_stop_market_order(trader: IBKRTrader):
    codes = Seen()
    ib = trader.client.ib

    def collect(req_id: int, code: int, message: str, contract: Any) -> None:
        codes(code)

    ib.errorEvent += collect
    try:
        contract = await trader.client._qualified_contract(AAPL)
        stop = StopOrder("SELL", 1, await far_below(trader, 0.3), outsideRth=True, tif="DAY")
        stop.account = ib.managedAccounts()[0]
        ib.placeOrder(contract, stop)
        await codes.until(lambda seen: OUTSIDE_RTH_IGNORED in seen)
    finally:
        ib.errorEvent -= collect
