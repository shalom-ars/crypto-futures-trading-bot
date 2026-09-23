"""Unit tests for the high-precision scoring engine and strict confirmation filters."""

import pytest
from indicators import IndicatorResults
from scoring import (
    DerivativeData,
    evaluate_btc_regime,
    evaluate_precision_gates,
    calculate_precision_score,
)


def create_mock_indicators(
    ema9: float = 105.0,
    ema21: float = 100.0,
    ema50: float = 95.0,
    ema21_slope: float = 0.5,
    adx14: float = 30.0,
    rsi14: float = 58.0,
    macd_hist: float = 2.0,
    macd_hist_prev: float = 1.0,
    volume_ratio: float = 2.0,
    is_green: bool = True,
    current_close: float = 106.0,
    current_open: float = 104.0,
) -> IndicatorResults:
    return IndicatorResults(
        ema9=ema9,
        ema21=ema21,
        ema50=ema50,
        ema200=80.0,
        ema21_slope=ema21_slope,
        adx14=adx14,
        rsi14=rsi14,
        rsi14_prev=rsi14 - 1.0,
        macd=3.0,
        macd_signal=1.0,
        macd_hist=macd_hist,
        macd_hist_prev=macd_hist_prev,
        volume_sma20=1000.0,
        current_volume=1000.0 * volume_ratio,
        volume_ratio=volume_ratio,
        current_close=current_close if is_green else 94.0,
        current_open=current_open if is_green else 96.0,
        current_high=current_close + 2.0 if is_green else 98.0,
        current_low=current_open - 2.0 if is_green else 92.0,
    )


def test_btc_regime():
    # BTC close > EMA 50 => BULLISH
    assert evaluate_btc_regime(btc_close=65000.0, btc_ema50=64000.0) == "BULLISH"
    # BTC close < EMA 50 => BEARISH
    assert evaluate_btc_regime(btc_close=63000.0, btc_ema50=64000.0) == "BEARISH"


def test_precision_gates_long_success():
    ind = create_mock_indicators(
        ema9=105.0,
        ema21=100.0,
        ema50=95.0,
        ema21_slope=0.2,
        adx14=32.0,
        rsi14=60.0,
        volume_ratio=1.8,
        is_green=True,
    )
    passed, reason = evaluate_precision_gates(ind, market_mode="BULLISH", target_side="LONG")
    assert passed is True
    assert reason == "PASSED_ALL_GATES"


def test_precision_gates_regime_mismatch():
    ind = create_mock_indicators(adx14=30.0, rsi14=60.0)
    # Long signal rejected under BEARISH BTC regime
    passed, reason = evaluate_precision_gates(ind, market_mode="BEARISH", target_side="LONG")
    assert passed is False
    assert "BTC_BEARISH_REGIME_REJECTS_LONG" in reason


def test_precision_gates_adx_chop():
    # ADX <= 25 rejected for chop
    ind = create_mock_indicators(adx14=22.0)
    passed, reason = evaluate_precision_gates(ind, market_mode="BULLISH", target_side="LONG")
    assert passed is False
    assert "ADX_CHOP" in reason


def test_precision_gates_rsi_bounds():
    # Long with RSI > 68 (overextended top trap)
    ind_overextended = create_mock_indicators(rsi14=72.0)
    passed, reason = evaluate_precision_gates(ind_overextended, market_mode="BULLISH", target_side="LONG")
    assert passed is False
    assert "RSI_OVEREXTENDED_TOP_TRAP" in reason

    # Long with RSI < 50
    ind_weak_rsi = create_mock_indicators(rsi14=45.0)
    passed, reason = evaluate_precision_gates(ind_weak_rsi, market_mode="BULLISH", target_side="LONG")
    assert passed is False
    assert "RSI_BEARISH" in reason


def test_precision_gates_ema_slope():
    # Long with EMA 21 slope <= 0
    ind_flat_slope = create_mock_indicators(ema21_slope=-0.1)
    passed, reason = evaluate_precision_gates(ind_flat_slope, market_mode="BULLISH", target_side="LONG")
    assert passed is False
    assert "EMA21_SLOPE_NOT_POSITIVE" in reason


def test_precision_gates_volume_ratio():
    # Volume <= 1.5x rejected
    ind_low_vol = create_mock_indicators(volume_ratio=1.2)
    passed, reason = evaluate_precision_gates(ind_low_vol, market_mode="BULLISH", target_side="LONG")
    assert passed is False
    assert "VOLUME_LOW" in reason


def test_calculate_precision_score_trigger():
    ind = create_mock_indicators(
        ema9=108.0,
        ema21=102.0,
        ema50=96.0,
        ema21_slope=0.5,
        adx14=35.0,
        rsi14=62.0,
        macd_hist=2.0,
        macd_hist_prev=1.0,
        volume_ratio=2.2,
        is_green=True,
    )
    deriv = DerivativeData(funding_rate=-0.0001, open_interest=105000.0, open_interest_prev=100000.0)

    res = calculate_precision_score("SOL/USDT:USDT", ind, market_mode="BULLISH", deriv=deriv, score_threshold=80.0)
    assert res.total_score >= 80.0
    assert res.action == "LONG"
    assert res.gate_failures == ""
