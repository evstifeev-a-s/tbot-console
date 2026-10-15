from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from ib_async.objects import (
    DepthMktDataDescription,
    HistoricalTickBidAsk,
    HistoricalTickLast,
    TickAttribBidAsk,
    TickAttribLast,
    TickByTickAllLast,
    TickByTickBidAsk,
)
from ib_async.util import UNSET_INTEGER

from tbot_console.connectors.ibkr import client as client_module
from tbot_console.connectors.ibkr.client import IBKRClient
from tbot_console.connectors.ibkr.config import IBKRConfig, MarketDataType
from tbot_console.connectors.ibkr.instruments import IBKRInstrument
from tbot_console.connectors.ibkr.models import (
    DepthBook,
    DepthExchange,
    DepthLevel,
    TapePrint,
    TapeQuote,
)
from tbot_console.connectors.ibkr.scanner import ScanRequest
from tbot_console.connectors.ibkr.streams import StreamRefusedError
from tbot_console.core.exceptions import (
    ConfigurationError,
    ExchangeConnectionError,
    ExchangeRequestError,
)

from .conftest import MOMENT, FakeIB, depth_tick, make_contract, make_details, push

ROUTED = IBKRInstrument.parse("DEMO-USD-STK@ISLAND")
SMART = IBKRInstrument.parse("DEMO-USD-STK")
OTHER = IBKRInstrument.parse("ELSE-USD-STK@ISLAND")
LIVE = IBKRConfig(market_data_type=MarketDataType.LIVE)


@pytest.fixture(autouse=True)
def quick_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "STREAM_CONFIRM_S", 0.01)


@pytest.fixture
def ib() -> FakeIB:
    fake = FakeIB()
    fake.connected = True
    fake.details_by_symbol = {
        "DEMO": [make_details(make_contract("DEMO", 4391, exchange="ISLAND"))],
        "ELSE": [make_details(make_contract("ELSE", 5502, exchange="ISLAND"))],
    }
    return fake


@pytest.fixture
def client(ib: FakeIB) -> IBKRClient:
    return IBKRClient(LIVE, ib=ib)


def _ignore(item: Any) -> None:
    return None


def trade_tick(price: float, size: float, venue: str = "ARCA", **attrib: bool) -> TickByTickAllLast:
    return TickByTickAllLast(1, MOMENT, price, size, TickAttribLast(**attrib), venue, " I")


async def test_the_book_is_built_and_updated_with_ib_row_semantics(client: IBKRClient, ib: FakeIB):
    books: list[DepthBook] = []
    watch = await client.watch_depth(ROUTED, books.append, rows=3)
    assert [(c.conId, rows, smart) for c, rows, smart in ib.depth_calls] == [(4391, 3, False)]

    push(
        watch.ticker,
        dom=[
            depth_tick(0, 0, 1, 2.50, 300, "NSDQ"),
            depth_tick(1, 0, 1, 2.49, 100),
            depth_tick(0, 0, 0, 2.52, 200),
            depth_tick(1, 0, 0, 2.53, 500),
        ],
    )
    first = books[-1]
    assert first.symbol == "DEMO-USD-STK" and first.smart is False
    assert first.time == MOMENT.timestamp()
    assert first.bids == (DepthLevel(2.50, 300.0, "NSDQ"), DepthLevel(2.49, 100.0, ""))
    assert first.asks == (DepthLevel(2.52, 200.0, ""), DepthLevel(2.53, 500.0, ""))

    push(
        watch.ticker,
        dom=[
            depth_tick(0, 1, 1, 2.50, 400, "NSDQ"),
            depth_tick(1, 2, 1, 2.49, 0),
            depth_tick(0, 0, 0, 2.51, 50),
            depth_tick(3, 0, 0, 2.54, 10),
        ],
    )
    second = books[-1]
    assert second.bids == (DepthLevel(2.50, 400.0, "NSDQ"),)
    assert [level.price for level in second.asks] == [2.51, 2.52, 2.53]


