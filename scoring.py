"""High-Precision Multi-Factor Scoring Engine with Strict Gates & Master BTC Regime Filter."""

from dataclasses import dataclass
from typing import Optional, Literal
from indicators import IndicatorResults


@dataclass
class DerivativeData:
    funding_rate: float = 0.0
    open_interest: float = 0.0
    open_interest_prev: float = 0.0

    @property
    def oi_change_pct(self) -> float:
        if self.open_interest_prev <= 0:
            return 0.0
        return (self.open_interest - self.open_interest_prev) / self.open_interest_prev


@dataclass
class ScoringBreakdown:
    symbol: str
    total_score: float
    trend_score: float
    momentum_score: float
    volume_score: float
    derivatives_score: float
    close_price: float
    action: Literal["LONG", "SHORT", "NEUTRAL"]
    details: str
    market_mode: Literal["BULLISH", "BEARISH"]
    gate_failures: str = ""


def evaluate_btc_regime(btc_close: float, btc_ema50: float) -> Literal["BULLISH", "BEARISH"]:
    """Master Trend Filter:
    - BTC close > EMA 50: BULLISH (Strictly Long Only)
    - BTC close < EMA 50: BEARISH (Strictly Short Only)
    """
    if btc_close >= btc_ema50:
        return "BULLISH"
    return "BEARISH"


def check_candidate_gates(
    ind: IndicatorResults,
    market_mode: Literal["BULLISH", "BEARISH"],
    target_side: Literal["LONG", "SHORT"],
) -> Tuple_Gates:
    pass

class Tuple_Gates:
    passed: bool
    reason: str


def evaluate_precision_gates(
    ind: IndicatorResults,
    market_mode: Literal["BULLISH", "BEARISH"],
    target_side: Literal["LONG", "SHORT"],
) -> tuple[bool, str]:
    """Strict confirmation filters:
    1. BTC Market Mode alignment: strictly reject opposing directions.
    2. Trend Strength (ADX): require ADX(14) > 25 (avoid chop).
    3. RSI Boundaries:
       - LONG: 50 <= RSI <= 68. Reject if RSI > 70 (Overextended top trap) or < 50.
       - SHORT: 32 <= RSI <= 50. Reject if RSI < 30 (Bottom trap) or > 50.
    4. EMA Alignment + Slope:
       - LONG: EMA 9 > EMA 21 > EMA 50 with EMA 21 slope > 0.
       - SHORT: EMA 9 < EMA 21 < EMA 50 with EMA 21 slope < 0.
    5. Volume Confirmation:
       - Volume ratio > 1.5x of 20 SMA AND candle body confirms direction.
    """
    failures = []

    # 1. Master Trend Filter (BTC Alignment)
    if target_side == "LONG" and market_mode != "BULLISH":
        failures.append("BTC_BEARISH_REGIME_REJECTS_LONG")
    if target_side == "SHORT" and market_mode != "BEARISH":
        failures.append("BTC_BULLISH_REGIME_REJECTS_SHORT")

    # 2. ADX Filter (> 25)
    if ind.adx14 <= 25.0:
        failures.append(f"ADX_CHOP(ADX={ind.adx14:.1f}<=25)")

    # 3. RSI Range Enforcement
    if target_side == "LONG":
        if ind.rsi14 < 50.0:
            failures.append(f"RSI_BEARISH(RSI={ind.rsi14:.1f}<50)")
        elif ind.rsi14 > 68.0:
            failures.append(f"RSI_OVEREXTENDED_TOP_TRAP(RSI={ind.rsi14:.1f}>68)")
    elif target_side == "SHORT":
        if ind.rsi14 > 50.0:
            failures.append(f"RSI_BULLISH(RSI={ind.rsi14:.1f}>50)")
        elif ind.rsi14 < 32.0:
            failures.append(f"RSI_BOTTOM_TRAP(RSI={ind.rsi14:.1f}<32)")

    # 4. EMA 9 / 21 / 50 Alignment + Slope
    if target_side == "LONG":
        if not (ind.ema9 > ind.ema21 > ind.ema50):
            failures.append("EMA_NOT_BULLISH_ALIGNED(9>21>50)")
        if ind.ema21_slope <= 0.0:
            failures.append(f"EMA21_SLOPE_NOT_POSITIVE({ind.ema21_slope:+.4f})")
    elif target_side == "SHORT":
        if not (ind.ema9 < ind.ema21 < ind.ema50):
            failures.append("EMA_NOT_BEARISH_ALIGNED(9<21<50)")
        if ind.ema21_slope >= 0.0:
            failures.append(f"EMA21_SLOPE_NOT_NEGATIVE({ind.ema21_slope:+.4f})")

    # 5. Volume Confirmation (> 1.5x with directional candle body)
    if ind.volume_ratio <= 1.5:
        failures.append(f"VOLUME_LOW({ind.volume_ratio:.2f}x<=1.5x)")

    is_green = ind.current_close > ind.current_open
    is_red = ind.current_close < ind.current_open

    if target_side == "LONG" and not is_green:
        failures.append("CANDLE_BODY_NOT_BULLISH")
    elif target_side == "SHORT" and not is_red:
        failures.append("CANDLE_BODY_NOT_BEARISH")

    if failures:
        return False, " | ".join(failures)
    return True, "PASSED_ALL_GATES"


