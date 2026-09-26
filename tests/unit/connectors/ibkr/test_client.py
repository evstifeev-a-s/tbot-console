from __future__ import annotations

import logging
from datetime import UTC, date, datetime

import pytest
from ib_async import ContractDetails
from ib_async.objects import OptionChain, OptionComputation
from ib_async.wrapper import RequestError

from tbot_console.analysis.models import Timeframe
from tbot_console.connectors.ibkr.client import (
    BAR_SIZE,
    WHAT_TO_SHOW,
    IBKRClient,
    bar_timestamp,
    build_contract,
    duration_for,
)
from tbot_console.connectors.ibkr.config import IBKRConfig, MarketDataType
from tbot_console.connectors.ibkr.instruments import IBKRInstrument, SecType
from tbot_console.core.exceptions import ExchangeConnectionError, ExchangeRequestError

from .conftest import FakeIB, make_bar, make_contract, make_details, make_ticker

NAN = float("nan")

AAPL = IBKRInstrument.parse("AAPL-USD-STK")
SPX_CALL = IBKRInstrument.parse("SPX-USD-OPT-20260320-5000-C")
EURUSD = IBKRInstrument.parse("EUR-USD-CASH")
ES_FUT = IBKRInstrument.parse("ES-USD-FUT-20260320@CME")


@pytest.fixture
def ib() -> FakeIB:
    fake = FakeIB()
    fake.details = [make_details(make_contract("AAPL", 265598, trading_class="NMS"))]
    return fake


@pytest.fixture
def client(ib: FakeIB) -> IBKRClient:
    return IBKRClient(IBKRConfig(), ib=ib)


class TestConnect:
    async def test_passes_config_and_sets_feed(self, client: IBKRClient, ib: FakeIB):
        await client.connect()

        assert client.is_connected
        assert ib.connect_kwargs["host"] == "127.0.0.1"
        assert ib.connect_kwargs["port"] == 7497
        assert ib.connect_kwargs["readonly"] is True
        assert ib.market_data_type == int(MarketDataType.DELAYED_FROZEN)

    async def test_skips_startup_fetches(self, client: IBKRClient, ib: FakeIB):
        await client.connect()
        assert ib.connect_kwargs["fetchFields"].value == 0

    async def test_is_idempotent(self, client: IBKRClient, ib: FakeIB):
        await client.connect()
        ib.connect_kwargs = {}
        await client.connect()
        assert ib.connect_kwargs == {}

    async def test_refused_socket_becomes_connection_error(self, client: IBKRClient, ib: FakeIB):
        ib.raise_on["connect"] = ConnectionRefusedError(61, "Connection refused")
        with pytest.raises(ExchangeConnectionError, match="127.0.0.1:7497"):
            await client.connect()

    async def test_timeout_becomes_connection_error(self, client: IBKRClient, ib: FakeIB):
        ib.raise_on["connect"] = TimeoutError()
        with pytest.raises(ExchangeConnectionError, match="IB Gateway"):
            await client.connect()

    async def test_disconnect_clears_cache(self, client: IBKRClient, ib: FakeIB):
        await client.connect()
        await client.qualify(AAPL)
        await client.disconnect()
        await client.connect()
        await client.qualify(AAPL)

        assert len(ib.detail_calls) == 2

    async def test_context_manager(self, ib: FakeIB):
        async with IBKRClient(IBKRConfig(), ib=ib) as opened:
            assert opened.is_connected
        assert not ib.connected


class TestQualify:
    async def test_maps_contract_details(self, client: IBKRClient):
        spec = await client.qualify(AAPL)

        assert spec.con_id == 265598
        assert spec.local_symbol == "AAPL"
        assert spec.exchange == "SMART"
        assert spec.trading_class == "NMS"
        assert spec.min_tick == 0.01
        assert spec.instrument is AAPL

    async def test_result_is_cached(self, client: IBKRClient, ib: FakeIB):
        await client.qualify(AAPL)
        await client.qualify(AAPL)
        assert len(ib.detail_calls) == 1

    async def test_sends_the_built_contract(self, client: IBKRClient, ib: FakeIB):
        await client.qualify(SPX_CALL)
        sent = ib.detail_calls[0]
        assert sent.secType == "OPT"
        assert sent.symbol == "SPX"
        assert sent.lastTradeDateOrContractMonth == "20260320"
        assert sent.strike == 5000.0
        assert sent.right == "C"

    async def test_unknown_contract_names_the_symbol_and_code(self, client: IBKRClient, ib: FakeIB):
        ib.details = []
        with pytest.raises(ExchangeRequestError, match="AAPL-USD-STK") as caught:
            await client.qualify(AAPL)
        assert caught.value.code == 200

    async def test_ambiguous_match_takes_the_first(self, client: IBKRClient, ib: FakeIB):
        ib.details = [
            make_details(make_contract("ES", 111, sec_type="FUT", exchange="CME")),
            make_details(make_contract("ES", 222, sec_type="FUT", exchange="CME")),
        ]
        spec = await client.qualify(ES_FUT)
        assert spec.con_id == 111

    async def test_request_error_carries_the_venue_code(self, client: IBKRClient, ib: FakeIB):
        ib.raise_on["details"] = RequestError(1, 321, "Error validating request")
        with pytest.raises(ExchangeRequestError) as caught:
            await client.qualify(AAPL)
        assert caught.value.code == 321
        assert "Error validating request" in caught.value.reason


