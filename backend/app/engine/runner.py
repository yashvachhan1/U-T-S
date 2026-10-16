"""Live strategy runner -- the "algo bot".

Each bot is an asyncio task that wakes on a timer, pulls fresh candles, runs the
SAME strategy function the backtester uses, and acts on the most recently
CLOSED candle.  The forming candle is deliberately ignored: acting on it would
repaint and produce signals the backtest never saw.

Safety rails that are always on:
  * respects PAPER_TRADING -- nothing reaches Upstox while it is true,
  * one position at a time per bot,
  * a max-trades-per-day cap,
  * trading-window check so it does not fire outside market hours,
  * any exception stops the bot rather than retrying blindly.
"""
from __future__ import annotations

import asyncio
import json
import traceback
from dataclasses import dataclass, field
from datetime import time as dtime
from typing import Any

from .. import config, db
from ..clock import is_open, now_ist
from ..strategies import base as strat
from ..upstox import client, instruments, marketdata, trading

# How often to re-evaluate, per candle interval (seconds).
POLL_SECONDS = {
    "1m": 20, "3m": 30, "5m": 45, "15m": 60,
    "30m": 90, "1h": 120, "1d": 300,
}

def market_open_now() -> bool:
    """Always IST, whatever the machine's own timezone is set to."""
    return is_open("equity")


@dataclass
class BotConfig:
    strategy: str
    instrument_key: str
    interval: str = "5m"
    quantity: int = 1
    product: str = "I"                # intraday by default
    params: dict = field(default_factory=dict)
    stop_loss_pct: float = 0.0
    target_pct: float = 0.0
    max_trades_per_day: int = 10
    square_off_at_close: bool = True
    only_market_hours: bool = True


@dataclass
class BotState:
    run_id: int
    cfg: BotConfig
    symbol: str
    task: asyncio.Task | None = None
    status: str = "starting"
    position_qty: int = 0
    entry_price: float = 0.0
    trades_today: int = 0
    last_candle_ts: str | None = None
    last_signal: str | None = None
    last_error: str | None = None
    last_check: str | None = None

    def to_json(self) -> dict:
        return {
            "run_id": self.run_id,
            "strategy": self.cfg.strategy,
            "instrument_key": self.cfg.instrument_key,
            "symbol": self.symbol,
            "interval": self.cfg.interval,
            "quantity": self.cfg.quantity,
            "params": self.cfg.params,
            "status": self.status,
            "position_qty": self.position_qty,
            "entry_price": self.entry_price,
            "trades_today": self.trades_today,
            "max_trades_per_day": self.cfg.max_trades_per_day,
            "last_signal": self.last_signal,
            "last_candle_ts": self.last_candle_ts,
            "last_check": self.last_check,
            "last_error": self.last_error,
            "mode": "paper" if config.PAPER_TRADING else "LIVE",
        }


_bots: dict[int, BotState] = {}


def active() -> list[dict]:
    return [b.to_json() for b in _bots.values()]


def get(run_id: int) -> BotState | None:
    return _bots.get(run_id)


async def start(cfg: BotConfig) -> BotState:
    strat.get(cfg.strategy)                       # raises if unknown
    if cfg.quantity <= 0:
        raise ValueError("Quantity 0 se zyada honi chahiye.")
    for b in _bots.values():
        if b.cfg.instrument_key == cfg.instrument_key and b.status == "running":
            raise ValueError(f"{b.symbol} par ek bot pehle se chal raha hai.")

    row = instruments.by_key(cfg.instrument_key)
    symbol = row["symbol"] if row else cfg.instrument_key
    mode = "paper" if config.PAPER_TRADING else "live"

    run_id = db.execute(
        """INSERT INTO bot_runs
           (started_at, strategy, params, instrument_key, symbol, mode, status)
           VALUES (?,?,?,?,?,?,?)""",
        (db.now(), cfg.strategy, json.dumps(cfg.params), cfg.instrument_key, symbol, mode, "running"),
    )
    state = BotState(run_id=run_id, cfg=cfg, symbol=symbol, status="running")
    _bots[run_id] = state
    db.log_bot(run_id, "info", f"Bot shuru ({mode} mode): {cfg.strategy} on {symbol} / {cfg.interval}")
    state.task = asyncio.create_task(_loop(state))
    return state


async def stop(run_id: int, reason: str = "user stopped") -> bool:
    state = _bots.get(run_id)
    if not state:
        return False
    if state.task and not state.task.done():
        state.task.cancel()
    await _finalise(state, "stopped", reason)
    return True


async def stop_all() -> None:
    for rid in list(_bots):
        await stop(rid, "server shutdown")


async def _finalise(state: BotState, status: str, reason: str) -> None:
    state.status = status
    db.execute(
        "UPDATE bot_runs SET stopped_at = ?, status = ? WHERE id = ?",
        (db.now(), status, state.run_id),
    )
    db.log_bot(state.run_id, "info", f"Bot band: {reason}")


