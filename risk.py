"""Portfolio and Risk Management Architecture with Regime Direction Locks."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
from config import BotConfig
from logger import BotLogger


@dataclass
class PositionInfo:
    symbol: str
    side: str  # "LONG" or "SHORT"
    entry_price: float
    quantity: float
    leverage: int
    margin_allocated: float
    stop_loss: float  # Initial Hard SL
    current_stop_loss: float = 0.0  # Dynamic (ratchets to breakeven + fee buffer)
    breakeven_locked: bool = False
    entry_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    peak_price: float = 0.0
    trough_price: float = 0.0
    trailing_active: bool = False
    task: Optional[asyncio.Task] = None

    def __post_init__(self):
        if self.current_stop_loss == 0.0:
            self.current_stop_loss = self.stop_loss
        if self.peak_price == 0.0:
            self.peak_price = self.entry_price
        if self.trough_price == 0.0:
            self.trough_price = self.entry_price


class PortfolioManager:
    def __init__(self, config: BotConfig):
        self.config = config
        self._active_positions: Dict[str, PositionInfo] = {}
        self._lock = asyncio.Lock()

    @property
    def active_slots(self) -> int:
        return len(self._active_positions)

    @property
    def max_slots(self) -> int:
        return self.config.MAX_ACTIVE_SLOTS

    @property
    def is_full(self) -> bool:
        return self.active_slots >= self.max_slots

    def has_position(self, symbol: str) -> bool:
        return symbol in self._active_positions

    def get_position(self, symbol: str) -> Optional[PositionInfo]:
        return self._active_positions.get(symbol)

    def get_all_positions(self) -> Dict[str, PositionInfo]:
        return dict(self._active_positions)

    async def can_open_position(self, symbol: str, incoming_side: Optional[str] = None) -> Tuple[bool, str]:
        """Check if a new position can be opened.
        Strictly enforces:
        1. Max slot capacity.
        2. Strict 1 position per symbol.
        3. No mixed Long/Short positions across the portfolio simultaneously.
        """
        async with self._lock:
            if self.active_slots >= self.max_slots:
                return False, f"Max slot capacity reached ({self.active_slots}/{self.max_slots})"
            if symbol in self._active_positions:
                return False, f"Position already open for symbol {symbol}"

            # Strict directional portfolio regime check
            if incoming_side and self._active_positions:
                existing_side = next(iter(self._active_positions.values())).side
                if incoming_side != existing_side:
                    return False, f"Mixed positions prohibited. Portfolio currently {existing_side}, rejecting {incoming_side}"

            return True, "OK"

    def calculate_position_size(
        self,
        wallet_equity: float,
        current_price: float,
        market_meta: Optional[dict] = None,
    ) -> Tuple[float, float]:
        """Calculate dynamic trade margin and contracts."""
        if current_price <= 0 or wallet_equity <= 0:
            return 0.0, 0.0

        trade_margin = wallet_equity * self.config.CAPITAL_RISK_PER_TRADE_PCT
        notional_value = trade_margin * self.config.DEFAULT_LEVERAGE
        raw_quantity = notional_value / current_price

        adjusted_quantity = raw_quantity
        if market_meta:
            precision_amount = market_meta.get("precision", {}).get("amount")
            limits = market_meta.get("limits", {}).get("amount", {})
            min_amount = limits.get("min", 0.0) if limits else 0.0

            if precision_amount is not None:
                if isinstance(precision_amount, int):
                    adjusted_quantity = round(raw_quantity, precision_amount)
                elif isinstance(precision_amount, float) and precision_amount > 0:
                    adjusted_quantity = (raw_quantity // precision_amount) * precision_amount

            if min_amount and adjusted_quantity < min_amount:
                BotLogger.warning(
                    f"Calculated size {adjusted_quantity} below min order size {min_amount} for {market_meta.get('symbol')}"
                )
                return 0.0, trade_margin

        return adjusted_quantity, trade_margin

    async def register_position(self, position: PositionInfo) -> bool:
        """Register newly opened position in active slot table."""
        async with self._lock:
            if len(self._active_positions) >= self.max_slots:
                BotLogger.warning(f"Slots full [{len(self._active_positions)}/{self.max_slots}]")
                return False

            if position.symbol in self._active_positions:
                return False

            # Ensure direction alignment
            if self._active_positions:
                existing_side = next(iter(self._active_positions.values())).side
                if position.side != existing_side:
                    BotLogger.warning(f"Rejecting {position.symbol} {position.side}: violates portfolio {existing_side} lock.")
                    return False

            self._active_positions[position.symbol] = position
            BotLogger.trade_event(
                symbol=position.symbol,
                action=f"ENTRY_{position.side}",
                active_slots=len(self._active_positions),
                max_slots=self.max_slots,
                entry_price=position.entry_price,
                extra=f"Qty: {position.quantity} | Margin: ${position.margin_allocated:.2f} | Hard SL (3%): {position.current_stop_loss:.4f}",
            )
            return True

    async def release_position(self, symbol: str) -> Optional[PositionInfo]:
        """Release slot when position is closed."""
        async with self._lock:
            pos = self._active_positions.pop(symbol, None)
            if pos:
                BotLogger.info(
                    f"Released slot for {symbol}. Active: [{len(self._active_positions)}/{self.max_slots}]"
                )
            return pos
