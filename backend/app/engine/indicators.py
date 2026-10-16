"""Pure-python technical indicators.

Every function takes a list of floats (or a list of candles) and returns a list
of the SAME length, with `None` padded at the front where the indicator has not
warmed up yet.  That alignment makes the backtester trivial.
"""
from __future__ import annotations

Num = float | None


def sma(values: list[float], period: int) -> list[Num]:
    out: list[Num] = [None] * len(values)
    if period <= 0:
        return out
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema(values: list[float], period: int) -> list[Num]:
    out: list[Num] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: list[float], period: int = 14) -> list[Num]:
    out: list[Num] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_g, avg_l = gains / period, losses / period
    out[period] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    for i in range(period + 1, len(values)):
        d = values[i] - values[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0.0)) / period
        out[i] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return out


def true_range(candles: list[dict]) -> list[float]:
    out = []
    for i, c in enumerate(candles):
        if i == 0:
            out.append(c["high"] - c["low"])
        else:
            pc = candles[i - 1]["close"]
            out.append(max(c["high"] - c["low"], abs(c["high"] - pc), abs(c["low"] - pc)))
    return out


def atr(candles: list[dict], period: int = 14) -> list[Num]:
    tr = true_range(candles)
    out: list[Num] = [None] * len(candles)
    if len(tr) < period:
        return out
    prev = sum(tr[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(tr)):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return out


def macd(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    ef, es = ema(values, fast), ema(values, slow)
    line: list[Num] = [
        (f - s) if (f is not None and s is not None) else None for f, s in zip(ef, es)
    ]
    start = next((i for i, v in enumerate(line) if v is not None), len(line))
    sig_src = [v for v in line[start:] if v is not None]
    sig_tail = ema(sig_src, signal)
    sig: list[Num] = [None] * start + sig_tail
    sig += [None] * (len(line) - len(sig))
    hist: list[Num] = [
        (l - s) if (l is not None and s is not None) else None for l, s in zip(line, sig)
    ]
    return line, sig, hist


def bollinger(values: list[float], period: int = 20, mult: float = 2.0):
    mid = sma(values, period)
    upper: list[Num] = [None] * len(values)
    lower: list[Num] = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        m = mid[i]
        if m is None:
            continue
        var = sum((x - m) ** 2 for x in window) / period
        sd = var ** 0.5
        upper[i], lower[i] = m + mult * sd, m - mult * sd
    return upper, mid, lower


def supertrend(candles: list[dict], period: int = 10, mult: float = 3.0):
    """Returns (trend_line, direction) where direction is 1 (up) / -1 (down)."""
    a = atr(candles, period)
    n = len(candles)
    line: list[Num] = [None] * n
    direction: list[int | None] = [None] * n
    final_ub = final_lb = None
    for i in range(n):
        if a[i] is None:
            continue
        hl2 = (candles[i]["high"] + candles[i]["low"]) / 2
        ub, lb = hl2 + mult * a[i], hl2 - mult * a[i]
        close, pclose = candles[i]["close"], candles[i - 1]["close"] if i else candles[i]["close"]

        final_ub = ub if (final_ub is None or ub < final_ub or pclose > final_ub) else final_ub
        final_lb = lb if (final_lb is None or lb > final_lb or pclose < final_lb) else final_lb

        prev_dir = direction[i - 1] if i and direction[i - 1] is not None else 1
        if prev_dir == 1:
            d = -1 if close < final_lb else 1
        else:
            d = 1 if close > final_ub else -1
        direction[i] = d
        line[i] = final_lb if d == 1 else final_ub
    return line, direction


def crossed_above(a: list[Num], b: list[Num], i: int) -> bool:
    if i < 1:
        return False
    p1, p2, c1, c2 = a[i - 1], b[i - 1], a[i], b[i]
    if None in (p1, p2, c1, c2):
        return False
    return p1 <= p2 and c1 > c2


def crossed_below(a: list[Num], b: list[Num], i: int) -> bool:
    if i < 1:
        return False
    p1, p2, c1, c2 = a[i - 1], b[i - 1], a[i], b[i]
    if None in (p1, p2, c1, c2):
        return False
    return p1 >= p2 and c1 < c2
