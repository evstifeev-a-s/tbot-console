from __future__ import annotations

import asyncio
import logging
from datetime import date

import pytest
from ib_async.wrapper import RequestError

from tbot_console.analysis.models import Timeframe
from tbot_console.connectors.ibkr.client import (
    IBKRClient,
    build_contract,
    hits_from_scan_data,
    instrument_from_contract,
)
from tbot_console.connectors.ibkr.config import IBKRConfig
from tbot_console.connectors.ibkr.instruments import IBKRInstrument, SecType
from tbot_console.connectors.ibkr.scanner import ScanHit, ScanRequest, ScanRequestError
from tbot_console.core.exceptions import ExchangeRequestError

from .conftest import FakeIB, make_bar, make_contract, make_details, make_scan_data
from .test_scanner import PARAMETERS_XML

GAINERS = ScanRequest(scan_code="TOP_PERC_GAIN", above_price=10.0, limit=3)


def _ignore(hits: list[ScanHit]) -> None:
    return None


@pytest.fixture
def ib() -> FakeIB:
    fake = FakeIB()
    fake.scanner_parameters_xml = PARAMETERS_XML
    fake.scan_rows = [
        make_scan_data(2, make_contract("MSFT", 272093, local_symbol="MSFT")),
        make_scan_data(0, make_contract("AAPL", 265598, local_symbol="AAPL")),
        make_scan_data(1, make_contract("NVDA", 4815747, local_symbol="NVDA")),
    ]
    return fake


@pytest.fixture
def client(ib: FakeIB) -> IBKRClient:
    return IBKRClient(IBKRConfig(), ib=ib)


class TestScanSnapshot:
    async def test_a_scan_ib_refused_is_an_error_with_its_code(
        self, client: IBKRClient, ib: FakeIB
    ):
        ib.scan_errors = [(162, "Scanner subscription limit reached")]
        with pytest.raises(ExchangeRequestError, match="subscription limit") as caught:
            await client.scan(GAINERS)
        assert caught.value.code == 162

    async def test_sends_the_built_subscription(self, client: IBKRClient, ib: FakeIB):
        await client.scan(GAINERS)

        subscription, options = ib.scan_calls[0]
        assert subscription.scanCode == "TOP_PERC_GAIN"
        assert subscription.abovePrice == 10.0
        assert subscription.numberOfRows == 3
        assert options == []

    async def test_forwards_extra_filters(self, client: IBKRClient, ib: FakeIB):
        await client.scan(
            ScanRequest(scan_code="TOP_PERC_GAIN", extra_filters=(("impliedVolAbove", "30"),))
        )
        _, options = ib.scan_calls[0]
        assert [(o.tag, o.value) for o in options] == [("impliedVolAbove", "30")]

    async def test_rows_become_hits_sorted_by_rank(self, client: IBKRClient):
        hits = await client.scan(GAINERS)

        assert [hit.rank for hit in hits] == [0, 1, 2]
        assert [hit.local_symbol for hit in hits] == ["AAPL", "NVDA", "MSFT"]
        assert [hit.con_id for hit in hits] == [265598, 4815747, 272093]

    async def test_hits_carry_a_usable_instrument(self, client: IBKRClient):
        hits = await client.scan(GAINERS)

        first = hits[0].instrument
        assert first is not None
        assert first.to_normalized() == "AAPL-USD-STK"

    async def test_a_hit_feeds_history_straight_back(self, client: IBKRClient, ib: FakeIB):
        ib.details = [make_details(make_contract("AAPL", 265598))]
        ib.bars = [make_bar(date(2026, 9, 18)), make_bar(date(2026, 9, 19))]

        hit = (await client.scan(GAINERS))[0]
        assert hit.instrument is not None
        candles = await client.fetch_bars(hit.instrument, Timeframe.D1, count=2)

        assert len(candles) == 2

    async def test_empty_result(self, client: IBKRClient, ib: FakeIB):
        ib.scan_rows = []
        assert await client.scan(GAINERS) == []

    async def test_rejection_carries_the_venue_code(self, client: IBKRClient, ib: FakeIB):
        ib.raise_on["scan"] = RequestError(1, 165, "Scanner subscription error")
        with pytest.raises(ExchangeRequestError) as caught:
            await client.scan(GAINERS)
        assert caught.value.code == 165


