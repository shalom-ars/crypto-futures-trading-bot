"""CLI Entrypoint for Crypto Futures Multi-Pair Scanner & Trailing Execution Bot."""

import argparse
import asyncio
import os
import signal
import sys
from config import BotConfig
from bot import CryptoFuturesBot
from logger import setup_logger, BotLogger


def parse_arguments() -> BotConfig:
    parser = argparse.ArgumentParser(
        description="Crypto Futures Multi-Pair Scanner & Trailing Execution Bot (Binance / Bybit USDT-M)"
    )

    parser.add_argument(
        "--exchange",
        type=str,
        default="binance",
        choices=["binance", "bybit"],
        help="Target exchange ID (default: binance)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run in LIVE trading mode (default is dry-run / simulation)",
    )
    parser.add_argument(
        "--testnet",
        action="store_true",
        help="Use exchange testnet / sandbox environment",
    )
    parser.add_argument(
        "--leverage",
        type=int,
        default=5,
        help="Default leverage multiplier (default: 5)",
    )
    parser.add_argument(
        "--margin-mode",
        type=str,
        default="cross",
        choices=["cross", "isolated"],
        help="Margin mode: cross or isolated (default: cross)",
    )
    parser.add_argument(
        "--max-slots",
        type=int,
        default=30,
        help="Maximum concurrent active position slots (default: 30)",
    )
    parser.add_argument(
        "--scan-interval",
        type=int,
        default=60,
        help="Seconds between universe scanning cycles (default: 60)",
    )
    parser.add_argument(
        "--min-volume",
        type=float,
        default=5_000_000.0,
        help="Minimum 24h quote volume in USDT for universe inclusion (default: 5000000)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )

    args = parser.parse_args()

    config = BotConfig(
        EXCHANGE_ID=args.exchange,
        DRY_RUN=not args.live,
        TESTNET=args.testnet,
        DEFAULT_LEVERAGE=args.leverage,
        MARGIN_MODE=args.margin_mode,
        MAX_ACTIVE_SLOTS=args.max_slots,
        SCAN_INTERVAL_SECONDS=args.scan_interval,
        MIN_24H_VOLUME_USDT=args.min_volume,
        LOG_LEVEL=args.log_level,
    )
    return config


def setup_signal_handlers(bot: CryptoFuturesBot, loop: asyncio.AbstractEventLoop):
    """Setup graceful shutdown signal handlers for SIGINT and SIGTERM."""
    def handle_signal():
        BotLogger.warning("Shutdown signal received! Terminating cleanly...")
        loop.create_task(bot.shutdown())

    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handle_signal)
    else:
        # Windows compatibility
        signal.signal(signal.SIGINT, lambda s, f: handle_signal())
        signal.signal(signal.SIGTERM, lambda s, f: handle_signal())


async def main_async():
    config = parse_arguments()
    setup_logger(
        log_level=config.LOG_LEVEL,
        log_to_file=config.LOG_TO_FILE,
        log_file_path=config.LOG_FILE_PATH,
    )

    bot = CryptoFuturesBot(config)
    loop = asyncio.get_running_loop()
    setup_signal_handlers(bot, loop)

    try:
        await bot.start()
    except KeyboardInterrupt:
        await bot.shutdown()


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
