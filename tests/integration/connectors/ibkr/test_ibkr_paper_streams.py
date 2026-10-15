from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

import pytest

from tbot_console.connectors.ibkr.client import IBKRClient
from tbot_console.connectors.ibkr.config import MarketDataType
from tbot_console.connectors.ibkr.instruments import IBKRInstrument
from tbot_console.connectors.ibkr.streams import STREAM_REFUSALS
from tbot_console.core.exceptions import ConfigurationError, ExchangeRequestError

pytestmark = [pytest.mark.integration, pytest.mark.private]

UPDATE_TIMEOUT = 15.0
NASDAQ = IBKRInstrument.parse("AAPL-USD-STK@ISLAND")
SMART = IBKRInstrument.parse("AAPL-USD-STK")
DELAYED = (MarketDataType.DELAYED, MarketDataType.DELAYED_FROZEN)


def last_session_morning() -> datetime:
    day = datetime.now(UTC).date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC)


async def first(queue: asyncio.Queue[Any], what: str) -> Any:
    try:
        return await asyncio.wait_for(queue.get(), UPDATE_TIMEOUT)
    except TimeoutError:
        pytest.skip(f"IBKR sent no {what} within the wait — market likely closed")


def refused_with_a_known_code(refused: ExchangeRequestError) -> NoReturn:
    assert refused.code in STREAM_REFUSALS, f"IB code {refused.code} has no reading: {refused}"
    pytest.skip(f"the account cannot stream this: {refused}")


async def test_depth_exchanges_are_listed(client: IBKRClient):
    venues = await client.depth_exchanges()

    assert venues
    assert all(venue.exchange and venue.sec_type for venue in venues)


async def test_depth_streams_or_names_its_refusal(client: IBKRClient):
    if client.config.market_data_type in DELAYED:
        with pytest.raises(ConfigurationError, match="real-time"):
            await client.watch_depth(NASDAQ, print)
        return
    books: asyncio.Queue[Any] = asyncio.Queue()
    try:
        watch = await client.watch_depth(NASDAQ, books.put_nowait, rows=5)
    except ExchangeRequestError as refused:
        refused_with_a_known_code(refused)
    try:
        book = await first(books, "depth update")
        assert len(book.bids) <= 5 and len(book.asks) <= 5
        assert [level.price for level in book.bids] == sorted(
            (level.price for level in book.bids), reverse=True
        )
        assert [level.price for level in book.asks] == sorted(level.price for level in book.asks)
    finally:
        await client.stop_depth(watch)
    assert client.active_streams == ()


async def test_trades_tape_streams_or_names_its_refusal(client: IBKRClient):
    if client.config.market_data_type in DELAYED:
        with pytest.raises(ConfigurationError, match="real-time"):
            await client.watch_tape(SMART, print)
        return
    prints: asyncio.Queue[Any] = asyncio.Queue()
    try:
        watch = await client.watch_tape(SMART, prints.put_nowait)
    except ExchangeRequestError as refused:
        refused_with_a_known_code(refused)
    try:
        trade = await first(prints, "print")
        assert trade.price > 0 and trade.size >= 0
        assert trade.symbol == "AAPL-USD-STK"
    finally:
        await client.stop_tape(watch)
    assert client.active_streams == ()


async def test_tick_history_pages_past_one_request(client: IBKRClient):
    try:
        prints = await client.fetch_ticks(SMART, last_session_morning(), limit=1500)
    except ExchangeRequestError as refused:
        pytest.skip(f"the account has no tick history permission: {refused}")
    if len(prints) < 1500:
        pytest.skip(f"IBKR returned only {len(prints)} prints for that session")

    times = [item.time for item in prints]
    assert times == sorted(times)
    assert all(item.price > 0 for item in prints)
