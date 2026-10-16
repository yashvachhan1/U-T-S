"""Phase 3 gates.

Three things have to hold before any model is trained on these numbers:

  1. **Incremental == batch.** If the streaming primitive and the batch
     function disagree, the live bot and the backtest are running different
     strategies and nothing looks broken.
  2. **Point-in-time.** A feature at time T may only use data at or before T.
  3. **O(1).** 40,000 updates a second, or the feed stalls.
"""
from __future__ import annotations

import random
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.engine import indicators as batch  # noqa: E402
from app.features import incremental as inc  # noqa: E402
from app.features import microstructure as micro  # noqa: E402
from app.features.engine import FeatureEngine, FeatureState, StaleUpdate  # noqa: E402
from app.features.registry import Horizon, registry  # noqa: E402


@pytest.fixture
def prices():
    random.seed(11)
    out, p = [], 1000.0
    for _ in range(600):
        p *= 1 + random.gauss(0.0002, 0.012)
        out.append(round(p, 2))
    return out


# ===================================================== gate 1: inc == batch
def test_ema_streaming_matches_batch(prices):
    """Drift here means the backtest and the live bot disagree silently."""
    expected = batch.ema(prices, 20)
    streaming = inc.EMA(20)
    for i, p in enumerate(prices):
        got = streaming.update(p)
        if expected[i] is None:
            assert got is None, f"index {i}: batch warming up, stream produced {got}"
        else:
            assert abs(got - expected[i]) < 1e-9, f"index {i}"


def test_sma_streaming_matches_batch(prices):
    expected = batch.sma(prices, 20)
    streaming = inc.SMA(20)
    for i, p in enumerate(prices):
        got = streaming.update(p)
        if expected[i] is None:
            assert got is None
        else:
            assert abs(got - expected[i]) < 1e-9, f"index {i}"


def test_rsi_streaming_matches_batch(prices):
    expected = batch.rsi(prices, 14)
    streaming = inc.RSI(14)
    for i, p in enumerate(prices):
        got = streaming.update(p)
        if expected[i] is None:
            assert got is None
        else:
            assert abs(got - expected[i]) < 1e-8, f"index {i}: {got} vs {expected[i]}"


def test_atr_streaming_matches_batch(prices):
    candles = []
    for p in prices:
        candles.append({"high": p * 1.004, "low": p * 0.996, "close": p})
    expected = batch.atr(candles, 14)
    streaming = inc.ATR(14)
    for i, c in enumerate(candles):
        got = streaming.update(c["high"], c["low"], c["close"])
        if expected[i] is None:
            assert got is None
        else:
            assert abs(got - expected[i]) < 1e-8, f"index {i}"


# ========================================================= ring buffer
def test_ring_buffer_sum_stays_exact(prices):
    """The running sum must not drift away from a fresh recomputation."""
    buf = inc.RingBuffer(50)
    for p in prices:
        buf.push(p)
        if buf.full:
            assert abs(buf._sum - sum(buf.values())) < 1e-6


def test_ring_buffer_evicts_oldest():
    buf = inc.RingBuffer(3)
    for x in (1, 2, 3):
        assert buf.push(x) is None
    assert buf.push(4) == 1.0
    assert buf.values() == [2.0, 3.0, 4.0]


def test_ring_buffer_std_matches_numpy_style(prices):
    buf = inc.RingBuffer(30)
    for p in prices[:30]:
        buf.push(p)
    vals = buf.values()
    mean = sum(vals) / len(vals)
    expected = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
    assert abs(buf.std - expected) < 1e-6


def test_rolling_extreme_matches_brute_force(prices):
    window = 20
    roll = inc.RollingExtreme(window)
    for i, p in enumerate(prices[:200]):
        roll.update(p)
        lo = max(0, i - window + 1)
        assert roll.min == min(prices[lo:i + 1])
        assert roll.max == max(prices[lo:i + 1])


def test_zscore_is_zero_for_constant_input():
    z = inc.ZScore(10)
    for _ in range(20):
        out = z.update(100.0)
    assert out == 0.0          # no variance, not a divide-by-zero


def test_welford_matches_direct_variance(prices):
    w = inc.Welford()
    for p in prices:
        w.update(p)
    mean = sum(prices) / len(prices)
    expected = sum((p - mean) ** 2 for p in prices) / len(prices)
    assert abs(w.variance - expected) < 1e-6


# =========================================================== gate 2: PIT
def test_future_tick_cannot_be_folded_in():
    """A tick stamped before the last one is rejected, not blended."""
    state = FeatureState(instrument_key="X")
    state.update_tick(ts_ms=1000, ltp=100.0)
    state.update_tick(ts_ms=2000, ltp=101.0)
    with pytest.raises(StaleUpdate):
        state.update_tick(ts_ms=1500, ltp=999.0)
    assert state.rejected_stale == 1


