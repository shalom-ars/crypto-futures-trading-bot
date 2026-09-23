"""FastAPI Web Server & Continuous Automated Trading Engine with Master BTC Regime & 3-Step Trailing Protection."""

import asyncio
import os
import random
from datetime import datetime, timezone
from typing import Dict, List, Optional, Literal
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from config import BotConfig
from logger import BotLogger

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
    DRY_RUN=True,
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
        self.market_mode: Literal["BULLISH", "BEARISH"] = "BULLISH"
        self.btc_price: float = 84350.0
        self.btc_ema50: float = 83800.0

        self.positions: Dict[str, dict] = {}
        self.price_history: Dict[str, List[dict]] = {}
        self.closed_trades: List[dict] = []
        self.candidates: List[dict] = []
        self.connected_websockets: List[WebSocket] = []
        self.last_scan_time: str = ""

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

# Top 24 liquid crypto futures universe
MARKET_UNIVERSE = [
    {"symbol": "BTC/USDT:USDT", "base_price": 84350.0},
    {"symbol": "ETH/USDT:USDT", "base_price": 3125.0},
    {"symbol": "SOL/USDT:USDT", "base_price": 188.5},
    {"symbol": "BNB/USDT:USDT", "base_price": 582.0},
    {"symbol": "XRP/USDT:USDT", "base_price": 0.582},
    {"symbol": "DOGE/USDT:USDT", "base_price": 0.184},
    {"symbol": "NEAR/USDT:USDT", "base_price": 4.85},
    {"symbol": "SUI/USDT:USDT", "base_price": 2.15},
    {"symbol": "AVAX/USDT:USDT", "base_price": 28.6},
    {"symbol": "LINK/USDT:USDT", "base_price": 14.8},
    {"symbol": "ADA/USDT:USDT", "base_price": 0.385},
    {"symbol": "DOT/USDT:USDT", "base_price": 4.65},
    {"symbol": "PEPE/USDT:USDT", "base_price": 0.0000085},
    {"symbol": "SHIB/USDT:USDT", "base_price": 0.0000178},
    {"symbol": "OP/USDT:USDT", "base_price": 1.48},
    {"symbol": "ARB/USDT:USDT", "base_price": 0.54},
    {"symbol": "INJ/USDT:USDT", "base_price": 19.8},
    {"symbol": "TIA/USDT:USDT", "base_price": 5.45},
    {"symbol": "RENDER/USDT:USDT", "base_price": 5.82},
    {"symbol": "SEI/USDT:USDT", "base_price": 0.34},
    {"symbol": "APT/USDT:USDT", "base_price": 7.85},
    {"symbol": "FET/USDT:USDT", "base_price": 1.35},
    {"symbol": "GALA/USDT:USDT", "base_price": 0.0215},
    {"symbol": "WIF/USDT:USDT", "base_price": 2.12},
]


def update_master_btc_regime():
    """Evaluate Master Trend Filter: BTC 15m close vs EMA 50."""
    # Micro-tick BTC price
    drift = random.uniform(-0.001, 0.0012)
    engine.btc_price = round(engine.btc_price * (1.0 + drift), 2)

    # Determine regime
    if engine.btc_price >= engine.btc_ema50:
        engine.market_mode = "BULLISH"
    else:
        engine.market_mode = "BEARISH"


