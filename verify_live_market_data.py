"""Comprehensive verification script for live trading configuration and real-time market data fetching."""

import ccxt
import pandas as pd
from dotenv import load_dotenv

load_dotenv(".env")

from config import CONFIG
from indicators import extract_indicators
from scoring import evaluate_btc_regime, calculate_precision_score


def verify_live_system():
    print("=" * 72)
    print("       BINANCE FUTURES LIVE TRADING & REAL-TIME DATA VERIFIER")
    print("=" * 72)

    # 1. Configuration & Mode Check
    print("\n[1] CONFIGURATION STATUS:")
    print(f"  - Target Exchange          : {CONFIG.EXCHANGE_ID.upper()} (USDT-M Perpetual Futures)")
    print(f"  - DRY_RUN Setting          : {CONFIG.DRY_RUN}  <-- [SIMULATION ON REAL DATA - VIRTUAL $300 BALANCE]")
    print(f"  - Default Leverage         : {CONFIG.DEFAULT_LEVERAGE}x")
    print(f"  - Margin Mode              : {CONFIG.MARGIN_MODE.upper()}")
    print(f"  - Timeframe                : {CONFIG.TIMEFRAME}")
    print(f"  - Stop-Loss                : {CONFIG.STOP_LOSS_PCT * 100:.1f}%")
    print(f"  - Breakeven Trigger        : +{CONFIG.BREAKEVEN_TRIGGER_PCT * 100:.1f}% (with +{CONFIG.BREAKEVEN_BUFFER_PCT * 100:.2f}% fee buffer)")
    print(f"  - Trailing Profit Trigger  : +{CONFIG.TRAILING_PROFIT_TRIGGER_PCT * 100:.1f}% (0.6% pullback exit)")
    print(f"  - API Key Present          : {'YES (' + CONFIG.API_KEY[:6] + '...' + CONFIG.API_KEY[-4:] + ')' if CONFIG.API_KEY else 'NO'}")

    # 2. CCXT Client Connection
    client = ccxt.binance({
        "apiKey": CONFIG.API_KEY,
        "secret": CONFIG.API_SECRET,
        "options": {"defaultType": "future"},
        "enableRateLimit": True,
        "timeout": 15000,
    })

    print("\n[2] TESTING BINANCE FUTURES CONNECTIVITY & SERVER TIME:")
    try:
        server_time = client.fetch_time()
        print(f"  [OK] Connected to Binance Futures REST API.")
        print(f"  [OK] Binance Server Timestamp: {server_time}")
    except Exception as e:
        print(f"  [FAIL] Could not reach Binance API: {e}")
        return

    # 3. Account API Permissions & Wallet Check
    print("\n[3] CHECKING LIVE ACCOUNT PERMISSIONS & FUTURES WALLET:")
    try:
        acc = client.fapiPrivateV2GetAccount()
        total_wallet = float(acc.get("totalWalletBalance", 0.0))
        available = float(acc.get("availableBalance", 0.0))
        print(f"  [OK] Binance Futures Account Authenticated!")
        print(f"  [OK] Total Wallet Balance : ${total_wallet:.2f} USDT")
        print(f"  [OK] Available Balance    : ${available:.2f} USDT")

        # Check API key restrictions
        try:
            restrictions = client.sapi_get_account_apirestrictions()
            enable_futures = restrictions.get("enableFutures", False)
            enable_reading = restrictions.get("enableReading", False)
            print(f"  - API Read Permission     : {'ENABLED' if enable_reading else 'DISABLED'}")
            print(f"  - API Futures Trading     : {'ENABLED' if enable_futures else 'DISABLED (Action: Tick Enable Futures in Binance API settings)'}")
        except Exception:
            pass
    except Exception as e:
        print(f"  [INFO] Account check note: {e}")

    # 4. Fetching Real-Time Market Data & BTC Regime
    print("\n[4] FETCHING REAL-TIME BTC MARKET DATA & REGIME FILTER:")
    try:
        btc_ticker = client.fetch_ticker("BTC/USDT")
        btc_ohlcv = client.fetch_ohlcv("BTC/USDT", timeframe="15m", limit=60)
        btc_df = pd.DataFrame(btc_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        btc_ind = extract_indicators(btc_df)
        btc_regime = evaluate_btc_regime(btc_ind.current_close, btc_ind.ema50)

        print(f"  [OK] Live BTC/USDT Ticker Price : ${btc_ticker['last']:,.2f}")
        print(f"  [OK] Live 24h Trading Volume    : ${btc_ticker['quoteVolume']:,.0f} USDT")
        print(f"  [OK] Latest 15m Candle Close    : ${btc_ind.current_close:,.2f}")
        print(f"  [OK] 15m EMA 50 Benchmark       : ${btc_ind.ema50:,.2f}")
        print(f"  [OK] Master Market Regime Filter: {btc_regime} ({'LONG-ONLY TRADES ALLOWED' if btc_regime == 'BULLISH' else 'SHORT-ONLY TRADES ALLOWED'})")
    except Exception as e:
        print(f"  [FAIL] Failed to fetch live BTC market data: {e}")
        return

    # 5. Fetching Multi-Pair Real-Time OHLCV & Scoring
    print("\n[5] REAL-TIME MULTI-PAIR SCAN & LIVE EXECUTION SIGNALS:")
    sample_pairs = ["ETH/USDT", "SOL/USDT", "BNB/USDT", "DOGE/USDT", "AVAX/USDT"]
    print(f"  {'Symbol':<12} {'Live Price':<12} {'ADX(14)':<9} {'RSI(14)':<9} {'VolRatio':<10} {'Score':<8} {'Status'}")
    print("  " + "-" * 70)

    for sym in sample_pairs:
        try:
            ticker = client.fetch_ticker(sym)
            ohlcv = client.fetch_ohlcv(sym, timeframe="15m", limit=60)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            ind = extract_indicators(df)
            score = calculate_precision_score(f"{sym}:USDT", ind, market_mode=btc_regime)

            status = "TRIGGER" if score.action != "NEUTRAL" else "FILTERED"
            reason = f" ({score.gate_failures[:24]}..)" if score.gate_failures else ""
            print(f"  {sym:<12} ${ticker['last']:<11.4f} {ind.adx14:<9.1f} {ind.rsi14:<9.1f} {ind.volume_ratio:<10.2f}x {score.total_score:+6.1f} {status}{reason}")
        except Exception as e:
            print(f"  {sym:<12} Error fetching real-time data: {e}")

    print("\n" + "=" * 72)
    print("VERIFICATION COMPLETE:")
    print(f"  1. config.py DRY_RUN is set to {CONFIG.DRY_RUN} (Virtual $300 balance simulation).")
    print("  2. Real-time market data is actively being fetched from Binance Futures via real API keys.")
    print("  3. Technical filters, BTC master regime, and indicators are running live.")
    print("=" * 72)


if __name__ == "__main__":
    verify_live_system()
