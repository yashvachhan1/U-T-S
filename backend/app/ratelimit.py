"""Token-bucket rate limiting.

SEBI/NSE treat anything under 10 orders per second as low-frequency, which
needs no exchange registration. We stay well under that on purpose: the limit
is a cliff, not a target, and one runaway loop should not push us over it.

The same bucket also protects the market-data endpoints, where Upstox applies
its own per-second and per-minute caps.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


class RateLimitExceeded(Exception):
    """Raised when a caller asked not to wait and the bucket was empty."""


@dataclass
class TokenBucket:
    """Classic token bucket.

    `rate` tokens refill per second, up to `capacity`. A burst can spend up to
    `capacity` at once, then the refill rate takes over -- which is exactly how
    exchange limits behave.
    """

    rate: float
    capacity: float
    name: str = "bucket"
    _tokens: float = field(init=False)
    _last: float = field(init=False)
    _lock: asyncio.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.rate <= 0 or self.capacity <= 0:
            raise ValueError("rate and capacity must be positive")
        self._tokens = float(self.capacity)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
        self._last = now

    @property
    def available(self) -> float:
        self._refill()
        return self._tokens

    async def acquire(self, tokens: float = 1.0, *, wait: bool = True) -> float:
        """Take `tokens`. Returns how long we waited, in seconds.

        With `wait=False` an empty bucket raises instead of blocking -- used
        where being late is worse than being skipped.
        """
        if tokens > self.capacity:
            raise ValueError(f"{tokens} tokens requested but capacity is {self.capacity}")

        waited = 0.0
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return waited
                deficit = tokens - self._tokens
                sleep_for = deficit / self.rate

            if not wait:
                raise RateLimitExceeded(
                    f"{self.name}: {tokens:g} tokens chahiye, "
                    f"{self._tokens:.2f} available. {sleep_for:.2f}s wait karna padega."
                )
            await asyncio.sleep(sleep_for)
            waited += sleep_for


@dataclass
class DailyCounter:
    """Hard cap on how many of something may happen in one trading day.

    Resets on date change rather than after 24 hours, so the count always lines
    up with the trading session.
    """

    limit: int
    name: str = "counter"
    _count: int = field(default=0, init=False)
    _day: str = field(default="", init=False)

    def _roll(self) -> None:
        from .clock import now_ist

        today = now_ist().strftime("%Y-%m-%d")
        if today != self._day:
            self._day = today
            self._count = 0

    @property
    def used(self) -> int:
        self._roll()
        return self._count

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def check(self) -> None:
        """Raise if the cap is already reached."""
        if self.remaining <= 0:
            raise RateLimitExceeded(
                f"{self.name}: aaj ka limit ({self.limit}) poora ho gaya."
            )

    def record(self, n: int = 1) -> None:
        self._roll()
        self._count += n


# --------------------------------------------------------------------------
# Shared buckets. Orders get their own so a burst of quote requests can never
# eat into the budget that order placement depends on.
# --------------------------------------------------------------------------
from . import config  # noqa: E402  (circular-safe: config imports nothing of ours)

# Capacity is deliberately below the rate: a bucket can release its whole
# capacity at once and then keep refilling, so the true peak inside one second
# is capacity + rate. Keeping that under 10 is what keeps us low-frequency.
orders = TokenBucket(
    rate=config.MAX_ORDERS_PER_SECOND,
    capacity=config.MAX_ORDER_BURST,
    name="orders",
)
market_data = TokenBucket(rate=20.0, capacity=20.0, name="market_data")
daily_orders = DailyCounter(limit=config.MAX_ORDERS_PER_DAY, name="daily orders")