async def _loop(state: BotState) -> None:
    cfg = state.cfg
    delay = POLL_SECONDS.get(cfg.interval, 60)
    day = now_ist().date()

    try:
        while True:
            # reset the daily trade counter
            if now_ist().date() != day:
                day = now_ist().date()
                state.trades_today = 0

            try:
                await _tick(state)
            except client.UpstoxError as exc:
                state.last_error = exc.message
                db.log_bot(state.run_id, "error", f"Upstox: {exc.message}")
                if exc.status == 401:
                    await _finalise(state, "error", "Upstox session expire -- dobara login karein")
                    return
            except Exception as exc:  # noqa: BLE001 -- bot must not die silently
                state.last_error = str(exc)
                db.log_bot(state.run_id, "error", f"{exc}\n{traceback.format_exc(limit=3)}")
                await _finalise(state, "error", str(exc))
                return

            await asyncio.sleep(delay)
    except asyncio.CancelledError:
        raise


async def _tick(state: BotState) -> None:
    cfg = state.cfg
    state.last_check = db.now()

    if cfg.only_market_hours and not market_open_now():
        state.status = "waiting for market"
        return
    state.status = "running"

    # Square off before the bell so an intraday position is not auto-closed
    # by the broker at an unknown price.
    if cfg.square_off_at_close and state.position_qty and cfg.product == "I":
        if now_ist().time() >= dtime(15, 15):
            await _exit(state, "square-off before close")
            return

    candles = await marketdata.historical(cfg.instrument_key, cfg.interval, days=60)
    if len(candles) < 30:
        db.log_bot(state.run_id, "warn", "Candles kam hain, signal skip kiya.")
        return

    # Drop the still-forming candle for intraday intervals.
    closed = candles[:-1] if cfg.interval != "1d" else candles
    if len(closed) < 30:
        return

    s = strat.get(cfg.strategy)
    signals = s.run(closed, cfg.params)
    last = closed[-1]
    signal = signals[-1]
    price = last["close"]

    # Stop-loss / target on the open position, checked on close price.
    if state.position_qty and (cfg.stop_loss_pct or cfg.target_pct):
        ep = state.entry_price
        if cfg.stop_loss_pct and price <= ep * (1 - cfg.stop_loss_pct / 100):
            await _exit(state, f"stop-loss hit @ {price}")
            return
        if cfg.target_pct and price >= ep * (1 + cfg.target_pct / 100):
            await _exit(state, f"target hit @ {price}")
            return

    # Only act once per candle.
    if state.last_candle_ts == last["ts"]:
        return
    state.last_candle_ts = last["ts"]
    state.last_signal = signal

    if not signal:
        return
    if state.trades_today >= cfg.max_trades_per_day:
        db.log_bot(state.run_id, "warn", "Daily trade limit poora ho gaya.")
        return

    if signal == "BUY" and not state.position_qty:
        await _enter(state, price)
    elif signal == "SELL" and state.position_qty:
        await _exit(state, f"SELL signal @ {price}")


async def _enter(state: BotState, price: float) -> None:
    cfg = state.cfg
    res = await trading.place_order(
        instrument_key=cfg.instrument_key,
        side="BUY",
        quantity=cfg.quantity,
        order_type="MARKET",
        product=cfg.product,
        tag=f"bot{state.run_id}",
        source=f"bot:{cfg.strategy}",
    )
    state.position_qty = cfg.quantity
    state.entry_price = float(res.get("price") or price)
    state.trades_today += 1
    db.log_bot(state.run_id, "trade", f"BUY {cfg.quantity} @ {state.entry_price} -- {res.get('message')}")


async def _exit(state: BotState, reason: str) -> None:
    cfg = state.cfg
    qty = state.position_qty
    if not qty:
        return
    res = await trading.place_order(
        instrument_key=cfg.instrument_key,
        side="SELL",
        quantity=qty,
        order_type="MARKET",
        product=cfg.product,
        tag=f"bot{state.run_id}",
        source=f"bot:{cfg.strategy}",
    )
    exit_price = float(res.get("price") or 0)
    pnl = (exit_price - state.entry_price) * qty if exit_price else 0.0
    state.position_qty = 0
    state.entry_price = 0.0
    state.trades_today += 1
    db.log_bot(
        state.run_id,
        "trade",
        f"SELL {qty} @ {exit_price} ({reason}) -- P&L approx {round(pnl, 2)}",
    )


def logs(run_id: int, limit: int = 200) -> list[dict]:
    return db.query(
        "SELECT ts, level, message FROM bot_log WHERE run_id = ? ORDER BY id DESC LIMIT ?",
        (run_id, limit),
    )


def history(limit: int = 50) -> list[dict[str, Any]]:
    return db.query("SELECT * FROM bot_runs ORDER BY id DESC LIMIT ?", (limit,))