def evaluate_candidate_precision(item: dict) -> dict:
    """Evaluate precision indicators and strict confirmation gates for a symbol."""
    sym = item["symbol"]
    curr_price = item["base_price"]

    if sym in engine.positions:
        curr_price = engine.positions[sym]["current_price"]

    mode = engine.market_mode

    # Generate realistic indicator readings
    # ADX: 15 to 45
    adx = round(random.uniform(18.0, 42.0), 1)

    # Volume Ratio vs 20 SMA: 0.8x to 2.8x
    vol_ratio = round(random.uniform(0.9, 2.5), 2)

    # EMA Alignment & Slopes
    ema_aligned_bull = random.random() > 0.4
    ema_aligned_bear = not ema_aligned_bull and (random.random() > 0.4)
    ema21_slope = round(random.uniform(0.01, 0.25) if ema_aligned_bull else random.uniform(-0.25, -0.01), 4)

    # RSI
    if mode == "BULLISH" and ema_aligned_bull:
        rsi = round(random.uniform(52.0, 67.0), 1)
        is_green = random.random() > 0.25
    else:
        rsi = round(random.uniform(34.0, 49.0), 1)
        is_green = random.random() > 0.75

    # Check Gates
    gate_failures = []

    # 1. Master BTC Alignment Gate
    target_side = "LONG" if mode == "BULLISH" else "SHORT"
    if target_side == "LONG" and mode != "BULLISH":
        gate_failures.append("BTC_REGIME_NOT_BULLISH")
    elif target_side == "SHORT" and mode != "BEARISH":
        gate_failures.append("BTC_REGIME_NOT_BEARISH")

    # 2. ADX Gate (> 25)
    if adx <= config.ADX_MIN_THRESHOLD:
        gate_failures.append(f"ADX_CHOP({adx}<=25)")

    # 3. RSI Range Gate
    if target_side == "LONG":
        if rsi < config.RSI_LONG_MIN:
            gate_failures.append(f"RSI_LOW({rsi}<50)")
        elif rsi > config.RSI_LONG_MAX:
            gate_failures.append(f"RSI_TOP_TRAP({rsi}>68)")
    else:
        if rsi > config.RSI_SHORT_MAX:
            gate_failures.append(f"RSI_HIGH({rsi}>50)")
        elif rsi < config.RSI_SHORT_MIN:
            gate_failures.append(f"RSI_BOTTOM_TRAP({rsi}<32)")

    # 4. EMA Alignment + Slope Gate
    if target_side == "LONG":
        if not ema_aligned_bull or ema21_slope <= 0:
            gate_failures.append("EMA_NOT_BULLISH_OR_SLOPE_DOWN")
    else:
        if not ema_aligned_bear or ema21_slope >= 0:
            gate_failures.append("EMA_NOT_BEARISH_OR_SLOPE_UP")

    # 5. Volume Confirmation Gate (> 1.5x with matching candle)
    if vol_ratio <= config.VOLUME_SMA_MULTIPLIER:
        gate_failures.append(f"VOL_LOW({vol_ratio}x<=1.5x)")

    if target_side == "LONG" and not is_green:
        gate_failures.append("CANDLE_RED_FOR_LONG")
    elif target_side == "SHORT" and is_green:
        gate_failures.append("CANDLE_GREEN_FOR_SHORT")

    # Calculate Score
    trend_pts = 30.0 if (target_side == "LONG" and ema_aligned_bull) else (-30.0 if (target_side == "SHORT" and ema_aligned_bear) else 0.0)
    mom_pts = 25.0 if (target_side == "LONG" and adx > 25 and 50 <= rsi <= 68) else (-25.0 if (target_side == "SHORT" and adx > 25 and 32 <= rsi <= 50) else 0.0)
    vol_pts = 25.0 if (vol_ratio > 1.5 and is_green) else (-25.0 if (vol_ratio > 1.5 and not is_green) else 0.0)
    deriv_pts = 10.0 if target_side == "LONG" else -10.0

    total_score = trend_pts + mom_pts + vol_pts + deriv_pts
    total_score = max(-100.0, min(100.0, total_score))

    passed = len(gate_failures) == 0

    action: Literal["LONG", "SHORT", "NEUTRAL"] = "NEUTRAL"
    if passed:
        if target_side == "LONG" and total_score >= config.SCORE_THRESHOLD_LONG:
            action = "LONG"
        elif target_side == "SHORT" and total_score <= config.SCORE_THRESHOLD_SHORT:
            action = "SHORT"

    return {
        "symbol": sym,
        "price": round(curr_price, 6 if curr_price < 0.01 else 4),
        "adx": adx,
        "rsi": rsi,
        "vol_ratio": vol_ratio,
        "ema_slope": ema21_slope,
        "score": total_score,
        "action": action,
        "passed_gates": passed,
        "gate_failures": " | ".join(gate_failures) if gate_failures else "PASSED",
        "auto_triggered": sym in engine.positions,
    }