async def test_levels_come_best_first_even_from_a_scrambled_feed(client: IBKRClient):
    books: list[DepthBook] = []
    watch = await client.watch_depth(ROUTED, books.append)
    push(
        watch.ticker,
        dom=[
            depth_tick(0, 0, 1, 2.40, 100),
            depth_tick(1, 0, 1, 2.50, 100),
            depth_tick(2, 0, 1, 2.45, 0),
            depth_tick(9, 1, 1, 2.30, 100),
            depth_tick(0, 0, 0, 2.60, 100),
            depth_tick(1, 0, 0, 2.55, 100),
            depth_tick(2, 0, 0, float("nan"), 100),
        ],
    )
    assert [level.price for level in books[-1].bids] == [2.50, 2.40, 2.30]
    assert [level.price for level in books[-1].asks] == [2.55, 2.60]


async def test_a_book_reset_empties_the_book_before_new_rows(client: IBKRClient, ib: FakeIB):
    books: list[DepthBook] = []
    watch = await client.watch_depth(ROUTED, books.append)
    push(watch.ticker, dom=[depth_tick(0, 0, 1, 2.50, 300), depth_tick(0, 0, 0, 2.52, 200)])

    ticker = watch.ticker
    ticker.domTicks = [depth_tick(0, 1, 1, 2.51, 100)]
    ticker.domTicks += [depth_tick(0, 2, 0, 2.52, 0), depth_tick(0, 2, 1, 2.51, 0)]
    ib.errorEvent.emit(watch.req_id, 317, "Market depth data has been RESET", watch.contract)
    ticker.domTicks += [depth_tick(0, 0, 1, 2.40, 50)]
    ticker.updateEvent.emit(ticker)

    assert books[-1].bids == (DepthLevel(2.40, 50.0, ""),)
    assert books[-1].asks == ()

    ticker.domTicks = []
    ib.errorEvent.emit(watch.req_id, 317, "Market depth data has been RESET", watch.contract)
    ticker.updateEvent.emit(ticker)
    assert books[-1].bids == () and books[-1].asks == ()
    assert client.active_streams == (watch,)


async def test_smart_depth_merges_the_venues(client: IBKRClient, ib: FakeIB):
    ib.details_by_symbol["DEMO"] = [make_details(make_contract("DEMO", 4391))]
    books: list[DepthBook] = []
    watch = await client.watch_depth(SMART, books.append, smart=True)
    push(
        watch.ticker,
        dom=[depth_tick(0, 0, 1, 2.50, 300, "ISLAND"), depth_tick(1, 0, 1, 2.50, 100, "ARCA")],
    )
    assert ib.depth_calls[0][2] is True
    assert books[-1].smart is True
    assert [level.venue for level in books[-1].bids] == ["ISLAND", "ARCA"]


async def test_a_smart_routed_instrument_needs_smart_depth(client: IBKRClient, ib: FakeIB):
    with pytest.raises(ConfigurationError, match="smart=True"):
        await client.watch_depth(SMART, _ignore)
    with pytest.raises(ValueError, match="rows must be positive"):
        await client.watch_depth(ROUTED, _ignore, rows=0)
    assert ib.depth_calls == []


@pytest.mark.parametrize("feed", [MarketDataType.DELAYED, MarketDataType.DELAYED_FROZEN])
async def test_depth_and_tape_refuse_delayed_market_data(ib: FakeIB, feed: MarketDataType):
    delayed = IBKRClient(IBKRConfig(market_data_type=feed), ib=ib)
    with pytest.raises(ConfigurationError, match="real-time market data"):
        await delayed.watch_depth(ROUTED, _ignore)
    with pytest.raises(ConfigurationError, match="IBKR_MARKET_DATA_TYPE=1"):
        await delayed.watch_tape(ROUTED, _ignore)
    assert ib.depth_calls == [] and ib.tape_calls == [] and ib.detail_calls == []


