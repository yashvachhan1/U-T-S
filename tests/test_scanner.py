"""The scanner: where signals come from, and what it is not allowed to do.

Before this existed, `decide()` could only be reached from two API routes
a human had to call, so a paper run left alone for two months would have
recorded zero trades and proved nothing. That is the gap being closed.

What matters most here is the boundary. The scanner produces proposals; it
has no path to an order that does not go through the risk engine. The old
bot runner calls `place_order` directly and therefore skips sizing,
exposure limits, the edge test and the blackout list -- these tests are
what stop this path from drifting into the same shape.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config  # noqa: E402
from app.clock import now_ist  # noqa: E402
from app.decision import scanner  # noqa: E402


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def clean(tmp_path, monkeypatch):
    from app import db
    from app.execution import tracker
    from app.forward import journal
    from app.risk import killswitch
    from app.risk import state as risk_state
    from app.store import agent_outputs, audit, events

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "_local", type("L", (), {})())
    stores = (tracker, risk_state, killswitch, audit, events, agent_outputs,
              journal)
    for store in stores:
        store._ready = False
    scanner._reset_for_tests()
    yield
    for store in stores:
        store._ready = False
    scanner._reset_for_tests()


def candles(n: int = 80, *, trend: str = "up"):
    """Candles whose crossover lands on the LAST bar.

    A crossover strategy fires on the bar where the EMAs cross and is
    silent afterwards, so the turn has to be placed so that the final
    candle is the crossing one -- otherwise the data is a perfectly good
    uptrend that produces no signal, which is correct behaviour and a
    useless fixture.
    """
    base = now_ist() - timedelta(minutes=5 * n)
    turn = n - 2                       # found empirically: cross lands on the last bar
    rows = []
    price = 100.0
    for i in range(n):
        if i >= turn:
            price += 1.2 if trend == "up" else -1.2
        else:
            price -= 0.05 if trend == "up" else -0.05
        ts = (base + timedelta(minutes=5 * i)).isoformat(timespec="seconds")
        rows.append({"ts": ts, "open": price - 0.3, "high": price + 0.5,
                     "low": price - 0.6, "close": price, "volume": 100000})
    return rows


# ==========================================================================
# Reading a signal
# ==========================================================================
def test_a_trend_produces_a_buy():
    verdict, detail = scanner.signal_from_candles(candles(), "ema_crossover")
    assert verdict == "BUY"
    assert detail["atr"] and detail["atr"] > 0


def test_too_few_candles_produces_nothing():
    """Below the warm-up the indicators are artefacts, not signals."""
    verdict, detail = scanner.signal_from_candles(candles(10), "ema_crossover")
    assert verdict is None
    assert "candles" in detail["reason"]


def test_the_same_strategy_code_as_the_backtester():
    """Live and backtested behaviour drift apart the moment these differ."""
    from app.strategies import base as strat

    rows = candles()
    direct = strat.get("ema_crossover").run(rows, {})
    verdict, _ = scanner.signal_from_candles(rows, "ema_crossover")
    assert verdict == direct[-1]


# ==========================================================================
# Freshness and repeats
# ==========================================================================
def test_a_stale_candle_is_not_acted_on():
    """An old candle means the scan fell behind, and acting on it is
    acting on a price that has moved."""
    old = (now_ist() - timedelta(hours=3)).isoformat(timespec="seconds")
    assert scanner.candle_is_fresh(old) is False


def test_a_recent_candle_is_acted_on():
    recent = (now_ist() - timedelta(minutes=2)).isoformat(timespec="seconds")
    assert scanner.candle_is_fresh(recent) is True


def test_a_candle_from_the_future_is_refused():
    ahead = (now_ist() + timedelta(minutes=30)).isoformat(timespec="seconds")
    assert scanner.candle_is_fresh(ahead) is False


def test_an_unreadable_timestamp_is_refused():
    assert scanner.candle_is_fresh(None) is False
    assert scanner.candle_is_fresh("not a date") is False


def test_the_same_candle_does_not_fire_twice(clean, monkeypatch):
    """The loop runs every fifteen seconds; a signal is a property of a
    closed candle, so re-firing it would send the same trade repeatedly."""
    rows = candles()
    monkeypatch.setattr(scanner, "watchlist", lambda: ["NSE_EQ|X"])

    async def fake_candles(key, interval, days=30):
        return rows + [rows[-1]]      # last one is "still forming"

    monkeypatch.setattr("app.upstox.marketdata.historical", fake_candles)

    first = run(scanner.scan())
    second = run(scanner.scan())
    assert len(first) == 1
    assert second == []


# ==========================================================================
# The boundary: no path to an order that skips the risk engine
# ==========================================================================
def test_scanning_alone_places_nothing(clean, monkeypatch):
    monkeypatch.setattr(scanner, "watchlist", lambda: ["NSE_EQ|X"])

    async def fake_candles(key, interval, days=30):
        return candles() + [candles()[-1]]

    monkeypatch.setattr("app.upstox.marketdata.historical", fake_candles)

    sent = []
    async def spy(**kw):
        sent.append(kw)
        return {"status": "complete", "order_id": "X"}

    monkeypatch.setattr("app.upstox.trading.place_order", spy)
    run(scanner.scan())
    assert sent == []


def test_the_scanner_is_off_until_switched_on(clean):
    """Automatic trading must not start as a side effect of the app
    starting."""
    assert scanner.status()["config"]["enabled"] is False
    assert run(scanner.run())["skipped"] == "scanner off"


def test_enabling_the_scan_does_not_enable_execution(clean):
    """Looking for signals and acting on them are different decisions."""
    scanner.configure(enabled=True)
    assert scanner.status()["config"]["auto_execute"] is False


def test_a_closed_market_is_skipped(clean, monkeypatch):
    scanner.configure(enabled=True)
    monkeypatch.setattr(scanner, "is_open", lambda seg: False)
    assert run(scanner.run())["skipped"] == "market closed"


def test_every_signal_goes_through_the_risk_engine(clean, monkeypatch):
    """The whole reason this module exists rather than calling the broker."""
    scanner.configure(enabled=True, auto_execute=True)
    monkeypatch.setattr(scanner, "is_open", lambda seg: True)
    monkeypatch.setattr(scanner, "watchlist", lambda: ["NSE_EQ|X"])

    async def fake_candles(key, interval, days=30):
        return candles() + [candles()[-1]]

    monkeypatch.setattr("app.upstox.marketdata.historical", fake_candles)

    seen = []
    from app.decision import engine as dec_engine

    real = dec_engine.evaluate

    def spy(proposal, book, at=None):
        seen.append(proposal)
        return real(proposal, book, at)

    monkeypatch.setattr("app.risk.engine.evaluate", spy)
    monkeypatch.setattr("app.decision.engine.evaluate", spy)

    out = run(scanner.run())
    assert out["signals"] == 1
    assert len(seen) == 1              # it went through the engine


def test_a_halt_stops_the_scanner_from_trading(clean, monkeypatch):
    """Belt and braces: the engine refuses, and the order path refuses."""
    from app.risk import killswitch

    scanner.configure(enabled=True, auto_execute=True)
    killswitch.stop_all(by="test")
    monkeypatch.setattr(scanner, "is_open", lambda seg: True)
    monkeypatch.setattr(scanner, "watchlist", lambda: ["NSE_EQ|X"])

    async def fake_candles(key, interval, days=30):
        return candles() + [candles()[-1]]

    monkeypatch.setattr("app.upstox.marketdata.historical", fake_candles)

    out = run(scanner.run())
    assert out["approved"] == 0
    assert out["executed"] == 0


def test_rejections_are_reported_with_their_reasons(clean, monkeypatch):
    """"Why did it not trade" is the question asked every quiet day."""
    scanner.configure(enabled=True)
    monkeypatch.setattr(scanner, "is_open", lambda seg: True)
    monkeypatch.setattr(scanner, "watchlist", lambda: ["NSE_EQ|X"])

    async def fake_candles(key, interval, days=30):
        return candles() + [candles()[-1]]

    monkeypatch.setattr("app.upstox.marketdata.historical", fake_candles)

    out = run(scanner.run())
    assert out["signals"] == 1
    assert out["approved"] == 0        # no capital configured in tests
    assert out["rejected"]
    assert out["rejected"][0]["reasons"]


# ==========================================================================
# Robustness
# ==========================================================================
def test_one_unreadable_instrument_does_not_stop_the_scan(clean, monkeypatch):
    monkeypatch.setattr(scanner, "watchlist", lambda: ["NSE_EQ|BAD", "NSE_EQ|GOOD"])

    async def fake_candles(key, interval, days=30):
        if key.endswith("BAD"):
            raise RuntimeError("no data")
        return candles() + [candles()[-1]]

    monkeypatch.setattr("app.upstox.marketdata.historical", fake_candles)

    out = run(scanner.scan())
    assert len(out) == 1
    assert out[0].instrument_key == "NSE_EQ|GOOD"
    assert "BAD" in scanner.status()["stats"]["last_error"]


def test_the_status_reports_symbols_not_just_keys(clean, monkeypatch):
    """An equity key is an ISIN. "INE002A01018" tells a reader nothing
    about which stock the scanner is watching."""
    monkeypatch.setattr(scanner, "watchlist",
                        lambda: ["NSE_EQ|INE009A01021"])
    out = scanner.status()
    assert out["watchlist_symbols"] == ["INFY"]


def test_the_watchlist_is_capped(clean, monkeypatch):
    """Caps the damage if someone adds two hundred names."""
    from app import db

    scanner.configure(instruments=[f"NSE_EQ|X{i}" for i in range(200)])
    assert len(scanner.watchlist()) == scanner.MAX_INSTRUMENTS


def test_the_watchlist_falls_back_to_the_saved_one(clean):
    from app import db

    db.conn().executescript(db.SCHEMA)
    db.execute("INSERT INTO watchlist (instrument_key, symbol, added_at) "
               "VALUES (?,?,?)", ("NSE_EQ|SAVED", "SAVED", db.now()))
    assert scanner.watchlist() == ["NSE_EQ|SAVED"]


def test_a_stop_is_derived_from_volatility(clean, monkeypatch):
    """The risk engine refuses to size without one, so the scanner has to
    carry a stop or nothing it produces can ever trade."""
    monkeypatch.setattr(scanner, "watchlist", lambda: ["NSE_EQ|X"])

    async def fake_candles(key, interval, days=30):
        return candles() + [candles()[-1]]

    monkeypatch.setattr("app.upstox.marketdata.historical", fake_candles)
    signals = run(scanner.scan())
    assert signals[0].stop_price > 0
    assert signals[0].stop_price < signals[0].atr * 100   # sane magnitude


def test_a_sell_signal_does_not_open_a_short(clean, monkeypatch):
    """SELL means "exit a long" in this strategy vocabulary. Opening
    shorts is a separate decision and is not made here."""
    monkeypatch.setattr(scanner, "watchlist", lambda: ["NSE_EQ|X"])
    rows = candles(trend="down")

    async def fake_candles(key, interval, days=30):
        return rows + [rows[-1]]

    monkeypatch.setattr("app.upstox.marketdata.historical", fake_candles)
    assert run(scanner.scan()) == []


def test_config_survives_a_bad_key(clean):
    scanner.configure(enabled=True, nonsense="x")
    assert scanner.status()["config"]["enabled"] is True
    assert "nonsense" not in scanner.status()["config"]


def test_an_unknown_strategy_is_refused_at_config_time(clean):
    """The scan swallows its own errors so the loop survives, which means
    a bad strategy name would fail silently every cycle forever."""
    with pytest.raises(KeyError):
        scanner.configure(strategy="does_not_exist")
    assert scanner.status()["config"]["strategy"] == "ema_crossover"


def test_the_default_strategy_actually_exists(clean):
    """A default that does not resolve breaks every scan on a fresh
    install, where nobody has configured anything yet."""
    from app.strategies import base as strat

    strat.get(scanner.ScanConfig().strategy)


def test_the_scanner_does_not_persist_across_a_restart(clean):
    """A scanner that survives a restart is one that starts trading
    because the machine rebooted."""
    scanner.configure(enabled=True, auto_execute=True)
    scanner._reset_for_tests()         # stands in for a fresh process
    assert scanner.status()["config"]["enabled"] is False
