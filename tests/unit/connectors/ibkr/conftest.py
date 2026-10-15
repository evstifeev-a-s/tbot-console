from __future__ import annotations

import asyncio
import copy
import math
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from eventkit import Event
from ib_async import Contract, ContractDetails, ScanData, ScannerSubscription, Ticker
from ib_async.objects import (
    BarData,
    BarDataList,
    CommissionReport,
    DepthMktDataDescription,
    Execution,
    ExecutionFilter,
    Fill,
    MktDepthData,
    OptionChain,
    Position,
    ScanDataList,
    TagValue,
    TradeLogEntry,
)
from ib_async.order import Order, OrderState, OrderStatus, Trade

MOMENT = datetime(2026, 10, 15, 14, 30, tzinfo=UTC)


class FakeWrapper:
    def __init__(self) -> None:
        self.ticker2ReqId: defaultdict[str, dict[Ticker, int]] = defaultdict(dict)


class FakeClient:
    def __init__(self) -> None:
        self.next_id = 100

    def getReqId(self) -> int:
        self.next_id += 1
        return self.next_id - 1


Rule = tuple[Callable[[Order], bool], int, str]


class FakeIB:
    """Stand-in for ``ib_async.IB`` — records calls, replays canned responses.

    ``raise_on`` maps a stage name ("connect", "details", "tickers", "history",
    "chain", "scan", "scanner_parameters", "executions", "orders", "depth_exchanges",
    "ticks") to the exception that stage should raise, which is how the error mapping
    in ``IBKRClient`` gets exercised without a gateway. ``history_errors``,
    ``ticker_errors`` (by conId), ``scan_errors`` and ``tick_errors`` are emitted on
    ``errorEvent`` the way ib_async reports a failed request while still returning an
    empty result. Depth and tape streams share one ``Ticker`` per conId as ib_async's do;
    ``stream_errors`` (by tick type: "mktDepth", "AllLast", "BidAsk") is emitted on the
    next loop turn after the request, as a refusal from the gateway arrives.

    Orders follow the gateway's answers as ib_async surfaces them: ``placeOrder`` keeps
    an order untransmitted until one with ``transmit=True`` arrives, then confirms the
    batch (``Submitted`` for an entry, ``PreSubmitted`` for a child) or rejects the
    orders a ``reject_when`` rule matches the way ib_async reports an order error
    (status ``Cancelled`` with the code in the trade log, ``orderStatusEvent``, then
    ``errorEvent``). ``fill`` executes shares; it models IBKR's bracket rule that the
    children of one parent form one OCA group reduced by each fill, so a child that fills
    lifts its siblings by the same amount and one that reaches zero is cancelled.
    """

    def __init__(self) -> None:
        self.connected = False
        self.connect_kwargs: dict[str, Any] = {}
        self.market_data_type: int | None = None
        self.details: list[ContractDetails] = []
        self.details_by_symbol: dict[str, list[ContractDetails]] = {}
        self.tickers: list[Ticker] = []
        self.bars: list[BarData] = []
        self.chains: list[OptionChain] = []
        self.detail_calls: list[Contract] = []
        self.ticker_calls: list[tuple[Contract, ...]] = []
        self.history_calls: list[dict[str, Any]] = []
        self.chain_calls: list[tuple[str, str, str, int]] = []
        self.scan_rows: list[ScanData] = []
        self.scanner_parameters_xml = ""
        self.scan_calls: list[tuple[ScannerSubscription, list[TagValue]]] = []
        self.scanner_parameter_calls = 0
        self.live_scans: list[ScanDataList] = []
        self.cancelled_scans: list[ScanDataList] = []
        self.raise_on: dict[str, BaseException] = {}
        self.errorEvent = Event("errorEvent")
        self.history_errors: list[tuple[int, str]] = []
        self.history_delay = 0.0
        self.ticker_errors: dict[int, tuple[int, str]] = {}
        self.scan_errors: list[tuple[int, str]] = []
        self.last_req_id = 0
        self.fills: list[Fill] = []
        self.execution_calls: list[ExecutionFilter] = []
        self.open_trades: list[Trade] = []
        self.open_order_calls = 0
        self.wrapper = FakeWrapper()
        self.disconnectedEvent = Event("disconnectedEvent")
        self.stream_tickers: dict[int, Ticker] = {}
        self.depth_calls: list[tuple[Contract, int, bool]] = []
        self.depth_cancels: list[tuple[Contract, bool]] = []
        self.tape_calls: list[tuple[Contract, str]] = []
        self.tape_cancels: list[tuple[Contract, str]] = []
        self.stream_errors: dict[str, tuple[int, str]] = {}
        self.depth_venues: list[DepthMktDataDescription] = []
        self.history_ticks: list[Any] = []
        self.tick_calls: list[dict[str, Any]] = []
        self.tick_errors: list[tuple[int, str]] = []
        self.client = FakeClient()
        self.client_id = 0
        self.accounts: list[str] = ["DU1234567"]
        self.orderStatusEvent = Event("orderStatusEvent")
        self.execDetailsEvent = Event("execDetailsEvent")
        self.placed: list[Order] = []
        self.cancel_calls: list[int] = []
        self.own_trades: dict[int, Trade] = {}
        self.untransmitted: list[Trade] = []
        self.answer_orders = True
        self.reject_when: list[Rule] = []
        self.modify_error: tuple[int, str] | None = None
        self.position_rows: list[Position] = []
        self.what_if_state: OrderState | None = None
        self.what_if_errors: list[tuple[int, str]] = []
        self.what_if_calls: list[Order] = []
        self.executions = 0

    def _req_id(self) -> int:
        self.last_req_id += 1
        return self.last_req_id

    def _maybe_raise(self, stage: str) -> None:
        error = self.raise_on.get(stage)
        if error is not None:
            raise error

    def isConnected(self) -> bool:
        return self.connected

    async def connectAsync(self, **kwargs: Any) -> None:
        self._maybe_raise("connect")
        self.connect_kwargs = kwargs
        self.client_id = int(kwargs.get("clientId", 0))
        self.connected = True

    def disconnect(self) -> None:
        was_connected = self.connected
        self.connected = False
        if was_connected:
            self.disconnectedEvent.emit()

    def reqMarketDataType(self, market_data_type: int) -> None:
        self.market_data_type = market_data_type

    async def reqContractDetailsAsync(self, contract: Contract) -> list[ContractDetails]:
        self.detail_calls.append(contract)
        self._maybe_raise("details")
        if self.details_by_symbol:
            return list(self.details_by_symbol.get(contract.symbol, []))
        return list(self.details)

    async def reqTickersAsync(
        self, *contracts: Contract, regulatorySnapshot: bool = False
    ) -> list[Ticker]:
        self.ticker_calls.append(contracts)
        self._maybe_raise("tickers")
        for contract in contracts:
            req_id = self._req_id()
            failure = self.ticker_errors.get(contract.conId)
            if failure is not None:
                self.errorEvent.emit(req_id, *failure, contract)
        return list(self.tickers)

    async def reqHistoricalDataAsync(self, contract: Contract, **kwargs: Any) -> BarDataList:
        self.history_calls.append({"contract": contract, **kwargs})
        self._maybe_raise("history")
        bars = BarDataList(self.bars)
        bars.reqId = self._req_id()
        self.errorEvent.emit(-1, 2104, "Market data farm connection is OK:usfarm", None)
        for code, message in self.history_errors:
            self.errorEvent.emit(bars.reqId, code, message, contract)
        if self.history_errors:
            bars.clear()
        if self.history_delay:
            await asyncio.sleep(self.history_delay)
        return bars

    async def reqExecutionsAsync(self, execFilter: ExecutionFilter | None = None) -> list[Fill]:
        self.execution_calls.append(execFilter or ExecutionFilter())
        self._maybe_raise("executions")
        return list(self.fills)

    async def reqAllOpenOrdersAsync(self) -> list[Trade]:
        self.open_order_calls += 1
        self._maybe_raise("orders")
        working = [trade for trade in self.own_trades.values() if not trade.isDone()]
        return [*self.open_trades, *working]

    def managedAccounts(self) -> list[str]:
        return list(self.accounts)

    def trades(self) -> list[Trade]:
        return [*self.own_trades.values(), *self.open_trades]

    def openTrades(self) -> list[Trade]:
        return [trade for trade in self.trades() if not trade.isDone()]

    def placeOrder(self, contract: Contract, order: Order) -> Trade:
        self.placed.append(copy.copy(order))
        known = self.own_trades.get(order.orderId)
        if known is not None:
            return self._modify(known, order)
        order.clientId = self.client_id
        status = OrderStatus(orderId=order.orderId, status=OrderStatus.PendingSubmit)
        log = [TradeLogEntry(MOMENT, OrderStatus.PendingSubmit)]
        trade = Trade(contract, order, status, [], log)
        self.own_trades[order.orderId] = trade
        self.untransmitted.append(trade)
        if order.transmit and self.answer_orders:
            self.transmit()
        return trade

    def transmit(self) -> None:
        batch, self.untransmitted = self.untransmitted, []
        refused: set[int] = set()
        for trade in batch:
            rule = next(((c, m) for test, c, m in self.reject_when if test(trade.order)), None)
            if rule is not None or trade.order.parentId in refused:
                refused.add(trade.order.orderId)
                self.fail(trade, *(rule or (202, "Order Canceled - reason: parent rejected")))
                continue
            trade.order.permId = trade.order.orderId * 10
            self.set_status(trade, "PreSubmitted" if trade.order.parentId else "Submitted")

    def set_status(self, trade: Trade, status: str, **fields: Any) -> None:
        trade.orderStatus.status = status
        trade.orderStatus.permId = trade.order.permId
        for name, value in fields.items():
            setattr(trade.orderStatus, name, value)
        trade.log.append(TradeLogEntry(MOMENT, status, ""))
        self.orderStatusEvent.emit(trade)

    def fail(self, trade: Trade, code: int, message: str, *, warning: bool = False) -> None:
        status = OrderStatus.ValidationError if warning else OrderStatus.Cancelled
        trade.orderStatus.status = status
        trade.log.append(TradeLogEntry(MOMENT, status, message, code))
        self.orderStatusEvent.emit(trade)
        self.errorEvent.emit(trade.order.orderId, code, message, trade.contract)

    def _modify(self, trade: Trade, order: Order) -> Trade:
        trade.log.append(TradeLogEntry(MOMENT, trade.orderStatus.status, "Modify"))
        if self.modify_error is not None:
            self.fail(trade, *self.modify_error, warning=True)
            return trade
        trade.order.lmtPrice = order.lmtPrice
        trade.order.auxPrice = order.auxPrice
        trade.order.totalQuantity = order.totalQuantity
        trade.log.append(TradeLogEntry(MOMENT, trade.orderStatus.status, "Modified"))
        self.orderStatusEvent.emit(trade)
        return trade

    def cancelOrder(self, order: Order) -> Trade | None:
        self.cancel_calls.append(order.orderId)
        trade = self.own_trades.get(order.orderId)
        if trade is None or trade.isDone():
            return trade
        self.set_status(trade, OrderStatus.Cancelled)
        if not trade.order.parentId and trade.orderStatus.filled == 0:
            for child in self.children(order.orderId):
                if not child.isDone():
                    self.set_status(child, OrderStatus.Cancelled)
        return trade

    def children(self, parent_id: int) -> list[Trade]:
        return [t for t in self.own_trades.values() if t.order.parentId == parent_id]

    def fill(self, order_id: int, shares: float, price: float) -> None:
        trade = self.own_trades[order_id]
        order = trade.order
        self.executions += 1
        execution = Execution(
            execId=f"0001.{self.executions}",
            time=MOMENT,
            acctNumber=order.account,
            side="BOT" if order.action == "BUY" else "SLD",
            shares=shares,
            price=price,
            permId=order.permId,
            clientId=order.clientId,
            orderId=order_id,
            orderRef=order.orderRef,
        )
        fill = Fill(trade.contract, execution, CommissionReport(), MOMENT)
        trade.fills.append(fill)
        self.execDetailsEvent.emit(trade, fill)
        done = sum(f.execution.shares for f in trade.fills)
        paid = sum(f.execution.shares * f.execution.price for f in trade.fills)
        total = order.totalQuantity
        self.set_status(
            trade,
            OrderStatus.Filled if done >= total else OrderStatus.Submitted,
            filled=done,
            remaining=total - done,
            avgFillPrice=paid / done,
        )
        if not order.parentId:
            for child in self.children(order_id):
                if child.orderStatus.status == OrderStatus.PreSubmitted:
                    self.set_status(child, OrderStatus.Submitted)
            return
        for sibling in self.children(order.parentId):
            if sibling is trade or sibling.isDone():
                continue
            left = sibling.order.totalQuantity - shares
            if left <= 0:
                self.set_status(sibling, OrderStatus.Cancelled)
            else:
                sibling.order.totalQuantity = left
                self.set_status(sibling, sibling.orderStatus.status, remaining=left)

    async def reqPositionsAsync(self) -> list[Position]:
        self._maybe_raise("positions")
        return list(self.position_rows)

    async def whatIfOrderAsync(self, contract: Contract, order: Order) -> OrderState | list[Any]:
        self.what_if_calls.append(order)
        self._maybe_raise("what_if")
        for code, message in self.what_if_errors:
            self.errorEvent.emit(self._req_id(), code, message, contract)
        if self.what_if_errors or self.what_if_state is None:
            return []
        return self.what_if_state

    async def reqSecDefOptParamsAsync(
        self,
        underlyingSymbol: str,
        futFopExchange: str,
        underlyingSecType: str,
        underlyingConId: int,
    ) -> list[OptionChain]:
        self.chain_calls.append(
            (underlyingSymbol, futFopExchange, underlyingSecType, underlyingConId)
        )
        self._maybe_raise("chain")
        return list(self.chains)

    async def reqScannerParametersAsync(self) -> str:
        self.scanner_parameter_calls += 1
        self._maybe_raise("scanner_parameters")
        return self.scanner_parameters_xml

    async def reqScannerDataAsync(
        self,
        subscription: ScannerSubscription,
        scannerSubscriptionOptions: list[TagValue] | None = None,
        scannerSubscriptionFilterOptions: list[TagValue] | None = None,
    ) -> ScanDataList:
        self.scan_calls.append((subscription, list(scannerSubscriptionFilterOptions or [])))
        self._maybe_raise("scan")
        rows = ScanDataList(self.scan_rows)
        rows.reqId = self._req_id()
        for code, message in self.scan_errors:
            self.errorEvent.emit(rows.reqId, code, message, None)
        if self.scan_errors:
            rows.clear()
        return rows

    def reqScannerSubscription(
        self,
        subscription: ScannerSubscription,
        scannerSubscriptionOptions: list[TagValue] | None = None,
        scannerSubscriptionFilterOptions: list[TagValue] | None = None,
    ) -> ScanDataList:
        self.scan_calls.append((subscription, list(scannerSubscriptionFilterOptions or [])))
        self._maybe_raise("scan")
        rows = ScanDataList(self.scan_rows)
        rows.subscription = subscription
        self.live_scans.append(rows)
        return rows

    def cancelScannerSubscription(self, dataList: ScanDataList) -> None:
        self.cancelled_scans.append(dataList)

    def _stream(self, contract: Contract, family: str) -> Ticker:
        req_id = self._req_id()
        ticker = self.stream_tickers.setdefault(contract.conId, Ticker(contract=contract))
        self.wrapper.ticker2ReqId[family][ticker] = req_id
        failure = self.stream_errors.get(family)
        if failure is not None:
            asyncio.get_running_loop().call_soon(self.errorEvent.emit, req_id, *failure, contract)
        return ticker

    def request_id(self, ticker: Ticker, family: str) -> int:
        return self.wrapper.ticker2ReqId[family][ticker]

    def reqMktDepth(
        self,
        contract: Contract,
        numRows: int = 5,
        isSmartDepth: bool = False,
        mktDepthOptions: Any = None,
    ) -> Ticker:
        self.depth_calls.append((contract, numRows, isSmartDepth))
        return self._stream(contract, "mktDepth")

    def cancelMktDepth(self, contract: Contract, isSmartDepth: bool = False) -> None:
        self.depth_cancels.append((contract, isSmartDepth))

    def reqTickByTickData(
        self,
        contract: Contract,
        tickType: str,
        numberOfTicks: int = 0,
        ignoreSize: bool = False,
    ) -> Ticker:
        self.tape_calls.append((contract, tickType))
        return self._stream(contract, tickType)

    def cancelTickByTickData(self, contract: Contract, tickType: str) -> bool:
        self.tape_cancels.append((contract, tickType))
        return True

    async def reqMktDepthExchangesAsync(self) -> list[DepthMktDataDescription]:
        self._maybe_raise("depth_exchanges")
        return list(self.depth_venues)

    async def reqHistoricalTicksAsync(
        self,
        contract: Contract,
        startDateTime: datetime,
        endDateTime: Any,
        numberOfTicks: int,
        whatToShow: str,
        useRth: bool,
        ignoreSize: bool = False,
        miscOptions: Any = None,
    ) -> list[Any]:
        self.tick_calls.append(
            {
                "contract": contract,
                "start": startDateTime,
                "end": endDateTime,
                "count": numberOfTicks,
                "show": whatToShow,
                "rth": useRth,
            }
        )
        self._maybe_raise("ticks")
        req_id = self._req_id()
        for code, message in self.tick_errors:
            self.errorEvent.emit(req_id, code, message, contract)
        if self.tick_errors:
            return []
        start = math.floor(startDateTime.timestamp())
        later = [tick for tick in self.history_ticks if tick.time.timestamp() >= start]
        return later[:numberOfTicks]