async def test_depth_and_tape_share_one_stream_limit(ib: FakeIB):
    client = IBKRClient(LIVE, ib=ib)
    depth = await client.watch_depth(ROUTED, _ignore)
    await client.watch_tape(ROUTED, _ignore)
    await client.watch_tape(OTHER, _ignore, kind="quotes")
    with pytest.raises(StreamRefusedError, match="3 of 3 depth and tape streams") as refused:
        await client.watch_depth(OTHER, _ignore)
    assert refused.value.code is None
    assert len(ib.depth_calls) == 1

    await client.stop_depth(depth)
    await client.watch_depth(OTHER, _ignore)
    assert len(client.active_streams) == 3

    boosted = IBKRClient(IBKRConfig(market_data_type=MarketDataType.LIVE, stream_limit=4), ib=ib)
    for instrument, kind in ((ROUTED, "trades"), (ROUTED, "quotes"), (OTHER, "trades")):
        await boosted.watch_tape(instrument, _ignore, kind=kind)
    await boosted.watch_depth(ROUTED, _ignore)
    assert len(boosted.active_streams) == 4


async def test_a_second_stream_of_the_same_kind_on_one_instrument_is_refused(
    client: IBKRClient, ib: FakeIB
):
    await client.watch_depth(ROUTED, _ignore)
    await client.watch_tape(ROUTED, _ignore)
    with pytest.raises(StreamRefusedError, match="already streams depth"):
        await client.watch_depth(ROUTED, _ignore, rows=5)
    with pytest.raises(StreamRefusedError, match="already streams trades tape"):
        await client.watch_tape(ROUTED, _ignore)
    assert len(ib.depth_calls) == 1 and len(ib.tape_calls) == 1


@pytest.mark.parametrize(
    ("family", "code", "meaning"),
    [
        ("mktDepth", 309, "depth streams at once"),
        ("mktDepth", 10092, "no depth for this security type"),
        ("mktDepth", 354, "no market data subscription"),
        ("mktDepth", 10089, "extra market data subscription"),
        ("mktDepth", 10168, "delayed data is off"),
        ("AllLast", 10189, "tick-by-tick"),
        ("AllLast", 10190, "tick-by-tick streams at once"),
        ("BidAsk", 354, "no market data subscription"),
    ],
)
async def test_a_stream_ib_refuses_raises_with_its_code(
    client: IBKRClient, ib: FakeIB, family: str, code: int, meaning: str
):
    ib.stream_errors[family] = (code, "refused by the gateway")
    kind = "quotes" if family == "BidAsk" else "trades"
    with pytest.raises(ExchangeRequestError, match=meaning) as refused:
        if family == "mktDepth":
            await client.watch_depth(ROUTED, _ignore)
        else:
            await client.watch_tape(ROUTED, _ignore, kind=kind)
    assert refused.value.code == code
    assert "refused by the gateway" in str(refused.value)
    assert client.active_streams == ()
    assert ib.depth_cancels == [] and ib.tape_cancels == []


async def test_notices_keep_the_stream(
    client: IBKRClient, ib: FakeIB, caplog: pytest.LogCaptureFixture
):
    ib.stream_errors["mktDepth"] = (10090, "Part of requested market data is not subscribed")
    with caplog.at_level(logging.WARNING):
        watch = await client.watch_depth(ROUTED, _ignore)
    ib.errorEvent.emit(watch.req_id, 2104, "Market data farm connection is OK", None)
    ib.errorEvent.emit(watch.req_id + 100, 309, "Max number (3) of market depth requests", None)
    assert client.active_streams == (watch,)
    assert "part of this data is not subscribed" in caplog.text


