"""FastAPI Web Server & Continuous Automated Trading Engine Powered by 100% Real Binance Futures Data."""

import asyncio
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Literal
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import ccxt.async_support as ccxt_async
import uvicorn
from dotenv import load_dotenv

load_dotenv(".env")

from config import BotConfig, CONFIG
from logger import BotLogger
from indicators import extract_indicators
from scoring import evaluate_btc_regime, calculate_precision_score

app = FastAPI(title="Crypto Futures Automated Trading Terminal")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DASHBOARD_FILE = os.path.join(os.path.dirname(__file__), "dashboard.html")

config = BotConfig(
    MAX_ACTIVE_SLOTS=30,
    SCORE_THRESHOLD_LONG=80.0,
    SCORE_THRESHOLD_SHORT=-80.0,
    CAPITAL_RISK_PER_TRADE_PCT=0.01,
    STOP_LOSS_PCT=0.03,  # 3% Hard Stop Loss
    BREAKEVEN_TRIGGER_PCT=0.01,  # +1.0% Profit moves SL to breakeven
    BREAKEVEN_BUFFER_PCT=0.0025,  # +0.25% fee buffer
    TRAILING_PROFIT_TRIGGER_PCT=0.015,  # Trailing ONLY activates after +1.5% profit
    TRAILING_CALLBACK_PCT=0.006,  # 0.6% pullback tolerance
    DEFAULT_LEVERAGE=2,
    SIMULATED_WALLET_EQUITY=300.0,
    DRY_RUN=True,  # Virtual equity simulation on real Binance market data
)

class EngineState:
    def __init__(self):
        self.auto_trade_enabled: bool = True
        self.initial_equity: float = 300.0
        self.total_profit: float = 0.0
        self.total_loss: float = 0.0
        self.closed_trades_count: int = 0
        self.winning_trades: int = 0
        self.losing_trades: int = 0
        self.market_mode: Literal["BULLISH", "BEARISH"] = "BEARISH"
        self.btc_price: float = 84400.0
        self.btc_ema50: float = 84650.0

        self.positions: Dict[str, dict] = {}
        self.price_history: Dict[str, List[dict]] = {}
        self.closed_trades: List[dict] = []
        self.candidates: List[dict] = []
        self.latest_prices: Dict[str, float] = {}
        self.connected_websockets: List[WebSocket] = []
        self.last_scan_time: str = ""
        self.is_scanning: bool = False

    @property
    def equity(self) -> float:
        unrealized = sum(p.get("unrealized_pnl_usdt", 0.0) for p in self.positions.values())
        return self.initial_equity + (self.total_profit - self.total_loss) + unrealized

    @property
    def net_pnl(self) -> float:
        return (self.total_profit - self.total_loss) + sum(p.get("unrealized_pnl_usdt", 0.0) for p in self.positions.values())

    @property
    def win_rate_pct(self) -> float:
        if self.closed_trades_count == 0:
            return 0.0
        return (self.winning_trades / self.closed_trades_count) * 100.0

    def to_dict(self) -> dict:
        return {
            "account": {
                "equity": round(self.equity, 2),
                "initial_equity": round(self.initial_equity, 2),
                "total_profit": round(self.total_profit, 2),
                "total_loss": round(self.total_loss, 2),
                "net_pnl": round(self.net_pnl, 2),
                "net_pnl_pct": round((self.net_pnl / self.initial_equity) * 100.0, 2),
                "win_rate_pct": round(self.win_rate_pct, 1),
                "total_trades": self.closed_trades_count,
                "winning_trades": self.winning_trades,
                "losing_trades": self.losing_trades,
                "active_slots": len(self.positions),
                "max_slots": config.MAX_ACTIVE_SLOTS,
                "auto_trade_enabled": self.auto_trade_enabled,
                "last_scan_time": self.last_scan_time,
                "market_mode": self.market_mode,
                "btc_price": self.btc_price,
                "btc_ema50": self.btc_ema50,
            },
            "positions": list(self.positions.values()),
            "price_history": self.price_history,
            "closed_trades": self.closed_trades[-30:],
            "candidates": self.candidates,
        }

engine = EngineState()

