from __future__ import annotations

import asyncio
import inspect
import logging
import os
from pathlib import Path
from typing import Any

import pytest
from ib_async import Contract
from ib_async.ib import StartupFetchALL
from ib_async.objects import Position
from ib_async.order import Order, OrderState, Trade
from ib_async.util import UNSET_DOUBLE

from tbot_console.connectors.ibkr import trader as trader_module
from tbot_console.connectors.ibkr.config import LIVE_TRADING_ENV, IBKRConfig
from tbot_console.connectors.ibkr.instruments import IBKRInstrument
from tbot_console.connectors.ibkr.journal import read_journal
from tbot_console.connectors.ibkr.models import (
    IBKRPosition,
    MarginImpact,
    OrderEvent,
    PlacedChain,
    Target,
)
from tbot_console.connectors.ibkr.orders import (
    LiveAccountError,
    OrderCheckError,
    OrderRejectedError,
    account_kind,
    mask_account,
    parse_number,
    price_step,
)
from tbot_console.connectors.ibkr.trader import IBKRTrader
from tbot_console.core.exceptions import (
    ConfigurationError,
    ExchangeConnectionError,
    ExchangeRequestError,
)

from .conftest import FakeIB, make_contract, make_details

DEMO = IBKRInstrument.parse("DEMO-USD-STK")
CHEAP = IBKRInstrument.parse("CHEAP-USD-STK")
LOOSE = IBKRInstrument.parse("LOOSE-USD-STK")
OPTION = IBKRInstrument.parse("DEMO-USD-OPT-20261218-5-C")
ACCOUNT = "DU1234567"
CLIENT_ID = 31


@pytest.fixture
def ib() -> FakeIB:
    fake = FakeIB()
    fake.details_by_symbol = {
        "DEMO": [make_details(make_contract("DEMO", 4391), min_tick=0.01)],
        "CHEAP": [make_details(make_contract("CHEAP", 7001), min_tick=0.0001)],
        "LOOSE": [make_details(make_contract("LOOSE", 7002), min_tick=0.0)],
    }
    return fake


@pytest.fixture
def journal(tmp_path: Path) -> Path:
    return tmp_path / "orders" / "journal.jsonl"


def build(ib: FakeIB, journal: Path, **config: Any) -> IBKRTrader:
    return IBKRTrader(
        IBKRConfig(client_id=CLIENT_ID, **config), max_order_value=5_000, journal=journal, ib=ib
    )


@pytest.fixture
async def trader(ib: FakeIB, journal: Path) -> IBKRTrader:
    connected = build(ib, journal)
    await connected.connect()
    return connected


def events_of(path: Path, event: str) -> list[dict[str, Any]]:
    return [record for record in read_journal(path) if record["event"] == event]


def by_id(ib: FakeIB, order_id: int) -> Trade:
    return ib.own_trades[order_id]


async def two_target_chain(trader: IBKRTrader, ref: str = "sig-1") -> PlacedChain:
    return await trader.place_bracket(
        DEMO, "buy", 100, 2.50, 2.30, [Target(2.80, 60), Target(3.10, 40)], ref=ref
    )


