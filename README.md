# Crypto Futures Multi-Pair Scanner & Trailing Execution Bot

A production-grade, asynchronous cryptocurrency futures trading bot in Python using `asyncio` and `ccxt.pro` / `ccxt` targeting Binance and Bybit USDT-M Perpetual Futures.

---

## Key Features

1. **Centralized Configuration (`config.py`)**:
   - `MAX_ACTIVE_SLOTS`: 30 concurrent open positions across portfolio.
   - `SCORE_THRESHOLD_LONG`: `+70` trigger for Long entries.
   - `SCORE_THRESHOLD_SHORT`: `-70` trigger for Short entries.
   - `CAPITAL_RISK_PER_TRADE_PCT`: `0.01` (1% available wallet equity per trade margin).
   - `STOP_LOSS_PCT`: `0.05` (strict 5% Stop-Loss from entry price).
   - `TRAILING_PROFIT_TRIGGER_PCT`: `0.005` (activates trailing once position hits +0.5% profit).
   - `TRAILING_CALLBACK_PCT`: `0.008` (exits immediately if price retraces 0.8% from peak/trough).
   - `TIMEFRAME`: `"15m"`.
   - `DEFAULT_LEVERAGE`: `5` (configurable cross / isolated margin).

2. **Dynamic Universe & Multi-Factor Scoring Engine (-100 to +100)**:
   - **Universe Filtering**: Queries active USDT-M linear perpetual contracts and discards low-liquidity pairs below 24h volume threshold (default $5,000,000).
   - **Trend Alignment (Max ±30 pts)**: Evaluates EMA 20 > EMA 50 > EMA 200 alignment.
   - **Momentum (Max ±25 pts)**: Evaluates Wilder's RSI(14) direction + MACD histogram expansion/crossover.
   - **Volume Dynamics (Max ±25 pts)**: Compares current 15m volume against 20-period Volume SMA (> 1.8x multiplier directional weighting).
   - **Derivative Specifics (Max ±20 pts)**: Evaluates funding rate bias and Open Interest (OI) momentum.
   - **Real-Time Ranking**: Ranks top candidates; triggers Long on score >= +70 and Short on score <= -70.

3. **Portfolio & Risk Architecture**:
   - Strict 30-slot ceiling enforced dynamically.
   - Strict 1 position per symbol constraint.
   - Dynamic Margin Calculation: `Trade Margin = Wallet Equity * 0.01`.
   - Dynamic Order Sizing: `Contracts = (Trade Margin * Leverage) / Market Price`, normalized to exchange step size and lot minimums.

4. **Execution & Trailing Stop Engine**:
   - Each active position is managed by an independent `TrailingWorker` async task.
   - Tracks live WebSocket ticker stream (`watch_ticker_stream`).
   - Hard Stop-Loss: Immediate market `reduceOnly: True` exit upon 5% adverse breach.
   - Dynamic Trailing Take-Profit:
     - Longs: Activates at `entry * (1 + 0.005)`. Tracks running `peak_price`. Exits when `price <= peak * (1 - 0.008)`.
     - Shorts: Activates at `entry * (1 - 0.005)`. Tracks running `trough_price`. Exits when `price >= trough * (1 + 0.008)`.

5. **Resilience & Production Hardening**:
   - CCXT rate limiting (`enableRateLimit: True`) with exponential backoff on REST and WebSocket streams.
   - Automatic WebSocket reconnection handlers.
   - High-fidelity dry-run / paper trading simulation mode.
   - Clean SIGINT/SIGTERM graceful shutdown listeners preventing orphaned orders.
   - Structured logging: `Timestamp | Symbol | Action | Entry/Exit Price | PnL % | [X/30 Slots Used]`.

---

## Directory Structure

```
crypto_futures_bot/
├── config.py             # Central dataclass configuration
├── indicators.py         # Vectorized indicator math (EMA, RSI, MACD, Volume SMA)
├── scoring.py            # Multi-factor composite scoring engine (-100 to +100)
├── universe.py           # Universe filtering & candidate scanner
├── risk.py               # Portfolio slot tracking (max 30 slots), 1% equity dynamic margin
├── exchange.py           # CCXT.pro wrapper with rate limiting, exponential backoff, WS streaming
├── trailing.py           # Per-slot asynchronous TrailingWorker task
├── logger.py             # Structured logger with custom event format
├── bot.py                # Main orchestrator & scan cycle loop
├── main.py               # Application CLI entrypoint & signal handling
├── requirements.txt      # Python dependencies
├── .env.example          # Environment variables template
└── tests/                # Automated test suite (18 unit tests)
    ├── test_indicators.py
    ├── test_scoring.py
    ├── test_risk.py
    └── test_trailing.py
```

---

## Quickstart

### 1. Installation

```bash
pip install -r requirements.txt
```

### 2. Running Unit Tests

```bash
python -m pytest tests -v
```

### 3. Running in Dry-Run / Paper Trading Mode (Default)

```bash
# Default: Binance USDT-M Perpetuals, Paper Trading
python main.py

# Target Bybit instead:
python main.py --exchange bybit

# Custom options:
python main.py --exchange binance --leverage 5 --margin-mode cross --max-slots 30 --scan-interval 60 --min-volume 5000000
```

### 4. Running in Live Trading Mode

1. Copy `.env.example` to `.env` and fill in your API credentials:
   ```env
   EXCHANGE_API_KEY="your_binance_or_bybit_api_key"
   EXCHANGE_API_SECRET="your_binance_or_bybit_api_secret"
   ```

2. Run with `--live` flag:
   ```bash
   python main.py --exchange binance --live
   ```
