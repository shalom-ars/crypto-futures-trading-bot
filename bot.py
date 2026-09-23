"""Crypto Futures Bot Main Orchestrator."""

import asyncio
from typing import Dict, List, Optional
from config import BotConfig
from exchange import ExchangeService
from risk import PortfolioManager, PositionInfo
from universe import UniverseScanner
from trailing import TrailingWorker
from scoring import ScoringBreakdown
from logger import BotLogger


class CryptoFuturesBot:
    def __init__(self, config: BotConfig):
        self.config = config
        self.exchange = ExchangeService(config)
        self.portfolio = PortfolioManager(config)
        self.scanner = UniverseScanner(config, self.exchange)
        self._running = False
        self._stop_event = asyncio.Event()
        self._workers: Dict[str, TrailingWorker] = {}
        self._tasks: Dict[str, asyncio.Task] = {}

    async def start(self):
        """Start the trading bot main event loop."""
        self._running = True
        BotLogger.info("Starting Crypto Futures Multi-Pair Scanner & Trailing Execution Bot...")
        await self.exchange.initialize()

        equity = await self.exchange.get_wallet_equity()
        BotLogger.info(
            f"Initialized Portfolio. Total Equity: ${equity:,.2f} USDT | "
            f"Max Slots: {self.config.MAX_ACTIVE_SLOTS} | "
            f"Risk/Trade: {self.config.CAPITAL_RISK_PER_TRADE_PCT * 100:.1f}% | "
            f"Leverage: {self.config.DEFAULT_LEVERAGE}x | "
            f"Hard SL: {self.config.STOP_LOSS_PCT * 100:.1f}% | "
            f"Trailing: +{self.config.TRAILING_PROFIT_TRIGGER_PCT * 100:.2f}% / -{self.config.TRAILING_CALLBACK_PCT * 100:.2f}%"
        )

        try:
            while not self._stop_event.is_set():
                await self._run_scan_cycle()

                # Sleep until next scan interval, checking stop_event periodically
                for _ in range(self.config.SCAN_INTERVAL_SECONDS):
                    if self._stop_event.is_set():
                        break
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            BotLogger.info("Main loop received cancellation signal.")
        finally:
            await self.shutdown()

    async def _run_scan_cycle(self):
        """Execute one universe scan cycle and dispatch entry signals."""
        self._cleanup_finished_tasks()

        equity = await self.exchange.get_wallet_equity()
        BotLogger.info(
            f"Beginning Scan Cycle. Active Slots: [{self.portfolio.active_slots}/{self.portfolio.max_slots}] | "
            f"Wallet Equity: ${equity:,.2f} USDT"
        )

        try:
            longs, shorts = await self.scanner.scan_universe()
        except Exception as e:
            BotLogger.error(f"Error during universe scan: {e}")
            return

        BotLogger.info(
            f"Scan Complete: Found {len(longs)} Long candidates (>= +{self.config.SCORE_THRESHOLD_LONG}) "
            f"and {len(shorts)} Short candidates (<= {self.config.SCORE_THRESHOLD_SHORT})."
        )

        # Process Long signals
        for candidate in longs:
            if self._stop_event.is_set():
                return
            await self._process_signal(candidate)

        # Process Short signals
        for candidate in shorts:
            if self._stop_event.is_set():
                return
            await self._process_signal(candidate)

    async def _process_signal(self, candidate: ScoringBreakdown):
        """Evaluate slot availability and execute entry order."""
        symbol = candidate.symbol
        side = candidate.action

        # Check slot constraints and directional portfolio regime
        can_open, reason = await self.portfolio.can_open_position(symbol, incoming_side=side)
        if not can_open:
            BotLogger.scan_result(
                symbol=symbol,
                score=candidate.total_score,
                action=f"REJECT_{side}",
                active_slots=self.portfolio.active_slots,
                max_slots=self.portfolio.max_slots,
                details=f"Reason: {reason}",
            )
            return

        # Fetch current equity and market metadata
        equity = await self.exchange.get_wallet_equity()
        market_meta = self.exchange.markets.get(symbol)
        price = candidate.close_price

        # Calculate position size
        qty, margin = self.portfolio.calculate_position_size(
            wallet_equity=equity,
            current_price=price,
            market_meta=market_meta,
        )

        if qty <= 0:
            BotLogger.warning(f"Calculated 0 quantity for {symbol} at ${price:.4f}. Skipping trade.")
            return

        # Log candidate trigger
        BotLogger.scan_result(
            symbol=symbol,
            score=candidate.total_score,
            action=side,
            active_slots=self.portfolio.active_slots,
            max_slots=self.portfolio.max_slots,
            details=candidate.details,
        )

        # Configure leverage on exchange
        await self.exchange.configure_symbol_leverage(symbol)

        # Execute market entry order
        order_side = "buy" if side == "LONG" else "sell"
        try:
            order_res = await self.exchange.execute_market_order(
                symbol=symbol,
                side=order_side,
                amount=qty,
                reduce_only=False,
            )
        except Exception as e:
            BotLogger.error(f"Failed to execute entry order for {symbol}: {e}")
            return

        exec_price = float(order_res.get("average") or order_res.get("price") or price)

        # Calculate hard Stop Loss (strict 3%)
        if side == "LONG":
            sl_price = exec_price * (1.0 - self.config.STOP_LOSS_PCT)
        else:
            sl_price = exec_price * (1.0 + self.config.STOP_LOSS_PCT)

        # Construct position object with 3-step protection tracking
        position = PositionInfo(
            symbol=symbol,
            side=side,
            entry_price=exec_price,
            quantity=qty,
            leverage=self.config.DEFAULT_LEVERAGE,
            margin_allocated=margin,
            stop_loss=sl_price,
            current_stop_loss=sl_price,
        )

        # Register in portfolio
        registered = await self.portfolio.register_position(position)
        if not registered:
            return

        # Spawn TrailingWorker task
        worker = TrailingWorker(
            position=position,
            config=self.config,
            exchange=self.exchange,
            portfolio=self.portfolio,
            on_exit_callback=self._on_worker_exit,
        )
        task = asyncio.create_task(worker.run(), name=f"Worker_{symbol}")
        position.task = task
        self._workers[symbol] = worker
        self._tasks[symbol] = task

    def _on_worker_exit(self, symbol: str, exit_price: float, pnl_pct: float):
        """Callback invoked when TrailingWorker completes an exit."""
        self._workers.pop(symbol, None)
        self._tasks.pop(symbol, None)

    def _cleanup_finished_tasks(self):
        """Remove completed tasks from registry."""
        finished = [sym for sym, t in self._tasks.items() if t.done()]
        for sym in finished:
            self._workers.pop(sym, None)
            self._tasks.pop(sym, None)

    async def shutdown(self):
        """Graceful shutdown: cancel worker tasks and close exchange sockets."""
        if self._stop_event.is_set() and not self._running:
            return
        self._stop_event.set()
        self._running = False
        BotLogger.info("Initiating graceful shutdown...")

        active_positions = self.portfolio.get_all_positions()
        if active_positions:
            BotLogger.warning(f"Active positions at shutdown: {len(active_positions)}")
            for sym, pos in active_positions.items():
                BotLogger.warning(
                    f"Position: {sym} | {pos.side} | Qty: {pos.quantity} | Entry: {pos.entry_price:.4f}"
                )

        # Stop workers
        for worker in self._workers.values():
            worker.stop()

        # Cancel tasks
        for task in self._tasks.values():
            if not task.done():
                task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

        await self.exchange.close()
        BotLogger.success("Shutdown complete. All resources cleanly released.")