def test_engine_drops_stale_ticks_without_raising():
    class T:
        def __init__(self, ts, ltp):
            self.instrument_key, self.ts, self.ltp = "X", ts, ltp
            self.depth, self.volume = [], 0.0

    eng = FeatureEngine()
    assert eng.on_tick(T(1000, 100.0)) is not None
    assert eng.on_tick(T(2000, 101.0)) is not None
    assert eng.on_tick(T(1500, 999.0)) is None      # silently dropped
    assert eng.stale_rejected == 1


def test_unwarmed_features_are_none_not_zero():
    """None means 'unknown'. Zero is a value, and a wrong one."""
    state = FeatureState(instrument_key="X")
    state.update_tick(ts_ms=1000, ltp=100.0)
    snap = state.snapshot()
    assert snap["rsi_14"] is None
    assert snap["atr_14"] is None
    assert snap["ret_5m"] is None


def test_feature_becomes_available_only_after_warmup():
    state = FeatureState(instrument_key="X")
    for i in range(14):
        state.update_tick(ts_ms=1000 + i, ltp=100.0 + i)
    assert state.snapshot()["rsi_14"] is None       # needs 15 points
    state.update_tick(ts_ms=2000, ltp=115.0)
    assert state.snapshot()["rsi_14"] is not None


def test_session_reset_clears_intraday_only():
    state = FeatureState(instrument_key="X")
    for i in range(30):
        state.update_tick(ts_ms=1000 + i * 100, ltp=100.0 + i,
                          cumulative_volume=100.0 * (i + 1))
    assert state.vwap.value is not None
    rsi_before = state.rsi.value

    state.on_new_session()
    assert state.vwap.value is None                 # session statistic, cleared
    assert state.rsi.value == rsi_before            # multi-day, preserved


def test_volume_decrease_is_treated_as_a_new_session():
    """Cumulative volume only falls when the exchange day rolls over."""
    state = FeatureState(instrument_key="X")
    for i in range(10):
        state.update_tick(ts_ms=1000 + i, ltp=100.0, cumulative_volume=1000.0 * (i + 1))
    assert state.vwap.value is not None
    state.update_tick(ts_ms=2000, ltp=100.0, cumulative_volume=50.0)
    assert state.vwap.value is None


# ================================================================== OFI
def book(bp, bq, ap, aq):
    return micro.BookSnapshot(bp, bq, ap, aq)


def test_ofi_new_better_bid_is_full_size():
    """A better bid is fresh demand, so its whole size counts."""
    prev = book(100.0, 50, 100.5, 50)
    curr = book(100.1, 80, 100.5, 50)
    assert micro.ofi_increment(prev, curr) == 80


def test_ofi_bid_pulled_removes_previous_size():
    prev = book(100.0, 50, 100.5, 50)
    curr = book(99.9, 20, 100.5, 50)
    assert micro.ofi_increment(prev, curr) == -50


def test_ofi_same_price_counts_only_the_change():
    prev = book(100.0, 50, 100.5, 50)
    curr = book(100.0, 70, 100.5, 50)
    assert micro.ofi_increment(prev, curr) == 20


def test_ofi_ask_side_has_opposite_sign():
    """Size added to the offer is supply, so it pushes OFI negative."""
    prev = book(100.0, 50, 100.5, 50)
    curr = book(100.0, 50, 100.5, 90)
    assert micro.ofi_increment(prev, curr) == -40


def test_ofi_better_offer_is_selling_pressure():
    prev = book(100.0, 50, 100.5, 50)
    curr = book(100.0, 50, 100.4, 70)
    assert micro.ofi_increment(prev, curr) == -70


def test_ofi_ignores_an_invalid_book():
    assert micro.ofi_increment(book(0, 0, 0, 0), book(100, 10, 101, 10)) == 0.0


def test_ofi_accumulates_over_its_window():
    o = micro.OFI(window=10)
    o.update(book(100.0, 50, 100.5, 50))
    for q in (60, 70, 80):
        o.update(book(100.0, q, 100.5, 50))
    assert o.value == 30        # +10 +10 +10


def test_book_imbalance_bounds():
    assert micro.book_imbalance(100, 0) == 1.0
    assert micro.book_imbalance(0, 100) == -1.0
    assert micro.book_imbalance(50, 50) == 0.0
    assert micro.book_imbalance(0, 0) == 0.0


def test_weighted_mid_leans_toward_the_thin_side():
    """Heavy offer, light bid -- the next trade is likelier near the bid."""
    b = book(100.0, 10, 101.0, 1000)
    assert micro.weighted_mid(b) < b.mid