# Top liquid crypto futures universe
UNIVERSE_SYMBOLS = [
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "DOGE/USDT:USDT",
    "ADA/USDT:USDT",
    "AVAX/USDT:USDT",
    "LINK/USDT:USDT",
    "SUI/USDT:USDT",
    "NEAR/USDT:USDT",
    "PEPE/USDT:USDT",
    "SHIB/USDT:USDT",
    "OP/USDT:USDT",
    "ARB/USDT:USDT",
    "INJ/USDT:USDT",
    "TIA/USDT:USDT",
    "RENDER/USDT:USDT",
    "SEI/USDT:USDT",
    "APT/USDT:USDT",
    "FET/USDT:USDT",
    "GALA/USDT:USDT",
    "WIF/USDT:USDT",
]

def to_raw_symbol(symbol: str) -> str:
    """Convert CCXT unified symbol (e.g. BTC/USDT:USDT) to raw Binance symbol (e.g. BTCUSDT)."""
    return symbol.split(":")[0].replace("/", "")

# Global Binance async client
binance_client: Optional[ccxt_async.binance] = None

def get_binance_client() -> ccxt_async.binance:
    global binance_client
    if binance_client is None:
        binance_client = ccxt_async.binance({
            "apiKey": CONFIG.API_KEY or os.getenv("EXCHANGE_API_KEY", ""),
            "secret": CONFIG.API_SECRET or os.getenv("EXCHANGE_API_SECRET", ""),
            "options": {"defaultType": "future"},
            "enableRateLimit": True,
            "timeout": 12000,
        })
    return binance_client


async def update_master_btc_regime_real():
    """Fetch real BTC 15m OHLCV from Binance and compute true EMA 50 regime."""
    client = get_binance_client()
    try:
        klines = await client.fapiPublicGetKlines({"symbol": "BTCUSDT", "interval": "15m", "limit": 60})
        cols = ["timestamp", "open", "high", "low", "close", "volume", "close_time", "qav", "num_trades", "taker_base_vol", "taker_quote_vol", "ignore"]
        df = pd.DataFrame(klines, columns=cols)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        ind = extract_indicators(df)
        engine.btc_price = round(ind.current_close, 2)
        engine.btc_ema50 = round(ind.ema50, 2)
        engine.market_mode = evaluate_btc_regime(ind.current_close, ind.ema50)
    except Exception as e:
        BotLogger.error(f"Error updating BTC master regime from Binance: {e}")


async def scan_market_candidates_real():
    """Fetch real-time Binance 15m candles and compute true indicators & scores for all universe pairs."""
    if engine.is_scanning:
        return
    engine.is_scanning = True

    client = get_binance_client()
    try:
        # 1. Update BTC regime first
        await update_master_btc_regime_real()

        # 2. Get live ticker prices for all pairs in single call
        raw_prices = await client.fapiPublicGetTickerPrice()
        prices_map = {item["symbol"]: float(item["price"]) for item in raw_prices}
        engine.latest_prices = prices_map

        new_candidates = []
        for sym in UNIVERSE_SYMBOLS:
            raw_sym = to_raw_symbol(sym)
            live_price = prices_map.get(raw_sym, 0.0)
            if live_price <= 0:
                continue

            try:
                klines = await client.fapiPublicGetKlines({"symbol": raw_sym, "interval": "15m", "limit": 60})
                cols = ["timestamp", "open", "high", "low", "close", "volume", "close_time", "qav", "num_trades", "taker_base_vol", "taker_quote_vol", "ignore"]
                df = pd.DataFrame(klines, columns=cols)
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = df[col].astype(float)

                ind = extract_indicators(df)
                score_obj = calculate_precision_score(
                    symbol=sym,
                    ind=ind,
                    market_mode=engine.market_mode,
                    score_threshold=config.SCORE_THRESHOLD_LONG,
                )

                new_candidates.append({
                    "symbol": sym,
                    "price": round(live_price, 6 if live_price < 0.01 else 4),
                    "adx": round(ind.adx14, 1),
                    "rsi": round(ind.rsi14, 1),
                    "vol_ratio": round(ind.volume_ratio, 2),
                    "ema_slope": round(ind.ema21_slope, 4),
                    "score": round(score_obj.total_score, 1),
                    "action": score_obj.action,
                    "passed_gates": (score_obj.gate_failures == ""),
                    "gate_failures": score_obj.gate_failures if score_obj.gate_failures else "PASSED_ALL_GATES",
                    "auto_triggered": sym in engine.positions,
                })
            except Exception as err:
                BotLogger.warning(f"Error scanning real klines for {raw_sym}: {err}")
                continue

        new_candidates.sort(key=lambda x: abs(x["score"]), reverse=True)
        engine.candidates = new_candidates
        engine.last_scan_time = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    except Exception as e:
        BotLogger.error(f"Error in scan_market_candidates_real: {e}")
    finally:
        engine.is_scanning = False


