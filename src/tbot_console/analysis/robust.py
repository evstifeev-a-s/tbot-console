"""Робастные статистики для анализа свечей.

Обоснование выбора: OLS-наклон и ±k·std сдвигаются одним выбросом (одна
свеча-«прокол» задаёт границу канала). Theil–Sen (медиана попарных наклонов,
breakdown ≈29%) и квантили остатков / MAD (breakdown 50%) нечувствительны к
единичным спайкам. scipy в зависимостях нет — реализация на numpy.
"""

from __future__ import annotations

import numpy as np

from tbot_console.analysis.models import Candle


def theil_sen(values: np.ndarray) -> tuple[float, float]:
    """Наклон = медиана попарных наклонов, intercept = медиана (y - slope*x).

    O(n^2) пар; при n=100 это 4950 наклонов — тривиально на кадансе анализа.
    """
    n = len(values)
    if n < 2:
        return 0.0, float(values[0]) if n else 0.0
    x = np.arange(n, dtype=np.float64)
    dy = values[:, None] - values[None, :]
    dx = x[:, None] - x[None, :]
    mask = dx > 0
    slope = float(np.median(dy[mask] / dx[mask]))
    intercept = float(np.median(values - slope * x))
    return slope, intercept


def spearman_rho(values: np.ndarray) -> float:
    """Ранговая корреляция значений с их порядковым номером.

    Робастный аналог R²-гейта: монотонность тренда, не искажаемая величиной
    отдельного выброса. Для чистого линейного тренда rho² ≈ R².
    """
    n = len(values)
    if n < 3:
        return 0.0
    order = np.argsort(values, kind="stable")
    ranks = np.empty(n, dtype=np.float64)
    ranks[order] = np.arange(n, dtype=np.float64)
    unique, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    if len(unique) < 2:
        return 0.0
    if np.any(counts > 1):
        sums = np.zeros(len(unique))
        np.add.at(sums, inverse, ranks)
        ranks = sums[inverse] / counts[inverse]
    x = np.arange(n, dtype=np.float64)
    rx = x - x.mean()
    ry = ranks - ranks.mean()
    denom = float(np.sqrt(np.sum(rx * rx) * np.sum(ry * ry)))
    if denom == 0.0:
        return 0.0
    return float(np.sum(rx * ry) / denom)


def mad_sigma(residuals: np.ndarray) -> float:
    """σ-оценка через MAD: 1.4826·median|r - median(r)| (консистентна для нормали)."""
    if len(residuals) == 0:
        return 0.0
    med = float(np.median(residuals))
    return 1.4826 * float(np.median(np.abs(residuals - med)))


def atr(candles: list[Candle], period: int = 14) -> float:
    """Средний true range по последним `period` барам (простое среднее)."""
    if len(candles) < 2:
        return 0.0
    highs = np.array([c.high for c in candles], dtype=np.float64)
    lows = np.array([c.low for c in candles], dtype=np.float64)
    closes = np.array([c.close for c in candles], dtype=np.float64)
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])),
    )
    tail = tr[-period:] if len(tr) > period else tr
    return float(np.mean(tail))
