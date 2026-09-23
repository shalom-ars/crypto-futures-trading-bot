"""Asynchronous 3-Step Trailing Stop & Breakeven Protection Engine per Slot."""

import asyncio
from typing import Callable, Optional
from config import BotConfig
from exchange import ExchangeService
from risk import PortfolioManager, PositionInfo
from logger import BotLogger


class TrailingWorker:
    """Independent asynchronous worker running for each active position slot with 3-step protection."""

    def __init__(
        self,
        position: PositionInfo,
        config: BotConfig,
        exchange: ExchangeService,
        portfolio: PortfolioManager,
        on_exit_callback: Optional[Callable[[str, float, float], None]] = None,
    ):
        self.position = position
        self.config = config
        self.exchange = exchange
        self.portfolio = portfolio
        self.on_exit_callback = on_exit_callback
        self._stop_event = asyncio.Event()

    def stop(self):
        """Signal worker to terminate."""
        self._stop_event.set()

    async def run(self):
        """Execute WebSocket price monitoring with 3-step protection."""
        symbol = self.position.symbol
        side = self.position.side
        entry_price = self.position.entry_price

        BotLogger.info(
            f"Spawned TrailingWorker for {symbol} ({side}). Entry: {entry_price:.4f} | "
            f"Initial Hard SL (3%): {self.position.current_stop_loss:.4f} | "
            f"Breakeven Trigger (+1.0%): {self.config.BREAKEVEN_TRIGGER_PCT * 100:.1f}% (+0.25% buffer) | "
            f"Trailing Activation: +{self.config.TRAILING_PROFIT_TRIGGER_PCT * 100:.1f}% | "
            f"Callback Pullback: {self.config.TRAILING_CALLBACK_PCT * 100:.1f}%"
        )

        try:
            async for ticker in self.exchange.watch_ticker_stream(symbol, self._stop_event):
                if self._stop_event.is_set():
                    break

                current_price = float(ticker.get("last") or ticker.get("close") or 0.0)
                if current_price <= 0:
                    continue

                exited = await self._check_price_conditions(current_price)
                if exited:
                    break

        except asyncio.CancelledError:
            BotLogger.info(f"TrailingWorker cancelled for {symbol}")
        except Exception as e:
            BotLogger.error(f"TrailingWorker exception for {symbol}: {e}")
        finally:
            self._stop_event.set()

    async def _check_price_conditions(self, current_price: float) -> bool:
        """Evaluate 3-step protection:
        1. Hard SL or Ratcheted Breakeven SL breach check.
        2. Breakeven Lock at +1.0% unrealized PnL (moves SL to entry + 0.25% fee buffer).
        3. Trailing Activation at +1.5% unrealized PnL with 0.6% peak pullback exit.
        """
        symbol = self.position.symbol
        side = self.position.side
        entry = self.position.entry_price

        if side == "LONG":
            unrealized_pnl_pct = (current_price - entry) / entry

            # 1. Stop-Loss Breach Check (either initial 3% SL or Breakeven +0.25% SL)
            if current_price <= self.position.current_stop_loss:
                reason = "BREAKEVEN_STOP" if self.position.breakeven_locked else "EMERGENCY_STOP_LOSS"
                await self._execute_exit(
                    exit_price=current_price,
                    reason=reason,
                    pnl_pct=unrealized_pnl_pct,
                )
                return True

            # 2. Step 1: Break-Even Lock at +1.0% PnL
            if not self.position.breakeven_locked and unrealized_pnl_pct >= self.config.BREAKEVEN_TRIGGER_PCT:
                self.position.breakeven_locked = True
                new_sl = entry * (1.0 + self.config.BREAKEVEN_BUFFER_PCT)  # +0.25% fee buffer
                self.position.current_stop_loss = max(self.position.current_stop_loss, new_sl)
                BotLogger.trade_event(
                    symbol=symbol,
                    action="BREAKEVEN_LOCK",
                    active_slots=self.portfolio.active_slots,
                    max_slots=self.portfolio.max_slots,
                    entry_price=entry,
                    exit_price=new_sl,
                    pnl_pct=unrealized_pnl_pct,
                    extra="Protected at Entry + 0.25% fee buffer",
                )

            # 3. Step 2: Trailing Take-Profit Activation ONLY after +1.5% profit
            if not self.position.trailing_active and unrealized_pnl_pct >= self.config.TRAILING_PROFIT_TRIGGER_PCT:
                self.position.trailing_active = True
                self.position.peak_price = current_price
                BotLogger.info(
                    f"{symbol} (LONG): Trailing ACTIVATED at {current_price:.4f} (+{unrealized_pnl_pct * 100:.2f}% profit)"
                )

            # 4. Step 3: Trailing Execution (0.6% Pullback Tolerance from Peak)
            if self.position.trailing_active:
                if current_price > self.position.peak_price:
                    self.position.peak_price = current_price

                trailing_stop_price = self.position.peak_price * (1.0 - self.config.TRAILING_CALLBACK_PCT)
                if current_price <= trailing_stop_price:
                    await self._execute_exit(
                        exit_price=current_price,
                        reason="TRAILING_TAKE_PROFIT",
                        pnl_pct=unrealized_pnl_pct,
                    )
                    return True

        elif side == "SHORT":
            unrealized_pnl_pct = (entry - current_price) / entry

            # 1. Stop-Loss Breach Check (either initial 3% SL or Breakeven -0.25% SL)
            if current_price >= self.position.current_stop_loss:
                reason = "BREAKEVEN_STOP" if self.position.breakeven_locked else "EMERGENCY_STOP_LOSS"
                await self._execute_exit(
                    exit_price=current_price,
                    reason=reason,
                    pnl_pct=unrealized_pnl_pct,
                )
                return True

            # 2. Step 1: Break-Even Lock at +1.0% PnL
            if not self.position.breakeven_locked and unrealized_pnl_pct >= self.config.BREAKEVEN_TRIGGER_PCT:
                self.position.breakeven_locked = True
                new_sl = entry * (1.0 - self.config.BREAKEVEN_BUFFER_PCT)  # -0.25% fee buffer for Short
                self.position.current_stop_loss = min(self.position.current_stop_loss, new_sl)
                BotLogger.trade_event(
                    symbol=symbol,
                    action="BREAKEVEN_LOCK",
                    active_slots=self.portfolio.active_slots,
                    max_slots=self.portfolio.max_slots,
                    entry_price=entry,
                    exit_price=new_sl,
                    pnl_pct=unrealized_pnl_pct,
                    extra="Protected at Entry - 0.25% fee buffer",
                )

            # 3. Step 2: Trailing Take-Profit Activation ONLY after +1.5% profit
            if not self.position.trailing_active and unrealized_pnl_pct >= self.config.TRAILING_PROFIT_TRIGGER_PCT:
                self.position.trailing_active = True
                self.position.trough_price = current_price
                BotLogger.info(
                    f"{symbol} (SHORT): Trailing ACTIVATED at {current_price:.4f} (+{unrealized_pnl_pct * 100:.2f}% profit)"
                )

            # 4. Step 3: Trailing Execution (0.6% Pullback Tolerance from Trough)
            if self.position.trailing_active:
                if current_price < self.position.trough_price:
                    self.position.trough_price = current_price

                trailing_stop_price = self.position.trough_price * (1.0 + self.config.TRAILING_CALLBACK_PCT)
                if current_price >= trailing_stop_price:
                    await self._execute_exit(
                        exit_price=current_price,
                        reason="TRAILING_TAKE_PROFIT",
                        pnl_pct=unrealized_pnl_pct,
                    )
                    return True

        return False

    async def _execute_exit(self, exit_price: float, reason: str, pnl_pct: float):
        """Execute market reduce-only order, update metrics, and release portfolio slot."""
        symbol = self.position.symbol
        close_side = "sell" if self.position.side == "LONG" else "buy"

        try:
            await self.exchange.execute_market_order(
                symbol=symbol,
                side=close_side,
                amount=self.position.quantity,
                reduce_only=True,
            )
        except Exception as e:
            BotLogger.error(f"Failed to submit exit order for {symbol}: {e}")

        notional = self.position.quantity * self.position.entry_price
        realized_pnl_usdt = pnl_pct * notional

        if self.config.DRY_RUN:
            self.exchange.update_simulated_equity(realized_pnl_usdt)

        await self.portfolio.release_position(symbol)

        BotLogger.trade_event(
            symbol=symbol,
            action=reason,
            active_slots=self.portfolio.active_slots,
            max_slots=self.portfolio.max_slots,
            entry_price=self.position.entry_price,
            exit_price=exit_price,
            pnl_pct=pnl_pct,
            extra=f"Realized PnL: ${realized_pnl_usdt:+.2f} USDT",
        )

        if self.on_exit_callback:
            try:
                self.on_exit_callback(symbol, exit_price, pnl_pct)
            except Exception as e:
                BotLogger.error(f"Exit callback error: {e}")