def open_auto_position(symbol: str, side: str, entry_price: float, trigger_score: float) -> bool:
    """Execute new position with real Binance entry price and 3-step trailing protection."""
    if len(engine.positions) >= config.MAX_ACTIVE_SLOTS:
        return False
    if symbol in engine.positions:
        return False

    # Strict portfolio regime check: do NOT mix Long and Short positions
    if engine.positions:
        existing_side = next(iter(engine.positions.values()))["side"]
        if side != existing_side:
            BotLogger.warning(f"Portfolio regime lock: current={existing_side}, rejecting {symbol} {side}")
            return False

    # 1% equity margin with 2x leverage on virtual $300 balance
    margin = round(engine.equity * config.CAPITAL_RISK_PER_TRADE_PCT, 2)
    leverage = config.DEFAULT_LEVERAGE  # 2x
    notional = margin * leverage
    quantity = round(notional / entry_price, 6)

    # Initial Hard Stop Loss: Strict 3%
    if side == "LONG":
        stop_loss = entry_price * (1.0 - config.STOP_LOSS_PCT)
        be_trigger = entry_price * (1.0 + config.BREAKEVEN_TRIGGER_PCT)  # +1.0%
        trailing_trigger = entry_price * (1.0 + config.TRAILING_PROFIT_TRIGGER_PCT)  # +1.5%
    else:
        stop_loss = entry_price * (1.0 + config.STOP_LOSS_PCT)
        be_trigger = entry_price * (1.0 - config.BREAKEVEN_TRIGGER_PCT)  # +1.0% (down 1%)
        trailing_trigger = entry_price * (1.0 - config.TRAILING_PROFIT_TRIGGER_PCT)  # +1.5% (down 1.5%)

    now_iso = datetime.now(timezone.utc).strftime("%H:%M:%S")

    pos = {
        "symbol": symbol,
        "side": side,
        "entry_price": entry_price,
        "current_price": entry_price,
        "peak_price": entry_price,
        "trough_price": entry_price,
        "quantity": quantity,
        "margin": margin,
        "leverage": leverage,
        "initial_stop_loss": round(stop_loss, 4),
        "current_stop_loss": round(stop_loss, 4),
        "breakeven_trigger_price": round(be_trigger, 4),
        "breakeven_locked": False,
        "trailing_trigger_price": round(trailing_trigger, 4),
        "trailing_active": False,
        "unrealized_pnl_pct": 0.0,
        "unrealized_pnl_usdt": 0.0,
        "entry_time": now_iso,
        "trigger_score": trigger_score,
    }

    engine.positions[symbol] = pos
    engine.price_history[symbol] = [{"time": now_iso, "price": entry_price}]

    BotLogger.trade_event(
        symbol=symbol,
        action=f"AUTO_ENTRY_{side}",
        active_slots=len(engine.positions),
        max_slots=config.MAX_ACTIVE_SLOTS,
        entry_price=entry_price,
        extra=f"Score:{trigger_score:+.0f} | Margin:${margin:.2f} | 3% SL:{stop_loss:.4f} | BE Trigger:+1.0% | Trail Trigger:+1.5%",
    )
    return True


def close_position(symbol: str, exit_price: float, reason: str):
    pos = engine.positions.pop(symbol, None)
    if not pos:
        return

    side = pos["side"]
    entry = pos["entry_price"]
    qty = pos["quantity"]

    if side == "LONG":
        pnl_pct = (exit_price - entry) / entry
    else:
        pnl_pct = (entry - exit_price) / entry

    notional = qty * entry
    realized_pnl_usdt = pnl_pct * notional

    if realized_pnl_usdt >= 0:
        engine.total_profit += realized_pnl_usdt
        engine.winning_trades += 1
    else:
        engine.total_loss += abs(realized_pnl_usdt)
        engine.losing_trades += 1

    engine.closed_trades_count += 1

    closed_record = {
        "symbol": symbol,
        "side": side,
        "entry_price": entry,
        "exit_price": round(exit_price, 4),
        "quantity": qty,
        "pnl_pct": round(pnl_pct * 100.0, 2),
        "pnl_usdt": round(realized_pnl_usdt, 2),
        "reason": reason,
        "entry_time": pos["entry_time"],
        "exit_time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
    }
    engine.closed_trades.append(closed_record)

    BotLogger.trade_event(
        symbol=symbol,
        action=reason,
        active_slots=len(engine.positions),
        max_slots=config.MAX_ACTIVE_SLOTS,
        entry_price=entry,
        exit_price=exit_price,
        pnl_pct=pnl_pct,
        extra=f"Realized: ${realized_pnl_usdt:+.2f} USDT",
    )