class TestScanValidation:
    async def test_unknown_scan_code_is_refused_before_the_request(
        self, client: IBKRClient, ib: FakeIB
    ):
        with pytest.raises(ScanRequestError, match="scan code"):
            await client.scan(ScanRequest(scan_code="NOPE"), validate=True)
        assert ib.scan_calls == []

    async def test_valid_request_passes_validation(self, client: IBKRClient, ib: FakeIB):
        await client.scan(GAINERS, validate=True)
        assert len(ib.scan_calls) == 1

    async def test_validation_is_opt_in(self, client: IBKRClient, ib: FakeIB):
        await client.scan(ScanRequest(scan_code="NOPE"))
        assert ib.scanner_parameter_calls == 0
        assert len(ib.scan_calls) == 1


class TestVocabulary:
    async def test_parsed_from_the_gateway(self, client: IBKRClient):
        vocabulary = await client.scan_vocabulary()
        assert vocabulary.knows_scan_code("TOP_PERC_GAIN")

    async def test_cached_after_the_first_fetch(self, client: IBKRClient, ib: FakeIB):
        await client.scan_vocabulary()
        await client.scan_vocabulary()
        assert ib.scanner_parameter_calls == 1

    async def test_refresh_refetches(self, client: IBKRClient, ib: FakeIB):
        await client.scan_vocabulary()
        await client.scan_vocabulary(refresh=True)
        assert ib.scanner_parameter_calls == 2

    async def test_survives_a_reconnect(self, client: IBKRClient, ib: FakeIB):
        await client.connect()
        await client.scan_vocabulary()
        await client.disconnect()
        await client.connect()
        await client.scan_vocabulary()
        assert ib.scanner_parameter_calls == 1

    async def test_rejection_is_mapped(self, client: IBKRClient, ib: FakeIB):
        ib.raise_on["scanner_parameters"] = RequestError(1, 321, "not available")
        with pytest.raises(ExchangeRequestError):
            await client.scan_vocabulary()


class TestWatchScan:
    async def test_sync_callback_receives_hits(self, client: IBKRClient, ib: FakeIB):
        seen: list[list[ScanHit]] = []
        watch = await client.watch_scan(GAINERS, seen.append)

        watch.rows.updateEvent.emit(watch.rows)

        assert len(seen) == 1
        assert [hit.local_symbol for hit in seen[0]] == ["AAPL", "NVDA", "MSFT"]

    async def test_async_callback_is_scheduled(self, client: IBKRClient):
        seen: list[int] = []

        async def collect(hits: list[ScanHit]) -> None:
            seen.append(len(hits))

        watch = await client.watch_scan(GAINERS, collect)
        watch.rows.updateEvent.emit(watch.rows)
        await asyncio.sleep(0)

        assert seen == [3]

    async def test_callback_failure_does_not_break_the_feed(
        self, client: IBKRClient, caplog: pytest.LogCaptureFixture
    ):
        calls: list[int] = []

        def flaky(hits: list[ScanHit]) -> None:
            calls.append(len(hits))
            raise RuntimeError("boom")

        watch = await client.watch_scan(GAINERS, flaky)
        with caplog.at_level(logging.ERROR):
            watch.rows.updateEvent.emit(watch.rows)
            watch.rows.updateEvent.emit(watch.rows)

        assert calls == [3, 3]
        assert "scan callback failed" in caplog.text

    async def test_async_callback_failure_is_logged(
        self, client: IBKRClient, caplog: pytest.LogCaptureFixture
    ):
        async def flaky(hits: list[ScanHit]) -> None:
            raise RuntimeError("boom")

        watch = await client.watch_scan(GAINERS, flaky)
        with caplog.at_level(logging.ERROR):
            watch.rows.updateEvent.emit(watch.rows)
            for _ in range(3):
                await asyncio.sleep(0)

        assert "scan callback raised" in caplog.text

    async def test_stop_unsubscribes_and_cancels(self, client: IBKRClient, ib: FakeIB):
        seen: list[list[ScanHit]] = []
        await client.connect()
        watch = await client.watch_scan(GAINERS, seen.append)

        await client.stop_scan(watch)
        watch.rows.updateEvent.emit(watch.rows)

        assert seen == []
        assert ib.cancelled_scans == [watch.rows]
        assert client.active_scans == ()

    async def test_active_scans_are_tracked(self, client: IBKRClient):
        first = await client.watch_scan(GAINERS, _ignore)
        second = await client.watch_scan(ScanRequest(scan_code="MOST_ACTIVE"), _ignore)

        assert client.active_scans == (first, second)

    async def test_disconnect_cancels_every_watch(self, client: IBKRClient, ib: FakeIB):
        await client.connect()
        await client.watch_scan(GAINERS, _ignore)
        await client.watch_scan(ScanRequest(scan_code="MOST_ACTIVE"), _ignore)

        await client.disconnect()

        assert len(ib.cancelled_scans) == 2
        assert client.active_scans == ()

    async def test_warns_past_the_concurrent_limit(
        self, client: IBKRClient, caplog: pytest.LogCaptureFixture
    ):
        with caplog.at_level(logging.WARNING):
            for _ in range(11):
                await client.watch_scan(GAINERS, _ignore)

        assert "concurrent scanner subscriptions" in caplog.text