async def test_a_later_failure_reaches_on_error_and_frees_the_slot(
    client: IBKRClient, ib: FakeIB, caplog: pytest.LogCaptureFixture
):
    failures: list[ExchangeConnectionError] = []
    watch = await client.watch_depth(ROUTED, _ignore, on_error=failures.append)
    with caplog.at_level(logging.ERROR):
        ib.errorEvent.emit(watch.req_id, 316, "Market depth data has been HALTED", watch.contract)
    assert isinstance(failures[0], ExchangeRequestError) and failures[0].code == 316
    assert watch.error is failures[0]
    assert client.active_streams == ()
    assert "halted this depth stream" in caplog.text
    await client.stop_depth(watch)
    assert ib.depth_cancels == []


async def test_the_trades_tape_delivers_each_print_in_order(client: IBKRClient, ib: FakeIB):
    prints: list[TapePrint] = []
    watch = await client.watch_tape(ROUTED, prints.append)
    assert [(c.conId, tick_type) for c, tick_type in ib.tape_calls] == [(4391, "AllLast")]
    quote = TickByTickBidAsk(MOMENT, 2.50, 2.52, 300, 200, TickAttribBidAsk())
    push(
        watch.ticker,
        tape=[
            trade_tick(2.51, 100, unreported=True),
            trade_tick(float("nan"), 0),
            quote,
            trade_tick(2.52, 300, "ISLAND", pastLimit=True),
        ],
    )
    assert prints == [
        TapePrint("DEMO-USD-STK", MOMENT.timestamp(), 2.51, 100.0, "ARCA", "I", False, True),
        TapePrint("DEMO-USD-STK", MOMENT.timestamp(), 2.52, 300.0, "ISLAND", "I", True, False),
    ]


async def test_the_quotes_tape_delivers_bid_and_ask(client: IBKRClient, ib: FakeIB):
    quotes: list[TapeQuote] = []
    watch = await client.watch_tape(ROUTED, quotes.append, kind="quotes")
    assert ib.tape_calls[0][1] == "BidAsk"
    push(
        watch.ticker,
        tape=[
            TickByTickBidAsk(MOMENT, 2.50, 2.52, 300, 200, TickAttribBidAsk()),
            TickByTickBidAsk(MOMENT, 0.0, 2.53, 0, 100, TickAttribBidAsk()),
            trade_tick(2.51, 100),
        ],
    )
    assert quotes == [
        TapeQuote("DEMO-USD-STK", MOMENT.timestamp(), 2.50, 2.52, 300.0, 200.0),
        TapeQuote("DEMO-USD-STK", MOMENT.timestamp(), None, 2.53, 0.0, 100.0),
    ]


async def test_depth_and_tape_of_one_instrument_do_not_cross(client: IBKRClient):
    books: list[DepthBook] = []
    prints: list[TapePrint] = []
    depth = await client.watch_depth(ROUTED, books.append)
    tape = await client.watch_tape(ROUTED, prints.append)
    assert depth.ticker is tape.ticker
    push(tape.ticker, tape=[trade_tick(2.51, 100)])
    push(depth.ticker, dom=[depth_tick(0, 0, 1, 2.50, 300)])
    assert len(books) == 1 and len(prints) == 1


async def test_callbacks_may_be_async_and_a_failing_one_keeps_the_stream(
    client: IBKRClient, caplog: pytest.LogCaptureFixture
):
    calls: list[int] = []

    def flaky(book: DepthBook) -> None:
        calls.append(len(book.bids))
        raise RuntimeError("boom")

    depth = await client.watch_depth(ROUTED, flaky)
    with caplog.at_level(logging.ERROR):
        push(depth.ticker, dom=[depth_tick(0, 0, 1, 2.50, 300)])
        push(depth.ticker, dom=[depth_tick(1, 0, 1, 2.49, 100)])
    assert calls == [1, 2]
    assert "depth callback failed" in caplog.text

    seen: list[float] = []

    async def collect(item: TapePrint) -> None:
        seen.append(item.price)

    async def broken(item: TapePrint) -> None:
        raise RuntimeError("bad")

    tape = await client.watch_tape(ROUTED, collect)
    other = await client.watch_tape(OTHER, broken)
    with caplog.at_level(logging.ERROR):
        push(tape.ticker, tape=[trade_tick(2.51, 100), trade_tick(2.52, 100)])
        push(other.ticker, tape=[trade_tick(9.0, 1)])
        for _ in range(3):
            await asyncio.sleep(0)
    assert seen == [2.51, 2.52]
    assert "trades tape callback raised" in caplog.text


