"""O(1) streaming primitives.

Every feature in the fast loop updates in constant time. That is not an
optimisation, it is the design constraint: at 200 instruments and ~20 features
each, a single O(n) recompute per tick turns 40,000 updates/second into a
stalled feed.

The rule that keeps this honest is that each primitive must produce the same
answer as the batch function in engine/indicators.py. If they drift, the
backtest and the live bot are running different strategies -- which is the
worst failure mode this system has, because nothing looks broken.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Iterable


class RingBuffer:
    """Fixed-size window with O(1) append and O(1) running sum.

    deque(maxlen) evicts for us; the sum is maintained incrementally so mean
    and variance never walk the window.
    """

    __slots__ = ("size", "_buf", "_sum", "_sumsq")

    def __init__(self, size: int):
        if size < 1:
            raise ValueError("size must be >= 1")
        self.size = size
        self._buf: deque[float] = deque(maxlen=size)
        self._sum = 0.0
        self._sumsq = 0.0

    def push(self, value: float) -> float | None:
        """Append; returns the evicted value if the window was full."""
        evicted = None
        if len(self._buf) == self.size:
            evicted = self._buf[0]
            self._sum -= evicted
            self._sumsq -= evicted * evicted
        self._buf.append(value)
        self._sum += value
        self._sumsq += value * value
        return evicted

    @property
    def full(self) -> bool:
        return len(self._buf) == self.size

    @property
    def count(self) -> int:
        return len(self._buf)

    @property
    def mean(self) -> float | None:
        n = len(self._buf)
        return self._sum / n if n else None

    @property
    def std(self) -> float | None:
        """Population standard deviation, from running sums."""
        n = len(self._buf)
        if n < 2:
            return None
        var = self._sumsq / n - (self._sum / n) ** 2
        # Floating-point drift can push a near-zero variance negative.
        return math.sqrt(var) if var > 0 else 0.0

    @property
    def first(self) -> float | None:
        return self._buf[0] if self._buf else None

    @property
    def last(self) -> float | None:
        return self._buf[-1] if self._buf else None

    def values(self) -> list[float]:
        return list(self._buf)

    def __len__(self) -> int:
        return len(self._buf)


class EMA:
    """Exponential moving average.

    Seeded with the simple average of the first `period` values, matching
    engine/indicators.ema so the streaming and batch versions agree.
    """

    __slots__ = ("period", "k", "value", "_seed", "_n")

    def __init__(self, period: int):
        if period < 1:
            raise ValueError("period must be >= 1")
        self.period = period
        self.k = 2.0 / (period + 1)
        self.value: float | None = None
        self._seed = 0.0
        self._n = 0

    def update(self, price: float) -> float | None:
        if self.value is None:
            self._n += 1
            self._seed += price
            if self._n == self.period:
                self.value = self._seed / self.period
            return self.value
        self.value = price * self.k + self.value * (1 - self.k)
        return self.value


class SMA:
    __slots__ = ("buf",)

    def __init__(self, period: int):
        self.buf = RingBuffer(period)

    def update(self, price: float) -> float | None:
        self.buf.push(price)
        return self.buf.mean if self.buf.full else None

    @property
    def value(self) -> float | None:
        return self.buf.mean if self.buf.full else None


class RSI:
    """Wilder's RSI, matching the batch implementation's smoothing."""

    __slots__ = ("period", "avg_gain", "avg_loss", "_prev", "_n", "value")

    def __init__(self, period: int = 14):
        self.period = period
        self.avg_gain = 0.0
        self.avg_loss = 0.0
        self._prev: float | None = None
        self._n = 0
        self.value: float | None = None

    def update(self, price: float) -> float | None:
        if self._prev is None:
            self._prev = price
            return None

        delta = price - self._prev
        self._prev = price
        gain, loss = max(delta, 0.0), max(-delta, 0.0)
        self._n += 1

        if self._n <= self.period:
            self.avg_gain += gain
            self.avg_loss += loss
            if self._n == self.period:
                self.avg_gain /= self.period
                self.avg_loss /= self.period
                self.value = self._rsi()
            return self.value

        self.avg_gain = (self.avg_gain * (self.period - 1) + gain) / self.period
        self.avg_loss = (self.avg_loss * (self.period - 1) + loss) / self.period
        self.value = self._rsi()
        return self.value

    def _rsi(self) -> float:
        if self.avg_loss == 0:
            return 100.0
        return 100 - 100 / (1 + self.avg_gain / self.avg_loss)


class ATR:
    """Average true range, Wilder-smoothed. Takes (high, low, close)."""

    __slots__ = ("period", "value", "_prev_close", "_seed", "_n")

    def __init__(self, period: int = 14):
        self.period = period
        self.value: float | None = None
        self._prev_close: float | None = None
        self._seed = 0.0
        self._n = 0

    def update(self, high: float, low: float, close: float) -> float | None:
        if self._prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self._prev_close),
                     abs(low - self._prev_close))
        self._prev_close = close

        if self.value is None:
            self._n += 1
            self._seed += tr
            if self._n == self.period:
                self.value = self._seed / self.period
            return self.value

        self.value = (self.value * (self.period - 1) + tr) / self.period
        return self.value


class ZScore:
    """How unusual the latest value is, in standard deviations.

    Levels beat momentum for mean-reverting series like USDINR, where the RBI
    leans against sharp moves and a raw trend signal gets chopped up.
    """

    __slots__ = ("buf", "value")

    def __init__(self, period: int = 60):
        self.buf = RingBuffer(period)
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        self.buf.push(x)
        if not self.buf.full:
            self.value = None
            return None
        mean, std = self.buf.mean, self.buf.std
        self.value = 0.0 if not std else (x - mean) / std
        return self.value


class RollingExtreme:
    """Rolling min/max over a window, O(1) amortised via monotonic deques."""

    __slots__ = ("size", "_vals", "_min", "_max", "_i")

    def __init__(self, size: int):
        self.size = size
        self._vals: deque[tuple[int, float]] = deque()
        self._min: deque[tuple[int, float]] = deque()
        self._max: deque[tuple[int, float]] = deque()
        self._i = 0

    def update(self, x: float) -> None:
        i = self._i
        self._i += 1
        self._vals.append((i, x))

        while self._min and self._min[-1][1] >= x:
            self._min.pop()
        self._min.append((i, x))
        while self._max and self._max[-1][1] <= x:
            self._max.pop()
        self._max.append((i, x))

        cutoff = i - self.size
        while self._vals and self._vals[0][0] <= cutoff:
            self._vals.popleft()
        while self._min and self._min[0][0] <= cutoff:
            self._min.popleft()
        while self._max and self._max[0][0] <= cutoff:
            self._max.popleft()

    @property
    def min(self) -> float | None:
        return self._min[0][1] if self._min else None

    @property
    def max(self) -> float | None:
        return self._max[0][1] if self._max else None

    @property
    def count(self) -> int:
        return len(self._vals)


class Returns:
    """Simple return against the value `lag` updates ago."""

    __slots__ = ("buf", "value")

    def __init__(self, lag: int = 1):
        self.buf = RingBuffer(lag + 1)
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        self.buf.push(x)
        if not self.buf.full:
            self.value = None
            return None
        old = self.buf.first
        self.value = ((x - old) / old * 100) if old else 0.0
        return self.value


class Welford:
    """Running mean and variance over *all* values seen, no window.

    Welford's method rather than sum-of-squares: over a long session the naive
    form loses precision badly.
    """

    __slots__ = ("n", "mean", "_m2")

    def __init__(self):
        self.n = 0
        self.mean = 0.0
        self._m2 = 0.0

    def update(self, x: float) -> None:
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        self._m2 += delta * (x - self.mean)

    @property
    def variance(self) -> float | None:
        return self._m2 / self.n if self.n >= 2 else None

    @property
    def std(self) -> float | None:
        var = self.variance
        return math.sqrt(var) if var and var > 0 else (0.0 if var == 0 else None)


def warmup_from(primitive, values: Iterable[float]) -> None:
    """Replay history into a primitive so it starts live already warmed up."""
    for v in values:
        primitive.update(v)