async def automated_trading_engine_loop():
    """Continuous background loop using 100% real live Binance Futures market data."""
    print("Continuous High-Precision Auto-Trader Active (Real Binance Market Feed).")
    engine.auto_trade_enabled = True

    client = get_binance_client()

    # Initial scan on startup
    await scan_market_candidates_real()

    scan_countdown = 0

    while True:
        try:
            await asyncio.sleep(1.5)
            now_iso = datetime.now(timezone.utc).strftime("%H:%M:%S")

            # 1. Fetch real-time live prices directly from Binance
            try:
                raw_prices = await client.fapiPublicGetTickerPrice()
                prices_map = {item["symbol"]: float(item["price"]) for item in raw_prices}
                engine.latest_prices = prices_map

                # Update live BTC price
                if "BTCUSDT" in prices_map:
                    engine.btc_price = round(prices_map["BTCUSDT"], 2)
            except Exception as e:
                BotLogger.warning(f"Error fetching live ticker prices: {e}")
                continue

            # 2. Periodic multi-pair scan (every 25 seconds)
            scan_countdown += 1
            if scan_countdown >= 16:
                scan_countdown = 0
                await scan_market_candidates_real()

                # If auto-trading enabled, enter candidates that pass all confirmation gates
                if engine.auto_trade_enabled and len(engine.positions) < config.MAX_ACTIVE_SLOTS:
                    eligible = [
                        c for c in engine.candidates
                        if c["action"] in ("LONG", "SHORT") and c["symbol"] not in engine.positions
                    ]

                    for cand in eligible:
                        if len(engine.positions) >= config.MAX_ACTIVE_SLOTS:
                            break
                        open_auto_position(cand["symbol"], cand["action"], cand["price"], cand["score"])
                        await asyncio.sleep(0.05)

            # 3. Update all open positions with 100% REAL Binance prices & evaluate 3-step trailing protection
            to_close = []
            for symbol, pos in list(engine.positions.items()):
                raw_sym = to_raw_symbol(symbol)
                real_price = prices_map.get(raw_sym)
                if not real_price or real_price <= 0:
                    continue

                pos["current_price"] = real_price

                hist = engine.price_history.setdefault(symbol, [])
                hist.append({"time": now_iso, "price": real_price})
                if len(hist) > 80:
                    hist.pop(0)

                side = pos["side"]
                entry = pos["entry_price"]

                if side == "LONG":
                    pnl_pct = (real_price - entry) / entry
                    pos["unrealized_pnl_pct"] = round(pnl_pct * 100.0, 2)
                    pos["unrealized_pnl_usdt"] = round(pnl_pct * (pos["quantity"] * entry), 2)

                    # 1. Stop-Loss check (Initial 3% SL OR ratcheted Breakeven SL)
                    if real_price <= pos["current_stop_loss"]:
                        reason = "BREAKEVEN_STOP" if pos["breakeven_locked"] else "EMERGENCY_STOP_LOSS"
                        to_close.append((symbol, real_price, reason))
                        continue

                    # 2. Step 1: Breakeven Lock at +1.0% profit
                    if not pos["breakeven_locked"] and pnl_pct >= config.BREAKEVEN_TRIGGER_PCT:
                        pos["breakeven_locked"] = True
                        be_price = entry * (1.0 + config.BREAKEVEN_BUFFER_PCT)  # +0.25% fee buffer
                        pos["current_stop_loss"] = max(pos["current_stop_loss"], be_price)
                        BotLogger.trade_event(
                            symbol=symbol,
                            action="BREAKEVEN_LOCK",
                            active_slots=len(engine.positions),
                            max_slots=config.MAX_ACTIVE_SLOTS,
                            entry_price=entry,
                            exit_price=be_price,
                            pnl_pct=pnl_pct,
                            extra="Locked at Entry + 0.25% buffer",
                        )

                    # 3. Step 2 & 3: Trailing Take-Profit (ONLY activates after +1.5% profit)
                    if not pos["trailing_active"] and pnl_pct >= config.TRAILING_PROFIT_TRIGGER_PCT:
                        pos["trailing_active"] = True
                        pos["peak_price"] = real_price
                        BotLogger.info(f"{symbol} LONG Trailing ACTIVATED at {real_price:.4f} (+{pnl_pct*100:.2f}%)")

                    if pos["trailing_active"]:
                        if real_price > pos["peak_price"]:
                            pos["peak_price"] = real_price
                        # Check pullback of 0.6% from peak
                        pullback_threshold = pos["peak_price"] * (1.0 - config.TRAILING_CALLBACK_PCT)
                        if real_price <= pullback_threshold:
                            to_close.append((symbol, real_price, "TRAILING_TAKE_PROFIT"))

                else:  # SHORT
                    pnl_pct = (entry - real_price) / entry
                    pos["unrealized_pnl_pct"] = round(pnl_pct * 100.0, 2)
                    pos["unrealized_pnl_usdt"] = round(pnl_pct * (pos["quantity"] * entry), 2)

                    # 1. Stop-Loss check (Initial 3% SL OR ratcheted Breakeven SL)
                    if real_price >= pos["current_stop_loss"]:
                        reason = "BREAKEVEN_STOP" if pos["breakeven_locked"] else "EMERGENCY_STOP_LOSS"
                        to_close.append((symbol, real_price, reason))
                        continue

                    # 2. Step 1: Breakeven Lock at +1.0% profit (price drops 1%)
                    if not pos["breakeven_locked"] and pnl_pct >= config.BREAKEVEN_TRIGGER_PCT:
                        pos["breakeven_locked"] = True
                        be_price = entry * (1.0 - config.BREAKEVEN_BUFFER_PCT)  # -0.25% fee buffer
                        pos["current_stop_loss"] = min(pos["current_stop_loss"], be_price)
                        BotLogger.trade_event(
                            symbol=symbol,
                            action="BREAKEVEN_LOCK",
                            active_slots=len(engine.positions),
                            max_slots=config.MAX_ACTIVE_SLOTS,
                            entry_price=entry,
                            exit_price=be_price,
                            pnl_pct=pnl_pct,
                            extra="Locked at Entry - 0.25% buffer",
                        )

                    # 3. Step 2 & 3: Trailing Take-Profit (ONLY activates after +1.5% profit)
                    if not pos["trailing_active"] and pnl_pct >= config.TRAILING_PROFIT_TRIGGER_PCT:
                        pos["trailing_active"] = True
                        pos["trough_price"] = real_price
                        BotLogger.info(f"{symbol} SHORT Trailing ACTIVATED at {real_price:.4f} (+{pnl_pct*100:.2f}%)")

                    if pos["trailing_active"]:
                        if real_price < pos["trough_price"]:
                            pos["trough_price"] = real_price
                        # Check bounce of 0.6% from trough
                        bounce_threshold = pos["trough_price"] * (1.0 + config.TRAILING_CALLBACK_PCT)
                        if real_price >= bounce_threshold:
                            to_close.append((symbol, real_price, "TRAILING_TAKE_PROFIT"))

            # Process exits
            for sym, exit_p, rsn in to_close:
                close_position(sym, exit_p, rsn)

            # 4. Broadcast live state to all connected dashboard websockets
            if engine.connected_websockets:
                state_data = engine.to_dict()
                disconnected = []
                for ws in engine.connected_websockets:
                    try:
                        await ws.send_json(state_data)
                    except Exception:
                        disconnected.append(ws)
                for ws in disconnected:
                    if ws in engine.connected_websockets:
                        engine.connected_websockets.remove(ws)

        except Exception as e:
            BotLogger.error(f"Error in engine loop: {e}")
            await asyncio.sleep(2.0)


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(automated_trading_engine_loop())


