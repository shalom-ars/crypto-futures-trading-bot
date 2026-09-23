"""Universe Discovery, Filtering, and Candidate Scanning with Master BTC Regime."""

import asyncio
from typing import List, Tuple, Literal
import pandas as pd
from config import BotConfig
from exchange import ExchangeService
from indicators import extract_indicators
from scoring import DerivativeData, ScoringBreakdown, calculate_precision_score, evaluate_btc_regime
from logger import BotLogger


class UniverseScanner:
    def __init__(self, config: BotConfig, exchange_service: ExchangeService):
        self.config = config
        self.exchange = exchange_service
        self._previous_oi = {}
        self.current_market_mode: Literal["BULLISH", "BEARISH"] = "BULLISH"

    def filter_usdt_perpetuals(self, tickers: dict) -> List[str]:
        """Filter active USDT-M linear perpetual contracts above minimum 24h volume."""
        qualified = []
        min_vol = self.config.MIN_24H_VOLUME_USDT

        for symbol, market in self.exchange.markets.items():
            is_active = market.get("active", True)
            is_swap = market.get("swap", False) or market.get("type") == "swap"
            is_linear = market.get("linear", False) or market.get("settle") == "USDT"
            is_usdt_quote = market.get("quote") == "USDT"

            if not (is_active and is_swap and is_linear and is_usdt_quote):
                continue

            ticker = tickers.get(symbol)
            if not ticker:
                continue

            quote_volume = ticker.get("quoteVolume")
            if quote_volume is None:
                base_volume = ticker.get("baseVolume", 0.0) or 0.0
                last = ticker.get("last", 0.0) or 0.0
                quote_volume = base_volume * last

            if quote_volume >= min_vol:
                qualified.append((symbol, quote_volume))

        qualified.sort(key=lambda x: x[1], reverse=True)
        selected = [s for s, _ in qualified[: self.config.MAX_UNIVERSE_CANDIDATES]]
        return selected

    async def determine_btc_regime(self) -> Literal["BULLISH", "BEARISH"]:
        """Evaluate BTC/USDT 15m master trend filter (close vs EMA 50)."""
        try:
            btc_sym = "BTC/USDT:USDT" if "BTC/USDT:USDT" in self.exchange.markets else "BTC/USDT"
            raw_ohlcv = await self.exchange.fetch_ohlcv(btc_sym, self.config.TIMEFRAME, limit=100)
            df = pd.DataFrame(raw_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            ind = extract_indicators(df)
            regime = evaluate_btc_regime(ind.current_close, ind.ema50)
            self.current_market_mode = regime
            BotLogger.info(
                f"Master Trend Filter (BTC/USDT 15m): Close={ind.current_close:.1f} vs EMA50={ind.ema50:.1f} "
                f"==> MARKET REGIME: {regime} ({'STRICTLY LONG ONLY' if regime == 'BULLISH' else 'STRICTLY SHORT ONLY'})"
            )
            return regime
        except Exception as e:
            BotLogger.warning(f"Could not fetch BTC regime: {e}. Defaulting to BULLISH.")
            self.current_market_mode = "BULLISH"
            return "BULLISH"

    async def analyze_symbol(
        self, symbol: str, market_mode: Literal["BULLISH", "BEARISH"]
    ) -> Tuple[str, ScoringBreakdown]:
        """Fetch market data and compute precision score for a single symbol."""
        raw_ohlcv = await self.exchange.fetch_ohlcv(symbol, self.config.TIMEFRAME, limit=250)
        df = pd.DataFrame(raw_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        indicators = extract_indicators(df)

        funding = await self.exchange.fetch_funding_rate(symbol)
        curr_oi = await self.exchange.fetch_open_interest(symbol)
        prev_oi = self._previous_oi.get(symbol, curr_oi)
        self._previous_oi[symbol] = curr_oi

        deriv = DerivativeData(
            funding_rate=funding,
            open_interest=curr_oi,
            open_interest_prev=prev_oi,
        )

        breakdown = calculate_precision_score(
            symbol=symbol,
            ind=indicators,
            market_mode=market_mode,
            deriv=deriv,
            score_threshold=self.config.SCORE_THRESHOLD_LONG,
        )
        return symbol, breakdown

    async def scan_universe(self) -> Tuple[List[ScoringBreakdown], List[ScoringBreakdown]]:
        """Scan filtered universe with BTC master trend check."""
        market_mode = await self.determine_btc_regime()

        tickers = await self.exchange.fetch_tickers()
        candidates = self.filter_usdt_perpetuals(tickers)

        scored_results: List[ScoringBreakdown] = []

        batch_size = 10
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i : i + batch_size]
            tasks = [self.analyze_symbol(sym, market_mode) for sym in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for res in batch_results:
                if isinstance(res, Exception):
                    continue
                _, breakdown = res
                scored_results.append(breakdown)

            await asyncio.sleep(0.2)

        longs = [b for b in scored_results if b.action == "LONG"]
        shorts = [b for b in scored_results if b.action == "SHORT"]

        longs.sort(key=lambda x: x.total_score, reverse=True)
        shorts.sort(key=lambda x: x.total_score)

        return longs, shorts