class TestQuotes:
    async def test_maps_ticker_fields(self, client: IBKRClient, ib: FakeIB):
        contract = make_contract("AAPL", 265598)
        ib.details = [make_details(contract)]
        ib.tickers = [
            make_ticker(
                contract,
                bid=190.0,
                ask=190.5,
                last=190.2,
                close=188.0,
                bidSize=3.0,
                askSize=4.0,
                time=datetime(2026, 9, 19, 14, 30, tzinfo=UTC),
                marketDataType=int(MarketDataType.DELAYED),
            )
        ]

        quote = await client.fetch_quote(AAPL)

        assert quote is not None
        assert quote.symbol == "AAPL-USD-STK"
        assert (quote.bid, quote.ask, quote.last, quote.close) == (190.0, 190.5, 190.2, 188.0)
        assert quote.mid == pytest.approx(190.25)
        assert quote.spread == pytest.approx(0.5)
        assert quote.delayed is True
        assert quote.greeks is None

    async def test_missing_prices_become_none(self, client: IBKRClient, ib: FakeIB):
        contract = make_contract("AAPL", 265598)
        ib.details = [make_details(contract)]
        ib.tickers = [make_ticker(contract, bid=NAN, ask=NAN, last=NAN, close=NAN)]

        quote = await client.fetch_quote(AAPL)

        assert quote is not None
        assert (quote.bid, quote.ask, quote.last, quote.close) == (None, None, None, None)
        assert quote.mid is None
        assert quote.spread is None
        assert quote.timestamp == 0.0

    async def test_negative_sizes_are_clamped(self, client: IBKRClient, ib: FakeIB):
        contract = make_contract("AAPL", 265598)
        ib.details = [make_details(contract)]
        ib.tickers = [make_ticker(contract, bidSize=-1.0, askSize=NAN)]

        quote = await client.fetch_quote(AAPL)
        assert quote is not None
        assert (quote.bid_size, quote.ask_size) == (0.0, 0.0)

    async def test_mid_falls_back_to_last_then_close(self, client: IBKRClient, ib: FakeIB):
        contract = make_contract("AAPL", 265598)
        ib.details = [make_details(contract)]
        ib.tickers = [make_ticker(contract, bid=NAN, ask=NAN, last=190.2, close=188.0)]

        quote = await client.fetch_quote(AAPL)
        assert quote is not None
        assert quote.mid == 190.2

    async def test_greeks_are_mapped_including_negatives(self, client: IBKRClient, ib: FakeIB):
        contract = make_contract("SPX", 416904, sec_type="OPT", multiplier="100")
        ib.details = [make_details(contract)]
        ib.tickers = [
            make_ticker(
                contract,
                modelGreeks=OptionComputation(
                    tickAttrib=0,
                    impliedVol=0.21,
                    delta=-0.45,
                    optPrice=12.5,
                    pvDividend=0.0,
                    gamma=0.003,
                    vega=1.2,
                    theta=-0.8,
                    undPrice=5000.0,
                ),
            )
        ]

        quote = await client.fetch_quote(SPX_CALL)

        assert quote is not None and quote.greeks is not None
        assert quote.greeks.delta == -0.45
        assert quote.greeks.theta == -0.8
        assert quote.greeks.implied_vol == 0.21
        assert quote.greeks.underlying_price == 5000.0

    async def test_unknown_instrument_is_skipped_not_fatal(self, client: IBKRClient, ib: FakeIB):
        apple = make_contract("AAPL", 265598)
        ib.details_by_symbol = {"AAPL": [make_details(apple)], "SPX": []}
        ib.tickers = [make_ticker(apple, bid=190.0, ask=190.5)]

        quotes = await client.fetch_quotes([SPX_CALL, AAPL])

        assert set(quotes) == {"AAPL-USD-STK"}

    async def test_empty_input(self, client: IBKRClient, ib: FakeIB):
        assert await client.fetch_quotes([]) == {}
        assert not ib.ticker_calls


