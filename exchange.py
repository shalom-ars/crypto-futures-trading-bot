"""Exchange Adapter Wrapping CCXT & CCXT.pro with Resilience & Dry-Run Support."""

import asyncio
from typing import AsyncGenerator, Dict, List, Optional
import ccxt.pro as ccxtpro
import ccxt.async_support as ccxt_async
from config import BotConfig
from logger import BotLogger


class ExchangeService:
    def __init__(self, config: BotConfig):
        self.config = config
        self._exchange_cls = getattr(ccxtpro, config.EXCHANGE_ID, None)
        if self._exchange_cls is None:
            raise ValueError(f"Exchange '{config.EXCHANGE_ID}' not supported by ccxt.pro")

        options = {
            "defaultType": "swap",
            "defaultSubType": "linear",
            "adjustForTimeDifference": True,
        }

        exchange_params = {
            "apiKey": config.API_KEY,
            "secret": config.API_SECRET,
            "password": config.API_PASSWORD,
            "enableRateLimit": True,
            "options": options,
        }

        # Initialize ccxt.pro instance for WebSockets & REST
        self.client = self._exchange_cls(exchange_params)
        if config.TESTNET:
            self.client.set_sandbox_mode(True)

        self.markets: Dict[str, dict] = {}
        self._simulated_equity = config.SIMULATED_WALLET_EQUITY

    async def initialize(self):
        """Load exchange markets and configure environment."""
        BotLogger.info(f"Connecting to {self.config.EXCHANGE_ID.upper()} (Testnet={self.config.TESTNET}, DryRun={self.config.DRY_RUN})...")
        await self.retry_api_call(self.client.load_markets)
        self.markets = self.client.markets
        BotLogger.info(f"Loaded {len(self.markets)} markets from {self.config.EXCHANGE_ID}.")

    async def retry_api_call(self, func, *args, max_retries: int = 5, initial_delay: float = 1.0, **kwargs):
        """Execute async API call with exponential backoff and rate limit handling."""
        delay = initial_delay
        for attempt in range(1, max_retries + 1):
            try:
                return await func(*args, **kwargs)
            except (ccxt_async.RateLimitExceeded, ccxt_async.DDoSProtection) as e:
                wait_time = delay * 2
                BotLogger.warning(f"Rate limit hit on {func.__name__} (attempt {attempt}/{max_retries}): {e}. Backing off {wait_time:.1f}s")
                await asyncio.sleep(wait_time)
                delay *= 2
            except (ccxt_async.NetworkError, ccxt_async.RequestTimeout) as e:
                BotLogger.warning(f"Network error on {func.__name__} (attempt {attempt}/{max_retries}): {e}. Retrying in {delay:.1f}s")
                await asyncio.sleep(delay)
                delay *= 1.5
            except Exception as e:
                BotLogger.error(f"Error on {func.__name__}: {e}")
                if attempt == max_retries:
                    raise
                await asyncio.sleep(delay)
                delay *= 1.5
        raise RuntimeError(f"Exceeded max retries ({max_retries}) for {func.__name__}")

    async def get_wallet_equity(self) -> float:
        """Fetch total wallet equity in USDT."""
        if self.config.DRY_RUN:
            return self._simulated_equity

        try:
            balance = await self.retry_api_call(self.client.fetch_balance)
            total = balance.get("total", {})
            return float(total.get("USDT", 0.0))
        except Exception as e:
            BotLogger.error(f"Failed to fetch live balance: {e}. Falling back to simulated equity.")
            return self._simulated_equity

    def update_simulated_equity(self, pnl_usdt: float):
        """Update dry-run simulated equity."""
        self._simulated_equity += pnl_usdt

    async def fetch_tickers(self) -> Dict[str, dict]:
        """Fetch all 24-hour tickers."""
        return await self.retry_api_call(self.client.fetch_tickers)

    async def fetch_ohlcv(self, symbol: str, timeframe: str = "15m", limit: int = 250) -> List[list]:
        """Fetch historical OHLCV candles."""
        return await self.retry_api_call(self.client.fetch_ohlcv, symbol, timeframe, limit=limit)

    async def fetch_funding_rate(self, symbol: str) -> float:
        """Fetch current funding rate for symbol."""
        try:
            res = await self.retry_api_call(self.client.fetch_funding_rate, symbol)
            return float(res.get("fundingRate") or 0.0)
        except Exception:
            # Fallback if unsupported or error
            return 0.0

    async def fetch_open_interest(self, symbol: str) -> float:
        """Fetch current open interest."""
        try:
            res = await self.retry_api_call(self.client.fetch_open_interest, symbol)
            return float(res.get("openInterestAmount") or res.get("openInterestValue") or 0.0)
        except Exception:
            return 0.0

    async def configure_symbol_leverage(self, symbol: str):
        """Set leverage and margin mode on exchange if not dry-run."""
        if self.config.DRY_RUN:
            return

        try:
            # Set leverage
            await self.retry_api_call(self.client.set_leverage, self.config.DEFAULT_LEVERAGE, symbol)
            # Set margin mode
            try:
                await self.retry_api_call(self.client.set_margin_mode, self.config.MARGIN_MODE, symbol)
            except Exception as e:
                # Some exchanges reject setting margin mode if already set or not allowed
                BotLogger.debug(f"Margin mode note for {symbol}: {e}")
        except Exception as e:
            BotLogger.warning(f"Could not configure leverage for {symbol}: {e}")

    async def execute_market_order(
        self,
        symbol: str,
        side: str,  # "buy" or "sell"
        amount: float,
        reduce_only: bool = False,
    ) -> dict:
        """Execute market order with reduceOnly support."""
        if self.config.DRY_RUN:
            # Simulated execution
            ticker = await self.retry_api_call(self.client.fetch_ticker, symbol)
            price = float(ticker.get("last") or ticker.get("close") or 0.0)
            return {
                "id": f"sim_{int(asyncio.get_event_loop().time() * 1000)}",
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "price": price,
                "average": price,
                "status": "closed",
                "reduceOnly": reduce_only,
            }

        params = {}
        if reduce_only:
            params["reduceOnly"] = True

        return await self.retry_api_call(
            self.client.create_market_order,
            symbol,
            side,
            amount,
            params=params,
        )

    async def watch_ticker_stream(
        self, symbol: str, stop_event: asyncio.Event
    ) -> AsyncGenerator[dict, None]:
        """Stream live prices via ccxt.pro WebSocket with automatic reconnection."""
        backoff = 1.0
        while not stop_event.is_set():
            try:
                ticker = await self.client.watch_ticker(symbol)
                backoff = 1.0  # Reset backoff on successful packet
                yield ticker
            except asyncio.CancelledError:
                break
            except Exception as e:
                if stop_event.is_set():
                    break
                BotLogger.warning(f"WS Ticker disconnected for {symbol}: {e}. Reconnecting in {backoff:.1f}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def close(self):
        """Gracefully close all WebSocket and REST connections."""
        BotLogger.info("Closing exchange connections...")
        try:
            await self.client.close()
        except Exception as e:
            BotLogger.warning(f"Error while closing exchange client: {e}")
