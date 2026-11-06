from __future__ import annotations

import pytest

from tbot_console.connectors.ibkr.instruments import (
    IBKRInstrument,
    InstrumentSyntaxError,
    SecType,
    format_strike,
)

ROUND_TRIP = [
    "AAPL-USD-STK",
    "SPY-USD-STK",
    "SPX-USD-IND",
    "EUR-USD-CASH",
    "ES-USD-CONTFUT",
    "ES-USD-FUT-20260320",
    "ES-USD-FUT-202603",
    "SPX-USD-OPT-20260320-5000-C",
    "SPXW-USD-OPT-20260320-4987.5-P",
    "ES-USD-FOP-20260320-5000-C",
]


class TestParsing:
    @pytest.mark.parametrize("normalized", ROUND_TRIP)
    def test_round_trip(self, normalized: str):
        assert IBKRInstrument.parse(normalized).to_normalized() == normalized

    def test_fields(self):
        option = IBKRInstrument.parse("SPX-USD-OPT-20260320-4987.5-P")
        assert option.sec_type is SecType.OPT
        assert option.symbol == "SPX"
        assert option.currency == "USD"
        assert option.expiry == "20260320"
        assert option.strike == 4987.5
        assert option.right == "P"
        assert option.is_option
        assert option.is_dated

    def test_lowercase_is_accepted(self):
        assert IBKRInstrument.parse("aapl-usd-stk").to_normalized() == "AAPL-USD-STK"

    def test_forex_splits_base_and_quote(self):
        pair = IBKRInstrument.parse("EUR-USD-CASH")
        assert (pair.symbol, pair.currency) == ("EUR", "USD")
        assert pair.effective_exchange == "IDEALPRO"

    def test_stock_and_option_default_to_smart(self):
        assert IBKRInstrument.parse("AAPL-USD-STK").effective_exchange == "SMART"
        assert IBKRInstrument.parse("SPX-USD-OPT-20260320-5000-C").effective_exchange == "SMART"

    def test_future_has_no_default_exchange(self):
        assert IBKRInstrument.parse("ES-USD-FUT-20260320").effective_exchange == ""


class TestRouting:
    def test_suffix_sets_exchange_without_changing_identity(self):
        instrument = IBKRInstrument.parse("ES-USD-FUT-20260320@CME")
        assert instrument.exchange == "CME"
        assert instrument.to_normalized() == "ES-USD-FUT-20260320"
        assert instrument.to_routed() == "ES-USD-FUT-20260320@CME"

    def test_suffix_wins_over_argument(self):
        instrument = IBKRInstrument.parse("ES-USD-FUT-20260320@NYMEX", exchange="CME")
        assert instrument.exchange == "NYMEX"

    def test_argument_used_when_no_suffix(self):
        instrument = IBKRInstrument.parse("ES-USD-FUT-20260320", exchange="CME")
        assert instrument.to_routed() == "ES-USD-FUT-20260320@CME"

    def test_routed_equals_normalized_without_exchange(self):
        instrument = IBKRInstrument.parse("SPX-USD-IND")
        assert instrument.to_routed() == instrument.to_normalized()