async def test_stop_cancels_the_stream_once(client: IBKRClient, ib: FakeIB):
    books: list[DepthBook] = []
    depth = await client.watch_depth(ROUTED, books.append)
    tape = await client.watch_tape(ROUTED, _ignore, kind="quotes")
    await client.stop_depth(depth)
    await client.stop_depth(depth)
    await client.stop_tape(tape)
    push(depth.ticker, dom=[depth_tick(0, 0, 1, 2.50, 300)])
    assert books == []
    assert [(c.conId, smart) for c, smart in ib.depth_cancels] == [(4391, False)]
    assert [(c.conId, tick_type) for c, tick_type in ib.tape_cancels] == [(4391, "BidAsk")]
    assert client.active_streams == ()


async def test_disconnect_ends_every_stream_and_scan(client: IBKRClient, ib: FakeIB):
    failures: list[ExchangeConnectionError] = []
    await client.watch_depth(ROUTED, _ignore, on_error=failures.append)
    await client.watch_tape(OTHER, _ignore, on_error=failures.append)
    await client.watch_scan(ScanRequest(scan_code="TOP_PERC_GAIN"), _ignore)
    await client.disconnect()
    assert len(ib.depth_cancels) == 1 and len(ib.tape_cancels) == 1
    assert len(ib.cancelled_scans) == 1
    assert client.active_streams == () and client.active_scans == ()
    assert failures == []


async def test_a_lost_connection_ends_the_streams_and_frees_their_slots(
    client: IBKRClient, ib: FakeIB
):
    failures: list[ExchangeConnectionError] = []
    await client.watch_depth(ROUTED, _ignore, on_error=failures.append)
    await client.watch_tape(ROUTED, _ignore, on_error=failures.append)
    ib.disconnect()
    assert [type(error) for error in failures] == [ExchangeConnectionError] * 2
    assert "connection lost" in str(failures[0])
    assert client.active_streams == ()
    await client.connect()
    await client.watch_depth(ROUTED, _ignore)
    assert len(client.active_streams) == 1


async def test_depth_exchanges_are_listed(client: IBKRClient, ib: FakeIB):
    ib.depth_venues = [
        DepthMktDataDescription("NYSE", "STK", "NYSE", "Deep2", UNSET_INTEGER),
        DepthMktDataDescription("ISLAND", "STK", "NASDAQ", "Deep2", 1),
        DepthMktDataDescription("CME", "FUT", "", "Deep", 2),
    ]
    assert await client.depth_exchanges() == [
        DepthExchange("CME", "FUT", "", "Deep", 2),
        DepthExchange("ISLAND", "STK", "NASDAQ", "Deep2", 1),
        DepthExchange("NYSE", "STK", "NYSE", "Deep2", None),
    ]


def history(seconds: list[int], kind: str = "trades") -> list[Any]:
    base = datetime(2026, 10, 14, 14, 0, tzinfo=UTC)
    ticks: list[Any] = []
    for index, offset in enumerate(seconds):
        moment = base + timedelta(seconds=offset)
        if kind == "trades":
            ticks.append(
                HistoricalTickLast(moment, TickAttribLast(), 2.0 + index / 1000, 100, "ARCA", "")
            )
        else:
            ticks.append(
                HistoricalTickBidAsk(moment, TickAttribBidAsk(), 2.0, 2.0 + index / 1000, 100, 50)
            )
    return ticks


START = datetime(2026, 10, 14, 14, 0, tzinfo=UTC)


