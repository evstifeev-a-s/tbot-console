from __future__ import annotations

import asyncio
import copy
import dataclasses
import logging
import math
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Literal

from ib_async import IB, Contract, Fill, LimitOrder, Order, StopLimitOrder, StopOrder, Trade
from ib_async.ib import StartupFetchALL
from ib_async.order import OrderState, OrderStatus

from tbot_console.connectors.ibkr.client import (
    STOP_ORDER_TYPES,
    IBKRClient,
    _order_price,
    _to_float,
    instrument_from_contract,
    is_notice,
)
from tbot_console.connectors.ibkr.config import IBKRConfig, live_trading_allowed
from tbot_console.connectors.ibkr.instruments import IBKRInstrument, SecType
from tbot_console.connectors.ibkr.journal import OrderJournal
from tbot_console.connectors.ibkr.models import (
    AccountSummary,
    ContractSpec,
    IBKRPosition,
    MarginImpact,
    OrderEvent,
    OrderEventKind,
    PlacedChain,
    PlacedOrder,
    Side,
)
from tbot_console.connectors.ibkr.orders import (
    CANCELLED_CODE,
    FAILED,
    LIVE_ORDER,
    REJECTED_CODE,
    VALIDATION_ERROR_CODE,
    AccountKind,
    BracketPlan,
    LiveAccountError,
    OrderCheckError,
    OrderRejectedError,
    account_kind,
    check_ticks,
    check_value,
    checked_price,
    checked_ref,
    checked_shares,
    checked_side,
    checked_tif,
    described,
    failure_of,
    ib_action,
    is_answered,
    mask_account,
    parse_number,
    placed_from,
    plan_bracket,
    price_step,
    role_of,
    snap,
)
from tbot_console.core.exceptions import (
    ConfigurationError,
    ExchangeConnectionError,
    ExchangeRequestError,
)

LOG = logging.getLogger(__name__)

OrderCallback = Callable[[OrderEvent], Any]


@dataclass(slots=True, eq=False)
class OrderWatch:
    callback: OrderCallback


