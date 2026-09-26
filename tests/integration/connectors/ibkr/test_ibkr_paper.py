from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from tbot_console.analysis.models import Timeframe
from tbot_console.connectors.ibkr.client import IBKRClient
from tbot_console.connectors.ibkr.config import IBKRConfig
from tbot_console.connectors.ibkr.instruments import IBKRInstrument
from tbot_console.connectors.ibkr.scanner import ScanRequest

pytestmark = [pytest.mark.integration, pytest.mark.private]

PROBE_TIMEOUT = 1.0
SCAN_UPDATE_TIMEOUT = 20.0


async def _port_is_open(host: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=PROBE_TIMEOUT
        )
    except (TimeoutError, OSError):
        return False
    writer.close()
    await writer.wait_closed()
    return True


@pytest.fixture
async def client() -> AsyncIterator[IBKRClient]:
    config = IBKRConfig.from_env()
    if not config.is_paper_port:
        pytest.skip(f"IBKR integration runs against a paper gateway only, got port {config.port}")
    if not await _port_is_open(config.host, config.port):
        pytest.skip(
            f"No IBKR gateway listening on {config.host}:{config.port} — "
            "start TWS or IB Gateway in paper mode with the API enabled"
        )

    connected = IBKRClient(config)
    await connected.connect()
    try:
        yield connected
    finally:
        await connected.disconnect()


async def test_connects_readonly(client: IBKRClient):
    assert client.is_connected
    assert client.config.readonly


async def test_qualifies_a_stock(client: IBKRClient):
    spec = await client.qualify(IBKRInstrument.parse("AAPL-USD-STK"))

    assert spec.con_id > 0
    assert spec.local_symbol
    assert spec.min_tick > 0


async def test_fetches_daily_bars(client: IBKRClient):
    candles = await client.fetch_bars(IBKRInstrument.parse("AAPL-USD-STK"), Timeframe.D1, 20)

    assert candles
    assert all(candle.high >= candle.low for candle in candles)
    assert all(candle.timestamp > 0 for candle in candles)
    assert candles == sorted(candles, key=lambda candle: candle.timestamp)


async def test_fetches_a_quote(client: IBKRClient):
    quote = await client.fetch_quote(IBKRInstrument.parse("AAPL-USD-STK"))

    assert quote is not None
    assert quote.symbol == "AAPL-USD-STK"
    assert quote.mid is None or quote.mid > 0


async def test_fetches_an_option_chain(client: IBKRClient):
    chains = await client.fetch_option_chain(IBKRInstrument.parse("AAPL-USD-STK"))

    assert chains
    assert any(chain.expirations and chain.strikes for chain in chains)


async def test_scanner_vocabulary_parses(client: IBKRClient):
    vocabulary = await client.scan_vocabulary()

    assert not vocabulary.is_empty
    assert vocabulary.knows_scan_code("TOP_PERC_GAIN")
    assert vocabulary.knows_instrument("STK")
    assert "STK.US.MAJOR" in vocabulary.locations_for("STK")
    assert vocabulary.filters


async def test_scans_top_gainers(client: IBKRClient):
    hits = await client.scan(
        ScanRequest(scan_code="TOP_PERC_GAIN", above_price=5.0, limit=10), validate=True
    )

    assert hits
    assert [hit.rank for hit in hits] == sorted(hit.rank for hit in hits)
    assert any(hit.instrument is not None for hit in hits)
    assert all(hit.con_id > 0 for hit in hits)


async def test_quotes_come_back_keyed_by_instrument(client: IBKRClient):
    instruments = [IBKRInstrument.parse("AAPL-USD-STK"), IBKRInstrument.parse("EUR-USD-CASH")]

    quotes = await client.fetch_quotes(instruments)

    assert set(quotes) <= {i.to_normalized() for i in instruments}
    assert quotes
    assert all(quote.symbol == key for key, quote in quotes.items())


async def test_streaming_scan_delivers_and_stops(client: IBKRClient):
    received: asyncio.Queue[list] = asyncio.Queue()
    watch = await client.watch_scan(ScanRequest(scan_code="MOST_ACTIVE", limit=10), received.put)
    try:
        try:
            hits = await asyncio.wait_for(received.get(), timeout=SCAN_UPDATE_TIMEOUT)
        except TimeoutError:
            pytest.skip("IBKR sent no scanner update within the wait — market likely closed")
        assert [hit.rank for hit in hits] == sorted(hit.rank for hit in hits)
        assert client.active_scans == (watch,)
    finally:
        await client.stop_scan(watch)

    assert client.active_scans == ()


async def test_a_scan_hit_feeds_history(client: IBKRClient):
    hits = await client.scan(ScanRequest(scan_code="MOST_ACTIVE", limit=5))
    instrument = next((hit.instrument for hit in hits if hit.instrument is not None), None)
    if instrument is None:
        pytest.skip("scan returned no row expressible as an instrument")

    candles = await client.fetch_bars(instrument, Timeframe.D1, 5)

    assert candles
