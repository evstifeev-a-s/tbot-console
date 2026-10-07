from __future__ import annotations

import asyncio
from typing import Any

from eventkit import Event
from ib_async import Contract, ContractDetails, ScanData, ScannerSubscription, Ticker
from ib_async.objects import (
    BarData,
    BarDataList,
    ExecutionFilter,
    Fill,
    OptionChain,
    ScanDataList,
    TagValue,
)
from ib_async.order import Trade


class FakeIB:
    """Stand-in for ``ib_async.IB`` — records calls, replays canned responses.

    ``raise_on`` maps a stage name ("connect", "details", "tickers", "history",
    "chain", "scan", "scanner_parameters", "executions", "orders") to the exception that stage should
    raise, which is how the error mapping in ``IBKRClient`` gets exercised
    without a gateway. ``history_errors``, ``ticker_errors`` (by conId) and
    ``scan_errors`` are emitted on ``errorEvent`` the way ib_async reports a
    failed request while still returning an empty result.
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
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

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
        return list(self.open_trades)

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