class TestRejections:
    @pytest.mark.parametrize(
        "bad",
        [
            "AAPL",
            "AAPL-USD",
            "AAPL-USD-WIDGET",
            "ES-USD-FUT",
            "ES-USD-FUT-2026032",
            "ES-USD-FUT-NOTADATE",
            "AAPL-USD-STK-20260320",
            "SPX-USD-OPT-20260320-5000",
            "SPX-USD-OPT-20260320-5000-X",
            "SPX-USD-OPT-20260320-0-C",
            "SPX-USD-OPT-20260320-abc-C",
        ],
    )
    def test_syntax_errors(self, bad: str):
        with pytest.raises(InstrumentSyntaxError):
            IBKRInstrument.parse(bad)

    def test_error_names_the_input(self):
        with pytest.raises(InstrumentSyntaxError, match="WIDGET"):
            IBKRInstrument.parse("AAPL-USD-WIDGET")

    def test_direct_construction_is_validated(self):
        with pytest.raises(InstrumentSyntaxError):
            IBKRInstrument(sec_type=SecType.OPT, symbol="SPX", expiry="20260320", strike=0.0)
        with pytest.raises(InstrumentSyntaxError):
            IBKRInstrument(sec_type=SecType.STK, symbol="AAPL", expiry="20260320")
        with pytest.raises(InstrumentSyntaxError):
            IBKRInstrument(sec_type=SecType.STK, symbol="")

    @pytest.mark.parametrize(
        ("bad", "message"),
        [
            ("AAPL", "'AAPL': expected SYMBOL-CURRENCY-SECTYPE[-EXPIRY[-STRIKE-RIGHT]][@EXCHANGE]"),
            ("AAPL-USD@ARCA", "'AAPL-USD@ARCA': expected SYMBOL-CURRENCY-SECTYPE"),
            (
                "AAPL-USD-WIDGET",
                "'AAPL-USD-WIDGET': unknown security type 'WIDGET'; "
                "expected one of STK, IND, CASH, FUT, CONTFUT, OPT, FOP",
            ),
            ("AAPL--STK", "AAPL: currency must not be empty"),
            ("ES-USD-FUT", "'ES-USD-FUT': FUT needs EXPIRY"),
            ("ES-USD-FUT-2026032", "ES: expiry '2026032' must be YYYYMM or YYYYMMDD"),
            ("AAPL-USD-STK-20260320", "'AAPL-USD-STK-20260320': STK takes no trailing fields"),
            ("SPX-USD-OPT-20260320-5000", "'SPX-USD-OPT-20260320-5000': OPT needs EXPIRY-STRIKE"),
            ("SPX-USD-OPT-2026-5000-C", "SPX: expiry '2026' must be YYYYMM or YYYYMMDD"),
            ("SPX-USD-OPT-20260320-5000-X", "SPX: option right must be C or P, got 'X'"),
            ("SPX-USD-OPT-20260320-0-C", "'SPX-USD-OPT-20260320-0-C': strike must be positive"),
            ("spx-usd-opt-2026-abc-c", "'spx-usd-opt-2026-abc-c': strike 'ABC' is not a number"),
        ],
    )
    def test_each_syntax_error_says_what_is_wrong(self, bad: str, message: str):
        with pytest.raises(InstrumentSyntaxError) as refused:
            IBKRInstrument.parse(bad)
        assert str(refused.value).startswith(message)

    @pytest.mark.parametrize(
        ("fields", "message"),
        [
            ({"sec_type": SecType.STK, "symbol": "", "currency": ""}, "symbol must not be empty"),
            (
                {"sec_type": SecType.STK, "symbol": "AAPL", "currency": "", "expiry": "1"},
                "AAPL: currency must not be empty",
            ),
            (
                {"sec_type": SecType.STK, "symbol": "AAPL", "expiry": "20260320", "strike": 5.0},
                "AAPL: STK carries no expiry, got '20260320'",
            ),
            (
                {"sec_type": SecType.OPT, "symbol": "SPX", "expiry": "bad", "strike": 0.0},
                "SPX: expiry 'bad' must be YYYYMM or YYYYMMDD",
            ),
            (
                {"sec_type": SecType.OPT, "symbol": "SPX", "expiry": "20260320", "right": "X"},
                "SPX: option needs a positive strike",
            ),
            (
                {"sec_type": SecType.OPT, "symbol": "SPX", "expiry": "20260320", "strike": 1.0},
                "SPX: option right must be C or P, got ''",
            ),
            (
                {"sec_type": SecType.STK, "symbol": "AAPL", "strike": 5.0},
                "AAPL: STK carries no strike or right",
            ),
            (
                {"sec_type": SecType.CASH, "symbol": "EUR", "right": "C"},
                "EUR: CASH carries no strike or right",
            ),
        ],
    )
    def test_each_bad_field_is_named(self, fields: dict, message: str):
        with pytest.raises(InstrumentSyntaxError) as refused:
            IBKRInstrument(**fields)
        assert str(refused.value) == message

    def test_any_positive_strike_makes_an_option(self):
        for strike in (0.5, 1.0):
            option = IBKRInstrument(SecType.OPT, "SPX", expiry="20260320", strike=strike, right="P")
            assert option.strike == strike


class TestStrikeFormatting:
    @pytest.mark.parametrize(
        ("strike", "expected"),
        [
            (5000.0, "5000"),
            (4987.5, "4987.5"),
            (0.25, "0.25"),
            (1000000.0, "1000000"),
            (12.125, "12.125"),
        ],
    )
    def test_format(self, strike: float, expected: str):
        assert format_strike(strike) == expected

    def test_large_strike_survives_round_trip(self):
        normalized = "SPX-USD-OPT-20260320-1000000-C"
        assert IBKRInstrument.parse(normalized).to_normalized() == normalized