class TestInstrumentFromContract:
    def test_stock(self):
        instrument = instrument_from_contract(make_contract("AAPL", 1))
        assert instrument is not None
        assert instrument.to_normalized() == "AAPL-USD-STK"

    def test_option_right_spelled_out_is_normalised(self):
        contract = make_contract("SPX", 1, sec_type="OPT", exchange="SMART")
        contract.lastTradeDateOrContractMonth = "20260320"
        contract.strike = 5000.0
        contract.right = "CALL"

        instrument = instrument_from_contract(contract)
        assert instrument is not None
        assert instrument.to_normalized() == "SPX-USD-OPT-20260320-5000-C"

    def test_future_keeps_its_expiry(self):
        contract = make_contract("ES", 1, sec_type="FUT", exchange="CME")
        contract.lastTradeDateOrContractMonth = "202603"

        instrument = instrument_from_contract(contract)
        assert instrument is not None
        assert instrument.to_normalized() == "ES-USD-FUT-202603"

    def test_continuous_future_drops_the_expiry(self):
        contract = make_contract("ES", 1, sec_type="CONTFUT", exchange="CME")
        contract.lastTradeDateOrContractMonth = "20260320"

        instrument = instrument_from_contract(contract)
        assert instrument is not None
        assert instrument.to_normalized() == "ES-USD-CONTFUT"

    def test_unsupported_security_type_is_none(self):
        assert instrument_from_contract(make_contract("T", 1, sec_type="BOND")) is None

    def test_future_without_an_expiry_is_none(self):
        assert instrument_from_contract(make_contract("ES", 1, sec_type="FUT")) is None

    def test_round_trips_through_build_contract(self):
        weekly = IBKRInstrument(
            sec_type=SecType.OPT,
            symbol="SPX",
            expiry="20260320",
            strike=5000.0,
            right="C",
            exchange="CBOE",
            multiplier="100",
            trading_class="SPXW",
        )
        assert instrument_from_contract(build_contract(weekly)) == weekly

    def test_keeps_the_trading_class_that_disambiguates_the_contract(self):
        contract = make_contract("SPX", 1, sec_type="OPT", trading_class="SPXW", multiplier="100")
        contract.lastTradeDateOrContractMonth = "20260320"
        contract.strike = 5000.0
        contract.right = "C"

        instrument = instrument_from_contract(contract)

        assert instrument is not None
        assert instrument.trading_class == "SPXW"
        assert instrument.multiplier == "100"

    def test_missing_currency_defaults_to_usd(self):
        contract = make_contract("AAPL", 1)
        contract.currency = ""
        instrument = instrument_from_contract(contract)
        assert instrument is not None
        assert instrument.currency == "USD"

    def test_primary_exchange_is_the_fallback_route(self):
        contract = make_contract("AAPL", 1, exchange="")
        contract.primaryExchange = "NASDAQ"
        instrument = instrument_from_contract(contract)
        assert instrument is not None
        assert instrument.exchange == "NASDAQ"


class TestHitsFromScanData:
    def test_row_without_contract_details_still_yields_a_hit(self):
        row = make_scan_data(0, make_contract("AAPL", 1))
        row.contractDetails = None

        hits = hits_from_scan_data([row])

        assert len(hits) == 1
        assert hits[0].instrument is None
        assert hits[0].con_id == 0
        assert hits[0].local_symbol == ""

    def test_unsupported_security_type_yields_a_hit_without_an_instrument(self):
        hits = hits_from_scan_data([make_scan_data(0, make_contract("T", 7, sec_type="BOND"))])

        assert hits[0].instrument is None
        assert hits[0].con_id == 7

    def test_metadata_is_carried_through(self):
        hits = hits_from_scan_data([make_scan_data(0, make_contract("AAPL", 1), distance="12.5")])
        assert hits[0].distance == "12.5"