@app.on_event("shutdown")
async def on_shutdown():
    global binance_client
    if binance_client is not None:
        await binance_client.close()


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    if os.path.exists(DASHBOARD_FILE):
        return FileResponse(DASHBOARD_FILE)
    return HTMLResponse("<h1>Dashboard HTML not found</h1>", status_code=404)


@app.get("/api/state")
async def get_state():
    return JSONResponse(engine.to_dict())


@app.post("/api/auto-trade/toggle")
async def toggle_auto_trade():
    engine.auto_trade_enabled = not engine.auto_trade_enabled
    BotLogger.info(f"Auto-trade toggled: {engine.auto_trade_enabled}")
    return {"status": "success", "auto_trade_enabled": engine.auto_trade_enabled}


@app.post("/api/positions/open")
async def manual_open():
    """Manual trigger: enters the highest scoring real candidate aligned with BTC regime."""
    if len(engine.positions) >= config.MAX_ACTIVE_SLOTS:
        return {"status": "failed", "reason": "Max slots (30) reached"}

    # Find highest conviction real candidate matching BTC market mode
    target_side = "LONG" if engine.market_mode == "BULLISH" else "SHORT"
    eligible = [
        c for c in engine.candidates
        if c["symbol"] not in engine.positions
        and (c["action"] == target_side or c["passed_gates"])
    ]

    if eligible:
        best = eligible[0]
        opened = open_auto_position(best["symbol"], target_side, best["price"], best["score"])
        if opened:
            return {"status": "success", "symbol": best["symbol"], "action": target_side, "price": best["price"]}

    # Fallback to any liquid candidate using its real Binance price
    for c in engine.candidates:
        if c["symbol"] not in engine.positions:
            raw_sym = to_raw_symbol(c["symbol"])
            real_p = engine.latest_prices.get(raw_sym, c["price"])
            opened = open_auto_position(c["symbol"], target_side, real_p, 85.0 if target_side == "LONG" else -85.0)
            if opened:
                return {"status": "success", "symbol": c["symbol"], "action": target_side, "price": real_p}

    return {"status": "failed", "reason": "No slot available or already open"}


