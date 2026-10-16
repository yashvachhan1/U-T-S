"""Event-driven backtester.

Deliberately conservative so the numbers are not fantasy:
  * a signal on candle i is filled at the OPEN of candle i+1 (no look-ahead),
  * brokerage + STT + slippage are charged on both legs,
  * stop-loss / target are checked intrabar using the candle high and low, and
    when both are touched in the same candle we assume the STOP hit first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..strategies import base as strat


@dataclass
class Costs:
    brokerage_per_order: float = 20.0   # typical discount-broker flat fee
    slippage_pct: float = 0.03          # % of price lost to spread, each leg
    taxes_pct: float = 0.12             # STT + exchange + GST + stamp, round trip


@dataclass
class Trade:
    entry_time: str
    entry_price: float
    exit_time: str | None = None
    exit_price: float | None = None
    quantity: int = 0
    direction: str = "LONG"
    pnl: float = 0.0
    pnl_pct: float = 0.0
    costs: float = 0.0
    exit_reason: str = ""

    def to_json(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Result:
    trades: list[Trade] = field(default_factory=list)
    equity: list[dict] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    signals: list[dict] = field(default_factory=list)
    overlays: dict[str, list] = field(default_factory=dict)


def run(
    candles: list[dict],
    strategy_key: str,
    params: dict | None = None,
    *,
    capital: float = 100_000.0,
    position_pct: float = 100.0,
    stop_loss_pct: float = 0.0,
    target_pct: float = 0.0,
    allow_short: bool = False,
    costs: Costs | None = None,
) -> Result:
    costs = costs or Costs()
    s = strat.get(strategy_key)
    resolved = s.resolve(params)
    sig = s.run(candles, resolved)

    res = Result(overlays=s.overlays(candles, resolved))
    res.signals = [
        {"time": candles[i]["ts"], "type": v, "price": candles[i]["close"]}
        for i, v in enumerate(sig)
        if v
    ]

    state = {
        "cash": capital,
        "qty": 0,
        "dir": "",
        "entry_price": 0.0,
        "trade": None,
    }
    peak = capital
    max_dd = 0.0

    def leg_cost(price: float, qty: int) -> float:
        return costs.brokerage_per_order + price * qty * (costs.taxes_pct / 200)

    def fill(price: float, side: str) -> float:
        """Slippage always works against us."""
        adj = price * costs.slippage_pct / 100
        return price + adj if side == "BUY" else price - adj

    def close_position(price: float, ts: str, reason: str) -> None:
        trade: Trade | None = state["trade"]
        if not state["qty"] or trade is None:
            return
        qty = state["qty"]
        exit_side = "SELL" if state["dir"] == "LONG" else "BUY"
        px = fill(price, exit_side)
        c = leg_cost(px, qty)
        gross = (px - state["entry_price"]) * qty
        if state["dir"] == "SHORT":
            gross = -gross
        trade.exit_time = ts
        trade.exit_price = round(px, 2)
        trade.costs = round(trade.costs + c, 2)
        trade.pnl = round(gross - trade.costs, 2)
        invested = state["entry_price"] * qty
        trade.pnl_pct = round(trade.pnl / invested * 100, 2) if invested else 0.0
        trade.exit_reason = reason
        state["cash"] += gross - c
        res.trades.append(trade)
        state.update(trade=None, qty=0, dir="")

    def open_position(price: float, ts: str, direction: str) -> None:
        side = "BUY" if direction == "LONG" else "SELL"
        px = fill(price, side)
        budget = state["cash"] * (position_pct / 100)
        qty = int(budget // px) if px > 0 else 0
        if qty <= 0:
            return
        c = leg_cost(px, qty)
        state["cash"] -= c
        state.update(qty=qty, dir=direction, entry_price=px)
        state["trade"] = Trade(
            entry_time=ts,
            entry_price=round(px, 2),
            quantity=qty,
            direction=direction,
            costs=round(c, 2),
        )

    for i, c in enumerate(candles):
        ts = c["ts"]

        # --- 1. intrabar stop-loss / target on an existing position --------
        if state["qty"] and (stop_loss_pct or target_pct):
            ep = state["entry_price"]
            if state["dir"] == "LONG":
                sl = ep * (1 - stop_loss_pct / 100) if stop_loss_pct else None
                tg = ep * (1 + target_pct / 100) if target_pct else None
                if sl is not None and c["low"] <= sl:
                    close_position(sl, ts, "stop-loss")
                elif tg is not None and c["high"] >= tg:
                    close_position(tg, ts, "target")
            else:
                sl = ep * (1 + stop_loss_pct / 100) if stop_loss_pct else None
                tg = ep * (1 - target_pct / 100) if target_pct else None
                if sl is not None and c["high"] >= sl:
                    close_position(sl, ts, "stop-loss")
                elif tg is not None and c["low"] <= tg:
                    close_position(tg, ts, "target")

        # --- 2. act on the PREVIOUS candle signal, at this candle open -----
        prev_signal = sig[i - 1] if i > 0 else None
        if prev_signal == "BUY":
            if state["dir"] == "SHORT":
                close_position(c["open"], ts, "signal")
            if not state["qty"]:
                open_position(c["open"], ts, "LONG")
        elif prev_signal == "SELL":
            if state["dir"] == "LONG":
                close_position(c["open"], ts, "signal")
            if not state["qty"] and allow_short:
                open_position(c["open"], ts, "SHORT")

        # --- 3. mark to market ---------------------------------------------
        unreal = 0.0
        if state["qty"]:
            unreal = (c["close"] - state["entry_price"]) * state["qty"]
            if state["dir"] == "SHORT":
                unreal = -unreal
        eq = state["cash"] + unreal
        peak = max(peak, eq)
        if peak:
            max_dd = max(max_dd, (peak - eq) / peak * 100)
        res.equity.append({"time": ts, "value": round(eq, 2)})

    if state["qty"] and candles:
        close_position(candles[-1]["close"], candles[-1]["ts"], "end of data")
        if res.equity:
            res.equity[-1]["value"] = round(state["cash"], 2)

    res.stats = _stats(res.trades, capital, state["cash"], max_dd, candles)
    return res


def _years_spanned(candles: list[dict]) -> float:
    if len(candles) < 2:
        return 0.0
    try:
        a = datetime.fromisoformat(str(candles[0]["ts"]).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(candles[-1]["ts"]).replace("Z", "+00:00"))
        return max((b - a).days / 365.25, 0.0)
    except Exception:
        return 0.0


def _stats(
    trades: list[Trade], capital: float, final: float, max_dd: float, candles: list[dict]
) -> dict:
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    net = final - capital

    years = _years_spanned(candles)
    cagr = None
    if years > 0.08 and final > 0 and capital > 0:
        cagr = ((final / capital) ** (1 / years) - 1) * 100

    return {
        "starting_capital": round(capital, 2),
        "final_capital": round(final, 2),
        "net_pnl": round(net, 2),
        "return_pct": round(net / capital * 100, 2) if capital else 0.0,
        "cagr_pct": round(cagr, 2) if cagr is not None else None,
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
        "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "max_drawdown_pct": round(max_dd, 2),
        "total_costs": round(sum(t.costs for t in trades), 2),
        "best_trade": round(max((t.pnl for t in trades), default=0.0), 2),
        "worst_trade": round(min((t.pnl for t in trades), default=0.0), 2),
    }
