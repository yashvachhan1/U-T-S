"""Order-book features.

This is where intraday edge actually lives, and it is the one family in the
research with clean academic support rather than vendor marketing. None of it
can be computed from a polled price -- it needs the book, which is why the
WebSocket feed had to come first.

Order Flow Imbalance follows Cont, Kukanov and Stoikov: it measures the net
pressure added to or removed from the touch between two book snapshots, which
turns out to explain short-horizon price moves far better than trade volume
does. Volume tells you what happened; OFI tells you what the queue is doing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .incremental import EMA, RingBuffer, Welford, ZScore


@dataclass
class BookSnapshot:
    """Top of book at one instant."""

    bid_price: float = 0.0
    bid_qty: float = 0.0
    ask_price: float = 0.0
    ask_qty: float = 0.0

    @property
    def valid(self) -> bool:
        return self.bid_price > 0 and self.ask_price > 0

    @property
    def mid(self) -> float:
        return (self.bid_price + self.ask_price) / 2 if self.valid else 0.0

    @property
    def spread(self) -> float:
        return self.ask_price - self.bid_price if self.valid else 0.0


def ofi_increment(prev: BookSnapshot, curr: BookSnapshot) -> float:
    """One OFI term, per Cont-Kukanov-Stoikov.

    The intuition is about queue position, not size:

      * bid price **up** -- a new, better bid appeared, so its whole size is
        fresh demand
      * bid price **down** -- the old bid was pulled or hit, so the whole of
        the previous size left
      * bid price **unchanged** -- only the change in size counts

    The ask side mirrors it with the opposite sign, because size added to the
    offer is selling pressure.
    """
    if not prev.valid or not curr.valid:
        return 0.0

    # ---- bid side: positive means demand ----
    if curr.bid_price > prev.bid_price:
        bid_term = curr.bid_qty
    elif curr.bid_price < prev.bid_price:
        bid_term = -prev.bid_qty
    else:
        bid_term = curr.bid_qty - prev.bid_qty

    # ---- ask side: positive means supply, so it subtracts ----
    if curr.ask_price < prev.ask_price:
        ask_term = curr.ask_qty
    elif curr.ask_price > prev.ask_price:
        ask_term = -prev.ask_qty
    else:
        ask_term = curr.ask_qty - prev.ask_qty

    return bid_term - ask_term


class OFI:
    """Rolling order-flow imbalance.

    Raw OFI is in shares, so it is not comparable across instruments. The
    z-scored form is what a model should see.
    """

    __slots__ = ("window", "buf", "prev", "last_increment", "stats", "z")

    def __init__(self, window: int = 50, zscore_period: int = 200):
        self.window = window
        self.buf = RingBuffer(window)
        self.prev: BookSnapshot | None = None
        self.last_increment = 0.0
        self.stats = Welford()
        self.z = ZScore(zscore_period)

    def update(self, book: BookSnapshot) -> float:
        if self.prev is None:
            self.prev = book
            return 0.0

        inc = ofi_increment(self.prev, book)
        self.prev = book
        self.last_increment = inc
        self.buf.push(inc)
        self.stats.update(inc)
        self.z.update(self.value)
        return self.value

    @property
    def value(self) -> float:
        """Sum of increments over the window -- net pressure."""
        return self.buf._sum

    @property
    def normalised(self) -> float | None:
        """Z-scored OFI, comparable across instruments."""
        return self.z.value


def book_imbalance(bid_qty: float, ask_qty: float) -> float:
    """(bid - ask) / (bid + ask), in [-1, 1].

    The cheap cousin of OFI: no history needed, available on every tick.
    Positive means more size resting on the bid.
    """
    total = bid_qty + ask_qty
    return (bid_qty - ask_qty) / total if total else 0.0


def weighted_mid(book: BookSnapshot) -> float:
    """Mid price leaning toward the heavier side of the book.

    A mid with 10 lots bid and 1000 offered is not really the middle -- the
    next trade is far likelier to happen near the bid.
    """
    if not book.valid:
        return 0.0
    total = book.bid_qty + book.ask_qty
    if not total:
        return book.mid
    # Weight each price by the size resting on the *other* side.
    return (book.bid_price * book.ask_qty + book.ask_price * book.bid_qty) / total


class VWAP:
    """Session VWAP and the deviation of price from it."""

    __slots__ = ("pv", "volume", "value")

    def __init__(self):
        self.pv = 0.0
        self.volume = 0.0
        self.value: float | None = None

    def update(self, price: float, volume: float) -> float | None:
        if volume <= 0:
            return self.value
        self.pv += price * volume
        self.volume += volume
        self.value = self.pv / self.volume
        return self.value

    def deviation_pct(self, price: float) -> float | None:
        if not self.value:
            return None
        return (price - self.value) / self.value * 100

    def reset(self) -> None:
        """VWAP is a session statistic -- it must not carry across days."""
        self.pv = self.volume = 0.0
        self.value = None


class TradeSign:
    """Lee-Ready style classification of who crossed the spread.

    A trade at or above the offer is buyer-initiated; at or below the bid,
    seller-initiated. Between the two, fall back on whether price ticked up or
    down. Summed, this is the aggressor flow.
    """

    __slots__ = ("_prev_price", "buf", "last")

    def __init__(self, window: int = 50):
        self._prev_price: float | None = None
        self.buf = RingBuffer(window)
        self.last = 0

    def update(self, price: float, book: BookSnapshot) -> int:
        sign = 0
        if book.valid:
            if price >= book.ask_price:
                sign = 1
            elif price <= book.bid_price:
                sign = -1

        if sign == 0 and self._prev_price is not None:
            if price > self._prev_price:
                sign = 1
            elif price < self._prev_price:
                sign = -1

        self._prev_price = price
        self.last = sign
        self.buf.push(float(sign))
        return sign

    @property
    def flow(self) -> float:
        """Net aggressor flow over the window."""
        return self.buf._sum

    @property
    def ratio(self) -> float | None:
        """Share of aggressive trades that were buys, in [-1, 1]."""
        n = self.buf.count
        return self.buf._sum / n if n else None


class SpreadTracker:
    """Spread in basis points, plus how it compares to its own recent norm.

    A spread that widens beyond its usual range is a liquidity warning: the
    cost of being wrong just went up, so position size should come down.
    """

    __slots__ = ("ema", "z", "value_bps")

    def __init__(self, period: int = 100):
        self.ema = EMA(period)
        self.z = ZScore(period)
        self.value_bps: float | None = None

    def update(self, book: BookSnapshot) -> float | None:
        if not book.valid or not book.mid:
            return self.value_bps
        bps = book.spread / book.mid * 10_000
        self.value_bps = bps
        self.ema.update(bps)
        self.z.update(bps)
        return bps

    @property
    def is_wide(self) -> bool:
        """Two standard deviations past normal -- treat liquidity as impaired."""
        return self.z.value is not None and self.z.value > 2.0


@dataclass
class OpeningRange:
    """High and low of the first N minutes.

    The opening range sets the day's reference: a break of it on volume is one
    of the few intraday patterns with a long record behind it.
    """

    minutes: int = 15
    high: float = 0.0
    low: float = 0.0
    _start_ts: int | None = field(default=None, repr=False)
    complete: bool = False

    def update(self, price: float, ts_ms: int) -> None:
        if self._start_ts is None:
            self._start_ts = ts_ms
            self.high = self.low = price
            return
        if ts_ms - self._start_ts >= self.minutes * 60_000:
            self.complete = True
            return
        self.high = max(self.high, price)
        self.low = min(self.low, price)

    def position(self, price: float) -> float | None:
        """Where price sits in the range: 0 at the low, 1 at the high."""
        if not self.complete or self.high <= self.low:
            return None
        return (price - self.low) / (self.high - self.low)

    def broke_out(self, price: float) -> int:
        """1 above the range, -1 below, 0 inside."""
        if not self.complete:
            return 0
        if price > self.high:
            return 1
        if price < self.low:
            return -1
        return 0

    def reset(self) -> None:
        self.high = self.low = 0.0
        self._start_ts = None
        self.complete = False