class TestAccountLock:
    async def test_a_paper_account_connects_with_the_full_startup_sync(
        self, trader: IBKRTrader, ib: FakeIB, journal: Path
    ):
        assert trader.account_kind == "paper"
        assert trader.account == "DU****567"
        assert ib.connect_kwargs["readonly"] is False
        assert ib.connect_kwargs["fetchFields"] == StartupFetchALL
        assert ib.connect_kwargs["clientId"] == CLIENT_ID
        (connect,) = events_of(journal, "connect")
        assert connect["account"] == "DU****567" and connect["kind"] == "paper"
        assert ACCOUNT not in journal.read_text(encoding="utf-8")

    async def test_a_live_account_without_the_switch_is_refused_and_disconnected(
        self, ib: FakeIB, journal: Path
    ):
        ib.accounts = ["U7654321"]
        trader = build(ib, journal)
        with pytest.raises(LiveAccountError, match="is live") as refused:
            await trader.connect()
        assert isinstance(refused.value, ConfigurationError)
        assert "U7****321" in str(refused.value) and "U7654321" not in str(refused.value)
        assert "Разрешить заявки на живом счёте" in str(refused.value)
        assert not ib.connected
        assert trader.account_kind is None and trader.account is None
        assert events_of(journal, "refused")[0]["action"] == "connect"
        with pytest.raises(ConfigurationError, match="not connected"):
            await two_target_chain(trader)
        assert ib.placed == []

    async def test_a_live_account_with_the_switch_connects_loudly(
        self, ib: FakeIB, journal: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        monkeypatch.setenv(LIVE_TRADING_ENV, "true")
        ib.accounts = ["U7654321"]
        trader = build(ib, journal)
        with caplog.at_level(logging.WARNING):
            await trader.connect()
        assert trader.live_allowed and trader.account_kind == "live"
        assert "LIVE account U7****321" in caplog.text and "real money" in caplog.text

    async def test_mixed_accounts_count_as_live(self, ib: FakeIB, journal: Path):
        ib.accounts = ["DU1111111", "U2222222"]
        with pytest.raises(LiveAccountError):
            await build(ib, journal, account="DU1111111").connect()
        assert account_kind(["DU1", "DF2"]) == "paper"
        assert account_kind(["DU1", "U2"]) == "live"
        assert account_kind([]) == "live"

    async def test_the_configured_account_must_be_one_the_login_sees(
        self, ib: FakeIB, journal: Path
    ):
        with pytest.raises(ConfigurationError, match="DU\\*\\*\\*\\*777 is not among") as refused:
            await build(ib, journal, account="DU7777777").connect()
        assert not isinstance(refused.value, LiveAccountError)
        assert not ib.connected

    async def test_several_accounts_need_ibkr_account(self, ib: FakeIB, journal: Path):
        ib.accounts = ["DU1111111", "DU2222222"]
        with pytest.raises(ConfigurationError, match="set IBKR_ACCOUNT"):
            await build(ib, journal).connect()
        chosen = build(ib, journal, account="DU2222222")
        await chosen.connect()
        await chosen.place_limit(DEMO, "buy", 10, 2.5, ref="one")
        assert {order.account for order in ib.placed} == {"DU2222222"}

    async def test_the_check_runs_again_after_every_reconnect(
        self, trader: IBKRTrader, ib: FakeIB, journal: Path
    ):
        ib.disconnect()
        assert trader.account_kind is None
        assert events_of(journal, "disconnect")[0]["account"] == "DU****567"
        with pytest.raises(ConfigurationError, match="not connected"):
            await trader.place_limit(DEMO, "buy", 10, 2.5, ref="after-drop")
        ib.accounts = ["U7654321"]
        with pytest.raises(LiveAccountError):
            await trader.connect()
        ib.accounts = [ACCOUNT]
        await trader.connect()
        assert trader.account_kind == "paper"

    async def test_a_connection_opened_elsewhere_is_reopened_with_the_check(
        self, ib: FakeIB, journal: Path
    ):
        trader = build(ib, journal)
        await trader.client.connect()
        assert ib.connect_kwargs["fetchFields"] != StartupFetchALL
        await trader.connect()
        assert ib.connect_kwargs["fetchFields"] == StartupFetchALL
        assert trader.account_kind == "paper"

    def test_the_port_gate_still_applies(
        self, ib: FakeIB, journal: Path, monkeypatch: pytest.MonkeyPatch
    ):
        with pytest.raises(ConfigurationError, match=LIVE_TRADING_ENV):
            build(ib, journal, port=7496)
        monkeypatch.setenv(LIVE_TRADING_ENV, "true")
        assert build(ib, journal, port=7496).config.readonly is False

    def test_a_trader_needs_a_positive_order_limit(self, ib: FakeIB, journal: Path):
        with pytest.raises(TypeError):
            IBKRTrader(IBKRConfig(), journal=journal, ib=ib)
        for bad in (0, -1, float("nan"), float("inf"), True):
            with pytest.raises(ConfigurationError, match="max_order_value"):
                IBKRTrader(IBKRConfig(), max_order_value=bad, journal=journal, ib=ib)

    def test_account_numbers_are_masked(self):
        assert mask_account("DU1234567") == "DU****567"
        assert mask_account("U12") == "U****"


class TestChainShape:
    async def test_two_targets_make_two_independent_chains(self, trader: IBKRTrader, ib: FakeIB):
        chain = await two_target_chain(trader)
        orders = ib.placed
        shape = [
            (o.action, o.orderType, o.totalQuantity, o.lmtPrice, o.auxPrice, o.transmit)
            for o in orders
        ]
        unset = Order().lmtPrice
        assert shape == [
            ("BUY", "LMT", 60, 2.5, unset, False),
            ("SELL", "LMT", 60, 2.8, unset, False),
            ("SELL", "STP", 60, unset, 2.3, True),
            ("BUY", "LMT", 40, 2.5, unset, False),
            ("SELL", "LMT", 40, 3.1, unset, False),
            ("SELL", "STP", 40, unset, 2.3, True),
        ]
        first, second = orders[0].orderId, orders[3].orderId
        assert [o.parentId for o in orders] == [0, first, first, 0, second, second]
        assert {o.orderRef for o in orders} == {"sig-1"}
        assert {o.account for o in orders} == {ACCOUNT}
        assert {(o.tif, o.outsideRth, o.ocaGroup) for o in orders} == {("DAY", False, "")}
        assert chain.parent_id == first and chain.ref == "sig-1"
        assert [leg.role for leg in chain.legs] == ["entry", "target", "stop"] * 2
        assert [leg.shares for leg in chain.of_role("stop")] == [60, 40]
        assert {leg.status for leg in chain.of_role("entry")} == {"Submitted"}
        assert {leg.status for leg in chain.of_role("target")} == {"PreSubmitted"}
        assert all(leg.perm_id for leg in chain.legs)

    async def test_shares_without_a_target_keep_only_their_stop(
        self, trader: IBKRTrader, ib: FakeIB
    ):
        chain = await trader.place_bracket(DEMO, "buy", 100, 2.50, 2.30, [(2.80, 60)], ref="s")
        assert [(leg.role, leg.shares) for leg in chain.legs] == [
            ("entry", 60),
            ("target", 60),
            ("stop", 60),
            ("entry", 40),
            ("stop", 40),
        ]
        alone = await trader.place_bracket(DEMO, "buy", 50, 2.50, 2.30, ref="bare")
        assert [(leg.role, leg.shares) for leg in alone.legs] == [("entry", 50), ("stop", 50)]
        assert [o.transmit for o in ib.placed[-2:]] == [False, True]

    async def test_a_stop_limit_turns_the_stop_into_stp_lmt(self, trader: IBKRTrader, ib: FakeIB):
        chain = await trader.place_bracket(
            DEMO, "buy", 100, 2.50, 2.30, [(2.80, 100)], stop_limit=2.25, ref="sl"
        )
        stop = ib.placed[-1]
        assert (stop.orderType, stop.auxPrice, stop.lmtPrice) == ("STP LMT", 2.3, 2.25)
        assert (chain.legs[-1].stop_price, chain.legs[-1].limit_price) == (2.3, 2.25)

    async def test_a_sell_mirrors_a_buy(self, trader: IBKRTrader, ib: FakeIB):
        await trader.place_bracket(
            DEMO, "sell", 100, 2.50, 2.70, [(2.20, 100)], stop_limit=2.75, tif="GTC", ref="short"
        )
        assert [(o.action, o.orderType) for o in ib.placed] == [
            ("SELL", "LMT"),
            ("BUY", "LMT"),
            ("BUY", "STP LMT"),
        ]
        assert {o.tif for o in ib.placed} == {"GTC"}

    async def test_outside_regular_hours_needs_a_stop_limit(self, trader: IBKRTrader, ib: FakeIB):
        with pytest.raises(OrderCheckError, match="does not trigger a market stop"):
            await trader.place_bracket(
                DEMO, "buy", 100, 2.5, 2.3, [(2.8, 100)], outside_rth=True, ref="x"
            )
        await trader.place_bracket(
            DEMO, "buy", 100, 2.5, 2.3, [(2.8, 100)], stop_limit=2.25, outside_rth=True, ref="x"
        )
        assert {o.outsideRth for o in ib.placed} == {True}

    async def test_the_first_target_lifts_only_its_own_stop(self, trader: IBKRTrader, ib: FakeIB):
        events: list[OrderEvent] = []
        trader.watch_orders(events.append)
        chain = await two_target_chain(trader)
        entry_1, target_1, stop_1, entry_2, target_2, stop_2 = (leg.order_id for leg in chain.legs)
        ib.fill(entry_1, 60, 2.50)
        ib.fill(entry_2, 40, 2.50)
        events.clear()
        ib.fill(target_1, 60, 2.80)

        assert by_id(ib, stop_1).orderStatus.status == "Cancelled"
        for working in (target_2, stop_2):
            trade = by_id(ib, working)
            assert trade.orderStatus.status == "Submitted"
            assert trade.order.totalQuantity == 40
        assert [(e.kind, e.order_id) for e in events] == [
            ("filled", target_1),
            ("cancelled", stop_1),
        ]

        ib.fill(target_2, 15, 3.10)
        assert by_id(ib, stop_2).order.totalQuantity == 25
        assert events[-1].kind == "partial" and events[-1].filled == 15


class TestChecksBeforeSending:
    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"side": "long"}, "side must be"),
            ({"shares": 0}, "whole number of shares"),
            ({"shares": 10.5}, "whole number of shares"),
            ({"shares": True}, "whole number of shares"),
            ({"entry": float("nan")}, "finite price"),
            ({"entry": -2.5}, "finite price"),
            ({"stop": float("inf")}, "finite price"),
            ({"entry": "2.5"}, "must be a price"),
            ({"targets": [(2.8, 0)]}, "target 1 shares"),
            ({"targets": [(0.0, 10)]}, "target 1 price"),
            ({"stop": 2.6}, "stop must be below its entry"),
            ({"targets": [(2.4, 10)]}, "target must be above its entry"),
            ({"targets": [(2.8, 60), (3.1, 50)]}, "add up to 110 shares"),
            ({"stop_limit": 2.35}, "at or below the stop"),
            ({"shares": 3000}, "above the 5,000.00 limit"),
            ({"tif": "IOC"}, "tif must be one of DAY, GTC"),
            ({"ref": ""}, "ref must be"),
            ({"ref": "x" * 65}, "ref must be"),
            ({"entry": 2.505}, "off the price step of DEMO"),
            ({"stop_limit": 2.255}, "off the price step"),
        ],
    )
    async def test_a_bad_bracket_is_refused_before_anything_is_sent(
        self, trader: IBKRTrader, ib: FakeIB, journal: Path, change: dict[str, Any], message: str
    ):
        request: dict[str, Any] = {
            "side": "buy",
            "shares": 100,
            "entry": 2.50,
            "stop": 2.30,
            "targets": [(2.80, 60), (3.10, 40)],
            "stop_limit": None,
            "tif": "DAY",
            "ref": "sig-1",
            **change,
        }
        with pytest.raises(OrderCheckError, match=message):
            await trader.place_bracket(
                DEMO,
                request["side"],
                request["shares"],
                request["entry"],
                request["stop"],
                request["targets"],
                stop_limit=request["stop_limit"],
                tif=request["tif"],
                ref=request["ref"],
            )
        assert ib.placed == []
        assert events_of(journal, "refused")[-1]["action"] == "place_bracket"

    async def test_a_sell_checks_its_sides(self, trader: IBKRTrader):
        with pytest.raises(OrderCheckError, match="stop must be above its entry"):
            await trader.place_bracket(DEMO, "sell", 10, 2.5, 2.4, ref="s")
        with pytest.raises(OrderCheckError, match="target must be below its entry"):
            await trader.place_bracket(DEMO, "sell", 10, 2.5, 2.7, [(2.6, 10)], ref="s")
        with pytest.raises(OrderCheckError, match="at or above the stop"):
            await trader.place_bracket(DEMO, "sell", 10, 2.5, 2.7, stop_limit=2.65, ref="s")

    async def test_only_stocks_are_traded(self, trader: IBKRTrader, ib: FakeIB):
        with pytest.raises(OrderCheckError, match="stock orders \\(STK\\) only; OPT"):
            await trader.place_limit(OPTION, "buy", 1, 1.0, ref="opt")
        assert ib.detail_calls == []

    async def test_the_price_step_follows_min_tick_and_the_us_rule(
        self, trader: IBKRTrader, ib: FakeIB
    ):
        await trader.place_limit(CHEAP, "buy", 100, 0.5123, ref="sub-dollar")
        with pytest.raises(OrderCheckError, match="steps of 0.01"):
            await trader.place_limit(CHEAP, "buy", 100, 1.2345, ref="above-a-dollar")
        await trader.place_limit(LOOSE, "buy", 100, 0.5001, ref="fallback-cent")
        with pytest.raises(OrderCheckError, match="steps of 0.01"):
            await trader.place_limit(LOOSE, "buy", 100, 1.005, ref="fallback-dollar")
        assert price_step(2.5, 0.01) == 0.01
        assert price_step(2.5, 0.0001) == 0.01
        assert price_step(0.5, 0.01) == 0.01
        assert price_step(0.5, 0.0001) == 0.0001
        assert price_step(0.5, 0.0) == 0.0001
        assert price_step(2.5, 0.005, "EUR") == 0.005

    async def test_a_ref_already_working_is_refused_even_after_a_reconnect(
        self, trader: IBKRTrader, ib: FakeIB
    ):
        await two_target_chain(trader)
        sent = len(ib.placed)
        with pytest.raises(OrderCheckError, match="'sig-1' already has working orders"):
            await two_target_chain(trader)
        await trader.disconnect()
        await trader.connect()
        with pytest.raises(OrderCheckError, match="already has working orders"):
            await trader.place_limit(DEMO, "buy", 10, 2.5, ref="sig-1")
        assert len(ib.placed) == sent

        foreign = Order(orderId=7, clientId=5, action="BUY", totalQuantity=1, orderType="LMT")
        foreign.orderRef, foreign.account = "sig-2", ACCOUNT
        ib.open_trades = [Trade(Contract(symbol="DEMO"), foreign)]
        ib.open_trades[0].orderStatus.status = "Submitted"
        with pytest.raises(OrderCheckError, match="'sig-2' already has working orders \\(7\\)"):
            await trader.place_limit(DEMO, "buy", 10, 2.5, ref="sig-2")

        await trader.cancel_chain(ib.placed[0].orderId)
        await two_target_chain(trader)