def push(ticker: Ticker, *, dom: Any = (), tape: Any = (), moment: datetime = MOMENT) -> None:
    ticker.domTicks = list(dom)
    ticker.tickByTicks = list(tape)
    ticker.time = moment
    ticker.updateEvent.emit(ticker)


def depth_tick(
    position: int, operation: int, side: int, price: float, size: float, venue: str = ""
) -> MktDepthData:
    return MktDepthData(MOMENT, position, venue, operation, side, price, size)


def make_scan_data(rank: int, contract: Contract, distance: str = "") -> ScanData:
    return ScanData(
        rank=rank,
        contractDetails=ContractDetails(contract=contract),
        distance=distance,
        benchmark="",
        projection="",
        legsStr="",
    )


def make_contract(
    symbol: str,
    con_id: int,
    sec_type: str = "STK",
    exchange: str = "SMART",
    currency: str = "USD",
    local_symbol: str = "",
    trading_class: str = "",
    multiplier: str = "",
) -> Contract:
    return Contract(
        secType=sec_type,
        conId=con_id,
        symbol=symbol,
        exchange=exchange,
        currency=currency,
        localSymbol=local_symbol or symbol,
        tradingClass=trading_class,
        multiplier=multiplier,
    )


def make_details(contract: Contract, min_tick: float = 0.01) -> ContractDetails:
    return ContractDetails(contract=contract, minTick=min_tick)


def make_ticker(contract: Contract, **fields: Any) -> Ticker:
    ticker = Ticker()
    ticker.contract = contract
    for name, value in fields.items():
        setattr(ticker, name, value)
    return ticker


def make_bar(
    date: Any,
    open_: float = 1.0,
    high: float = 2.0,
    low: float = 0.5,
    close: float = 1.5,
    volume: float = 10.0,
) -> BarData:
    return BarData(date=date, open=open_, high=high, low=low, close=close, volume=volume)