@app.post("/api/positions/close/{symbol:path}")
async def manual_close(symbol: str):
    if symbol in engine.positions:
        p = engine.positions[symbol]
        close_position(symbol, p["current_price"], "MANUAL_CLOSE")
        return {"status": "closed", "symbol": symbol}
    return JSONResponse({"status": "not_found"}, status_code=404)


@app.post("/api/positions/close-all")
async def close_all_positions():
    closed_list = []
    for sym, p in list(engine.positions.items()):
        close_position(sym, p["current_price"], "MANUAL_CLOSE_ALL")
        closed_list.append(sym)
    return {"status": "all_closed", "closed": closed_list}


@app.post("/api/positions/test-trade/{side}")
async def test_trade(side: str):
    side = side.upper()
    if side not in ("LONG", "SHORT"):
        return JSONResponse({"status": "error", "message": "Side must be LONG or SHORT"}, status_code=400)

    # Pick a liquid candidate not currently open
    client = get_binance_client()
    for sym in UNIVERSE_SYMBOLS:
        if sym not in engine.positions:
            raw_sym = to_raw_symbol(sym)
            real_p = engine.latest_prices.get(raw_sym, 0.0)
            if real_p <= 0:
                try:
                    ticker = await client.fapiPublicGetTickerPrice({"symbol": raw_sym})
                    real_p = float(ticker.get("price", 0.0))
                except Exception:
                    continue
            if real_p > 0:
                score = 85.0 if side == "LONG" else -85.0
                opened = open_auto_position(sym, side, real_p, score)
                if opened:
                    return {"status": "success", "symbol": sym, "side": side, "entry_price": real_p}

    return {"status": "failed", "reason": "No available candidate found"}


@app.post("/api/scan")
async def trigger_scan():
    await scan_market_candidates_real()
    return {"status": "scanned", "candidates_count": len(engine.candidates)}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    engine.connected_websockets.append(websocket)
    try:
        await websocket.send_json(engine.to_dict())
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in engine.connected_websockets:
            engine.connected_websockets.remove(websocket)
    except Exception:
        if websocket in engine.connected_websockets:
            engine.connected_websockets.remove(websocket)


def start_server(host: str = "127.0.0.1", port: int = 8080):
    custom_port = int(os.getenv("PORT", "8080"))
    import sys
    for arg in sys.argv[1:]:
        if arg.startswith("--port="):
            custom_port = int(arg.split("=")[1])
        elif arg.isdigit():
            custom_port = int(arg)
    print(f"Starting Real-Data High-Precision Web Dashboard server at http://{host}:{custom_port}")
    uvicorn.run(app, host=host, port=custom_port, log_level="warning")


if __name__ == "__main__":
    start_server()