class TestRejection:
    async def test_a_rejected_stop_withdraws_the_chain(self, trader: IBKRTrader, ib: FakeIB):
        events: list[OrderEvent] = []
        trader.watch_orders(events.append)
        ib.reject_when = [
            (
                lambda o: o.orderType == "STP" and o.totalQuantity == 40,
                201,
                "Order rejected - reason: no shortable",
            )
        ]
        with pytest.raises(OrderRejectedError, match="rejected the stop order") as refused:
            await two_target_chain(trader)
        assert refused.value.code == 201
        assert "no shortable" in refused.value.reason
        entries = [o.orderId for o in ib.placed if not o.parentId]
        assert ib.cancel_calls == entries
        assert all(t.isDone() for t in ib.own_trades.values())
        rejected = [e for e in events if e.kind == "rejected"]
        assert [(e.role, e.code) for e in rejected] == [("stop", 201)]
        assert "no shortable" in rejected[0].reason

    async def test_a_filled_portion_keeps_its_guard_when_a_later_one_fails(
        self, trader: IBKRTrader, ib: FakeIB, monkeypatch: pytest.MonkeyPatch
    ):
        ib.reject_when = [(lambda o: o.totalQuantity == 40 and not o.parentId, 201, "rejected")]
        original = ib.transmit

        def fill_first_then_answer() -> None:
            original()
            first = ib.placed[0].orderId
            if by_id(ib, first).orderStatus.filled == 0:
                ib.fill(first, 60, 2.5)

        monkeypatch.setattr(ib, "transmit", fill_first_then_answer)
        with pytest.raises(OrderRejectedError, match="kept, as they guard shares"):
            await two_target_chain(trader)
        target_1, stop_1 = ib.placed[1].orderId, ib.placed[2].orderId
        assert by_id(ib, target_1).orderStatus.status == "Submitted"
        assert by_id(ib, stop_1).orderStatus.status == "Submitted"

    async def test_an_unanswered_chain_comes_back_pending(self, ib: FakeIB, journal: Path, caplog):
        ib.answer_orders = False
        quick = build(ib, journal, timeout=0.05)
        await quick.connect()
        with caplog.at_level(logging.WARNING):
            chain = await quick.place_bracket(DEMO, "buy", 10, 2.5, 2.3, ref="slow")
        assert {leg.status for leg in chain.legs} == {"PendingSubmit"}
        assert "has not confirmed orders" in caplog.text
        assert events_of(journal, "unconfirmed")[0]["ref"] == "slow"


