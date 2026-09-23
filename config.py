"""Centralized Configuration for Crypto Futures Bot."""

from dataclasses import dataclass, field
import os
from typing import Literal
from dotenv import load_dotenv

load_dotenv()


@dataclass
class BotConfig:
    # Portfolio & Slot Architecture
    MAX_ACTIVE_SLOTS: int = 30
    SCORE_THRESHOLD_LONG: float = 80.0
    SCORE_THRESHOLD_SHORT: float = -80.0

    # Risk & Capital Allocation
    CAPITAL_RISK_PER_TRADE_PCT: float = 0.01  # 1% equity per trade
    STOP_LOSS_PCT: float = 0.03  # Strict 3% Initial Stop Loss
    BREAKEVEN_TRIGGER_PCT: float = 0.01  # +1.0% profit triggers breakeven lock
    BREAKEVEN_BUFFER_PCT: float = 0.0025  # +0.25% fee buffer locked above entry
    TRAILING_PROFIT_TRIGGER_PCT: float = 0.015  # Trailing take-profit ONLY activates after +1.5% profit
    TRAILING_CALLBACK_PCT: float = 0.006  # 0.6% pullback tolerance from peak/trough

    # Indicator & Filter Thresholds
    ADX_PERIOD: int = 14
    ADX_MIN_THRESHOLD: float = 25.0  # Require ADX > 25 (avoid chop)
    RSI_PERIOD: int = 14
    RSI_LONG_MIN: float = 50.0
    RSI_LONG_MAX: float = 68.0  # Reject Long if RSI > 70 (Overextended top trap)
    RSI_SHORT_MIN: float = 32.0
    RSI_SHORT_MAX: float = 50.0  # Reject Short if RSI < 30 (Bottom trap)
    VOLUME_SMA_MULTIPLIER: float = 1.5  # Require Volume > 1.5x of 20 SMA with matching candle

    # Execution & Market Settings
    TIMEFRAME: str = "15m"
    DEFAULT_LEVERAGE: int = 2
    MARGIN_MODE: Literal["cross", "isolated"] = "cross"
    EXCHANGE_ID: str = "binance"
    TESTNET: bool = False
    DRY_RUN: bool = True  # Strictly simulate all executions with virtual balance

    # Universe Filter
    MIN_24H_VOLUME_USDT: float = 5_000_000.0
    MAX_UNIVERSE_CANDIDATES: int = 100
    SCAN_INTERVAL_SECONDS: int = 60

    # Account / API Credentials
    API_KEY: str = field(
        default_factory=lambda: os.getenv("EXCHANGE_API_KEY") or os.getenv("BINANCE_API_KEY") or os.getenv("BINANCE_KEY") or ""
    )
    API_SECRET: str = field(
        default_factory=lambda: os.getenv("EXCHANGE_API_SECRET") or os.getenv("BINANCE_API_SECRET") or os.getenv("BINANCE_SECRET") or ""
    )
    API_PASSWORD: str = field(
        default_factory=lambda: os.getenv("EXCHANGE_API_PASSWORD") or os.getenv("BINANCE_API_PASSWORD") or ""
    )
    SIMULATED_WALLET_EQUITY: float = 300.0  # $300 USDT initial equity

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_TO_FILE: bool = True
    LOG_FILE_PATH: str = "bot.log"


CONFIG = BotConfig()