def calculate_precision_score(
    symbol: str,
    ind: IndicatorResults,
    market_mode: Literal["BULLISH", "BEARISH"],
    deriv: Optional[DerivativeData] = None,
    score_threshold: float = 80.0,
) -> ScoringBreakdown:
    """Calculate composite score and enforce strict gate validation."""
    # Potential side based on price vs EMA 50
    candidate_side: Literal["LONG", "SHORT"] = "LONG" if ind.current_close >= ind.ema50 else "SHORT"

    # Evaluate strict gates
    passed, gate_reasons = evaluate_precision_gates(ind, market_mode, candidate_side)

    # 1. Trend Alignment Score (Max 30 pts)
    trend_score = 0.0
    if candidate_side == "LONG":
        if ind.ema9 > ind.ema21 > ind.ema50:
            trend_score = 30.0 if ind.ema21_slope > 0 else 20.0
    else:
        if ind.ema9 < ind.ema21 < ind.ema50:
            trend_score = -30.0 if ind.ema21_slope < 0 else -20.0

    # 2. Momentum Score (Max 25 pts: ADX + RSI + MACD)
    mom_score = 0.0
    if candidate_side == "LONG":
        if ind.adx14 > 25.0 and (50.0 <= ind.rsi14 <= 68.0):
            mom_score += 15.0
        if ind.macd_hist > 0 and ind.macd_hist >= ind.macd_hist_prev:
            mom_score += 10.0
    else:
        if ind.adx14 > 25.0 and (32.0 <= ind.rsi14 <= 50.0):
            mom_score -= 15.0
        if ind.macd_hist < 0 and ind.macd_hist <= ind.macd_hist_prev:
            mom_score -= 10.0

    # 3. Volume Dynamics (Max 25 pts)
    vol_score = 0.0
    is_green = ind.current_close > ind.current_open
    is_red = ind.current_close < ind.current_open

    if ind.volume_ratio > 1.5:
        if candidate_side == "LONG" and is_green:
            vol_score = 25.0
        elif candidate_side == "SHORT" and is_red:
            vol_score = -25.0

    # 4. Derivatives (Max 20 pts)
    deriv_score = 0.0
    if deriv:
        if candidate_side == "LONG":
            if deriv.funding_rate < 0.0:  # Negative funding = short squeeze
                deriv_score += 10.0
            if deriv.oi_change_pct > 0.005:  # Rising OI
                deriv_score += 10.0
        else:
            if deriv.funding_rate > 0.0002:  # High positive funding = long squeeze
                deriv_score -= 10.0
            if deriv.oi_change_pct > 0.005:  # Rising OI on dump
                deriv_score -= 10.0

    total_score = trend_score + mom_score + vol_score + deriv_score
    total_score = max(-100.0, min(100.0, total_score))

    # Trigger action strictly requires passing all confirmation gates AND score >= threshold
    action: Literal["LONG", "SHORT", "NEUTRAL"] = "NEUTRAL"

    if passed:
        if candidate_side == "LONG" and total_score >= score_threshold:
            action = "LONG"
        elif candidate_side == "SHORT" and total_score <= -score_threshold:
            action = "SHORT"

    details = (
        f"Regime:{market_mode} | Trend:{trend_score:+.0f} | Mom:{mom_score:+.0f}(ADX:{ind.adx14:.1f}, RSI:{ind.rsi14:.1f}) | "
        f"Vol:{vol_score:+.0f}({ind.volume_ratio:.2f}x) | Deriv:{deriv_score:+.0f}"
    )

    return ScoringBreakdown(
        symbol=symbol,
        total_score=total_score,
        trend_score=trend_score,
        momentum_score=mom_score,
        volume_score=vol_score,
        derivatives_score=deriv_score,
        close_price=ind.current_close,
        action=action,
        details=details,
        market_mode=market_mode,
        gate_failures=gate_reasons if not passed else "",
    )