class TestModifyAndCancel:
    async def test_the_stop_moves_to_breakeven(self, trader: IBKRTrader, ib: FakeIB, journal: Path):
        chain = await two_target_chain(trader)
        stop_2 = chain.legs[5].order_id
        ib.fill(chain.legs[3].order_id, 40, 2.50)
        moved = await trader.modify(stop_2, stop=2.50)
        sent = ib.placed[-1]
        assert (sent.orderId, sent.auxPrice, sent.transmit) == (stop_2, 2.5, True)
        assert moved.stop_price == 2.5 and moved.role == "stop"
        assert events_of(journal, "request")[-1]["action"] == "modify"

    async def test_a_change_is_checked_before_it_is_sent(self, trader: IBKRTrader, ib: FakeIB):
        chain = await two_target_chain(trader)
        entry, target, stop = (leg.order_id for leg in chain.legs[:3])
        sent = len(ib.placed)
        for order_id, change, message in [
            (stop, {"price": 2.2}, "has no limit price"),
            (target, {"stop": 2.2}, "has no stop price"),
            (target, {"price": 2.805}, "off the price step"),
            (entry, {"shares": 3000}, "above the 5,000.00 limit"),
            (entry, {}, "needs a new price, stop or shares"),
            (999, {"price": 2.0}, "not a working order of this client"),
        ]:
            with pytest.raises(OrderCheckError, match=message):
                await trader.modify(order_id, **change)
        ib.fill(entry, 30, 2.5)
        with pytest.raises(OrderCheckError, match="30 shares filled already"):
            await trader.modify(entry, shares=30)
        assert len(ib.placed) == sent

    async def test_a_stop_limit_must_not_end_up_better_than_its_stop(self, trader: IBKRTrader):
        chain = await trader.place_bracket(DEMO, "buy", 10, 2.5, 2.3, stop_limit=2.25, ref="sl")
        with pytest.raises(OrderCheckError, match="would be better than its stop"):
            await trader.modify(chain.legs[-1].order_id, stop=2.2)
        moved = await trader.modify(chain.legs[-1].order_id, stop=2.2, price=2.15)
        assert (moved.stop_price, moved.limit_price) == (2.2, 2.15)

    async def test_a_refused_change_raises_with_the_ib_code(self, trader: IBKRTrader, ib: FakeIB):
        chain = await two_target_chain(trader)
        ib.modify_error = (110, "The price does not conform to the minimum price variation")
        with pytest.raises(ExchangeRequestError, match="did not apply the change") as refused:
            await trader.modify(chain.legs[1].order_id, price=2.9)
        assert refused.value.code == 110

    async def test_a_child_never_covers_more_than_its_entry(self, trader: IBKRTrader, ib: FakeIB):
        chain = await two_target_chain(trader)
        target_1, stop_1 = chain.legs[1].order_id, chain.legs[2].order_id
        sent = len(ib.placed)
        for order_id in (stop_1, target_1):
            with pytest.raises(OrderCheckError, match="may cover at most 60 shares, got 50000"):
                await trader.modify(order_id, shares=50_000)
        assert len(ib.placed) == sent
        assert (await trader.modify(stop_1, shares=50)).shares == 50

    async def test_a_refused_change_leaves_the_order_working(self, trader: IBKRTrader, ib: FakeIB):
        events: list[OrderEvent] = []
        trader.watch_orders(events.append)
        placed = await trader.place_limit(DEMO, "buy", 10, 2.5, ref="r")
        ib.modify_refusal = (201, "Order rejected - reason: the price is out of range")
        with pytest.raises(ExchangeRequestError, match="did not apply the change") as refused:
            await trader.modify(placed.order_id, price=2.4)
        assert refused.value.code == 201
        assert [(e.kind, e.code) for e in events] == [("accepted", None), ("notice", 201)]
        assert not by_id(ib, placed.order_id).isDone()
        ib.fill(placed.order_id, 10, 2.5)
        assert [(e.kind, e.filled) for e in events[2:]] == [("filled", 10)]

    async def test_a_refused_change_can_still_be_cancelled(self, trader: IBKRTrader, ib: FakeIB):
        placed = await trader.place_limit(DEMO, "buy", 10, 2.5, ref="r")
        ib.modify_refusal = (201, "Order rejected - reason: the price is out of range")
        with pytest.raises(ExchangeRequestError):
            await trader.modify(placed.order_id, price=2.4)
        await trader.cancel(placed.order_id)
        assert ib.cancel_calls == [placed.order_id]

    async def test_a_fill_is_reported_even_after_a_cancel(self, trader: IBKRTrader, ib: FakeIB):
        events: list[OrderEvent] = []
        trader.watch_orders(events.append)
        placed = await trader.place_limit(DEMO, "buy", 10, 2.5, ref="late-fill")
        ib.set_status(by_id(ib, placed.order_id), "Cancelled")
        ib.fill(placed.order_id, 10, 2.5)
        assert [e.kind for e in events] == ["accepted", "cancelled", "filled"]

    async def test_cancelling_an_unanswered_chain_waits_for_ib(self, ib: FakeIB, journal: Path):
        quick = build(ib, journal, timeout=0.05)
        await quick.connect()
        events: list[OrderEvent] = []
        quick.watch_orders(events.append)
        ib.answer_orders = False
        chain = await quick.place_bracket(DEMO, "buy", 10, 2.5, 2.3, ref="slow")
        assert await quick.cancel_chain(chain.parent_id) == [chain.parent_id]
        assert by_id(ib, chain.parent_id).orderStatus.status == "PendingCancel"
        assert events == []

    async def test_cancel_takes_one_order(self, trader: IBKRTrader, ib: FakeIB):
        chain = await two_target_chain(trader)
        await trader.cancel(chain.legs[1].order_id)
        assert ib.cancel_calls == [chain.legs[1].order_id]
        with pytest.raises(OrderCheckError, match="not a working order"):
            await trader.cancel(chain.legs[1].order_id)

    async def test_cancel_chain_before_the_entry_fills_drops_everything(
        self, trader: IBKRTrader, ib: FakeIB
    ):
        chain = await two_target_chain(trader)
        sent = await trader.cancel_chain(chain.parent_id)
        assert sent == [leg.order_id for leg in chain.of_role("entry")]
        assert all(t.isDone() for t in ib.own_trades.values())

    async def test_cancel_chain_after_a_fill_drops_what_is_left(
        self, trader: IBKRTrader, ib: FakeIB
    ):
        chain = await two_target_chain(trader)
        entry_1, target_1, stop_1, entry_2, _, _ = (leg.order_id for leg in chain.legs)
        ib.fill(entry_1, 60, 2.5)
        sent = await trader.cancel_chain(chain.parent_id)
        assert sorted(sent) == sorted([target_1, stop_1, entry_2])
        assert all(t.isDone() for t in ib.own_trades.values())
        assert await trader.cancel_chain(chain.parent_id) == []
        with pytest.raises(OrderCheckError, match="not an order of this client"):
            await trader.cancel_chain(424242)

    async def test_cancel_chain_refuses_the_parent_id_of_an_entry(
        self, trader: IBKRTrader, ib: FakeIB, journal: Path
    ):
        chain = await two_target_chain(trader)
        ib.fill(chain.parent_id, 60, 2.5)
        single = await trader.place_limit(DEMO, "buy", 10, 2.4, ref="single")
        assert single.parent_id == 0
        with pytest.raises(OrderCheckError, match="needs the order id of an entry, got 0"):
            await trader.cancel_chain(single.parent_id)
        assert ib.cancel_calls == []
        assert events_of(journal, "refused")[-1]["action"] == "cancel_chain"

    def test_the_trader_never_cancels_everything(self):
        source = inspect.getsource(trader_module)
        assert "reqGlobalCancel" not in source
        assert not any("global" in name for name in dir(IBKRTrader))