class TestPriceSentinels:
    async def test_worthless_option_reports_zero_not_missing(self, client: IBKRClient, ib: FakeIB):
        contract = make_contract("SPX", 416904, sec_type="OPT", multiplier="100")
        ib.details = [make_details(contract)]
        ib.tickers = [make_ticker(contract, bid=-1.0, ask=-1.0, last=0.0, close=0.0)]

        quote = await client.fetch_quote(SPX_CALL)

        assert quote is not None
        assert (quote.bid, quote.ask) == (None, None)
        assert (quote.last, quote.close) == (0.0, 0.0)
        assert quote.mid == 0.0

    async def test_zero_bid_is_a_real_bid(self, client: IBKRClient, ib: FakeIB):
        contract = make_contract("SPX", 416904, sec_type="OPT", multiplier="100")
        ib.details = [make_details(contract)]
        ib.tickers = [make_ticker(contract, bid=0.0, ask=0.05, last=NAN, close=NAN)]

        quote = await client.fetch_quote(SPX_CALL)

        assert quote is not None
        assert quote.bid == 0.0
        assert quote.mid == pytest.approx(0.025)
        assert quote.spread == pytest.approx(0.05)

    async def test_ib_no_data_sentinel_is_still_none(self, client: IBKRClient, ib: FakeIB):
        contract = make_contract("AAPL", 265598)
        ib.details = [make_details(contract)]
        ib.tickers = [make_ticker(contract, bid=-1.0, ask=-1.0, last=NAN, close=NAN)]

        quote = await client.fetch_quote(AAPL)

        assert quote is not None
        assert (quote.bid, quote.ask, quote.last, quote.close) == (None, None, None, None)

    async def test_zero_option_price_survives_in_greeks(self, client: IBKRClient, ib: FakeIB):
        contract = make_contract("SPX", 416904, sec_type="OPT", multiplier="100")
        ib.details = [make_details(contract)]
        ib.tickers = [
            make_ticker(
                contract,
                modelGreeks=OptionComputation(
                    tickAttrib=0,
                    impliedVol=0.21,
                    delta=0.0,
                    optPrice=0.0,
                    pvDividend=0.0,
                    gamma=0.0,
                    vega=0.0,
                    theta=0.0,
                    undPrice=5000.0,
                ),
            )
        ]

        quote = await client.fetch_quote(SPX_CALL)

        assert quote is not None and quote.greeks is not None
        assert quote.greeks.option_price == 0.0
        assert quote.greeks.underlying_price == 5000.0


class TestContractCache:
    async def test_routing_is_part_of_the_cache_key(self, client: IBKRClient, ib: FakeIB):
        ib.details = [make_details(make_contract("ES", 111, sec_type="FUT", exchange="CME"))]
        cme = await client.qualify(IBKRInstrument.parse("ES-USD-FUT-20260320@CME"))

        ib.details = [make_details(make_contract("ES", 999, sec_type="FUT", exchange="NYMEX"))]
        nymex = await client.qualify(IBKRInstrument.parse("ES-USD-FUT-20260320@NYMEX"))

        assert (cme.con_id, nymex.con_id) == (111, 999)
        assert len(ib.detail_calls) == 2

    async def test_trading_class_is_part_of_the_cache_key(self, client: IBKRClient, ib: FakeIB):
        monthly = IBKRInstrument(
            sec_type=SecType.OPT,
            symbol="SPX",
            expiry="20260320",
            strike=5000.0,
            right="C",
            trading_class="SPX",
        )
        weekly = IBKRInstrument(
            sec_type=SecType.OPT,
            symbol="SPX",
            expiry="20260320",
            strike=5000.0,
            right="C",
            trading_class="SPXW",
        )
        assert monthly.to_normalized() == weekly.to_normalized()

        ib.details = [make_details(make_contract("SPX", 11, sec_type="OPT", trading_class="SPX"))]
        first = await client.qualify(monthly)
        ib.details = [make_details(make_contract("SPX", 22, sec_type="OPT", trading_class="SPXW"))]
        second = await client.qualify(weekly)

        assert (first.con_id, second.con_id) == (11, 22)

    async def test_details_without_a_contract_are_not_cached(self, client: IBKRClient, ib: FakeIB):
        ib.details = [ContractDetails(contract=None, minTick=0.01)]

        with pytest.raises(ExchangeRequestError, match="without a contract"):
            await client.qualify(AAPL)
        with pytest.raises(ExchangeRequestError, match="without a contract"):
            await client.qualify(AAPL)

        assert len(ib.detail_calls) == 2