def generate_candidate_scores():
    update_master_btc_regime()
    scored = [evaluate_candidate_precision(item) for item in MARKET_UNIVERSE]
    scored.sort(key=lambda x: abs(x["score"]), reverse=True)
    engine.candidates = scored
    engine.last_scan_time = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def open_auto_position(symbol: str, side: str, entry_price: float, trigger_score: float) -> bool:
    """Execute new position with 3-step breakeven & trailing protection."""
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

    # 1% equity margin with 2x leverage
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
    """Continuous loop with BTC Master Trend check and 3-step trailing protection."""
    print("Continuous High-Precision Auto-Trader Active.")
    engine.auto_trade_enabled = True
    generate_candidate_scores()

    scan_tick = 0

    while True:
        try:
            await asyncio.sleep(1.0)
            now_iso = datetime.now(timezone.utc).strftime("%H:%M:%S")

            scan_tick += 1
            if scan_tick >= 3:
                scan_tick = 0
                generate_candidate_scores()

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

            # Update live ticks & 3-step protection for all open positions
            to_close = []
            for symbol, pos in list(engine.positions.items()):
                volatility = random.uniform(-0.0012, 0.0015)
                new_price = round(pos["current_price"] * (1.0 + volatility), 6 if pos["current_price"] < 0.01 else 4)
                pos["current_price"] = new_price

                hist = engine.price_history.setdefault(symbol, [])
                hist.append({"time": now_iso, "price": new_price})
                if len(hist) > 80:
                    hist.pop(0)

                side = pos["side"]
                entry = pos["entry_price"]

                if side == "LONG":
                    pnl_pct = (new_price - entry) / entry
                    pos["unrealized_pnl_pct"] = round(pnl_pct * 100.0, 2)
                    pos["unrealized_pnl_usdt"] = round(pnl_pct * (pos["quantity"] * entry), 2)

                    # 1. Stop-Loss check (Initial 3% SL OR ratcheted Breakeven SL)
                    if new_price <= pos["current_stop_loss"]:
                        reason = "BREAKEVEN_STOP" if pos["breakeven_locked"] else "EMERGENCY_STOP_LOSS"
                        to_close.append((symbol, new_price, reason))
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

                    # 3. Step 2: Trailing Take-Profit Activation ONLY after +1.5% profit
                    if not pos["trailing_active"] and pnl_pct >= config.TRAILING_PROFIT_TRIGGER_PCT:
                        pos["trailing_active"] = True
                        pos["peak_price"] = new_price
                        BotLogger.info(f"{symbol} LONG Trailing ACTIVATED at {new_price} (+{pnl_pct * 100:.2f}%)")

                    # 4. Step 3: Trailing Execution (0.6% pullback from peak)
                    if pos["trailing_active"]:
                        if new_price > pos["peak_price"]:
                            pos["peak_price"] = new_price

                        callback_threshold = pos["peak_price"] * (1.0 - config.TRAILING_CALLBACK_PCT)
                        if new_price <= callback_threshold:
                            to_close.append((symbol, new_price, "TRAILING_TAKE_PROFIT"))
                            continue

                elif side == "SHORT":
                    pnl_pct = (entry - new_price) / entry
                    pos["unrealized_pnl_pct"] = round(pnl_pct * 100.0, 2)
                    pos["unrealized_pnl_usdt"] = round(pnl_pct * (pos["quantity"] * entry), 2)

                    # 1. Stop-Loss check
                    if new_price >= pos["current_stop_loss"]:
                        reason = "BREAKEVEN_STOP" if pos["breakeven_locked"] else "EMERGENCY_STOP_LOSS"
                        to_close.append((symbol, new_price, reason))
                        continue

                    # 2. Step 1: Breakeven Lock at +1.0% profit
                    if not pos["breakeven_locked"] and pnl_pct >= config.BREAKEVEN_TRIGGER_PCT:
                        pos["breakeven_locked"] = True
                        be_price = entry * (1.0 - config.BREAKEVEN_BUFFER_PCT)  # -0.25% fee buffer for Short
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

                    # 3. Step 2: Trailing Take-Profit Activation ONLY after +1.5% profit
                    if not pos["trailing_active"] and pnl_pct >= config.TRAILING_PROFIT_TRIGGER_PCT:
                        pos["trailing_active"] = True
                        pos["trough_price"] = new_price
                        BotLogger.info(f"{symbol} SHORT Trailing ACTIVATED at {new_price} (+{pnl_pct * 100:.2f}%)")

                    # 4. Step 3: Trailing Execution (0.6% pullback from trough)
                    if pos["trailing_active"]:
                        if new_price < pos["trough_price"]:
                            pos["trough_price"] = new_price

                        callback_threshold = pos["trough_price"] * (1.0 + config.TRAILING_CALLBACK_PCT)
                        if new_price >= callback_threshold:
                            to_close.append((symbol, new_price, "TRAILING_TAKE_PROFIT"))
                            continue

            for sym, exit_p, reason in to_close:
                close_position(sym, exit_p, reason)

            if engine.connected_websockets:
                payload = engine.to_dict()
                disconnected = []
                for ws in engine.connected_websockets:
                    try:
                        await ws.send_json(payload)
                    except Exception:
                        disconnected.append(ws)
                for ws in disconnected:
                    if ws in engine.connected_websockets:
                        engine.connected_websockets.remove(ws)

        except Exception as e:
            BotLogger.error(f"Error in automated loop: {e}")
            await asyncio.sleep(1)


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(automated_trading_engine_loop())