class TestEvents:
    async def test_events_carry_size_price_ref_and_reason(self, trader: IBKRTrader, ib: FakeIB):
        events: list[OrderEvent] = []
        watch = trader.watch_orders(events.append)
        chain = await trader.place_bracket(DEMO, "buy", 100, 2.5, 2.3, [(2.8, 100)], ref="ev")
        entry, target, stop = (leg.order_id for leg in chain.legs)
        assert [(e.kind, e.role) for e in events] == [
            ("accepted", "entry"),
            ("accepted", "target"),
            ("accepted", "stop"),
        ]
        ib.fill(entry, 30, 2.50)
        ib.fill(entry, 70, 2.52)
        fills = [e for e in events if e.order_id == entry and e.kind in ("partial", "filled")]
        assert [(e.kind, e.filled, e.remaining) for e in fills] == [
            ("partial", 30, 70),
            ("filled", 100, 0),
        ]
        assert fills[-1].avg_price == pytest.approx(2.514)
        assert fills[-1].ref == "ev" and fills[-1].side == "buy" and fills[-1].symbol == "DEMO"

        ib.fail(by_id(ib, target), 399, "Order Message: Warning: held until the open", warning=True)
        assert (events[-1].kind, events[-1].code) == ("notice", 399)
        ib.fail(by_id(ib, stop), 202, "Order Canceled - reason: by the exchange")
        assert (events[-1].kind, events[-1].code, events[-1].role) == ("cancelled", 202, "stop")
        assert "by the exchange" in events[-1].reason

        trader.stop_orders(watch)
        seen = len(events)
        ib.fill(target, 100, 2.8)
        assert len(events) == seen

    async def test_a_failing_watcher_does_not_stop_the_others(self, trader: IBKRTrader, caplog):
        seen: list[str] = []

        def broken(event: OrderEvent) -> None:
            raise RuntimeError("boom")

        trader.watch_orders(broken)
        trader.watch_orders(lambda event: seen.append(event.kind))
        with caplog.at_level(logging.ERROR):
            await trader.place_limit(DEMO, "buy", 10, 2.5, ref="w")
        assert seen == ["accepted"]
        assert "order callback failed" in caplog.text

    async def test_errors_on_unrelated_requests_are_ignored(self, trader: IBKRTrader, ib: FakeIB):
        events: list[OrderEvent] = []
        trader.watch_orders(events.append)
        ib.errorEvent.emit(-1, 2104, "Market data farm connection is OK", None)
        ib.errorEvent.emit(31337, 200, "No security definition", None)
        assert events == []


