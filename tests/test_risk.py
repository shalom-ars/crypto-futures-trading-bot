"""Unit tests for portfolio and risk management."""

import pytest
from config import BotConfig
from risk import PortfolioManager, PositionInfo


@pytest.fixture
def portfolio():
    config = BotConfig(
        MAX_ACTIVE_SLOTS=30,
        CAPITAL_RISK_PER_TRADE_PCT=0.01,
        DEFAULT_LEVERAGE=5,
    )
    return PortfolioManager(config)


@pytest.mark.asyncio
async def test_slot_capacity_limit(portfolio):
    # Register 30 positions
    for i in range(30):
        sym = f"PAIR_{i}/USDT:USDT"
        can_open, _ = await portfolio.can_open_position(sym)
        assert can_open is True
        pos = PositionInfo(
            symbol=sym,
            side="LONG",
            entry_price=100.0,
            quantity=1.0,
            leverage=5,
            margin_allocated=10.0,
            stop_loss=95.0,
        )
        reg = await portfolio.register_position(pos)
        assert reg is True

    assert portfolio.active_slots == 30
    assert portfolio.is_full is True

    # 31st position must be rejected
    can_open_31, reason = await portfolio.can_open_position("PAIR_31/USDT:USDT")
    assert can_open_31 is False
    assert "Max slot capacity" in reason


@pytest.mark.asyncio
async def test_single_position_per_symbol_constraint(portfolio):
    sym = "BTC/USDT:USDT"
    can_open, _ = await portfolio.can_open_position(sym)
    assert can_open is True

    pos = PositionInfo(
        symbol=sym,
        side="LONG",
        entry_price=60000.0,
        quantity=0.1,
        leverage=5,
        margin_allocated=120.0,
        stop_loss=57000.0,
    )
    await portfolio.register_position(pos)

    # Attempt to open same symbol again
    can_open_again, reason = await portfolio.can_open_position(sym)
    assert can_open_again is False
    assert "already open" in reason


def test_position_sizing_calculation(portfolio):
    wallet_equity = 10_000.0  # $10,000 USDT
    current_price = 2_000.0   # $2,000 ETH
    # Dynamic margin: 10,000 * 0.01 = $100
    # Notional: $100 * 5 (leverage) = $500
    # Contracts: 500 / 2,000 = 0.25

    market_meta = {
        "precision": {"amount": 2},
        "limits": {"amount": {"min": 0.01}},
    }

    qty, margin = portfolio.calculate_position_size(
        wallet_equity=wallet_equity,
        current_price=current_price,
        market_meta=market_meta,
    )

    assert margin == 100.0
    assert qty == 0.25


@pytest.mark.asyncio
async def test_slot_release(portfolio):
    sym = "SOL/USDT:USDT"
    pos = PositionInfo(
        symbol=sym,
        side="LONG",
        entry_price=150.0,
        quantity=1.0,
        leverage=5,
        margin_allocated=30.0,
        stop_loss=142.5,
    )
    await portfolio.register_position(pos)
    assert portfolio.active_slots == 1

    released = await portfolio.release_position(sym)
    assert released is not None
    assert portfolio.active_slots == 0


@pytest.mark.asyncio
async def test_directional_locking(portfolio):
    # Register a LONG position
    pos_long = PositionInfo(
        symbol="BTC/USDT:USDT",
        side="LONG",
        entry_price=60000.0,
        quantity=0.01,
        leverage=2,
        margin_allocated=10.0,
        stop_loss=58200.0,
    )
    await portfolio.register_position(pos_long)

    # Another LONG should be permitted (if slot capacity allows)
    can_open_long, _ = await portfolio.can_open_position("ETH/USDT:USDT", incoming_side="LONG")
    assert can_open_long is True

    # Incoming SHORT must be strictly rejected due to directional portfolio lock
    can_open_short, reason = await portfolio.can_open_position("SOL/USDT:USDT", incoming_side="SHORT")
    assert can_open_short is False
    assert "Mixed positions prohibited" in reason