@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    if os.path.exists(DASHBOARD_FILE):
        return FileResponse(DASHBOARD_FILE)
    return HTMLResponse("<h1>dashboard.html not found</h1>", status_code=404)


@app.get("/api/state")
async def get_state():
    return JSONResponse(engine.to_dict())


@app.post("/api/auto-trade/toggle")
async def toggle_auto_trade():
    engine.auto_trade_enabled = not engine.auto_trade_enabled
    BotLogger.info(f"Auto-trade toggled: {'ENABLED' if engine.auto_trade_enabled else 'DISABLED'}")
    return {"auto_trade_enabled": engine.auto_trade_enabled}


@app.post("/api/auto-trade/force-entry")
async def force_entry():
    """Trigger an ultra-high conviction trade in accordance with BTC regime."""
    generate_candidate_scores()
    for c in engine.candidates:
        if c["action"] in ("LONG", "SHORT") and c["symbol"] not in engine.positions:
            opened = open_auto_position(c["symbol"], c["action"], c["price"], c["score"])
            if opened:
                return {"status": "success", "symbol": c["symbol"], "action": c["action"]}

    # If none currently qualified, prepare a high-conviction setup matching BTC regime
    target_side = "LONG" if engine.market_mode == "BULLISH" else "SHORT"
    for c in engine.candidates:
        if c["symbol"] not in engine.positions:
            c["score"] = 85.0 if target_side == "LONG" else -85.0
            c["action"] = target_side
            opened = open_auto_position(c["symbol"], target_side, c["price"], c["score"])
            if opened:
                return {"status": "success", "symbol": c["symbol"], "action": target_side}

    return {"status": "failed", "reason": "No slot available or already open"}


@app.post("/api/positions/close/{symbol}")
async def manual_close(symbol: str):
    if symbol in engine.positions:
        p = engine.positions[symbol]
        close_position(symbol, p["current_price"], "MANUAL_CLOSE")
        return {"status": "closed", "symbol": symbol}
    return JSONResponse({"status": "not_found"}, status_code=404)


@app.post("/api/scan")
async def trigger_scan():
    generate_candidate_scores()
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
    print(f"Starting High-Precision Web Dashboard server at http://{host}:{custom_port}")
    uvicorn.run(app, host=host, port=custom_port, log_level="warning")


if __name__ == "__main__":
    start_server()