class TestPositionsAndWhatIf:
    async def test_positions_of_the_traded_account(self, trader: IBKRTrader, ib: FakeIB):
        option = make_contract("DEMO", 8, sec_type="OPT", multiplier="100")
        option.lastTradeDateOrContractMonth, option.strike, option.right = "20261218", 5.0, "C"
        ib.position_rows = [
            Position(ACCOUNT, make_contract("DEMO", 4391), 300, 2.4567),
            Position(ACCOUNT, option, -2, 125.0),
            Position(ACCOUNT, make_contract("FLAT", 9), 0, 1.0),
            Position("DU7654321", make_contract("ELSE", 5), 10, 9.0),
        ]
        positions = await trader.fetch_positions()
        assert positions == [
            IBKRPosition(ACCOUNT, "DEMO", IBKRInstrument.parse("DEMO-USD-STK@SMART"), 300, 2.4567),
            IBKRPosition(ACCOUNT, "DEMO", positions[1].instrument, -2, 1.25),
        ]
        assert len(await trader.client.fetch_positions()) == 3

    async def test_what_if_reads_the_margin_impact(self, trader: IBKRTrader, ib: FakeIB):
        ib.what_if_state = OrderState(
            initMarginChange="1250.5",
            maintMarginChange="1000",
            equityWithLoanChange="-3.2",
            commission=1.0,
            warningText="",
        )
        impact = await trader.what_if(DEMO, "buy", 1000, 2.5)
        assert impact == MarginImpact(1250.5, 1000.0, -3.2, 1.0, "")
        assert ib.what_if_calls[-1].account == ACCOUNT and ib.placed == []
        ib.what_if_errors = [(201, "Order rejected - reason: margin")]
        with pytest.raises(ExchangeRequestError, match="margin") as refused:
            await trader.what_if(DEMO, "buy", 1000, 2.5)
        assert refused.value.code == 201
        with pytest.raises(OrderCheckError, match="above the 5,000.00 limit"):
            await trader.what_if(DEMO, "buy", 3000, 2.5)