class TestBars:
    async def test_request_shape(self, client: IBKRClient, ib: FakeIB):
        ib.bars = [make_bar(datetime(2026, 9, 19, 14, 30, tzinfo=UTC))]

        await client.fetch_bars(AAPL, Timeframe.H1, count=100)

        call = ib.history_calls[0]
        assert call["barSizeSetting"] == BAR_SIZE[Timeframe.H1]
        assert call["whatToShow"] == "TRADES"
        assert call["durationStr"] == duration_for(Timeframe.H1, 100)
        assert call["formatDate"] == 2
        assert call["useRTH"] is False

    async def test_forex_uses_midpoint(self, client: IBKRClient, ib: FakeIB):
        ib.details = [make_details(make_contract("EUR", 12087792, sec_type="CASH"))]
        await client.fetch_bars(EURUSD, Timeframe.D1)
        assert ib.history_calls[0]["whatToShow"] == "MIDPOINT"

    async def test_rth_is_opt_in(self, client: IBKRClient, ib: FakeIB):
        await client.fetch_bars(AAPL, Timeframe.D1, use_rth=True)
        assert ib.history_calls[0]["useRTH"] is True

    async def test_maps_bar_fields(self, client: IBKRClient, ib: FakeIB):
        ib.bars = [make_bar(datetime(2026, 9, 19, 14, 30, tzinfo=UTC), 1.0, 2.0, 0.5, 1.5, 10.0)]

        candles = await client.fetch_bars(AAPL, Timeframe.H1)

        assert len(candles) == 1
        assert candles[0].open == 1.0
        assert candles[0].high == 2.0
        assert candles[0].low == 0.5
        assert candles[0].close == 1.5
        assert candles[0].volume == 10.0
        assert candles[0].timestamp == datetime(2026, 9, 19, 14, 30, tzinfo=UTC).timestamp()

    async def test_result_is_trimmed_to_count(self, client: IBKRClient, ib: FakeIB):
        ib.bars = [make_bar(date(2026, 9, day)) for day in range(1, 21)]

        candles = await client.fetch_bars(AAPL, Timeframe.D1, count=5)

        assert len(candles) == 5
        assert candles[-1].timestamp == datetime(2026, 9, 20, tzinfo=UTC).timestamp()

    async def test_negative_volume_is_clamped(self, client: IBKRClient, ib: FakeIB):
        ib.bars = [make_bar(date(2026, 9, 1), volume=-1.0)]
        candles = await client.fetch_bars(AAPL, Timeframe.D1)
        assert candles[0].volume == 0.0

    async def test_rejection_carries_the_code(self, client: IBKRClient, ib: FakeIB):
        ib.raise_on["history"] = RequestError(3, 162, "Historical Market Data Service error")
        with pytest.raises(ExchangeRequestError) as caught:
            await client.fetch_bars(AAPL, Timeframe.D1)
        assert caught.value.code == 162


