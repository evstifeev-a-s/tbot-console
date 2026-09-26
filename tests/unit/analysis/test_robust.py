import numpy as np
import pytest

from tbot_console.analysis.models import Candle
from tbot_console.analysis.robust import atr, mad_sigma, spearman_rho, theil_sen


def candle(high: float, low: float, close: float) -> Candle:
    return Candle(timestamp=0.0, open=close, high=high, low=low, close=close, volume=1.0)


class TestTheilSen:
    def test_a_line_is_recovered_exactly(self):
        slope, intercept = theil_sen(3.0 + 2.0 * np.arange(20, dtype=np.float64))
        assert slope == pytest.approx(2.0)
        assert intercept == pytest.approx(3.0)

    def test_one_spike_does_not_move_the_slope(self):
        values = 10.0 + 0.5 * np.arange(30, dtype=np.float64)
        values[15] = 1000.0
        slope, _ = theil_sen(values)
        assert slope == pytest.approx(0.5)

    def test_short_inputs(self):
        assert theil_sen(np.array([], dtype=np.float64)) == (0.0, 0.0)
        assert theil_sen(np.array([7.0])) == (0.0, 7.0)


class TestSpearman:
    def test_a_monotone_series_ranks_perfectly(self):
        assert spearman_rho(np.array([1.0, 2.0, 5.0, 9.0, 30.0])) == pytest.approx(1.0)
        assert spearman_rho(np.array([30.0, 9.0, 5.0, 2.0, 1.0])) == pytest.approx(-1.0)

    def test_the_size_of_an_outlier_does_not_matter(self):
        plain = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        spiked = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6000.0])
        assert spearman_rho(plain) == pytest.approx(spearman_rho(spiked))

    def test_ties_share_their_rank(self):
        rho = spearman_rho(np.array([1.0, 1.0, 2.0, 2.0, 3.0]))
        assert 0.9 < rho < 1.0

    @pytest.mark.parametrize("values", [[1.0, 2.0], [4.0, 4.0, 4.0]])
    def test_degenerate_inputs_are_zero(self, values):
        assert spearman_rho(np.array(values)) == 0.0


class TestMadSigma:
    def test_it_matches_the_normal_sigma(self):
        rng = np.random.default_rng(7)
        assert mad_sigma(rng.normal(0.0, 2.0, 20_000)) == pytest.approx(2.0, rel=0.05)

    def test_outliers_barely_move_it(self):
        base = np.array([-1.0, -0.5, 0.0, 0.5, 1.0] * 20)
        spiked = base.copy()
        spiked[:5] = 1e6
        assert mad_sigma(spiked) == pytest.approx(mad_sigma(base), rel=0.5)

    def test_empty_is_zero(self):
        assert mad_sigma(np.array([], dtype=np.float64)) == 0.0


class TestAtr:
    def test_it_takes_the_largest_of_the_three_ranges(self):
        bars = [candle(10, 9, 10), candle(12, 11, 11), candle(11, 8, 9)]
        assert atr(bars) == pytest.approx((2.0 + 3.0) / 2)

    def test_only_the_last_period_counts(self):
        bars = [candle(100, 0, 50)] + [candle(51, 49, 50) for _ in range(20)]
        assert atr(bars, period=5) == pytest.approx(2.0)

    def test_fewer_than_two_bars_is_zero(self):
        assert atr([]) == 0.0
        assert atr([candle(2, 1, 1)]) == 0.0
