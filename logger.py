"""Structured Logging for Crypto Futures Bot.

Format: Timestamp | Symbol | Action | Entry/Exit Price | PnL % | [X/30 Slots Used]
"""

import sys
from datetime import datetime, timezone
from typing import Optional
from loguru import logger


def setup_logger(log_level: str = "INFO", log_to_file: bool = True, log_file_path: str = "bot.log"):
    """Configure loguru logging with clean stdout and optional file sink."""
    logger.remove()

    # Standard console log format
    console_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <7}</level> | "
        "<cyan>{message}</cyan>"
    )

    logger.add(
        sys.stdout,
        format=console_format,
        level=log_level,
        colorize=True,
    )

    if log_to_file:
        file_format = "{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {message}"
        logger.add(
            log_file_path,
            format=file_format,
            level=log_level,
            rotation="10 MB",
            retention="7 days",
        )


class BotLogger:
    @staticmethod
    def trade_event(
        symbol: str,
        action: str,
        active_slots: int,
        max_slots: int = 30,
        entry_price: Optional[float] = None,
        exit_price: Optional[float] = None,
        pnl_pct: Optional[float] = None,
        extra: str = "",
    ):
        """Log structured trade event.

        Example:
        2026-09-24 02:10:00 | BTC/USDT:USDT | EXIT_TRAILING | Entry: 65000.0 | Exit: 65800.0 | PnL: +1.23% | [14/30 Slots Used]
        """
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        entry_str = f"Entry: {entry_price:.4f}" if entry_price is not None else "Entry: N/A"
        exit_str = f"Exit: {exit_price:.4f}" if exit_price is not None else "Exit: N/A"

        if pnl_pct is not None:
            sign = "+" if pnl_pct >= 0 else ""
            pnl_str = f"PnL: {sign}{pnl_pct * 100:.2f}%"
        else:
            pnl_str = "PnL: N/A"

        slots_str = f"[{active_slots}/{max_slots} Slots Used]"

        msg = f"{symbol} | {action: <14} | {entry_str} | {exit_str} | {pnl_str} | {slots_str}"
        if extra:
            msg += f" | {extra}"

        if "STOP_LOSS" in action or "EMERGENCY" in action:
            logger.warning(msg)
        elif "EXIT" in action:
            logger.success(msg)
        elif "ENTRY" in action:
            logger.info(msg)
        else:
            logger.info(msg)

    @staticmethod
    def scan_result(
        symbol: str,
        score: float,
        action: str,
        active_slots: int,
        max_slots: int = 30,
        details: str = "",
    ):
        slots_str = f"[{active_slots}/{max_slots} Slots Used]"
        msg = f"{symbol: <14} | SCORE: {score:+6.1f} | {action: <7} | {slots_str} | {details}"
        if action in ("LONG", "SHORT"):
            logger.info(msg)
        else:
            logger.debug(msg)

    @staticmethod
    def info(msg: str):
        logger.info(msg)

    @staticmethod
    def warning(msg: str):
        logger.warning(msg)

    @staticmethod
    def error(msg: str):
        logger.error(msg)

    @staticmethod
    def success(msg: str):
        logger.success(msg)
