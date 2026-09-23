"""Vectorized Technical Indicators including ADX, EMA slopes, RSI, and Volume."""

from typing import Tuple, NamedTuple
import numpy as np
import pandas as pd


class IndicatorResults(NamedTuple):
    ema9: float
    ema21: float
    ema21_slope: float
    ema50: float
    ema200: float
    adx14: float
    rsi14: float
    rsi14_prev: float
    macd: float
    macd_signal: float
    macd_hist: float
    macd_hist_prev: float
    volume_sma20: float
    current_volume: float
    volume_ratio: float
    current_close: float
    current_open: float
    current_high: float
    current_low: float


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Calculate Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Average Directional Index (ADX) with Wilder's smoothing."""
    high_prev = high.shift(1)
    low_prev = low.shift(1)
    close_prev = close.shift(1)

    # True Range (TR)
    tr1 = high - low
    tr2 = (high - close_prev).abs()
    tr3 = (low - close_prev).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Directional Movement (+DM, -DM)
    up_move = high - high_prev
    down_move = low_prev - low

    pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    pos_dm_s = pd.Series(pos_dm, index=high.index)
    neg_dm_s = pd.Series(neg_dm, index=high.index)

    # Wilder's smoothing (alpha = 1 / period)
    smoothed_tr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    smoothed_pos_dm = pos_dm_s.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    smoothed_neg_dm = neg_dm_s.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    # Directional Indicators (+DI, -DI)
    safe_tr = np.where(smoothed_tr == 0, 1e-9, smoothed_tr)
    pos_di = 100.0 * (smoothed_pos_dm / safe_tr)
    neg_di = 100.0 * (smoothed_neg_dm / safe_tr)

    # Directional Index (DX)
    di_sum = np.where((pos_di + neg_di) == 0, 1e-9, pos_di + neg_di)
    dx = 100.0 * (pos_di - neg_di).abs() / di_sum

    # Smoothed ADX
    adx = dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    return adx.fillna(0.0)


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index (Wilder's smoothing)."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = np.where(avg_loss == 0, 100.0, avg_gain / np.where(avg_loss == 0, 1e-9, avg_loss))
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return pd.Series(rsi, index=series.index)


def compute_macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate Moving Average Convergence Divergence (MACD)."""
    fast_ema = compute_ema(series, fast)
    slow_ema = compute_ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = compute_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_volume_sma(volumes: pd.Series, period: int = 20) -> pd.Series:
    """Calculate Simple Moving Average of Volume."""
    return volumes.rolling(window=period, min_periods=1).mean()


def extract_indicators(df: pd.DataFrame) -> IndicatorResults:
    """Extract indicator values from an OHLCV DataFrame."""
    if len(df) < 50:
        raise ValueError(f"Insufficient OHLCV data: {len(df)} candles provided, need at least 50.")

    closes = df["close"].astype(float)
    opens = df["open"].astype(float)
    highs = df["high"].astype(float)
    lows = df["low"].astype(float)
    volumes = df["volume"].astype(float)

    ema9 = compute_ema(closes, 9)
    ema21 = compute_ema(closes, 21)
    ema50 = compute_ema(closes, 50)
    ema200 = compute_ema(closes, 200)

    adx = compute_adx(highs, lows, closes, 14)
    rsi = compute_rsi(closes, 14)
    macd, signal, hist = compute_macd(closes, 12, 26, 9)
    vol_sma20 = compute_volume_sma(volumes, 20)

    idx = -1
    prev_idx = -2 if len(df) >= 2 else -1

    current_vol = float(volumes.iloc[idx])
    v_sma = float(vol_sma20.iloc[idx])
    vol_ratio = (current_vol / v_sma) if v_sma > 0 else 1.0

    # EMA 21 slope: positive if rising, negative if falling
    ema21_slope = float(ema21.iloc[idx] - ema21.iloc[prev_idx])

    return IndicatorResults(
        ema9=float(ema9.iloc[idx]),
        ema21=float(ema21.iloc[idx]),
        ema21_slope=ema21_slope,
        ema50=float(ema50.iloc[idx]),
        ema200=float(ema200.iloc[idx]),
        adx14=float(adx.iloc[idx]) if not np.isnan(adx.iloc[idx]) else 0.0,
        rsi14=float(rsi.iloc[idx]) if not np.isnan(rsi.iloc[idx]) else 50.0,
        rsi14_prev=float(rsi.iloc[prev_idx]) if not np.isnan(rsi.iloc[prev_idx]) else 50.0,
        macd=float(macd.iloc[idx]),
        macd_signal=float(signal.iloc[idx]),
        macd_hist=float(hist.iloc[idx]),
        macd_hist_prev=float(hist.iloc[prev_idx]),
        volume_sma20=v_sma,
        current_volume=current_vol,
        volume_ratio=vol_ratio,
        current_close=float(closes.iloc[idx]),
        current_open=float(opens.iloc[idx]),
        current_high=float(highs.iloc[idx]),
        current_low=float(lows.iloc[idx]),
    )
