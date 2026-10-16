"""Phase 1 guards: LIMIT conversion, tick rounding, idempotency, rate limits.

Run:  .venv\\Scripts\\python.exe -m pytest tests -q
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import config, ratelimit  # noqa: E402
from app.upstox import orders  # noqa: E402

KEY = "NSE_EQ|INE002A01018"       # RELIANCE


def req(**kw):
    base = dict(instrument_key=KEY, side="BUY", quantity=10, source="test")
    base.update(kw)
    return orders.OrderRequest(**base)


# ------------------------------------------------- MARKET -> LIMIT
def test_market_becomes_limit_on_the_wire():
    """Upstox stopped accepting MARKET over the API on 1 April 2026."""
    built = orders.build(req(order_type="MARKET"), ltp=1000.0)
    assert built.payload["order_type"] == "LIMIT"
    assert built.converted_from == "MARKET"


def test_buy_market_limit_is_above_ltp():
    """A marketable buy prices through the offer, or it will not fill."""
    built = orders.build(req(side="BUY", order_type="MARKET"), ltp=1000.0)
    assert built.limit_price > 1000.0
    assert built.limit_price <= 1000.0 * 1.01       # band is small


def test_sell_market_limit_is_below_ltp():
    built = orders.build(req(side="SELL", order_type="MARKET"), ltp=1000.0)
    assert built.limit_price < 1000.0


def test_sl_m_becomes_sl():
    built = orders.build(
        req(order_type="SL-M", trigger_price=990.0), ltp=1000.0)
    assert built.payload["order_type"] == "SL"
    assert built.converted_from == "SL-M"


def test_market_without_price_is_rejected():
    """No live price means no safe way to pick a limit."""
    with pytest.raises(orders.OrderRejected, match="live price"):
        orders.build(req(order_type="MARKET"), ltp=0.0)


# ------------------------------------------------------ tick rounding
def test_rounding_keeps_order_marketable():
    """Buy rounds up, sell rounds down -- the other way leaves it outside."""
    assert orders.round_to_tick(100.023, 0.05, "BUY") == 100.05
    assert orders.round_to_tick(100.023, 0.05, "SELL") == 100.00


def test_limit_price_lands_on_a_tick():
    built = orders.build(req(order_type="LIMIT", price=1234.567), ltp=1234.0)
    steps = built.limit_price / orders.tick_size_for(KEY)
    assert abs(steps - round(steps)) < 1e-6


# ------------------------------------------------------ safety guards
def test_order_value_cap():
    qty = int(config.MAX_ORDER_VALUE / 1000) + 50       # comfortably over
    with pytest.raises(orders.OrderRejected, match="Order value"):
        orders.build(req(quantity=qty, order_type="MARKET"), ltp=1000.0)


def test_price_far_from_ltp_is_rejected():
    """Catches stale quotes and fat fingers before the exchange sees them."""
    with pytest.raises(orders.OrderRejected, match="door hai"):
        orders.build(req(order_type="LIMIT", price=2000.0), ltp=1000.0)


def test_zero_quantity_rejected():
    with pytest.raises(orders.OrderRejected, match="Quantity"):
        orders.build(req(quantity=0, order_type="MARKET"), ltp=1000.0)


def test_bad_side_rejected():
    with pytest.raises(orders.OrderRejected, match="Side"):
        orders.build(req(side="HOLD", order_type="MARKET"), ltp=1000.0)


def test_limit_without_price_rejected():
    with pytest.raises(orders.OrderRejected, match="LIMIT order"):
        orders.build(req(order_type="LIMIT", price=0), ltp=1000.0)


# -------------------------------------------------------- idempotency
def test_same_intent_gives_same_tag():
    """A retry after a timeout must not create a second position."""
    a = orders.idempotency_key(instrument_key=KEY, side="BUY", quantity=10, source="bot1")
    b = orders.idempotency_key(instrument_key=KEY, side="BUY", quantity=10, source="bot1")
    assert a == b


def test_different_intent_gives_different_tag():
    a = orders.idempotency_key(instrument_key=KEY, side="BUY", quantity=10, source="bot1")
    b = orders.idempotency_key(instrument_key=KEY, side="BUY", quantity=11, source="bot1")
    c = orders.idempotency_key(instrument_key=KEY, side="SELL", quantity=10, source="bot1")
    assert len({a, b, c}) == 3


def test_tag_fits_upstox_limit():
    tag = orders.idempotency_key(
        instrument_key=KEY, side="BUY", quantity=999999, source="a-very-long-source-name")
    assert len(tag) <= 20


# ------------------------------------------------------- algo tagging
def test_algo_header_present():
    assert orders.algo_headers()["X-Algo-Name"] == config.ALGO_NAME


# -------------------------------------------------------- rate limits
def test_rate_limiter_enforces_per_second():
    """Burst of 12 at 5/sec must take at least ~1.4s."""
    async def run():
        bucket = ratelimit.TokenBucket(rate=5.0, capacity=5.0, name="t")
        t0 = time.monotonic()
        for _ in range(12):
            await bucket.acquire()
        return time.monotonic() - t0

    elapsed = asyncio.run(run())
    assert elapsed >= 1.3, f"12 orders at 5/sec took only {elapsed:.2f}s"


def test_rate_limiter_can_refuse_instead_of_waiting():
    async def run():
        bucket = ratelimit.TokenBucket(rate=1.0, capacity=1.0, name="t")
        await bucket.acquire()
        with pytest.raises(ratelimit.RateLimitExceeded):
            await bucket.acquire(wait=False)

    asyncio.run(run())


def test_daily_cap_blocks_when_reached():
    counter = ratelimit.DailyCounter(limit=3, name="t")
    for _ in range(3):
        counter.check()
        counter.record()
    with pytest.raises(ratelimit.RateLimitExceeded, match="limit"):
        counter.check()


def test_configured_rate_is_under_sebi_limit():
    """SEBI treats <10 orders/sec as low-frequency. Stay under it."""
    assert config.MAX_ORDERS_PER_SECOND < 10
    assert not config.validate(), config.validate()


# ------------------------------------------------- live-mode strictness
def test_live_mode_needs_a_reference_price(monkeypatch):
    """Paper mode tolerates a missing LTP; real money does not."""
    monkeypatch.setattr(config, "PAPER_TRADING", False)
    with pytest.raises(orders.OrderRejected, match="verify nahi ho sakti"):
        orders.build(req(order_type="LIMIT", price=50.0), ltp=0.0)


def test_paper_mode_allows_missing_reference_price(monkeypatch):
    monkeypatch.setattr(config, "PAPER_TRADING", True)
    built = orders.build(req(order_type="LIMIT", price=50.0), ltp=0.0)
    assert built.limit_price == 50.0


def test_order_value_cap_with_explicit_price():
    """The fat-finger guard fires on value, not on a missing quote."""
    qty = int(config.MAX_ORDER_VALUE / 1000) + 50
    with pytest.raises(orders.OrderRejected, match="Order value"):
        orders.build(req(quantity=qty, order_type="LIMIT", price=1000.0), ltp=1000.0)


def test_peak_order_rate_stays_under_exchange_limit():
    """A bucket emits its burst and keeps refilling: peak = burst + rate."""
    peak = config.MAX_ORDER_BURST + config.MAX_ORDERS_PER_SECOND
    assert peak <= config.ABSOLUTE_MAX_ORDERS_PER_SECOND
    assert peak < 10, f"peak {peak}/sec would breach the SEBI low-frequency limit"


def test_shared_order_bucket_is_configured_safely():
    assert ratelimit.orders.capacity == config.MAX_ORDER_BURST
    assert ratelimit.orders.capacity < ratelimit.orders.rate + 10
