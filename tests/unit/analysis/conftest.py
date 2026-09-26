import random
import time

import pytest

from tbot_console.analysis.models import Candle


@pytest.fixture
def make_candles():
    def _make(
        n: int = 50,
        start_price: float = 50000.0,
        slope: float = 0.0,
        noise: float = 50.0,
        volume_base: float = 100.0,
        start_time: float | None = None,
        interval_seconds: float = 3600.0,
        seed: int = 42,
    ) -> list[Candle]:
        rng = random.Random(seed)
        st = start_time or time.time() - n * interval_seconds
        candles = []
        for i in range(n):
            mid = start_price + slope * i + rng.gauss(0, noise)
            spread = abs(rng.gauss(0, noise * 0.5))
            o = mid - spread * 0.3
            c = mid + spread * 0.3
            h = max(o, c) + abs(rng.gauss(0, noise * 0.2))
            lo = min(o, c) - abs(rng.gauss(0, noise * 0.2))
            candles.append(
                Candle(
                    timestamp=st + i * interval_seconds,
                    open=o,
                    high=h,
                    low=lo,
                    close=c,
                    volume=max(1.0, volume_base + rng.gauss(0, volume_base * 0.1)),
                )
            )
        return candles

    return _make


@pytest.fixture
def uptrend_candles(make_candles):
    return make_candles(n=50, start_price=50000.0, slope=200.0, noise=30.0)


@pytest.fixture
def downtrend_candles(make_candles):
    return make_candles(n=50, start_price=60000.0, slope=-200.0, noise=30.0)


@pytest.fixture
def sideways_candles(make_candles):
    return make_candles(n=50, start_price=50000.0, slope=0.0, noise=100.0)


@pytest.fixture
def breakout_above_candles(make_candles):
    flat = make_candles(n=40, start_price=50000.0, slope=0.0, noise=50.0, seed=42)
    rng = random.Random(99)
    last_ts = flat[-1].timestamp
    interval = 3600.0
    spike_candles = []
    for i in range(10):
        base = 50800.0 + i * 100
        spread = abs(rng.gauss(0, 30))
        o = base - spread * 0.3
        c = base + spread * 0.3
        h = max(o, c) + abs(rng.gauss(0, 20))
        lo = min(o, c) - abs(rng.gauss(0, 20))
        spike_candles.append(
            Candle(
                timestamp=last_ts + (i + 1) * interval,
                open=o,
                high=h,
                low=lo,
                close=c,
                volume=300.0 + rng.gauss(0, 10),
            )
        )
    return flat + spike_candles