class IBKRTrader:
    def __init__(
        self,
        config: IBKRConfig,
        *,
        max_order_value: float,
        journal: str | os.PathLike[str],
        ib: IB | None = None,
    ) -> None:
        if (
            isinstance(max_order_value, bool)
            or not isinstance(max_order_value, int | float)
            or not math.isfinite(max_order_value)
            or max_order_value <= 0
        ):
            raise ConfigurationError(
                f"max_order_value must be a finite amount above zero, got {max_order_value!r}"
            )
        self._config = dataclasses.replace(config, readonly=False)
        self._live_allowed = live_trading_allowed()
        self._max_value = float(max_order_value)
        self._client = IBKRClient(self._config, ib=ib)
        self._ib = self._client.ib
        self._journal = OrderJournal(Path(journal))
        self._account: str | None = None
        self._kind: AccountKind | None = None
        self._watchers: list[OrderWatch] = []
        self._hooked = False
        self._send_lock = asyncio.Lock()
        self._accepted: set[int] = set()
        self._finished: set[int] = set()
        self._filled: dict[int, float] = {}
        self._status_errors: set[tuple[int, int]] = set()
        self._changing: set[int] = set()

    @property
    def client(self) -> IBKRClient:
        return self._client

    @property
    def config(self) -> IBKRConfig:
        return self._config

    @property
    def is_connected(self) -> bool:
        return self._client.is_connected

    @property
    def account(self) -> str | None:
        return None if self._account is None else mask_account(self._account)

    @property
    def account_kind(self) -> AccountKind | None:
        return self._kind

    @property
    def live_allowed(self) -> bool:
        return self._live_allowed

    @property
    def max_order_value(self) -> float:
        return self._max_value

    @property
    def journal_path(self) -> Path:
        return self._journal.path

    async def connect(self) -> None:
        if self.is_connected and self._account is not None:
            return
        if self.is_connected:
            await self._client.disconnect()
        await self._client._open(StartupFetchALL)
        try:
            account, kind = self._checked_account()
        except ConfigurationError as refusal:
            self._safe_write("refused", action="connect", reason=str(refusal))
            await self._client.disconnect()
            raise
        cfg = self._config
        try:
            self._journal.write(
                "connect",
                account=mask_account(account),
                kind=kind,
                client_id=cfg.client_id,
                host=cfg.host,
                port=cfg.port,
                live_switch=self._live_allowed,
            )
        except OSError:
            await self._client.disconnect()
            raise
        self._account, self._kind = account, kind
        self._hook()
        if self._kind == "live":
            LOG.warning(
                "IBKR trader is connected to LIVE account %s: its orders go to real money "
                "(IBKR_ALLOW_LIVE_TRADING=true)",
                self.account,
            )
        else:
            LOG.info("IBKR trader is connected to paper account %s", self.account)

    async def disconnect(self) -> None:
        await self._client.disconnect()
        self._forget_account()

    async def __aenter__(self) -> IBKRTrader:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.disconnect()

    def _checked_account(self) -> tuple[str, AccountKind]:
        accounts = [name for name in self._ib.managedAccounts() if name]
        if not accounts:
            raise ConfigurationError("IBKR named no account for this login; nothing is sent")
        kind = account_kind(accounts)
        masked = ", ".join(mask_account(name) for name in accounts)
        if kind == "live" and not self._live_allowed:
            raise LiveAccountError(
                f"IBKR account {masked} is live (real money) and live trading is off for this "
                "process: without the switch orders go to a paper account (DU…) only. To allow "
                "live orders turn on «Разрешить заявки на живом счёте» for this unit on its "
                "Processes page in the console, or run `uv run tbot live <unit> on`, then "
                "restart the trading process; a hand start needs IBKR_ALLOW_LIVE_TRADING=true "
                "in its terminal"
            )
        wanted = self._config.account
        if wanted and wanted not in accounts:
            raise ConfigurationError(
                f"IBKR_ACCOUNT {mask_account(wanted)} is not among the accounts this login "
                f"sees ({masked}); nothing is sent"
            )
        if not wanted and len(accounts) > 1:
            raise ConfigurationError(
                f"this IBKR login sees {len(accounts)} accounts ({masked}): set IBKR_ACCOUNT "
                "to the one orders go to"
            )
        return wanted or accounts[0], kind

    def _hook(self) -> None:
        if self._hooked:
            return
        self._ib.orderStatusEvent += self._on_status
        self._ib.execDetailsEvent += self._on_fill
        self._ib.errorEvent += self._on_error
        self._ib.disconnectedEvent += self._on_disconnected
        self._hooked = True

    def _forget_account(self) -> None:
        self._account = None
        self._kind = None

    def _on_disconnected(self) -> None:
        if self._account is not None:
            self._safe_write("disconnect", account=self.account)
        self._forget_account()

    def _require_ready(self) -> str:
        if not self.is_connected or self._account is None:
            raise ConfigurationError(
                "IBKR trader is not connected: call connect(), which checks the account, "
                "before any order"
            )
        return self._account

    def _safe_write(self, event: str, **fields: Any) -> None:
        try:
            self._journal.write(event, **fields)
        except OSError:
            LOG.exception("IBKR order journal %s could not record %s", self._journal.path, event)

    def _refused(self, action: str, ref: object, refusal: Exception) -> None:
        self._safe_write(
            "refused", action=action, ref=ref, account=self.account, reason=str(refusal)
        )

    async def _stock(self, instrument: IBKRInstrument) -> tuple[Contract, ContractSpec]:
        if instrument.sec_type is not SecType.STK:
            raise OrderCheckError(
                f"{instrument.to_normalized()}: this trader places stock orders (STK) only; "
                f"{instrument.sec_type.value} is not supported yet"
            )
        spec = await self._client.qualify(instrument)
        return await self._client._qualified_contract(instrument), spec

    def _ours(self, trade: Trade) -> bool:
        return trade.order.orderId > 0 and trade.order.clientId == self._config.client_id

    def _own_trade(self, order_id: int) -> Trade | None:
        return next(
            (t for t in self._ib.trades() if self._ours(t) and t.order.orderId == order_id), None
        )

    def _working(self, order_id: int) -> Trade:
        trade = self._own_trade(order_id)
        if trade is None or trade.isDone():
            raise OrderCheckError(
                f"order {order_id} is not a working order of this client "
                f"(client id {self._config.client_id}); IBKR lets only the client id that "
                "placed an order change or cancel it"
            )
        return trade

    async def _refuse_reused(self, ref: str, account: str) -> None:
        remote = await self._client._open_trades()
        working = {
            t.order.orderId
            for t in [*self._ib.openTrades(), *remote]
            if t.order.orderRef == ref
            and t.orderStatus.status not in OrderStatus.DoneStates
            and t.order.account in ("", account)
            and not self._never_placed(t)
        }
        if working:
            raise OrderCheckError(
                f"ref {ref!r} already has working orders ({', '.join(map(str, sorted(working)))}): "
                "a chain goes out once per ref; cancel it first or use a new ref"
            )

    def _new_order(
        self,
        kind: Literal["LMT", "STP"],
        action: str,
        shares: int,
        *,
        price: float,
        stop_limit: float | None = None,
        parent: int = 0,
        transmit: bool,
        tif: str,
        outside_rth: bool,
        ref: str,
        account: str,
    ) -> Order:
        common: dict[str, Any] = {
            "orderId": self._ib.client.getReqId(),
            "tif": tif,
            "outsideRth": outside_rth,
            "orderRef": ref,
            "account": account,
            "parentId": parent,
            "transmit": transmit,
        }
        if kind == "LMT":
            return LimitOrder(action, shares, price, **common)
        if stop_limit is None:
            return StopOrder(action, shares, price, **common)
        return StopLimitOrder(action, shares, stop_limit, price, **common)

    async def _await_answers(self, trades: Sequence[Trade], ref: str) -> None:
        if all(map(is_answered, trades)):
            return
        settled = asyncio.get_running_loop().create_future()

        def check(_: Trade) -> None:
            if not settled.done() and all(map(is_answered, trades)):
                settled.set_result(None)

        self._ib.orderStatusEvent += check
        try:
            await asyncio.wait_for(settled, self._config.timeout or None)
        except TimeoutError:
            waiting = [t.order.orderId for t in trades if not is_answered(t)]
            LOG.warning(
                "IBKR has not confirmed orders %s of %s within %g s; what becomes of them "
                "arrives through watch_orders",
                waiting,
                ref,
                self._config.timeout,
            )
            self._safe_write("unconfirmed", ref=ref, order_ids=waiting)
        finally:
            self._ib.orderStatusEvent -= check

    def _never_placed(self, trade: Trade) -> bool:
        return (
            trade.orderStatus.status == OrderStatus.ValidationError
            and trade.order.orderId not in self._accepted
            and any(entry.errorCode == VALIDATION_ERROR_CODE for entry in trade.log)
        )

    def _withdraw(self, trades: Sequence[Trade], ref: str) -> list[int]:
        withdrawn = []
        for trade in trades:
            unfilled_entry = not trade.order.parentId and trade.orderStatus.filled == 0
            if unfilled_entry and not trade.isDone() and not self._never_placed(trade):
                self._ib.cancelOrder(trade.order)
                withdrawn.append(trade.order.orderId)
        if withdrawn:
            self._safe_write("withdraw", ref=ref, account=self.account, order_ids=withdrawn)
        return withdrawn

    def _raise_if_rejected(self, trades: Sequence[Trade], ref: str) -> None:
        failed = [t for t in trades if t.orderStatus.status in FAILED or self._never_placed(t)]
        if not failed:
            return
        first = failed[0]
        code, reason = failure_of(first)
        bought = {t.order.orderId for t in trades if t.orderStatus.filled > 0}
        withdrawn = self._withdraw(trades, ref)
        kept = [t.order.orderId for t in trades if not t.isDone() and t.order.parentId in bought]
        rest = f"withdrawn: {withdrawn}" if withdrawn else "nothing else was working"
        if kept:
            rest += f"; kept, as they guard shares already bought or sold: {kept}"
        raise OrderRejectedError(
            f"IBKR rejected the {role_of(first.order)} order {first.order.orderId} of {ref}: "
            f"{reason}; {rest}",
            code=code,
            reason=reason,
        )

    async def place_limit(
        self,
        instrument: IBKRInstrument,
        side: Side,
        shares: int,
        limit: float,
        *,
        tif: str = "DAY",
        outside_rth: bool = False,
        ref: str,
    ) -> PlacedOrder:
        account = self._require_ready()
        async with self._send_lock:
            try:
                direction = checked_side(side)
                count = checked_shares("shares", shares)
                price = checked_price("limit", limit)
                horizon = checked_tif(tif)
                name = checked_ref(ref)
                check_value(count, price, self._max_value)
                contract, spec = await self._stock(instrument)
                check_ticks(spec, instrument.currency, [("limit", price)])
                await self._refuse_reused(name, account)
            except OrderCheckError as refusal:
                self._refused("place_limit", ref, refusal)
                raise
            account = self._require_ready()
            order = self._new_order(
                "LMT",
                ib_action(direction),
                count,
                price=snap(price, price_step(price, spec.min_tick, instrument.currency)),
                transmit=True,
                tif=horizon,
                outside_rth=outside_rth,
                ref=name,
                account=account,
            )
            self._journal.write(
                "request",
                action="place_limit",
                ref=name,
                account=self.account,
                symbol=instrument.to_normalized(),
                orders=[described(order)],
            )
            trade = self._ib.placeOrder(contract, order)
        await self._await_answers([trade], name)
        self._raise_if_rejected([trade], name)
        return placed_from(trade)

    async def place_bracket(
        self,
        instrument: IBKRInstrument,
        side: Side,
        shares: int,
        entry: float,
        stop: float,
        targets: Sequence[tuple[float, int]] = (),
        *,
        stop_limit: float | None = None,
        tif: str = "DAY",
        outside_rth: bool = False,
        ref: str,
    ) -> PlacedChain:
        account = self._require_ready()
        async with self._send_lock:
            try:
                plan = plan_bracket(
                    side,
                    shares,
                    entry,
                    stop,
                    targets,
                    stop_limit=stop_limit,
                    outside_rth=outside_rth,
                    max_order_value=self._max_value,
                )
                horizon = checked_tif(tif)
                name = checked_ref(ref)
                contract, spec = await self._stock(instrument)
                check_ticks(spec, instrument.currency, plan.prices())
                await self._refuse_reused(name, account)
            except OrderCheckError as refusal:
                self._refused("place_bracket", ref, refusal)
                raise
            account = self._require_ready()
            orders = self._chain_orders(
                plan, spec, instrument.currency, horizon, outside_rth, name, account
            )
            self._journal.write(
                "request",
                action="place_bracket",
                ref=name,
                account=self.account,
                symbol=instrument.to_normalized(),
                orders=[described(order) for order in orders],
            )
            trades = [self._ib.placeOrder(contract, order) for order in orders]
            for trade in trades:
                trade.order.transmit = True
        await self._await_answers(trades, name)
        self._raise_if_rejected(trades, name)
        return PlacedChain(
            parent_id=orders[0].orderId, legs=tuple(map(placed_from, trades)), ref=name
        )

    def _chain_orders(
        self,
        plan: BracketPlan,
        spec: ContractSpec,
        currency: str,
        tif: str,
        outside_rth: bool,
        ref: str,
        account: str,
    ) -> list[Order]:
        def tick(price: float) -> float:
            return snap(price, price_step(price, spec.min_tick, currency))

        action = ib_action(plan.side)
        reverse = "SELL" if action == "BUY" else "BUY"
        common: dict[str, Any] = {
            "tif": tif,
            "outside_rth": outside_rth,
            "ref": ref,
            "account": account,
        }
        orders: list[Order] = []
        for portion in plan.portions:
            entry = self._new_order(
                "LMT", action, portion.shares, price=tick(plan.entry), transmit=False, **common
            )
            orders.append(entry)
            if portion.target is not None:
                orders.append(
                    self._new_order(
                        "LMT",
                        reverse,
                        portion.shares,
                        price=tick(portion.target),
                        parent=entry.orderId,
                        transmit=False,
                        **common,
                    )
                )
            orders.append(
                self._new_order(
                    "STP",
                    reverse,
                    portion.shares,
                    price=tick(plan.stop),
                    stop_limit=None if plan.stop_limit is None else tick(plan.stop_limit),
                    parent=entry.orderId,
                    transmit=True,
                    **common,
                )
            )
        return orders

    async def modify(
        self,
        order_id: int,
        *,
        price: float | None = None,
        stop: float | None = None,
        shares: int | None = None,
    ) -> PlacedOrder:
        self._require_ready()
        try:
            trade = self._working(order_id)
            changed = await self._changed(trade, price, stop, shares)
        except OrderCheckError as refusal:
            self._refused("modify", order_id, refusal)
            raise
        self._require_ready()
        self._journal.write(
            "request",
            action="modify",
            ref=trade.order.orderRef,
            account=self.account,
            orders=[described(changed)],
        )
        failure = await self._send_change(trade, changed)
        if failure is not None:
            code, message = failure
            raise ExchangeRequestError(
                f"IBKR did not apply the change to order {order_id}: {message}; the order keeps "
                "its old values unless fetch_open_orders() shows otherwise",
                code=code,
                reason=message,
            )
        return placed_from(trade)

    async def _changed(
        self, trade: Trade, price: float | None, stop: float | None, shares: int | None
    ) -> Order:
        order = trade.order
        kind = order.orderType.upper()
        if price is None and stop is None and shares is None:
            raise OrderCheckError("modify needs a new price, stop or shares")
        if price is not None and "LMT" not in kind:
            raise OrderCheckError(f"order {order.orderId} is {kind} and has no limit price")
        if stop is not None and kind not in STOP_ORDER_TYPES:
            raise OrderCheckError(f"order {order.orderId} is {kind} and has no stop price")
        new_limit = _order_price(order.lmtPrice) if price is None else checked_price("price", price)
        new_stop = _order_price(order.auxPrice) if stop is None else checked_price("stop", stop)
        count = (
            int(_to_float(order.totalQuantity))
            if shares is None
            else checked_shares("shares", shares)
        )
        filled = trade.orderStatus.filled
        if count <= filled:
            raise OrderCheckError(
                f"order {order.orderId} has {filled:g} shares filled already: a new size must "
                f"be above that, got {count}"
            )
        instrument = instrument_from_contract(trade.contract)
        if instrument is not None:
            spec = await self._client.qualify(instrument)
            check_ticks(
                spec,
                instrument.currency,
                [
                    (name, value)
                    for name, value in (("price", price), ("stop", stop))
                    if value is not None
                ],
            )
            if price is not None and new_limit is not None:
                new_limit = snap(
                    new_limit, price_step(new_limit, spec.min_tick, instrument.currency)
                )
            if stop is not None and new_stop is not None:
                new_stop = snap(new_stop, price_step(new_stop, spec.min_tick, instrument.currency))
        if kind == "STP LMT" and new_limit is not None and new_stop is not None:
            selling = order.action.upper() == "SELL"
            if (selling and new_limit > new_stop) or (not selling and new_limit < new_stop):
                raise OrderCheckError(
                    f"the limit {new_limit:g} of stop order {order.orderId} would be better than "
                    f"its stop {new_stop:g}: a stop-limit must not be"
                )
        if not order.parentId and new_limit is not None:
            check_value(count, new_limit, self._max_value)
        if order.parentId and shares is not None:
            parent = self._own_trade(order.parentId)
            ceiling = _to_float((parent or trade).order.totalQuantity)
            if count > ceiling:
                raise OrderCheckError(
                    f"order {order.orderId} is the {role_of(order)} of entry {order.parentId}: "
                    f"it may cover at most {ceiling:g} shares, got {count}"
                )
        changed = copy.copy(order)
        if price is not None:
            changed.lmtPrice = new_limit
        if stop is not None:
            changed.auxPrice = new_stop
        changed.totalQuantity = count
        changed.transmit = True
        return changed

    async def _send_change(self, trade: Trade, changed: Order) -> tuple[int, str] | None:
        answered: asyncio.Future[tuple[int, str] | None] = (
            asyncio.get_running_loop().create_future()
        )
        order_id = trade.order.orderId
        before = trade.orderStatus.status

        def on_status(updated: Trade) -> None:
            if updated is trade and not answered.done():
                answered.set_result(None)

        def on_error(req_id: int, code: int, message: str, *_: Any) -> None:
            if req_id == order_id and not is_notice(code) and not answered.done():
                answered.set_result((code, message))

        self._ib.orderStatusEvent += on_status
        self._ib.errorEvent += on_error
        self._changing.add(order_id)
        try:
            self._ib.placeOrder(trade.contract, changed)
            outcome = await asyncio.wait_for(asyncio.shield(answered), self._config.timeout or None)
        except TimeoutError:
            LOG.warning("IBKR has not answered the change of order %s yet", order_id)
            self._safe_write("unconfirmed", ref=trade.order.orderRef, order_ids=[order_id])
            return None
        finally:
            self._ib.orderStatusEvent -= on_status
            self._ib.errorEvent -= on_error
            self._changing.discard(order_id)
        if outcome is None:
            last = trade.log[-1] if trade.log else None
            if last is not None and last.errorCode and not is_notice(last.errorCode):
                outcome = last.errorCode, last.message
        guessed = trade.orderStatus.status in (OrderStatus.Cancelled, OrderStatus.ApiCancelled)
        if outcome is not None and outcome[0] != CANCELLED_CODE and guessed:
            trade.orderStatus.status = before
        return outcome

    async def cancel(self, order_id: int) -> None:
        self._require_ready()
        try:
            trade = self._working(order_id)
        except OrderCheckError as refusal:
            self._refused("cancel", order_id, refusal)
            raise
        self._safe_write(
            "request",
            action="cancel",
            ref=trade.order.orderRef,
            account=self.account,
            order_ids=[order_id],
        )
        self._ib.cancelOrder(trade.order)

    def _chain(self, parent_id: int) -> list[Trade]:
        own = [t for t in self._ib.trades() if self._ours(t)]
        linked = [t for t in own if parent_id in (t.order.orderId, t.order.parentId)]
        refs = {t.order.orderRef for t in linked if t.order.orderRef}
        return [t for t in own if t in linked or t.order.orderRef in refs]

    async def cancel_chain(self, parent_id: int) -> list[int]:
        self._require_ready()
        if parent_id <= 0:
            refusal = OrderCheckError(
                f"cancel_chain needs the order id of an entry, got {parent_id}: an entry or a "
                "single limit order reports parent_id 0, so pass its order_id"
            )
            self._refused("cancel_chain", parent_id, refusal)
            raise refusal
        chain = self._chain(parent_id)
        if not chain:
            refusal = OrderCheckError(
                f"order {parent_id} is not an order of this client (client id "
                f"{self._config.client_id}); IBKR lets only the client id that placed an "
                "order cancel it"
            )
            self._refused("cancel_chain", parent_id, refusal)
            raise refusal
        working = [t for t in chain if not t.isDone()]
        dropping = {
            t.order.orderId for t in working if not t.order.parentId and t.orderStatus.filled == 0
        }
        cancels = [t for t in working if t.order.parentId not in dropping]
        ids = [t.order.orderId for t in cancels]
        self._safe_write(
            "request",
            action="cancel_chain",
            ref=chain[0].order.orderRef,
            account=self.account,
            parent_id=parent_id,
            order_ids=ids,
        )
        for trade in cancels:
            self._ib.cancelOrder(trade.order)
        return ids

    def watch_orders(self, callback: OrderCallback) -> OrderWatch:
        watch = OrderWatch(callback)
        self._watchers.append(watch)
        return watch

    def stop_orders(self, watch: OrderWatch) -> None:
        if watch in self._watchers:
            self._watchers.remove(watch)

    async def fetch_positions(self) -> list[IBKRPosition]:
        account = self._require_ready()
        return [p for p in await self._client.fetch_positions() if p.account == account]

    async def fetch_account_summary(self) -> AccountSummary | None:
        account = self._require_ready()
        found = await self._client.fetch_account_summary()
        return next((s for s in found if s.account == account), None)

    async def what_if(
        self,
        instrument: IBKRInstrument,
        side: Side,
        shares: int,
        limit: float,
        *,
        tif: str = "DAY",
        outside_rth: bool = False,
    ) -> MarginImpact:
        account = self._require_ready()
        direction = checked_side(side)
        count = checked_shares("shares", shares)
        price = checked_price("limit", limit)
        horizon = checked_tif(tif)
        check_value(count, price, self._max_value)
        contract, spec = await self._stock(instrument)
        check_ticks(spec, instrument.currency, [("limit", price)])
        order = LimitOrder(
            ib_action(direction),
            count,
            snap(price, price_step(price, spec.min_tick, instrument.currency)),
            account=account,
            tif=horizon,
            outsideRth=outside_rth,
        )
        key = instrument.to_normalized()
        self._journal.write("what_if", account=self.account, symbol=key, orders=[described(order)])
        with self._client._errors() as errors:
            state = await self._client._request(
                lambda: self._ib.whatIfOrderAsync(contract, order),
                f"what-if of {key}",
                limit=self._config.timeout,
            )
        if not isinstance(state, OrderState):
            failure = errors.by_contract.get(contract.conId)
            if failure is not None:
                code, message = failure
                raise ExchangeRequestError(
                    f"IBKR refused the what-if of {key}: {message}", code=code, reason=message
                )
            raise ExchangeConnectionError(f"IBKR sent no what-if answer for {key}")
        return MarginImpact(
            init_margin_change=parse_number(state.initMarginChange),
            maint_margin_change=parse_number(state.maintMarginChange),
            equity_with_loan_change=parse_number(state.equityWithLoanChange),
            commission=parse_number(state.commission),
            warning=state.warningText or "",
        )

    def _on_status(self, trade: Trade) -> None:
        if not self._ours(trade):
            return
        status = trade.orderStatus
        last = trade.log[-1] if trade.log else None
        code = last.errorCode if last is not None and last.errorCode else None
        reason = last.message if code is not None and last is not None else ""
        self._safe_write(
            "status",
            order_id=trade.order.orderId,
            perm_id=trade.order.permId or status.permId,
            ref=trade.order.orderRef,
            account=self.account,
            status=status.status,
            filled=status.filled,
            remaining=status.remaining,
            avg_price=status.avgFillPrice or None,
            code=code,
            reason=reason,
        )
        if code is not None:
            self._status_errors.add((trade.order.orderId, code))
        kind = self._kind_of(trade, code)
        if kind is not None:
            self._emit(kind, trade, code, reason)

    def _kind_of(self, trade: Trade, code: int | None) -> OrderEventKind | None:
        status = trade.orderStatus.status
        if status == OrderStatus.Filled:
            return "filled"
        if status in (OrderStatus.Cancelled, OrderStatus.ApiCancelled):
            if trade.order.orderId in self._changing and code not in (None, CANCELLED_CODE):
                return "notice"
            never_accepted = trade.order.orderId not in self._accepted
            if code == REJECTED_CODE or (code not in (None, CANCELLED_CODE) and never_accepted):
                return "rejected"
            return "cancelled"
        if status == OrderStatus.Inactive:
            return "rejected"
        if status == OrderStatus.ValidationError:
            return "rejected" if self._never_placed(trade) else "notice"
        if status in LIVE_ORDER:
            return "partial" if trade.orderStatus.filled > 0 else "accepted"
        return None

    def _on_fill(self, trade: Trade, fill: Fill) -> None:
        if not self._ours(trade):
            return
        execution = fill.execution
        self._safe_write(
            "fill",
            order_id=trade.order.orderId,
            perm_id=execution.permId,
            ref=trade.order.orderRef,
            account=self.account,
            exec_id=execution.execId,
            side=execution.side,
            shares=execution.shares,
            price=execution.price,
        )
        done = sum(f.execution.shares for f in trade.fills)
        paid = sum(f.execution.shares * f.execution.price for f in trade.fills)
        total = _to_float(trade.order.totalQuantity)
        kind: OrderEventKind = "filled" if done >= total else "partial"
        self._emit(kind, trade, None, "", filled=done, avg=paid / done if done else None)

    def _on_error(self, req_id: int, code: int, message: str, contract: Contract | None) -> None:
        trade = self._own_trade(req_id) if req_id > 0 else None
        if trade is None:
            return
        if (req_id, code) in self._status_errors:
            self._status_errors.discard((req_id, code))
            return
        self._safe_write(
            "error",
            order_id=req_id,
            ref=trade.order.orderRef,
            account=self.account,
            code=code,
            reason=message,
        )
        self._emit("notice", trade, code, message)

    def _emit(
        self,
        kind: OrderEventKind,
        trade: Trade,
        code: int | None,
        reason: str,
        *,
        filled: float | None = None,
        avg: float | None = None,
    ) -> None:
        order_id = trade.order.orderId
        done = trade.orderStatus.filled if filled is None else filled
        if kind in ("partial", "filled"):
            if done <= self._filled.get(order_id, 0.0):
                return
            self._filled[order_id] = done
            self._accepted.add(order_id)
            if kind == "filled":
                self._finished.add(order_id)
        elif kind == "accepted":
            if order_id in self._accepted or order_id in self._finished:
                return
            self._accepted.add(order_id)
        elif kind != "notice":
            if order_id in self._finished:
                return
            self._finished.add(order_id)
        event = self._event(kind, trade, code, reason, done, avg)
        for watch in list(self._watchers):
            self._client._deliver("order", event.ref or str(order_id), watch.callback, event)

    def _event(
        self,
        kind: OrderEventKind,
        trade: Trade,
        code: int | None,
        reason: str,
        filled: float,
        avg: float | None,
    ) -> OrderEvent:
        order, status = trade.order, trade.orderStatus
        total = _to_float(order.totalQuantity)
        return OrderEvent(
            kind=kind,
            order_id=order.orderId,
            perm_id=order.permId or status.permId,
            parent_id=order.parentId,
            ref=order.orderRef,
            role=role_of(order),
            symbol=trade.contract.symbol,
            side="buy" if order.action.upper() == "BUY" else "sell",
            shares=total,
            filled=filled,
            remaining=max(total - filled, 0.0),
            avg_price=avg if avg is not None else (status.avgFillPrice or None),
            status=OrderStatus.Filled if kind == "filled" else status.status,
            code=code,
            reason=reason,
            time=time.time(),
        )