class TestJournal:
    async def test_every_step_is_recorded_with_a_masked_account(
        self, trader: IBKRTrader, ib: FakeIB, journal: Path
    ):
        chain = await two_target_chain(trader)
        ib.fill(chain.parent_id, 60, 2.5)
        await trader.cancel_chain(chain.parent_id)
        records = read_journal(journal)
        kinds = [record["event"] for record in records]
        assert kinds[0] == "connect" and "request" in kinds and "fill" in kinds
        request = events_of(journal, "request")[0]
        assert request["ref"] == "sig-1" and len(request["orders"]) == 6
        assert request["orders"][2] == {
            "order_id": chain.legs[2].order_id,
            "parent_id": chain.parent_id,
            "role": "stop",
            "action": "SELL",
            "type": "STP",
            "shares": 60,
            "limit": None,
            "stop": 2.3,
            "tif": "DAY",
            "outside_rth": False,
            "transmit": True,
        }
        statuses = events_of(journal, "status")
        assert {r["order_id"] for r in statuses} >= {leg.order_id for leg in chain.legs}
        fill = events_of(journal, "fill")[0]
        assert (fill["shares"], fill["price"], fill["ref"]) == (60, 2.5, "sig-1")
        assert all(isinstance(r["at"], float) for r in records)
        assert {r.get("account") for r in records if "account" in r} == {"DU****567"}
        assert ACCOUNT not in journal.read_text(encoding="utf-8")

    async def test_a_torn_line_never_breaks_reading(
        self, ib: FakeIB, journal: Path, monkeypatch: pytest.MonkeyPatch
    ):
        journal.parent.mkdir(parents=True)
        journal.write_bytes(b'{"event": "status", "at": 1.0}\n{"event": "sta')
        synced: list[int] = []
        real = os.fsync

        def counted(fd: int) -> None:
            synced.append(fd)
            real(fd)

        monkeypatch.setattr(os, "fsync", counted)
        trader = build(ib, journal)
        await trader.connect()
        records = read_journal(journal)
        assert [r["event"] for r in records] == ["status", "connect"]
        assert synced
        assert read_journal(journal.with_name("missing.jsonl")) == []


