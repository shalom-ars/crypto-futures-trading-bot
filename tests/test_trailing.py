"""Unit tests for the asynchronous 3-step trailing stop & breakeven protection engine."""

import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from config import BotConfig
from risk import PortfolioManager, PositionInfo
from trailing import TrailingWorker


class MockExchange:
    def __init__(self):
        self.execute_market_order = AsyncMock(return_value={"status": "closed"})
        self.update_simulated_equity = MagicMock()


@pytest.fixture
def test_setup():
    config = BotConfig(
        STOP_LOSS_PCT=0.03,                 # 3% hard SL
        BREAKEVEN_TRIGGER_PCT=0.010,        # +1.0% breakeven trigger
        BREAKEVEN_BUFFER_PCT=0.0025,        # +0.25% fee buffer
        TRAILING_PROFIT_TRIGGER_PCT=0.015,  # +1.5% trailing activation
        TRAILING_CALLBACK_PCT=0.006,        # 0.6% callback
        DRY_RUN=True,
    )
    portfolio = PortfolioManager(config)
    exchange = MockExchange()
    return config, portfolio, exchange


@pytest.mark.asyncio
async def test_long_hard_stop_loss(test_setup):
    config, portfolio, exchange = test_setup
    entry_price = 100.0
    sl_price = entry_price * (1.0 - config.STOP_LOSS_PCT)  # 97.0

    pos = PositionInfo(
        symbol="BTC/USDT:USDT",
        side="LONG",
        entry_price=entry_price,
        quantity=1.0,
        leverage=2,
        margin_allocated=50.0,
        stop_loss=sl_price,
        current_stop_loss=sl_price,
    )
    await portfolio.register_position(pos)

    worker = TrailingWorker(pos, config, exchange, portfolio)

    # Price safe at 98.0 (> 97.0)
    res = await worker._check_price_conditions(98.0)
    assert res is False
    assert portfolio.active_slots == 1

    # Price breaches 3% hard SL at 96.9 (<= 97.0)
    res = await worker._check_price_conditions(96.9)
    assert res is True
    assert portfolio.active_slots == 0
    exchange.execute_market_order.assert_called_once_with(
        symbol="BTC/USDT:USDT",
        side="sell",
        amount=1.0,
        reduce_only=True,
    )


@pytest.mark.asyncio
async def test_long_breakeven_lock(test_setup):
    config, portfolio, exchange = test_setup
    entry_price = 100.0
    sl_price = 97.0

    pos = PositionInfo(
        symbol="ETH/USDT:USDT",
        side="LONG",
        entry_price=entry_price,
        quantity=1.0,
        leverage=2,
        margin_allocated=50.0,
        stop_loss=sl_price,
        current_stop_loss=sl_price,
    )
    await portfolio.register_position(pos)
    worker = TrailingWorker(pos, config, exchange, portfolio)

    # 1. Price moves to +0.8% (100.8) -> Breakeven not triggered yet
    res = await worker._check_price_conditions(100.8)
    assert res is False
    assert pos.breakeven_locked is False
    assert pos.current_stop_loss == 97.0

    # 2. Price hits +1.0% (101.0) -> Breakeven locks at entry + 0.25% (100.25)
    res = await worker._check_price_conditions(101.0)
    assert res is False
    assert pos.breakeven_locked is True
    assert pos.current_stop_loss == 100.25

    # 3. Trailing should NOT be active yet (needs +1.5%)
    assert pos.trailing_active is False

    # 4. Price falls back to 100.20 (below breakeven SL of 100.25) -> Exit with profit locked!
    res = await worker._check_price_conditions(100.20)
    assert res is True
    assert portfolio.active_slots == 0


@pytest.mark.asyncio
async def test_long_trailing_activation_and_pullback(test_setup):
    config, portfolio, exchange = test_setup
    entry_price = 100.0
    sl_price = 97.0

    pos = PositionInfo(
        symbol="SOL/USDT:USDT",
        side="LONG",
        entry_price=entry_price,
        quantity=2.0,
        leverage=2,
        margin_allocated=100.0,
        stop_loss=sl_price,
        current_stop_loss=sl_price,
    )
    await portfolio.register_position(pos)
    worker = TrailingWorker(pos, config, exchange, portfolio)

    # 1. Price hits +1.5% (101.5) -> Trailing activates! Peak = 101.5
    res = await worker._check_price_conditions(101.5)
    assert res is False
    assert pos.trailing_active is True
    assert pos.peak_price == 101.5

    # 2. Price pushes higher to 103.0 -> Peak updates to 103.0
    res = await worker._check_price_conditions(103.0)
    assert res is False
    assert pos.peak_price == 103.0

    # Trailing exit threshold: 103.0 * (1 - 0.006) = 102.382
    # 3. Pullback to 102.5 (> 102.382) -> holding
    res = await worker._check_price_conditions(102.5)
    assert res is False

    # 4. Pullback to 102.3 (<= 102.382) -> Exit triggered!
    res = await worker._check_price_conditions(102.3)
    assert res is True
    assert portfolio.active_slots == 0


@pytest.mark.asyncio
async def test_short_breakeven_and_trailing(test_setup):
    config, portfolio, exchange = test_setup
    entry_price = 100.0
    sl_price = 103.0  # +3% hard SL

    pos = PositionInfo(
        symbol="AVAX/USDT:USDT",
        side="SHORT",
        entry_price=entry_price,
        quantity=5.0,
        leverage=2,
        margin_allocated=100.0,
        stop_loss=sl_price,
        current_stop_loss=sl_price,
    )
    await portfolio.register_position(pos)
    worker = TrailingWorker(pos, config, exchange, portfolio)

    # 1. Price drops -1.0% to 99.0 -> Breakeven locks at entry - 0.25% (99.75)
    res = await worker._check_price_conditions(99.0)
    assert res is False
    assert pos.breakeven_locked is True
    assert pos.current_stop_loss == 99.75

    # 2. Price drops further to 98.0 (-2.0%) -> Trailing activates (trough=98.0)
    res = await worker._check_price_conditions(98.0)
    assert res is False
    assert pos.trailing_active is True
    assert pos.trough_price == 98.0

    # Retracement threshold: 98.0 * (1 + 0.006) = 98.588
    # 3. Price bounces to 98.4 (< 98.588) -> holding
    res = await worker._check_price_conditions(98.4)
    assert res is False

    # 4. Price bounces to 98.7 (>= 98.588) -> Trailing exit triggered!
    res = await worker._check_price_conditions(98.7)
    assert res is True
    assert portfolio.active_slots == 0
