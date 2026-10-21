"""Algo bots, now that every entry goes through the risk engine.

Before this, a bot called `place_order` directly with a fixed quantity, so
position sizing, exposure limits, the edge test and the news blackout never
applied to it. Only the kill switch did, and only because that sits on the
order path.

Two behaviour changes come with the fix and both are tested here, because
both will surprise someone: a bot will not start without a stop, and the
stop is now a real order at the broker rather than a comparison inside the
loop that stopped existing when the process died.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from datetime import datetime  # noqa: E402

from app import config  # noqa: E402
from app.clock import IST  # noqa: E402
from app.engine import runner  # noqa: E402
from app.execution import tracker  # noqa: E402


def run(coro):
    return asyncio.run(coro)


INFY = "NSE_EQ|INE009A01021"

# Mid-session on a Friday. Pinned because the entry checks include the
# market window -- without this the tests only pass while the market
# happens to be open, which is most of the time they are not run.
OPEN_TIME = datetime(2026, 10, 16, 11, 0, tzinfo=IST)


@pytest.fixture
def clean(tmp_path, monkeypatch):
    from app import db
    from app.forward import journal
    from app.risk import killswitch
    from app.risk import state as risk_state
    from app.store import agent_outputs, audit, events

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "_local", type("L", (), {})())
    db.conn().executescript(db.SCHEMA)
    stores = (tracker, risk_state, killswitch, audit, events, agent_outputs,
              journal)
    for store in stores:
        store._ready = False

    monkeypatch.setattr(config, "PAPER_TRADING", True)
    monkeypatch.setattr(config, "TRADING_CAPITAL", 1_000_000.0)

    async def quote(keys):
        return {k: {"ltp": 1500.0} for k in keys}

    monkeypatch.setattr("app.upstox.marketdata.ltp", quote)

    async def no_positions():
        return []

    monkeypatch.setattr("app.upstox.trading.positions", no_positions)

    # A live tick for the instrument. Without one the risk engine refuses
    # every entry -- correctly, since sizing and the limit price are both
    # read off the last price. It also means a bot now needs its
    # instrument subscribed on the feed, which it never used to.
    from app.engine import feed_service
    from app.upstox.ticks import DepthLevel, Tick

    # Stamped at the pinned decision time: a tick from a different day
    # reads as a clock mismatch, which the freshness check refuses -- and
    # refusing it is correct behaviour, just not what these tests are for.
    tick = Tick(instrument_key=INFY, ts=int(OPEN_TIME.timestamp() * 1000),
                ltp=1500.0, close_price=1480.0, volume=5_000_000,
                depth=[DepthLevel(bid_qty=200, bid_price=1499.5,
                                  ask_qty=200, ask_price=1500.5)])
    monkeypatch.setattr(feed_service, "_latest", {INFY: tick})

    runner._bots.clear()
    yield
    for store in stores:
        store._ready = False
    runner._bots.clear()


def cfg(**kw) -> runner.BotConfig:
    base = dict(strategy="ema_crossover", instrument_key=INFY, quantity=50,
                stop_loss_pct=2.0, target_pct=3.0, product="I")
    base.update(kw)
    return runner.BotConfig(**base)


def state(**kw) -> runner.BotState:
    from app import db

    run_id = db.execute(
        """INSERT INTO bot_runs (started_at, strategy, params, instrument_key,
                                 symbol, mode, status)
           VALUES (?,?,?,?,?,?,?)""",
        (db.now(), "ema_crossover", "{}", INFY, "INFY", "paper", "running"))
    return runner.BotState(run_id=run_id, cfg=cfg(**kw), symbol="INFY",
                           status="running")


# ==========================================================================
# Starting
# ==========================================================================
def test_a_bot_without_a_stop_will_not_start(clean):
    """Sizing comes from the stop distance, so without one there is
    nothing to size against. Refused at start rather than at the first
    signal, where it would look like the strategy never fired."""
    with pytest.raises(ValueError) as exc:
        run(runner.start(cfg(stop_loss_pct=0)))
    assert "Stop-loss" in str(exc.value)


def test_a_bot_will_not_start_without_capital(clean, monkeypatch):
    monkeypatch.setattr(config, "TRADING_CAPITAL", 0.0)
    with pytest.raises(ValueError) as exc:
        run(runner.start(cfg()))
    assert "TRADING_CAPITAL" in str(exc.value)


def test_a_properly_configured_bot_starts(clean):
    async def go():
        st = await runner.start(cfg())
        await runner.stop(st.run_id)
        return st

    st = run(go())
    assert st.status in ("running", "stopped")


# ==========================================================================
# Entries go through the risk engine
# ==========================================================================
def test_an_entry_is_sized_by_the_risk_engine_not_the_config(clean):
    """The config asked for 50; the engine decides what is actually safe."""
    st = state(quantity=10_000)
    run(runner._enter(st, 1500.0, at=OPEN_TIME))
    assert 0 < st.position_qty < 10_000
    assert st.entry_order_id is not None


def test_the_config_quantity_is_a_ceiling(clean):
    """Asking for less than the engine allows is tightening, which is
    always permitted. Asking for more is not."""
    st = state(quantity=3)
    run(runner._enter(st, 1500.0, at=OPEN_TIME))
    assert st.position_qty == 3


def test_a_rejected_entry_places_nothing_and_says_why(clean, monkeypatch):
    """"Why did it not trade" has to be answerable from the bot log."""
    sent = []

    async def spy(**kw):
        sent.append(kw)
        return {"status": "complete", "order_id": "X", "price": 1500.0}

    monkeypatch.setattr("app.upstox.trading.place_order", spy)
    monkeypatch.setattr(config, "MAX_OPEN_POSITIONS", 0)

    st = state()
    run(runner._enter(st, 1500.0, at=OPEN_TIME))
    assert st.position_qty == 0
    assert sent == []
    assert st.last_rejection
    assert any("rok di" in r["message"] for r in runner.logs(st.run_id))


def test_a_halted_system_stops_a_bot_entering(clean):
    from app.risk import killswitch

    killswitch.stop_all(by="test")
    st = state()
    run(runner._enter(st, 1500.0, at=OPEN_TIME))
    assert st.position_qty == 0
    assert "halted" in (st.last_rejection or "")


def test_a_blackout_stops_a_bot_entering(clean):
    """A filing on the name now blocks a bot, which it never used to."""
    from app.store import events

    events.save([events.Event("nse", "filing", "INFY: Board Meeting",
                              symbols=["INFY"])])
    st = state()
    run(runner._enter(st, 1500.0, at=OPEN_TIME))
    assert st.position_qty == 0
    assert "blackout" in (st.last_rejection or "")


def test_an_entry_gets_a_real_stop_at_the_broker(clean):
    """It used to be a comparison inside the loop, which meant it did not
    exist if the process died -- exactly when it mattered most."""
    st = state()
    run(runner._enter(st, 1500.0, at=OPEN_TIME))

    stops = tracker.children(st.entry_order_id, role="stop")
    assert len(stops) == 1
    assert stops[0]["trigger_price"] == pytest.approx(1500.0 * 0.98)
    assert tracker.filled_entries_without_stops() == []


def test_the_entry_is_logged_with_what_bound_the_size(clean):
    """"Why is this position so small" is otherwise unanswerable later."""
    st = state()
    run(runner._enter(st, 1500.0, at=OPEN_TIME))
    messages = " ".join(r["message"] for r in runner.logs(st.run_id))
    assert "sized by" in messages


# ==========================================================================
# Exits
# ==========================================================================
def test_an_exit_closes_the_position_and_cancels_the_stop(clean):
    """Leaving the stop working would sell a position we no longer hold."""
    st = state()
    run(runner._enter(st, 1500.0, at=OPEN_TIME))
    entry_id = st.entry_order_id

    run(runner._exit(st, "target hit"))
    assert st.position_qty == 0
    assert st.entry_order_id is None
    stops = tracker.children(entry_id, role="stop")
    assert stops[0]["status"] == "cancelled"


def test_an_exit_books_its_pnl(clean, monkeypatch):
    from app.risk import state as risk_state

    st = state()
    run(runner._enter(st, 1500.0, at=OPEN_TIME))

    # Exit above the entry.
    async def higher(keys):
        return {k: {"ltp": 1600.0} for k in keys}

    monkeypatch.setattr("app.upstox.marketdata.ltp", higher)
    run(runner._exit(st, "target hit"))
    assert risk_state._today_row()["trades"] >= 1


def test_a_failed_exit_does_not_forget_the_position(clean, monkeypatch):
    """A bot that forgets it holds something will re-enter on top of it."""
    st = state()
    run(runner._enter(st, 1500.0, at=OPEN_TIME))

    async def refuse(**kw):
        raise RuntimeError("exchange down")

    monkeypatch.setattr("app.upstox.trading.place_order", refuse)
    run(runner._exit(st, "target hit"))
    assert st.position_qty > 0
    assert any("abhi" in r["message"] for r in runner.logs(st.run_id))


def test_an_exit_with_no_position_does_nothing(clean):
    st = state()
    run(runner._exit(st, "nothing to do"))
    assert st.position_qty == 0


# ==========================================================================
# The broker's stop filling behind the bot's back
# ==========================================================================
def test_a_filled_stop_is_noticed(clean):
    """Otherwise the bot holds a phantom position and never trades again."""
    st = state()
    run(runner._enter(st, 1500.0, at=OPEN_TIME))
    stop = tracker.children(st.entry_order_id, role="stop")[0]
    tracker.update(stop["id"], filled_quantity=st.position_qty,
                   avg_fill_price=1470.0, status="complete")

    assert runner._stop_filled(st) is True


def test_an_unfilled_stop_is_not_mistaken_for_a_filled_one(clean):
    st = state()
    run(runner._enter(st, 1500.0, at=OPEN_TIME))
    assert runner._stop_filled(st) is False


def test_an_unreadable_tracker_does_not_claim_the_stop_filled(clean, monkeypatch):
    """Saying it filled would make the bot re-enter while still holding."""
    st = state()
    run(runner._enter(st, 1500.0, at=OPEN_TIME))

    def explode(*a, **kw):
        raise RuntimeError("db gone")

    monkeypatch.setattr(tracker, "children", explode)
    assert runner._stop_filled(st) is False


def test_a_bot_with_no_entry_has_no_stop_to_check(clean):
    assert runner._stop_filled(state()) is False
