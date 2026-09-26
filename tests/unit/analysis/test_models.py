from tbot_console.analysis.models import (
    BreakoutSignal,
    Candle,
    Channel,
    Timeframe,
    TimeframeAnalysis,
    TrendAnalysis,
    TrendType,
)


class TestTimeframe:
    def test_all_timeframes_exist(self):
        assert Timeframe.M1.value == "1"
        assert Timeframe.M5.value == "5"
        assert Timeframe.M15.value == "15"
        assert Timeframe.H1.value == "60"
        assert Timeframe.H4.value == "240"
        assert Timeframe.D1.value == "1D"
        assert Timeframe.W1.value == "1W"

    def test_timeframe_count(self):
        assert len(Timeframe) == 7


class TestTrendType:
    def test_values(self):
        assert TrendType.UPTREND.value == "uptrend"
        assert TrendType.DOWNTREND.value == "downtrend"
        assert TrendType.SIDEWAYS.value == "sideways"

    def test_count(self):
        assert len(TrendType) == 3


class TestCandle:
    def test_creation(self):
        c = Candle(
            timestamp=1700000000.0,
            open=50000.0,
            high=51000.0,
            low=49000.0,
            close=50500.0,
            volume=1000.0,
        )
        assert c.timestamp == 1700000000.0
        assert c.open == 50000.0
        assert c.high == 51000.0
        assert c.low == 49000.0
        assert c.close == 50500.0
        assert c.volume == 1000.0

    def test_uses_slots(self):
        assert hasattr(Candle, "__slots__")

    def test_fields_are_float(self):
        c = Candle(
            timestamp=1700000000.0,
            open=50000.0,
            high=51000.0,
            low=49000.0,
            close=50500.0,
            volume=1000.0,
        )
        assert isinstance(c.close, float)
        assert isinstance(c.volume, float)


class TestTrendAnalysis:
    def test_creation(self):
        t = TrendAnalysis(
            trend_type=TrendType.UPTREND,
            strength=0.85,
            slope=100.0,
            r_squared=0.92,
            adx=35.0,
        )
        assert t.trend_type == TrendType.UPTREND
        assert t.strength == 0.85
        assert t.slope == 100.0
        assert t.r_squared == 0.92
        assert t.adx == 35.0

    def test_uses_slots(self):
        assert hasattr(TrendAnalysis, "__slots__")


class TestChannel:
    def test_creation(self):
        ch = Channel(
            upper=51000.0,
            lower=49000.0,
            middle=50000.0,
            slope=50.0,
            width_pct=4.0,
        )
        assert ch.upper == 51000.0
        assert ch.lower == 49000.0
        assert ch.middle == 50000.0
        assert ch.slope == 50.0
        assert ch.width_pct == 4.0

    def test_uses_slots(self):
        assert hasattr(Channel, "__slots__")


class TestBreakoutSignal:
    def test_creation_above(self):
        b = BreakoutSignal(
            level=51000.0,
            direction="above",
            confirmed=True,
            close_confirmed=True,
            volume_confirmed=True,
            retest_confirmed=False,
        )
        assert b.level == 51000.0
        assert b.direction == "above"
        assert b.confirmed is True
        assert b.retest_confirmed is False

    def test_creation_below(self):
        b = BreakoutSignal(
            level=49000.0,
            direction="below",
            confirmed=False,
            close_confirmed=True,
            volume_confirmed=False,
            retest_confirmed=False,
        )
        assert b.direction == "below"
        assert b.confirmed is False

    def test_uses_slots(self):
        assert hasattr(BreakoutSignal, "__slots__")


class TestTimeframeAnalysis:
    def test_creation(self):
        candles = [Candle(1700000000.0, 50000.0, 51000.0, 49000.0, 50500.0, 100.0)]
        trend = TrendAnalysis(TrendType.SIDEWAYS, 0.3, 0.0, 0.2, 15.0)
        channel = Channel(51000.0, 49000.0, 50000.0, 0.0, 4.0)

        tfa = TimeframeAnalysis(
            timeframe=Timeframe.H1,
            trend=trend,
            channel=channel,
            breakout=None,
            candles=candles,
        )
        assert tfa.timeframe == Timeframe.H1
        assert tfa.breakout is None
        assert len(tfa.candles) == 1

    def test_with_breakout(self):
        candles = [Candle(1700000000.0, 50000.0, 51000.0, 49000.0, 50500.0, 100.0)]
        trend = TrendAnalysis(TrendType.UPTREND, 0.9, 100.0, 0.95, 40.0)
        channel = Channel(51000.0, 49000.0, 50000.0, 100.0, 4.0)
        breakout = BreakoutSignal(51000.0, "above", True, True, True, False)

        tfa = TimeframeAnalysis(
            timeframe=Timeframe.H4,
            trend=trend,
            channel=channel,
            breakout=breakout,
            candles=candles,
        )
        assert tfa.breakout is not None
        assert tfa.breakout.confirmed is True

    def test_uses_slots(self):
        assert hasattr(TimeframeAnalysis, "__slots__")
