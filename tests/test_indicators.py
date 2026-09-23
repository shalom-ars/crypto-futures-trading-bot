"""Unit tests for technical indicators."""

import numpy as np
import pandas as pd
import pytest
from indicators import (
    compute_ema,
    compute_rsi,
    compute_macd,
    compute_volume_sma,
    extract_indicators,
)


def generate_mock_ohlcv(n_bars: int = 250, trend: str = "bullish") -> pd.DataFrame:
    """Generate synthetic OHLCV data."""
    np.random.seed(42)
    if trend == "bullish":
        drift = 0.5
    elif trend == "bearish":
        drift = -0.5
    else:
        drift = 0.0

    steps = np.random.normal(loc=drift, scale=1.0, size=n_bars)
    closes = 100.0 + np.cumsum(steps)
    opens = closes - np.random.normal(loc=0.0, scale=0.5, size=n_bars)
    highs = np.maximum(opens, closes) + np.abs(np.random.normal(loc=0.5, scale=0.2, size=n_bars))
    lows = np.minimum(opens, closes) - np.abs(np.random.normal(loc=0.5, scale=0.2, size=n_bars))
    volumes = np.random.uniform(1000.0, 5000.0, size=n_bars)

    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=n_bars, freq="15min"),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )


def test_ema_computation():
    series = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0])
    ema = compute_ema(series, period=3)
    assert len(ema) == len(series)
    # EMA of an increasing series should be strictly increasing
    assert (ema.diff().dropna() > 0).all()


def test_rsi_bounds():
    df = generate_mock_ohlcv(100, trend="bullish")
    rsi = compute_rsi(df["close"], 14)
    # Valid RSI must always be within [0, 100]
    valid_rsi = rsi.dropna()
    assert (valid_rsi >= 0.0).all()
    assert (valid_rsi <= 100.0).all()


def test_macd_computation():
    df = generate_mock_ohlcv(100, trend="bullish")
    macd_line, signal_line, hist = compute_macd(df["close"])
    assert len(macd_line) == len(df)
    assert len(signal_line) == len(df)
    assert len(hist) == len(df)
    # Check relationship hist = macd - signal
    np.testing.assert_allclose(hist.values, (macd_line - signal_line).values, rtol=1e-5)


def test_volume_sma():
    volumes = pd.Series([100.0] * 30)
    sma = compute_volume_sma(volumes, 20)
    assert sma.iloc[-1] == 100.0


def test_extract_indicators():
    df = generate_mock_ohlcv(250, trend="bullish")
    res = extract_indicators(df)
    assert res.ema9 > 0
    assert res.ema21 > 0
    assert res.ema50 > 0
    assert res.ema200 > 0
    assert res.adx14 >= 0
    assert 0 <= res.rsi14 <= 100
    assert res.volume_ratio > 0
    assert res.current_close > 0
