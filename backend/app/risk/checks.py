"""Pre-trade filters: the reasons not to take an otherwise good trade.

Each one is a pure function returning a `Check`, so the whole set is
testable without a broker, a feed or a clock. That matters more here than
anywhere else in the system: these are the rules nobody will exercise by
hand before they matter.

The filters divide into two kinds, and the distinction is what makes exits
safe. A **blocking** check stops a new position. A check marked
`blocks_exit=False` -- all of them -- never stops a closing order. Being
unable to leave a position is worse than any condition these filters
detect.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Any

from .. import config
from ..clock import is_open, now_ist

# Equity session, from clock.py. Repeated rather than imported as a constant
# because these two are specifically about entries, not about the session.
MARKET_OPEN = time(9, 15)


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


def ok(name: str, detail: str = "") -> Check:
    return Check(name, True, detail)


def fail(name: str, detail: str) -> Check:
    return Check(name, False, detail)


# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------
def market_hours(at=None, segment: str = "equity") -> Check:
    at = at or now_ist()
    if not is_open(segment, at):
        return fail("market_hours", f"{segment} market is closed at "
                                    f"{at.strftime('%H:%M')} IST")
    return ok("market_hours")


def opening_window(at=None, minutes: int | None = None) -> Check:
    """No new entries in the first minutes of the session.

    The opening auction leaves spreads wide and prices that have not yet
    agreed with each other. Entering into that pays a spread for a price
    which is not information.
    """
    minutes = config.NO_ENTRY_FIRST_MINUTES if minutes is None else minutes
    if minutes <= 0:
        return ok("opening_window", "disabled")

    at = at or now_ist()
    since_open = ((at.hour * 60 + at.minute)
                  - (MARKET_OPEN.hour * 60 + MARKET_OPEN.minute))
    if 0 <= since_open < minutes:
        return fail("opening_window",
                    f"first {minutes} min of the session -- {since_open} min in")
    return ok("opening_window")


def square_off_window(at=None) -> Check:
    """No new intraday entries once square-off time has passed."""
    at = at or now_ist()
    cutoff = config.SQUARE_OFF_HOUR * 60 + config.SQUARE_OFF_MINUTE
    # Minutes since midnight, compared as plain integers. `at` is already
    # IST, so attaching a timezone to a bare time to compare it would only
    # add a way to get it wrong.
    if at.hour * 60 + at.minute >= cutoff:
        return fail("square_off_window",
                    f"past {config.SQUARE_OFF_HOUR:02d}:"
                    f"{config.SQUARE_OFF_MINUTE:02d} IST -- exits only")
    return ok("square_off_window")


# --------------------------------------------------------------------------
# Data quality
# --------------------------------------------------------------------------
def tick_fresh(tick_ts_ms: float | None, at=None,
               max_age: float | None = None) -> Check:
    """A quote older than a few seconds is not a quote.

    Sizing and limit prices are both computed from the last price. If that
    price is thirty seconds old the order is being placed into a market
    that has moved, and the first sign of it will be the fill.
    """
    max_age = config.MAX_TICK_AGE_SECONDS if max_age is None else max_age
    if not tick_ts_ms:
        return fail("tick_fresh", "no live tick for this instrument")

    now_ms = (at or now_ist()).timestamp() * 1000
    age = (now_ms - float(tick_ts_ms)) / 1000
    if age > max_age:
        return fail("tick_fresh", f"last tick {age:.1f}s old (limit {max_age}s)")
    # A tick from the future means a clock disagreement somewhere, and the
    # age check is meaningless until it is understood.
    if age < -5:
        return fail("tick_fresh", f"tick is {-age:.1f}s in the future -- clock mismatch")
    return ok("tick_fresh", f"{age:.1f}s old")


def spread_acceptable(bid: float, ask: float, ltp: float,
                      max_pct: float | None = None) -> Check:
    """A wide spread is a cost paid twice, on the way in and on the way out."""
    max_pct = config.MAX_SPREAD_PCT if max_pct is None else max_pct
    if bid <= 0 or ask <= 0:
        return fail("spread", "no two-sided quote")
    if ask < bid:
        return fail("spread", f"crossed book: bid {bid} above ask {ask}")

    reference = ltp or (bid + ask) / 2
    if reference <= 0:
        return fail("spread", "no reference price")
    pct = (ask - bid) / reference * 100
    if pct > max_pct:
        return fail("spread", f"spread {pct:.2f}% above the {max_pct}% limit")
    return ok("spread", f"{pct:.2f}%")


def liquid_enough(day_volume: float, min_volume: int | None = None) -> Check:
    """Thin names fill badly and are hard to leave in a hurry."""
    min_volume = config.MIN_DAILY_VOLUME if min_volume is None else min_volume
    if day_volume < min_volume:
        return fail("liquidity",
                    f"volume {day_volume:,.0f} below the {min_volume:,} minimum")
    return ok("liquidity", f"{day_volume:,.0f}")


def not_in_circuit(ltp: float, upper: float = 0.0, lower: float = 0.0) -> Check:
    """At a circuit limit there is no two-sided market to trade against."""
    if upper and ltp >= upper:
        return fail("circuit", f"at upper circuit {upper}")
    if lower and ltp <= lower:
        return fail("circuit", f"at lower circuit {lower}")
    return ok("circuit")


# --------------------------------------------------------------------------
# Event and edge
# --------------------------------------------------------------------------
def not_in_blackout(symbol: str, blackout_symbols: list[str]) -> Check:
    """No new position in a name with a fresh filing.

    Deliberately indifferent to whether the filing looked material. By the
    time we can read a headline the move has happened; the useful thing
    news gives us is a list of names to stay out of, not a direction.
    """
    if symbol.upper() in {s.upper() for s in blackout_symbols}:
        return fail("event_blackout", f"fresh filing on {symbol}")
    return ok("event_blackout")


def round_trip_cost(price: float, quantity: int) -> float:
    """Brokerage, taxes and slippage for getting in and out.

    The same numbers the backtester charges, so a trade the backtest called
    profitable is not one live trading loses on quietly.
    """
    value = price * quantity
    brokerage = config.BROKERAGE_PER_ORDER * 2
    taxes = value * config.TAXES_PCT / 100
    slippage = value * config.SLIPPAGE_PCT / 100 * 2
    return brokerage + taxes + slippage


def has_edge(*, price: float, quantity: int, expected_move_pct: float,
             probability: float) -> Check:
    """Expected gain must beat the cost of trading. Usually it does not.

    This is the filter that removes the most trades, and it should. A 0.3%
    expected move on a liquid stock is roughly the round trip, so half the
    signals a model produces are not trades at all -- they are a way of
    paying brokerage to express an opinion.
    """
    if price <= 0 or quantity <= 0:
        return fail("edge", "no price or quantity to evaluate")

    value = price * quantity
    expected_gain = value * (expected_move_pct / 100) * max(0.0, min(1.0, probability))
    cost = round_trip_cost(price, quantity)
    edge = expected_gain - cost

    if edge <= 0:
        return fail("edge",
                    f"expected Rs {expected_gain:,.0f} vs cost Rs {cost:,.0f} "
                    f"-- no edge")
    return ok("edge", f"Rs {edge:,.0f} after Rs {cost:,.0f} of cost")


# --------------------------------------------------------------------------
# Running the set
# --------------------------------------------------------------------------
def failures(checks: list[Check]) -> list[Check]:
    return [c for c in checks if not c.passed]


def summarise(checks: list[Check]) -> dict[str, Any]:
    bad = failures(checks)
    return {
        "passed": not bad,
        "checks": [c.as_dict() for c in checks],
        "failed": [c.name for c in bad],
        "reasons": [f"{c.name}: {c.detail}" for c in bad],
    }