def test_trade_sign_classification():
    ts = micro.TradeSign()
    b = book(100.0, 50, 100.5, 50)
    assert ts.update(100.5, b) == 1      # at the offer
    assert ts.update(100.0, b) == -1     # at the bid
    assert ts.update(100.2, b) == 1      # inside, ticked up


def test_vwap_and_deviation():
    v = micro.VWAP()
    v.update(100.0, 100)
    v.update(102.0, 100)
    assert v.value == 101.0
    assert abs(v.deviation_pct(103.0) - (2 / 101 * 100)) < 1e-9


def test_spread_widening_is_flagged():
    s = micro.SpreadTracker(period=50)
    for _ in range(60):
        s.update(book(100.0, 10, 100.05, 10))       # 5 bps, steady
    assert not s.is_wide
    for _ in range(3):
        s.update(book(100.0, 10, 101.0, 10))        # ~100 bps
    assert s.is_wide


def test_opening_range_tracks_then_completes():
    r = micro.OpeningRange(minutes=15)
    base = 1_760_000_000_000
    r.update(100.0, base)
    r.update(102.0, base + 60_000)
    r.update(99.0, base + 120_000)
    assert (r.high, r.low) == (102.0, 99.0)
    assert not r.complete
    r.update(105.0, base + 16 * 60_000)
    assert r.complete
    assert r.high == 102.0                   # frozen at the bell
    assert r.broke_out(103.0) == 1
    assert r.broke_out(98.0) == -1
    assert r.broke_out(100.0) == 0


# ============================================================ registry
def test_every_feature_has_a_version_and_horizon():
    for spec in registry.all():
        assert spec.version
        assert isinstance(spec.horizon, Horizon)
        assert spec.description


def test_set_hash_is_stable_and_order_independent():
    a = registry.set_hash(["ofi", "rsi_14", "spread_bps"])
    b = registry.set_hash(["spread_bps", "ofi", "rsi_14"])
    assert a == b
    assert a != registry.set_hash(["ofi", "rsi_14"])


def test_duplicate_name_with_new_version_is_refused():
    from app.features.registry import FeatureRegistry, FeatureSpec

    r = FeatureRegistry()
    r.register(FeatureSpec("x", "1.0", Horizon.DAILY, "d"))
    r.register(FeatureSpec("x", "1.0", Horizon.DAILY, "d"))       # same, fine
    with pytest.raises(ValueError, match="already registered"):
        r.register(FeatureSpec("x", "2.0", Horizon.DAILY, "d"))


def test_budget_warns_when_daily_set_is_too_large():
    """Two years of daily bars supports ~10 features, not 50."""
    daily = [s.name for s in registry.by_horizon(Horizon.DAILY)]
    assert registry.check_budget(daily) == []        # the catalogue is sane

    from app.features.registry import FeatureRegistry, FeatureSpec
    r = FeatureRegistry()
    names = []
    for i in range(15):
        r.register(FeatureSpec(f"f{i}", "1.0", Horizon.DAILY, "d"))
        names.append(f"f{i}")
    problems = r.check_budget(names)
    assert problems and "overfitting" in problems[0]


def test_max_warmup_is_the_longest_in_the_set():
    assert registry.max_warmup(["ofi", "momentum_12_1"]) == 252


def test_unknown_feature_raises():
    with pytest.raises(KeyError):
        registry.get("does_not_exist")


# ======================================================= gate 3: speed
def test_updates_are_fast_enough_for_the_feed():
    """Target from docs/06-performance.md: 40,000 feature updates/second."""
    state = FeatureState(instrument_key="BENCH")
    n = 20_000
    random.seed(5)
    ticks = [(1_760_000_000_000 + i * 10,
              100 + random.gauss(0, 1),
              random.uniform(10, 100)) for i in range(n)]

    t0 = time.perf_counter()
    for ts, price, qty in ticks:
        state.update_tick(ts_ms=ts, ltp=price,
                          bid_price=price - 0.05, bid_qty=qty,
                          ask_price=price + 0.05, ask_qty=qty,
                          cumulative_volume=0.0)
    elapsed = time.perf_counter() - t0

    rate = n / elapsed
    assert rate > 40_000, f"only {rate:,.0f} updates/sec -- feed would stall"


def test_memory_does_not_grow_with_tick_count():
    """Every primitive is bounded, so state size must stay flat."""
    state = FeatureState(instrument_key="X")
    for i in range(5_000):
        state.update_tick(ts_ms=1000 + i, ltp=100.0,
                          bid_price=99.9, bid_qty=10, ask_price=100.1, ask_qty=10)
    assert state.ofi.buf.count <= state.ofi.window
    assert state.trade_sign.buf.count <= 50
    assert state.volume_window.count <= 20
