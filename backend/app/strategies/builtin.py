"""The strategies that ship with the app."""
from __future__ import annotations

from ..engine import indicators as ta
from .base import Param, Signal, Strategy, register


def _closes(candles: list[dict]) -> list[float]:
    return [c["close"] for c in candles]


# ---------------------------------------------------------------- EMA cross
def _ema_cross(candles: list[dict], p: dict) -> list[Signal]:
    closes = _closes(candles)
    fast, slow = ta.ema(closes, int(p["fast"])), ta.ema(closes, int(p["slow"]))
    out: list[Signal] = [None] * len(candles)
    for i in range(len(candles)):
        if ta.crossed_above(fast, slow, i):
            out[i] = "BUY"
        elif ta.crossed_below(fast, slow, i):
            out[i] = "SELL"
    return out


def _ema_plot(candles, p):
    closes = _closes(candles)
    return {
        f"EMA {int(p['fast'])}": ta.ema(closes, int(p["fast"])),
        f"EMA {int(p['slow'])}": ta.ema(closes, int(p["slow"])),
    }


register(
    Strategy(
        key="ema_crossover",
        name="EMA Crossover",
        description="Fast EMA slow EMA ke upar jaye to BUY, neeche jaye to SELL. Trending market me achha chalta hai.",
        params=[
            Param("fast", "Fast EMA", 9, min=2, max=200, step=1),
            Param("slow", "Slow EMA", 21, min=3, max=400, step=1),
        ],
        fn=_ema_cross,
        plot=_ema_plot,
    )
)


# ---------------------------------------------------------------------- RSI
def _rsi(candles: list[dict], p: dict) -> list[Signal]:
    r = ta.rsi(_closes(candles), int(p["period"]))
    lo, hi = float(p["oversold"]), float(p["overbought"])
    out: list[Signal] = [None] * len(candles)
    for i in range(1, len(candles)):
        prev, cur = r[i - 1], r[i]
        if prev is None or cur is None:
            continue
        if prev <= lo < cur:          # climbing back out of oversold
            out[i] = "BUY"
        elif prev >= hi > cur:        # rolling over from overbought
            out[i] = "SELL"
    return out


register(
    Strategy(
        key="rsi_reversal",
        name="RSI Reversal",
        description="Oversold se bahar aate hi BUY, overbought se girte hi SELL. Sideways/range market ke liye.",
        params=[
            Param("period", "RSI Period", 14, min=2, max=50, step=1),
            Param("oversold", "Oversold level", 30, min=5, max=50, step=1),
            Param("overbought", "Overbought level", 70, min=50, max=95, step=1),
        ],
        fn=_rsi,
    )
)


# --------------------------------------------------------------- Supertrend
def _supertrend(candles: list[dict], p: dict) -> list[Signal]:
    _, direction = ta.supertrend(candles, int(p["period"]), float(p["multiplier"]))
    out: list[Signal] = [None] * len(candles)
    for i in range(1, len(candles)):
        prev, cur = direction[i - 1], direction[i]
        if prev is None or cur is None or prev == cur:
            continue
        out[i] = "BUY" if cur == 1 else "SELL"
    return out


def _supertrend_plot(candles, p):
    line, _ = ta.supertrend(candles, int(p["period"]), float(p["multiplier"]))
    return {"Supertrend": line}


register(
    Strategy(
        key="supertrend",
        name="Supertrend",
        description="Trend palatne par signal. Intraday me popular, lekin sideways market me whipsaw deta hai.",
        params=[
            Param("period", "ATR Period", 10, min=2, max=50, step=1),
            Param("multiplier", "Multiplier", 3, min=0.5, max=10, step=0.5),
        ],
        fn=_supertrend,
        plot=_supertrend_plot,
    )
)


# -------------------------------------------------------------------- MACD
def _macd(candles: list[dict], p: dict) -> list[Signal]:
    line, sig, _ = ta.macd(
        _closes(candles), int(p["fast"]), int(p["slow"]), int(p["signal"])
    )
    out: list[Signal] = [None] * len(candles)
    for i in range(len(candles)):
        if ta.crossed_above(line, sig, i):
            out[i] = "BUY"
        elif ta.crossed_below(line, sig, i):
            out[i] = "SELL"
    return out


register(
    Strategy(
        key="macd",
        name="MACD Crossover",
        description="MACD line signal line ko cross kare to trade. Medium-term trend follow karta hai.",
        params=[
            Param("fast", "Fast EMA", 12, min=2, max=100, step=1),
            Param("slow", "Slow EMA", 26, min=3, max=200, step=1),
            Param("signal", "Signal EMA", 9, min=2, max=50, step=1),
        ],
        fn=_macd,
    )
)


# -------------------------------------------------------- Bollinger bounce
def _bollinger(candles: list[dict], p: dict) -> list[Signal]:
    closes = _closes(candles)
    up, mid, low = ta.bollinger(closes, int(p["period"]), float(p["stddev"]))
    out: list[Signal] = [None] * len(candles)
    for i in range(1, len(candles)):
        if None in (low[i], up[i], low[i - 1], up[i - 1]):
            continue
        if closes[i - 1] < low[i - 1] and closes[i] > low[i]:
            out[i] = "BUY"
        elif closes[i - 1] > up[i - 1] and closes[i] < up[i]:
            out[i] = "SELL"
    return out


def _bollinger_plot(candles, p):
    up, mid, low = ta.bollinger(_closes(candles), int(p["period"]), float(p["stddev"]))
    return {"BB Upper": up, "BB Mid": mid, "BB Lower": low}


register(
    Strategy(
        key="bollinger_bounce",
        name="Bollinger Bounce",
        description="Lower band se bounce par BUY, upper band se wapas aane par SELL. Mean-reversion.",
        params=[
            Param("period", "Period", 20, min=5, max=100, step=1),
            Param("stddev", "Std Dev", 2, min=0.5, max=4, step=0.1),
        ],
        fn=_bollinger,
        plot=_bollinger_plot,
    )
)