async def test_tick_history_is_paged_past_the_ib_cap(client: IBKRClient, ib: FakeIB):
    ib.history_ticks = history([second // 2 for second in range(2500)])
    prints = await client.fetch_ticks(ROUTED, START, limit=2500)
    assert [p.price for p in prints] == [2.0 + i / 1000 for i in range(2500)]
    assert [call["count"] for call in ib.tick_calls] == [1000, 1000, 504]
    assert all(call["start"].microsecond == 0 for call in ib.tick_calls)
    assert {call["show"] for call in ib.tick_calls} == {"TRADES"}
    assert all(call["end"] == "" for call in ib.tick_calls)


async def test_paging_never_repeats_the_ticks_of_the_boundary_second(
    client: IBKRClient, ib: FakeIB, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(client_module, "TICK_PAGE", 4)
    ib.history_ticks = history([0, 0, 0, 1, 1, 1, 2, 2, 3])
    prints = await client.fetch_ticks(ROUTED, START, limit=100)
    assert [p.price for p in prints] == [2.0 + i / 1000 for i in range(9)]


async def test_a_second_busier_than_a_page_is_skipped_with_a_warning(
    client: IBKRClient,
    ib: FakeIB,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setattr(client_module, "TICK_PAGE", 3)
    ib.history_ticks = history([0, 0, 0, 0, 0, 1, 1])
    with caplog.at_level(logging.WARNING):
        prints = await client.fetch_ticks(ROUTED, START, limit=100)
    assert [p.price for p in prints] == [2.0, 2.001, 2.002, 2.005, 2.006]
    assert "cannot page inside one second" in caplog.text


async def test_tick_history_stops_at_the_end_and_the_limit(client: IBKRClient, ib: FakeIB):
    ib.history_ticks = history(list(range(10)))
    ib.history_ticks[2] = ib.history_ticks[2]._replace(price=float("nan"))
    assert len(await client.fetch_ticks(ROUTED, START)) == 9
    ib.history_ticks = history(list(range(10)))
    until = START + timedelta(seconds=4)
    assert len(await client.fetch_ticks(ROUTED, START, until)) == 5
    assert len(await client.fetch_ticks(ROUTED, START, limit=3)) == 3
    assert await client.fetch_ticks(ROUTED, START, START - timedelta(seconds=1)) == []
    assert await client.fetch_ticks(ROUTED, START, limit=0) == []


async def test_quote_history_reads_bid_and_ask(client: IBKRClient, ib: FakeIB):
    ib.history_ticks = history([0, 1], kind="quotes")
    quotes = await client.fetch_ticks(
        ROUTED, START.replace(tzinfo=None), kind="quotes", rth_only=True
    )
    assert quotes == [
        TapeQuote("DEMO-USD-STK", START.timestamp(), 2.0, 2.0, 100.0, 50.0),
        TapeQuote("DEMO-USD-STK", START.timestamp() + 1, 2.0, 2.001, 100.0, 50.0),
    ]
    assert ib.tick_calls[0]["show"] == "BID_ASK" and ib.tick_calls[0]["rth"] is True
    assert ib.tick_calls[0]["start"] == START


async def test_a_refused_tick_history_is_an_error_with_its_code(client: IBKRClient, ib: FakeIB):
    ib.tick_errors = [
        (162, "Historical Market Data Service error message:No market data permissions")
    ]
    with pytest.raises(ExchangeRequestError, match="No market data permissions") as refused:
        await client.fetch_ticks(ROUTED, START)
    assert refused.value.code == 162
    ib.tick_errors = [(162, "HMDS query returned no data")]
    assert await client.fetch_ticks(ROUTED, START) == []
    ib.tick_errors = []
    ib.raise_on["ticks"] = TimeoutError()
    with pytest.raises(ExchangeConnectionError):
        await client.fetch_ticks(ROUTED, START)
    with pytest.raises(ValueError, match="trades or quotes"):
        await client.fetch_ticks(ROUTED, START, kind="bars")
