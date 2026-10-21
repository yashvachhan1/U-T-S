"""The decision layer, and the order path it is supposed to protect.

Two things are being checked. That a signal picks up the right context --
sector, stop, agent caution, the current book. And, more importantly, that
the kill switch sits on the **order path** rather than only on this one:
a manual order from the dashboard never goes near the decision engine, and
a kill switch that only stops automated trades is not a kill switch.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config, decision  # noqa: E402
from app.clock import IST  # noqa: E402
from app.decision import Signal  # noqa: E402
from app.decision.engine import sector_for  # noqa: E402
from app.risk import killswitch  # noqa: E402
from app.risk.engine import Book  # noqa: E402
from app.risk.state import Position  # noqa: E402
from app.store import audit  # noqa: E402


def run(coro):
    return asyncio.run(coro)


OPEN_TIME = datetime(2026, 10, 16, 11, 0, tzinfo=IST)
INFY = "NSE_EQ|INE009A01021"


@pytest.fixture
def clean_db(tmp_path, monkeypatch):
    from app import db
    from app.risk import state as risk_state
    from app.store import agent_outputs, events as events_store

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "_local", type("L", (), {})())
    stores = (risk_state, killswitch, audit, events_store, agent_outputs)
    for store in stores:
        store._ready = False
    yield
    for store in stores:
        store._ready = False


@pytest.fixture
def live_quote(monkeypatch):
    """A healthy tick for INFY, so the data-quality filters pass."""
    from app.engine import feed_service
    from app.upstox.ticks import DepthLevel, Tick

    tick = Tick(instrument_key=INFY, ts=int(OPEN_TIME.timestamp() * 1000),
                ltp=1500.0, close_price=1480.0, volume=5_000_000,
                depth=[DepthLevel(bid_qty=100, bid_price=1499.5,
                                  ask_qty=100, ask_price=1500.5)])
    monkeypatch.setattr(feed_service, "_latest", {INFY: tick})
    return tick


@pytest.fixture
def no_positions(monkeypatch):
    from app.upstox import trading

    async def none():
        return []

    monkeypatch.setattr(trading, "positions", none)


# --------------------------------------------------------------------------
# Building the proposal
# --------------------------------------------------------------------------
def test_the_sector_comes_from_nses_own_industry_column():
    sector, industry = sector_for("INFY")
    assert industry == "INFORMATION_TECHNOLOGY"
    assert sector == "IT"


def test_an_unmapped_industry_keeps_its_own_name():
    """Better an unmapped label than one forced into the wrong bucket --
    the sector limits act on these names."""
    sector, industry = sector_for("UNKNOWNSYM", industry="FORESTRY")
    assert sector == "FORESTRY"


def test_a_proposal_picks_up_the_live_quote(live_quote):
    proposal = decision.propose(Signal(instrument_key=INFY, source="test"))
    assert proposal.symbol == "INFY"
    assert proposal.entry_price == 1500.0
    assert proposal.bid == 1499.5
    assert proposal.day_volume == 5_000_000
    assert proposal.sector == "IT"


def test_a_missing_stop_is_computed_from_volatility(live_quote):
    """A fixed percentage stop is too tight on a volatile name and too
    loose on a calm one."""
    proposal = decision.propose(Signal(instrument_key=INFY, atr=20.0))
    assert proposal.stop_price == pytest.approx(1500 - 2 * 20)


def test_an_explicit_stop_is_left_alone(live_quote):
    proposal = decision.propose(Signal(instrument_key=INFY, atr=20.0,
                                       stop_price=1450.0))
    assert proposal.stop_price == 1450.0


def test_no_quote_means_no_entry_price(live_quote, monkeypatch):
    from app.engine import feed_service

    monkeypatch.setattr(feed_service, "_latest", {})
    proposal = decision.propose(Signal(instrument_key=INFY, atr=20.0))
    assert proposal.entry_price == 0.0
    assert proposal.stop_price == 0.0      # nothing to compute it from


# --------------------------------------------------------------------------
# The agent multiplier
# --------------------------------------------------------------------------
def test_no_agent_view_is_worth_half_size_not_full(clean_db):
    """Absence of a view is not permission to trade at full size."""
    mult, _ = decision.agent_multiplier()
    assert mult <= 0.5


def test_a_broken_agent_layer_still_reduces(clean_db, monkeypatch):
    from app.agents import synthesis

    def explode():
        raise RuntimeError("store is on fire")

    monkeypatch.setattr(synthesis, "current", explode)
    mult, detail = decision.agent_multiplier()
    assert mult == 0.5
    assert "error" in detail


def test_the_agent_multiplier_is_clamped_before_it_is_used(clean_db, monkeypatch):
    from app.agents import synthesis

    monkeypatch.setattr(synthesis, "current", lambda: {"size_multiplier": 7.5})
    mult, _ = decision.agent_multiplier()
    assert mult == 1.0


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------
def test_a_signal_becomes_an_audited_decision(clean_db, live_quote, no_positions):
    signal = Signal(instrument_key=INFY, side="BUY", source="test",
                    stop_price=1470.0, expected_move_pct=1.5, probability=0.7)
    result = run(decision.decide(signal, equity=1_000_000))

    logged = audit.recent(kind="decision")
    assert len(logged) == 1
    assert logged[0]["outcome"] == ("approved" if result.approved else "rejected")
    assert audit.verify()["intact"] is True


def test_a_rejected_signal_is_audited_too(clean_db, live_quote, no_positions):
    """The refusals are the ones anyone will want to read later."""
    thin = Signal(instrument_key=INFY, stop_price=1470.0,
                  expected_move_pct=0.01, probability=0.5)
    result = run(decision.decide(thin, equity=1_000_000))
    assert result.approved is False
    assert audit.recent(kind="decision")[0]["outcome"] == "rejected"


def test_an_unavailable_position_list_is_recorded_not_swallowed(
        clean_db, live_quote, monkeypatch):
    """An empty book reads as "room for one more trade", so this must be
    visible rather than silent."""
    from app.upstox import trading

    async def broken():
        raise RuntimeError("broker down")

    monkeypatch.setattr(trading, "positions", broken)
    run(decision.build_book(equity=1_000_000))
    assert any(r["kind"] == "book_error" for r in audit.recent())


def test_the_book_reflects_the_brokers_positions(clean_db, monkeypatch):
    """Our records and the broker's can disagree, and when they do the
    broker is right -- ours is a story about what we thought we did."""
    from app.upstox import trading

    async def two():
        return [
            {"instrument_token": INFY, "tradingsymbol": "INFY",
             "quantity": 10, "average_price": 1500},
            {"instrument_token": "NSE_EQ|X", "tradingsymbol": "TCS",
             "quantity": 0, "average_price": 3000},     # closed, ignore
        ]

    monkeypatch.setattr(trading, "positions", two)
    book = run(decision.build_book(equity=1_000_000))
    assert len(book.positions) == 1
    assert book.positions[0].sector == "IT"


def many_quotes(monkeypatch, keys):
    """A healthy tick for each of several instruments."""
    from app.engine import feed_service
    from app.upstox.ticks import DepthLevel, Tick

    ticks = {
        key: Tick(instrument_key=key, ts=int(OPEN_TIME.timestamp() * 1000),
                  ltp=1500.0, close_price=1480.0, volume=5_000_000,
                  depth=[DepthLevel(bid_qty=100, bid_price=1499.5,
                                    ask_qty=100, ask_price=1500.5)])
        for key in keys
    }
    monkeypatch.setattr(feed_service, "_latest", ticks)


def test_repeat_signals_on_one_name_do_not_stack(
        clean_db, live_quote, no_positions, monkeypatch):
    """Five signals on the same stock are one position, not five.

    Caught by `already_held` rather than by the position limit, which is
    the stricter of the two: adding to a position is a separate decision
    from opening one.
    """
    monkeypatch.setattr(config, "MAX_OPEN_POSITIONS", 5)
    monkeypatch.setattr(config, "MAX_TRADES_PER_DAY", 50)

    signals = [Signal(instrument_key=INFY, stop_price=1470.0,
                      expected_move_pct=2.0, probability=0.8, source=f"s{i}")
               for i in range(5)]
    results = run(decision.decide_many(signals, equity=1_000_000, at=OPEN_TIME))

    assert sum(1 for r in results if r.approved) == 1
    assert any("already_held" in " ".join(r.reasons) for r in results)


def test_several_signals_cannot_each_fill_the_last_slot(
        clean_db, no_positions, monkeypatch):
    """The easiest limit to breach by accident.

    Five signals on five different names, evaluated against the same
    snapshot, are each individually fine and collectively a breach -- so
    the book has to grow as approvals land.

    Pinned to market hours: without that every signal is refused for being
    out of session and the assertion passes without testing anything,
    which is how a limit test quietly stops working after 15:30.
    """
    keys = [INFY, "NSE_EQ|INE467B01029", "NSE_EQ|INE040A01034",
            "NSE_EQ|INE002A01018", "NSE_EQ|INE090A01021"]
    many_quotes(monkeypatch, keys)
    monkeypatch.setattr(config, "MAX_OPEN_POSITIONS", 2)
    monkeypatch.setattr(config, "MAX_CORRELATED_POSITIONS", 99)
    monkeypatch.setattr(config, "MAX_SECTOR_EXPOSURE_PCT", 100.0)
    monkeypatch.setattr(config, "MAX_TRADES_PER_DAY", 50)

    signals = [Signal(instrument_key=key, stop_price=1470.0,
                      expected_move_pct=2.0, probability=0.8, source=f"s{i}")
               for i, key in enumerate(keys)]
    results = run(decision.decide_many(signals, equity=1_000_000, at=OPEN_TIME))

    approved = sum(1 for r in results if r.approved)
    assert approved == 2, [r.reasons for r in results]
    assert any("max_positions" in " ".join(r.reasons) for r in results)


# --------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------
def test_the_sweep_halts_on_a_losing_day(clean_db, monkeypatch):
    """A limit must be able to fire on a day when nothing wanted to trade."""
    from app.risk import state as risk_state

    risk_state.start_day(1_000_000)
    risk_state.record_trade(-40_000)
    out = run(decision.sweep(equity=960_000))
    assert "daily_loss" in out["tripped"]
    assert out["state"]["state"] == "halted"


def test_the_sweep_is_quiet_on_an_ordinary_day(clean_db):
    from app.risk import state as risk_state

    risk_state.start_day(1_000_000)
    risk_state.record_trade(-2_000)
    out = run(decision.sweep(equity=998_000))
    assert out["tripped"] == []
    assert out["state"]["state"] == "running"


def test_the_sweep_records_the_peak_for_the_drawdown_measure(clean_db):
    from app.risk import state as risk_state

    run(decision.sweep(equity=1_200_000))
    assert risk_state.peak_equity() == 1_200_000


def test_a_tripped_sweep_is_audited(clean_db):
    from app.risk import state as risk_state

    risk_state.start_day(1_000_000)
    risk_state.record_trade(-40_000)
    run(decision.sweep(equity=960_000))
    assert any(r["kind"] == "kill_switch" for r in audit.recent())


# --------------------------------------------------------------------------
# The order path -- where it actually has to hold
# --------------------------------------------------------------------------
def test_a_halt_blocks_a_manual_order(clean_db, live_quote, monkeypatch):
    """A dashboard order never touches the decision engine.

    This is the test that matters most in the file: if the switch only
    guarded the automated path, the fastest way around it would be the
    button the user already has.
    """
    from app.upstox import client, trading

    monkeypatch.setattr(config, "PAPER_TRADING", True)
    killswitch.stop_all(by="tester", detail="drill")

    with pytest.raises(client.UpstoxError) as exc:
        run(trading.place_order(instrument_key=INFY, side="BUY", quantity=1,
                                order_type="LIMIT", price=1500.0))
    assert "halted" in str(exc.value)


def test_a_blocked_order_is_audited(clean_db, live_quote, monkeypatch):
    from app.upstox import client, trading

    monkeypatch.setattr(config, "PAPER_TRADING", True)
    killswitch.stop_all(by="tester")
    with pytest.raises(client.UpstoxError):
        run(trading.place_order(instrument_key=INFY, side="BUY", quantity=1,
                                order_type="LIMIT", price=1500.0))
    assert any(r["kind"] == "order_blocked" for r in audit.recent())


def test_a_halt_does_not_block_a_closing_order(clean_db, live_quote, monkeypatch):
    """Being unable to leave is worse than any condition that caused the halt."""
    from app.upstox import trading

    monkeypatch.setattr(config, "PAPER_TRADING", True)
    killswitch.stop_all(by="tester")

    out = run(trading.place_order(instrument_key=INFY, side="SELL", quantity=1,
                                  order_type="LIMIT", price=1500.0,
                                  intent="close"))
    assert out["status"] == "complete"


def test_an_order_goes_through_normally_when_nothing_is_tripped(
        clean_db, live_quote, monkeypatch):
    from app.upstox import trading

    monkeypatch.setattr(config, "PAPER_TRADING", True)
    out = run(trading.place_order(instrument_key=INFY, side="BUY", quantity=1,
                                  order_type="LIMIT", price=1500.0))
    assert out["status"] == "complete"