class TestEdges:
    async def test_late_answers_and_errors_ib_sends_without_a_status(
        self, trader: IBKRTrader, ib: FakeIB, monkeypatch: pytest.MonkeyPatch
    ):
        events: list[OrderEvent] = []
        trader.watch_orders(events.append)
        ib.answer_orders = False
        place = ib.placeOrder

        def answer_on_the_next_turn(contract: Contract, order: Order) -> Trade:
            trade = place(contract, order)
            asyncio.get_running_loop().call_soon(ib.transmit)
            return trade

        monkeypatch.setattr(ib, "placeOrder", answer_on_the_next_turn)
        placed = await trader.place_limit(DEMO, "buy", 10, 2.5, ref="late")
        assert placed.status == "Submitted"
        trade = by_id(ib, placed.order_id)
        ib.fill(placed.order_id, 10, 2.5)
        ib.errorEvent.emit(
            placed.order_id,
            161,
            "Cancel attempted when order is not in a cancellable state",
            trade.contract,
        )
        assert (events[-1].kind, events[-1].code) == ("notice", 161)
        other = await trader.place_limit(DEMO, "buy", 10, 2.5, ref="dead")
        ib.set_status(by_id(ib, other.order_id), "Inactive")
        assert (events[-1].kind, events[-1].order_id) == ("rejected", other.order_id)

    async def test_a_change_ib_refuses_only_on_the_error_channel(
        self, trader: IBKRTrader, ib: FakeIB, monkeypatch: pytest.MonkeyPatch
    ):
        placed = await trader.place_limit(DEMO, "buy", 10, 2.5, ref="m")

        def refuse(trade: Trade, order: Order) -> Trade:
            ib.errorEvent.emit(order.orderId, 104, "Can't modify a filled order", trade.contract)
            return trade

        monkeypatch.setattr(ib, "_modify", refuse)
        with pytest.raises(ExchangeRequestError, match="filled order") as refused:
            await trader.modify(placed.order_id, price=2.4)
        assert refused.value.code == 104

    async def test_an_unanswered_change_comes_back_as_it_was(
        self, ib: FakeIB, journal: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        quick = build(ib, journal, timeout=0.05)
        await quick.connect()
        placed = await quick.place_limit(DEMO, "buy", 10, 2.5, ref="m")
        monkeypatch.setattr(ib, "_modify", lambda trade, order: trade)
        with caplog.at_level(logging.WARNING):
            unchanged = await quick.modify(placed.order_id, price=2.4)
        assert unchanged.limit_price == 2.5
        assert "has not answered the change" in caplog.text

    async def test_a_login_without_accounts_is_refused(self, ib: FakeIB, journal: Path):
        ib.accounts = []
        with pytest.raises(ConfigurationError, match="named no account"):
            await build(ib, journal).connect()

    async def test_a_silent_what_if_is_a_connection_error(self, trader: IBKRTrader):
        with pytest.raises(ExchangeConnectionError, match="no what-if answer"):
            await trader.what_if(DEMO, "buy", 10, 2.5)

    async def test_a_journal_that_cannot_write_never_drops_events(
        self, trader: IBKRTrader, ib: FakeIB, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        events: list[OrderEvent] = []
        trader.watch_orders(events.append)
        placed = await trader.place_limit(DEMO, "buy", 10, 2.5, ref="disk")

        def full(event: str, **fields: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(trader._journal, "write", full)
        with caplog.at_level(logging.ERROR):
            ib.fill(placed.order_id, 10, 2.5)
        assert events[-1].kind == "filled"
        assert "could not record fill" in caplog.text
        with pytest.raises(OSError, match="disk full"):
            await trader.place_limit(DEMO, "buy", 10, 2.5, ref="not-sent")
        assert ib.placed[-1].orderRef == "disk"

    async def test_the_trader_is_a_context_manager(self, ib: FakeIB, journal: Path):
        async with build(ib, journal) as trader:
            assert trader.account_kind == "paper"
            assert trader.max_order_value == 5_000 and trader.journal_path == journal
        assert not ib.connected and trader.account is None

    def test_ib_numbers_read_as_floats_or_none(self):
        assert parse_number("1250.5") == 1250.5
        assert parse_number(None) is None
        assert parse_number("") is None
        assert parse_number(UNSET_DOUBLE) is None
        assert parse_number(float("nan")) is None

    async def test_a_journal_that_cannot_write_blocks_new_risk_but_not_cancels(
        self, ib: FakeIB, journal: Path, monkeypatch: pytest.MonkeyPatch
    ):
        def full(event: str, **fields: Any) -> None:
            raise OSError("disk full")

        refused = build(ib, journal)
        monkeypatch.setattr(refused._journal, "write", full)
        with pytest.raises(OSError, match="disk full"):
            await refused.connect()
        assert not ib.connected and refused.account_kind is None

        trader = build(ib, journal)
        await trader.connect()
        placed = await trader.place_limit(DEMO, "buy", 10, 2.5, ref="keep")
        monkeypatch.setattr(trader._journal, "write", full)
        with pytest.raises(OrderCheckError, match="whole number"):
            await trader.place_limit(DEMO, "buy", 0, 2.5, ref="bad")
        await trader.cancel(placed.order_id)
        assert ib.cancel_calls == [placed.order_id]
        assert by_id(ib, placed.order_id).orderStatus.status == "Cancelled"
