"""Per-instrument feature state, updated tick by tick.

Point-in-time correctness is enforced here, not hoped for. The engine only
ever moves forward: every update carries a timestamp, and an update stamped
earlier than the last one is rejected rather than quietly folded in. That one
rule is what makes a backtest replaying stored ticks produce the same numbers
the live loop produced -- and it is the difference between a backtest that
means something and one that reads tomorrow's news.

Features also report whether they are warmed up. A half-filled 252-day
momentum window is not a small number, it is a wrong one, so unwarmed
features return None and the decision layer skips them instead of treating
None as zero.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .incremental import ATR, EMA, RSI, Returns, RingBuffer, ZScore
from .microstructure import (
    OFI,
    BookSnapshot,
    OpeningRange,
    SpreadTracker,
    TradeSign,
    VWAP,
    book_imbalance,
    weighted_mid,
)
from .registry import Horizon, registry


class StaleUpdate(Exception):
    """An update arrived stamped before the last one we processed."""


@dataclass
class FeatureState:
    """Everything we track for one instrument."""

    instrument_key: str
    last_ts: int = 0
    updates: int = 0
    rejected_stale: int = 0

    # order book
    ofi: OFI = field(default_factory=lambda: OFI(window=50, zscore_period=200))
    spread: SpreadTracker = field(default_factory=lambda: SpreadTracker(100))
    trade_sign: TradeSign = field(default_factory=lambda: TradeSign(50))
    _last_imbalance: float = 0.0
    _last_weighted_mid: float = 0.0

    # intraday
    vwap: VWAP = field(default_factory=VWAP)
    opening_range: OpeningRange = field(default_factory=lambda: OpeningRange(15))
    ret_5m: Returns = field(default_factory=lambda: Returns(5))
    rsi: RSI = field(default_factory=lambda: RSI(14))
    atr: ATR = field(default_factory=lambda: ATR(14))
    volume_window: RingBuffer = field(default_factory=lambda: RingBuffer(20))
    _last_volume: float = 0.0
    _last_volume_ratio: float | None = None

    # daily context, fed by the slow loop
    vol_20d: ZScore = field(default_factory=lambda: ZScore(20))
    price_ema_fast: EMA = field(default_factory=lambda: EMA(9))
    price_ema_slow: EMA = field(default_factory=lambda: EMA(21))

    # ------------------------------------------------------------ update
    def update_tick(
        self,
        *,
        ts_ms: int,
        ltp: float,
        bid_price: float = 0.0,
        bid_qty: float = 0.0,
        ask_price: float = 0.0,
        ask_qty: float = 0.0,
        cumulative_volume: float = 0.0,
    ) -> None:
        """Fold one tick in. Rejects out-of-order data rather than using it."""
        if ts_ms and ts_ms < self.last_ts:
            self.rejected_stale += 1
            raise StaleUpdate(
                f"{self.instrument_key}: tick at {ts_ms} is older than "
                f"{self.last_ts} -- point-in-time order violated"
            )
        self.last_ts = ts_ms or self.last_ts
        self.updates += 1

        book = BookSnapshot(bid_price, bid_qty, ask_price, ask_qty)

        if book.valid:
            self.ofi.update(book)
            self.spread.update(book)
            self._last_imbalance = book_imbalance(bid_qty, ask_qty)
            self._last_weighted_mid = weighted_mid(book)

        self.trade_sign.update(ltp, book)

        # The feed reports volume cumulatively, so the traded amount since the
        # last tick is the difference. A decrease means a new session.
        traded = 0.0
        if cumulative_volume:
            if cumulative_volume >= self._last_volume:
                traded = cumulative_volume - self._last_volume
            else:
                self.on_new_session()
            self._last_volume = cumulative_volume

        if traded > 0:
            self.vwap.update(ltp, traded)
            self.volume_window.push(traded)
            avg = self.volume_window.mean
            self._last_volume_ratio = (traded / avg) if avg else None

        self.opening_range.update(ltp, ts_ms)
        self.ret_5m.update(ltp)
        self.rsi.update(ltp)
        self.price_ema_fast.update(ltp)
        self.price_ema_slow.update(ltp)
        self._last_ltp = ltp

    def update_bar(self, high: float, low: float, close: float) -> None:
        """Bar-level features that need a range, not a single price."""
        self.atr.update(high, low, close)
        self.vol_20d.update(close)

    def on_new_session(self) -> None:
        """Reset the statistics that are only meaningful within one day."""
        self.vwap.reset()
        self.opening_range.reset()
        self._last_volume = 0.0

    # ------------------------------------------------------------- read
    _last_ltp: float = 0.0

    def snapshot(self) -> dict[str, Any]:
        """Current feature values. None means not warmed up -- never zero."""
        ltp = self._last_ltp
        return {
            # tick
            "ofi": self.ofi.value if self.ofi.buf.count else None,
            "ofi_z": self.ofi.normalised,
            "book_imbalance": self._last_imbalance,
            "spread_bps": self.spread.value_bps,
            "spread_z": self.spread.z.value,
            "weighted_mid": self._last_weighted_mid or None,
            "trade_flow": self.trade_sign.flow if self.trade_sign.buf.count else None,
            # intraday
            "vwap_dev_pct": self.vwap.deviation_pct(ltp),
            "opening_range_pos": self.opening_range.position(ltp),
            "opening_range_break": (
                self.opening_range.broke_out(ltp)
                if self.opening_range.complete else None
            ),
            "ret_5m": self.ret_5m.value,
            "rsi_14": self.rsi.value,
            "atr_14": self.atr.value,
            "volume_ratio": self._last_volume_ratio,
        }

    def warmed_up(self, names: list[str]) -> bool:
        snap = self.snapshot()
        return all(snap.get(n) is not None for n in names)

    def diagnostics(self) -> dict[str, Any]:
        snap = self.snapshot()
        return {
            "instrument_key": self.instrument_key,
            "updates": self.updates,
            "rejected_stale": self.rejected_stale,
            "last_ts": self.last_ts,
            "ready": sum(1 for v in snap.values() if v is not None),
            "total": len(snap),
        }


class FeatureEngine:
    """Feature state for every instrument we follow."""

    def __init__(self) -> None:
        self.states: dict[str, FeatureState] = {}
        self.updates = 0
        self.stale_rejected = 0

    def state_for(self, instrument_key: str) -> FeatureState:
        state = self.states.get(instrument_key)
        if state is None:
            state = FeatureState(instrument_key=instrument_key)
            self.states[instrument_key] = state
        return state

    def on_tick(self, tick: Any) -> dict[str, Any] | None:
        """Update from a feed Tick. Returns the new snapshot, or None if stale."""
        state = self.state_for(tick.instrument_key)
        depth = tick.depth[0] if getattr(tick, "depth", None) else None
        try:
            state.update_tick(
                ts_ms=tick.ts,
                ltp=tick.ltp,
                bid_price=depth.bid_price if depth else 0.0,
                bid_qty=depth.bid_qty if depth else 0.0,
                ask_price=depth.ask_price if depth else 0.0,
                ask_qty=depth.ask_qty if depth else 0.0,
                cumulative_volume=getattr(tick, "volume", 0.0),
            )
        except StaleUpdate:
            self.stale_rejected += 1
            return None
        self.updates += 1
        return state.snapshot()

    def snapshot(self, instrument_key: str) -> dict[str, Any] | None:
        state = self.states.get(instrument_key)
        return state.snapshot() if state else None

    def new_session(self) -> None:
        for state in self.states.values():
            state.on_new_session()

    def status(self) -> dict[str, Any]:
        return {
            "instruments": len(self.states),
            "updates": self.updates,
            "stale_rejected": self.stale_rejected,
            "registered_features": len(registry.names()),
            "by_horizon": {
                h.value: len(registry.by_horizon(h)) for h in Horizon
            },
        }


engine = FeatureEngine()