class TestOptionChain:
    async def test_maps_and_sorts(self, client: IBKRClient, ib: FakeIB):
        ib.details = [make_details(make_contract("SPX", 416904, sec_type="IND", exchange="CBOE"))]
        ib.chains = [
            OptionChain(
                exchange="CBOE",
                underlyingConId=416904,
                tradingClass="SPXW",
                multiplier="100",
                expirations={"20260417", "20260320"},
                strikes={5100.0, 4900.0, 5000.0},
            )
        ]

        chains = await client.fetch_option_chain(IBKRInstrument.parse("SPX-USD-IND@CBOE"))

        assert len(chains) == 1
        assert chains[0].expirations == ("20260320", "20260417")
        assert chains[0].strikes == (4900.0, 5000.0, 5100.0)
        assert chains[0].multiplier == 100.0
        assert chains[0].underlying_con_id == 416904

    async def test_nearest_strikes(self, client: IBKRClient, ib: FakeIB):
        ib.details = [make_details(make_contract("SPX", 416904, sec_type="IND", exchange="CBOE"))]
        ib.chains = [
            OptionChain(
                exchange="CBOE",
                underlyingConId=416904,
                tradingClass="SPXW",
                multiplier="100",
                expirations={"20260320"},
                strikes={4800.0, 4900.0, 5000.0, 5100.0, 5200.0},
            )
        ]

        chain = (await client.fetch_option_chain(IBKRInstrument.parse("SPX-USD-IND@CBOE")))[0]

        assert chain.nearest_strikes(4980.0, 3) == (4900.0, 5000.0, 5100.0)
        assert chain.nearest_strikes(4980.0, 0) == ()
        assert chain.nearest_strikes(0.0, 3) == ()

    async def test_stock_underlying_sends_no_futures_exchange(self, client: IBKRClient, ib: FakeIB):
        await client.fetch_option_chain(AAPL)
        symbol, fut_fop_exchange, sec_type, con_id = ib.chain_calls[0]
        assert (symbol, fut_fop_exchange, sec_type, con_id) == ("AAPL", "", "STK", 265598)

    async def test_future_underlying_sends_its_exchange(self, client: IBKRClient, ib: FakeIB):
        ib.details = [make_details(make_contract("ES", 495512563, sec_type="FUT", exchange="CME"))]
        await client.fetch_option_chain(ES_FUT)
        assert ib.chain_calls[0][1] == "CME"


class TestBarTimestamp:
    def test_aware_datetime(self):
        moment = datetime(2026, 9, 19, 14, 30, tzinfo=UTC)
        assert bar_timestamp(moment) == moment.timestamp()

    def test_naive_datetime_is_read_as_utc(self):
        assert (
            bar_timestamp(datetime(2026, 9, 19, 14, 30))
            == datetime(2026, 9, 19, 14, 30, tzinfo=UTC).timestamp()
        )

    def test_date_becomes_midnight_utc(self):
        assert bar_timestamp(date(2026, 9, 19)) == datetime(2026, 9, 19, tzinfo=UTC).timestamp()

    def test_anything_else_is_zero_and_warns(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING):
            assert bar_timestamp("20260919") == 0.0  # type: ignore[arg-type]
        assert "unreadable date" in caplog.text


class TestDuration:
    @pytest.mark.parametrize("timeframe", list(Timeframe))
    def test_every_timeframe_has_a_bar_size(self, timeframe: Timeframe):
        assert timeframe in BAR_SIZE

    @pytest.mark.parametrize("timeframe", list(Timeframe))
    def test_duration_is_a_legal_ib_string(self, timeframe: Timeframe):
        amount, unit = duration_for(timeframe, 100).split()
        assert int(amount) > 0
        assert unit in {"D", "W", "Y"}

    def test_grows_with_count(self):
        assert duration_for(Timeframe.D1, 10) != duration_for(Timeframe.D1, 500)

    def test_covers_more_calendar_time_than_bars(self):
        assert duration_for(Timeframe.D1, 100) == "145 D"

    def test_capped_at_the_ib_limit(self):
        assert duration_for(Timeframe.M1, 100000) == "7 D"

    def test_only_days_and_years_are_produced(self):
        units = {
            duration_for(tf, count).split()[1] for tf in Timeframe for count in (1, 100, 10**6)
        }
        assert units == {"D", "Y"}

    def test_zero_count_is_still_a_valid_request(self):
        assert duration_for(Timeframe.H1, 0) == "1 D"


class TestWhatToShow:
    @pytest.mark.parametrize("sec_type", list(SecType))
    def test_every_security_type_has_an_entry(self, sec_type: SecType):
        assert sec_type in WHAT_TO_SHOW


class TestContractBuilding:
    def test_stock(self):
        contract = build_contract(AAPL)
        assert contract.secType == "STK"
        assert contract.exchange == "SMART"
        assert contract.currency == "USD"

    def test_forex_keeps_base_and_quote_apart(self):
        contract = build_contract(EURUSD)
        assert contract.secType == "CASH"
        assert contract.symbol == "EUR"
        assert contract.currency == "USD"
        assert contract.exchange == "IDEALPRO"

    def test_future_uses_the_routed_exchange(self):
        contract = build_contract(ES_FUT)
        assert contract.secType == "FUT"
        assert contract.exchange == "CME"
        assert contract.lastTradeDateOrContractMonth == "20260320"

    def test_continuous_future(self):
        contract = build_contract(IBKRInstrument.parse("ES-USD-CONTFUT@CME"))
        assert contract.secType == SecType.CONTFUT.value
        assert contract.lastTradeDateOrContractMonth == ""
