"""Execution and reconciliation, and the two states that must never persist.

    a filled position with no stop
    our book disagreeing with the broker's

Both arise from ordinary things -- an API timeout, a restart mid-fill,
someone trading the same account from the Upstox app -- and neither
announces itself. The second is the quieter one: every risk limit is
computed from the position list, so a wrong list makes every limit wrong in
the same direction, under-reporting what we hold and therefore
over-permitting what we can add.

Most of this file is about those two, plus the polling bug that is easy to
write and hard to notice: reacting to an order's *state* rather than to a
change in it, which books the same fill on every cycle.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config  # noqa: E402
from app.clock import IST  # noqa: E402
from app.execution import executor, loop, reconcile, tracker  # noqa: E402
from app.risk import killswitch  # noqa: E402
from app.risk.engine import Decision  # noqa: E402
from app.store import audit  # noqa: E402


def run(coro):
    return asyncio.run(coro)


INFY = "NSE_EQ|INE009A01021"


@pytest.fixture
def clean_db(tmp_path, monkeypatch):
    from app import db
    from app.forward import journal
    from app.risk import state as risk_state
    from app.store import events as events_store

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "_local", type("L", (), {})())
    # The loop now writes the forward-test journal too, so its schema flag
    # has to be cleared along with the rest or it believes its tables exist
    # in a database that is empty.
    stores = (tracker, risk_state, killswitch, audit, events_store, journal)
    for store in stores:
        store._ready = False
    loop._reset_for_tests()
    yield
    for store in stores:
        store._ready = False
    loop._reset_for_tests()


@pytest.fixture
def paper(monkeypatch):
    """Paper mode with a price to trade against.

    MARKET orders become marketable limits, which needs a reference price;
    without one the order builder refuses, which is correct behaviour and
    not what these tests are about.
    """
    monkeypatch.setattr(config, "PAPER_TRADING", True)

    async def quote(keys):
        return {key: {"ltp": 1500.0} for key in keys}

    monkeypatch.setattr("app.upstox.marketdata.ltp", quote)


def approved(quantity: int = 10, entry: float = 1500.0,
             stop: float = 1470.0) -> Decision:
    return Decision(
        approved=True, quantity=quantity,
        proposal={"instrument": INFY, "symbol": "INFY", "side": "BUY",
                  "entry": entry, "stop": stop, "intent": "open",
                  "source": "test"})


def entry_row(**kw) -> int:
    base = dict(tag="t1", instrument_key=INFY, symbol="INFY", side="BUY",
                quantity=10, role="entry", trigger_price=1470.0)
    base.update(kw)
    return tracker.create(**base)


# ==========================================================================
# The tracker
# ==========================================================================
def test_an_order_is_recorded_before_it_is_sent(clean_db):
    """A timeout still leaves evidence of intent, which is what lets a
    retry find the order by tag instead of placing a second one."""
    order_id = entry_row()
    row = tracker.get(order_id)
    assert row["status"] == "pending"
    assert tracker.by_tag("t1")["id"] == order_id


def test_an_unknown_broker_status_leaves_the_order_live(clean_db):
    """Assuming an unrecognised status means "finished" stops us watching
    an order that may still fill."""
    assert tracker.normalise_status("some new status") == "open"
    assert tracker.normalise_status("", filled=3, quantity=10) == "partial"


def test_a_complete_status_with_a_short_fill_is_partial(clean_db):
    """Some brokers report complete on the last partial fill event."""
    assert tracker.normalise_status("complete", filled=4, quantity=10) == "partial"
    assert tracker.normalise_status("complete", filled=10, quantity=10) == "complete"


def test_applying_a_broker_row_reports_the_transition_not_the_state(clean_db):
    """Polling sees "complete" on every cycle. Acting on the state books
    the same trade repeatedly; acting on the change books it once."""
    order_id = entry_row()
    row = {"order_id": "B1", "quantity": 10, "filled_quantity": 10,
           "status": "complete", "average_price": 1501.0}

    first = tracker.apply_broker_row(order_id, row)
    assert first["newly_filled"] == 10
    assert first["became_terminal"] is True

    second = tracker.apply_broker_row(order_id, row)
    assert second["newly_filled"] == 0
    assert second["became_terminal"] is False


def test_partial_fills_accumulate(clean_db):
    order_id = entry_row()
    tracker.apply_broker_row(order_id, {"order_id": "B1", "quantity": 10,
                                        "filled_quantity": 4,
                                        "status": "partially filled"})
    change = tracker.apply_broker_row(order_id, {"order_id": "B1",
                                                 "quantity": 10,
                                                 "filled_quantity": 9,
                                                 "status": "partially filled"})
    assert change["newly_filled"] == 5
    assert tracker.get(order_id)["filled_quantity"] == 9


def test_our_positions_are_computed_from_fills_not_kept_as_a_total(clean_db):
    """A running total drifts, and the drift is invisible until it matters."""
    buy = entry_row(tag="b", quantity=10)
    tracker.update(buy, filled_quantity=10, status="complete")
    sell = tracker.create(tag="s", instrument_key=INFY, symbol="INFY",
                          side="SELL", quantity=4, role="exit")
    tracker.update(sell, filled_quantity=4, status="complete")

    assert tracker.our_positions() == {INFY: 6}


def test_a_flat_instrument_does_not_appear_as_a_position(clean_db):
    buy = entry_row(tag="b", quantity=10)
    tracker.update(buy, filled_quantity=10, status="complete")
    sell = tracker.create(tag="s", instrument_key=INFY, symbol="INFY",
                          side="SELL", quantity=10, role="exit")
    tracker.update(sell, filled_quantity=10, status="complete")
    assert tracker.our_positions() == {}


def test_an_unknown_role_is_refused(clean_db):
    """Losing track of which order is the stop is how a position ends up
    with two stops or none."""
    with pytest.raises(ValueError):
        entry_row(role="hedge")


# ==========================================================================
# No filled position without a stop
# ==========================================================================
def test_a_filled_entry_with_no_stop_is_listed(clean_db):
    order_id = entry_row()
    tracker.update(order_id, filled_quantity=10, status="complete")
    assert [r["id"] for r in tracker.filled_entries_without_stops()] == [order_id]


def test_an_entry_with_a_live_stop_is_not_listed(clean_db):
    order_id = entry_row()
    tracker.update(order_id, filled_quantity=10, status="complete")
    stop = tracker.create(tag="s1", instrument_key=INFY, symbol="INFY",
                          side="SELL", quantity=10, role="stop",
                          parent_id=order_id, trigger_price=1470.0)
    tracker.update(stop, status="open")
    assert tracker.filled_entries_without_stops() == []


def test_an_unfilled_entry_needs_no_stop_yet(clean_db):
    entry_row()                                    # pending, nothing filled
    assert tracker.filled_entries_without_stops() == []


def test_a_fill_gets_a_stop_placed(clean_db, paper):
    order_id = entry_row()
    tracker.update(order_id, filled_quantity=10, status="complete",
                   avg_fill_price=1500.0)

    stop = run(executor.ensure_stop(tracker.get(order_id)))
    assert stop is not None
    assert stop["role"] == "stop"
    assert stop["side"] == "SELL"
    assert stop["trigger_price"] == 1470.0
    assert tracker.filled_entries_without_stops() == []


def test_placing_a_stop_twice_is_harmless(clean_db, paper):
    """Called on every poll, so it has to be cheap and idempotent."""
    order_id = entry_row()
    tracker.update(order_id, filled_quantity=10, status="complete")
    run(executor.ensure_stop(tracker.get(order_id)))
    run(executor.ensure_stop(tracker.get(order_id)))
    assert len(tracker.children(order_id, role="stop")) == 1


def test_a_stop_that_cannot_be_placed_halts_trading(clean_db, monkeypatch):
    """An unprotected position must not sit there while new ones open
    alongside it."""
    async def refuse(**kwargs):
        raise RuntimeError("margin shortfall")

    monkeypatch.setattr("app.upstox.trading.place_order", refuse)
    order_id = entry_row()
    tracker.update(order_id, filled_quantity=10, status="complete")

    assert run(executor.ensure_stop(tracker.get(order_id))) is None
    assert killswitch.state() is killswitch.State.HALTED
    assert any("no stop placed" in r["detail"] for r in killswitch.reasons())


def test_a_failed_stop_is_retried_on_the_next_poll(clean_db, paper, monkeypatch):
    """Transient failures are the common case, so this must self-heal."""
    calls = {"n": 0}
    real = executor.trading.place_order

    async def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("timeout")
        return await real(**kwargs)

    monkeypatch.setattr("app.upstox.trading.place_order", flaky)
    monkeypatch.setattr(executor, "STOP_RETRIES", 1)

    order_id = entry_row()
    tracker.update(order_id, filled_quantity=10, status="complete")
    run(executor.ensure_stop(tracker.get(order_id)))
    assert tracker.filled_entries_without_stops() != []

    run(executor.ensure_stop(tracker.get(order_id)))
    assert tracker.filled_entries_without_stops() == []


def test_a_fill_with_no_recorded_stop_price_is_treated_as_a_bug(clean_db):
    """The risk engine will not size a trade without a stop, so this
    cannot happen from a market condition -- only from a defect."""
    order_id = entry_row(trigger_price=0.0)
    tracker.update(order_id, filled_quantity=10, status="complete")
    run(executor.ensure_stop(tracker.get(order_id)))
    assert killswitch.state() is killswitch.State.HALTED


# ==========================================================================
# Placing
# ==========================================================================
def test_an_unapproved_decision_is_never_sent(clean_db, paper):
    """A function that executes whatever it is handed turns the risk
    engine's answer into a suggestion."""
    rejected = Decision(approved=False, quantity=0,
                        proposal={"instrument": INFY, "symbol": "INFY",
                                  "side": "BUY"})
    with pytest.raises(executor.ExecutionError):
        run(executor.place_entry(rejected, stop_price=1470.0))


def test_an_entry_without_a_stop_is_refused(clean_db, paper):
    with pytest.raises(executor.ExecutionError) as exc:
        run(executor.place_entry(approved(), stop_price=0.0))
    assert "stop" in str(exc.value)


def test_a_paper_entry_fills_and_is_protected_immediately(clean_db, paper):
    """Paper fills are instant, so waiting for a poll would leave the
    position unprotected with nothing left to poll."""
    out = run(executor.place_entry(approved(), stop_price=1470.0))
    entry = tracker.get(out["tracked_id"])
    assert entry["filled_quantity"] == 10
    assert tracker.has_live_stop(entry["id"])
    assert tracker.filled_entries_without_stops() == []


def test_the_same_intent_twice_does_not_place_two_orders(clean_db, paper):
    """The retry-after-timeout path."""
    first = run(executor.place_entry(approved(), stop_price=1470.0))
    second = run(executor.place_entry(approved(), stop_price=1470.0))
    assert second["duplicate"] is True
    assert second["order"]["id"] == first["tracked_id"]


def test_a_rejected_send_is_recorded_as_rejected(clean_db, monkeypatch):
    async def refuse(**kwargs):
        raise RuntimeError("exchange down")

    monkeypatch.setattr("app.upstox.trading.place_order", refuse)
    with pytest.raises(RuntimeError):
        run(executor.place_entry(approved(), stop_price=1470.0))

    row = tracker.recent()[0]
    assert row["status"] == "rejected"
    assert "exchange down" in row["last_error"]


def test_every_order_is_audited(clean_db, paper):
    run(executor.place_entry(approved(), stop_price=1470.0))
    kinds = {r["kind"] for r in audit.recent()}
    assert "entry_sent" in kinds
    assert "stop_placed" in kinds
    assert audit.verify()["intact"] is True


# ==========================================================================
# Fills and P&L
# ==========================================================================
def test_realised_pnl_is_net_of_costs(clean_db):
    entry = entry_row()
    tracker.update(entry, filled_quantity=10, avg_fill_price=1000.0,
                   status="complete")
    stop = tracker.create(tag="s", instrument_key=INFY, symbol="INFY",
                          side="SELL", quantity=10, role="stop",
                          parent_id=entry)
    tracker.update(stop, filled_quantity=10, avg_fill_price=1100.0,
                   status="complete")

    pnl = executor.realised_pnl(tracker.get(stop))
    assert pnl is not None
    assert 0 < pnl < 1000           # gross 1000, less brokerage/tax/slippage


def test_a_short_that_falls_is_a_profit(clean_db):
    entry = entry_row(side="SELL")
    tracker.update(entry, filled_quantity=10, avg_fill_price=1000.0,
                   status="complete")
    cover = tracker.create(tag="c", instrument_key=INFY, symbol="INFY",
                           side="BUY", quantity=10, role="exit",
                           parent_id=entry)
    tracker.update(cover, filled_quantity=10, avg_fill_price=900.0,
                   status="complete")
    assert executor.realised_pnl(tracker.get(cover)) > 0


def test_an_orphan_exit_reports_no_pnl_rather_than_a_wrong_one(clean_db):
    """This number feeds the daily-loss kill switch, so a guess is worse
    than an absence."""
    orphan = tracker.create(tag="x", instrument_key=INFY, symbol="INFY",
                            side="SELL", quantity=10, role="exit")
    tracker.update(orphan, filled_quantity=10, avg_fill_price=1100.0,
                   status="complete")
    assert executor.realised_pnl(tracker.get(orphan)) is None


# ==========================================================================
# Stale orders
# ==========================================================================
def test_a_working_order_left_too_long_is_cancelled(clean_db, paper):
    """A limit order that never fills is a position you think you have."""
    order_id = entry_row()
    tracker.update(order_id, status="open")
    old = (datetime.now(IST) - timedelta(seconds=600)).isoformat(timespec="seconds")
    from app import db

    db.execute("UPDATE exec_orders SET created_at = ? WHERE id = ?",
               (old, order_id))

    assert run(executor.cancel_stale_orders(max_age=120)) == 1
    assert tracker.get(order_id)["status"] == "cancelled"


def test_a_stop_is_never_cancelled_for_being_old(clean_db, paper):
    """A stop is meant to sit there all day. Cancelling it for being old
    removes the protection precisely because nothing has gone wrong."""
    entry = entry_row()
    stop = tracker.create(tag="s", instrument_key=INFY, symbol="INFY",
                          side="SELL", quantity=10, role="stop",
                          parent_id=entry, trigger_price=1470.0)
    tracker.update(stop, status="open")
    old = (datetime.now(IST) - timedelta(seconds=99_999)).isoformat(timespec="seconds")
    from app import db

    db.execute("UPDATE exec_orders SET created_at = ? WHERE id = ?", (old, stop))

    run(executor.cancel_stale_orders(max_age=120))
    assert tracker.get(stop)["status"] == "open"


# ==========================================================================
# Square-off
# ==========================================================================
def test_square_off_does_nothing_before_the_time(clean_db, paper):
    morning = datetime(2026, 10, 16, 11, 0, tzinfo=IST)
    out = run(executor.square_off_intraday(at=morning))
    assert out["squared_off"] == 0


def test_square_off_closes_an_intraday_position(clean_db, paper):
    """The one automatic close in the system -- because the broker will do
    it anyway, at its own time and price."""
    out = run(executor.place_entry(approved(), stop_price=1470.0, product="I"))
    assert tracker.our_positions().get(INFY) == 10

    late = datetime(2026, 10, 16, 15, 20, tzinfo=IST)
    result = run(executor.square_off_intraday(at=late))
    assert result["squared_off"] == 1
    assert tracker.our_positions().get(INFY, 0) == 0


def test_square_off_leaves_a_delivery_position_alone(clean_db, paper):
    out = run(executor.place_entry(approved(), stop_price=1470.0, product="D"))
    late = datetime(2026, 10, 16, 15, 20, tzinfo=IST)
    assert run(executor.square_off_intraday(at=late))["squared_off"] == 0
    assert tracker.our_positions().get(INFY) == 10


# ==========================================================================
# Reconciliation
# ==========================================================================
def test_matching_books_reconcile(clean_db, monkeypatch):
    buy = entry_row()
    tracker.update(buy, filled_quantity=10, status="complete")

    async def broker():
        return [{"instrument_token": INFY, "quantity": 10}]

    async def empty_book():
        return []

    monkeypatch.setattr("app.upstox.trading.positions", broker)
    monkeypatch.setattr("app.upstox.trading.order_book", empty_book)

    stop = tracker.create(tag="s", instrument_key=INFY, symbol="INFY",
                          side="SELL", quantity=10, role="stop", parent_id=buy)
    tracker.update(stop, status="open")

    result = run(reconcile.run())
    assert result["ok"] is True
    assert killswitch.state() is killswitch.State.RUNNING


def test_a_quantity_disagreement_halts_trading(clean_db, monkeypatch):
    """A wrong position list under-reports what we hold, so it
    over-permits what we can add."""
    buy = entry_row()
    tracker.update(buy, filled_quantity=10, status="complete")

    async def broker():
        return [{"instrument_token": INFY, "quantity": 25}]

    async def empty_book():
        return []

    monkeypatch.setattr("app.upstox.trading.positions", broker)
    monkeypatch.setattr("app.upstox.trading.order_book", empty_book)

    result = run(reconcile.run())
    assert result["ok"] is False
    assert killswitch.state() is killswitch.State.HALTED
    assert result["mismatches"][0]["difference"] == 15


def test_a_position_we_know_nothing_about_halts_trading(clean_db, monkeypatch):
    """Nobody is watching it: no stop, and it counts towards no limit."""
    async def broker():
        return [{"instrument_token": "NSE_EQ|SURPRISE", "quantity": 5}]

    async def empty_book():
        return []

    monkeypatch.setattr("app.upstox.trading.positions", broker)
    monkeypatch.setattr("app.upstox.trading.order_book", empty_book)

    result = run(reconcile.run())
    assert result["mismatches"][0]["kind"] == "unknown_to_us"
    assert killswitch.state() is killswitch.State.HALTED


def test_an_unreadable_broker_is_not_a_mismatch(clean_db, monkeypatch):
    """An unreadable account is not a disagreeing account, and halting on
    a network blip would stop trading for the wrong reason."""
    async def broken():
        raise RuntimeError("502 from Upstox")

    monkeypatch.setattr("app.upstox.trading.positions", broken)
    result = run(reconcile.run())
    assert result["ok"] is False
    assert killswitch.state() is killswitch.State.RUNNING
    assert "not treated as a mismatch" in result["note"]


def test_an_unprotected_position_alone_halts_trading(clean_db, monkeypatch):
    buy = entry_row()
    tracker.update(buy, filled_quantity=10, status="complete")

    async def broker():
        return [{"instrument_token": INFY, "quantity": 10}]

    async def empty_book():
        return []

    monkeypatch.setattr("app.upstox.trading.positions", broker)
    monkeypatch.setattr("app.upstox.trading.order_book", empty_book)

    result = run(reconcile.run())
    assert result["matched"] is True            # quantities agree
    assert result["ok"] is False                # but it has no stop
    assert killswitch.state() is killswitch.State.HALTED


def test_an_order_the_broker_never_heard_of_is_reported(clean_db):
    """A stop in this state is protection that does not exist."""
    live = [{"id": 1, "symbol": "INFY", "role": "stop", "status": "open",
             "broker_order_id": "GHOST", "tag": "t1"}]
    out = reconcile.compare_orders(live, [{"order_id": "OTHER", "tag": "t2"}])
    assert out["unknown_to_broker"][0]["broker_order_id"] == "GHOST"


def test_comparison_is_pure_and_symmetric():
    out = reconcile.compare({"A": 10, "B": 5}, {"A": 10, "C": 3})
    kinds = {m["instrument_key"]: m["kind"] for m in out["mismatches"]}
    assert kinds == {"B": "missing_at_broker", "C": "unknown_to_us"}


def test_there_is_no_tolerance_on_a_share_count():
    """There is no such thing as a rounding error in a share count."""
    assert reconcile.compare({"A": 10}, {"A": 11})["matched"] is False


# ==========================================================================
# The loop
# ==========================================================================
def test_a_cycle_runs_every_step_and_never_raises(clean_db, paper, monkeypatch):
    async def broker():
        return []

    monkeypatch.setattr("app.upstox.trading.positions", broker)
    out = run(loop.cycle())
    assert "poll" in out and "reconcile" in out and "sweep" in out


def test_one_failing_step_does_not_stop_the_others(clean_db, paper, monkeypatch):
    """The later steps are the ones that halt trading when something is
    wrong, so an early failure must not skip them."""
    async def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(executor, "poll", broken)

    async def broker():
        return []

    monkeypatch.setattr("app.upstox.trading.positions", broker)
    out = run(loop.cycle())
    assert out["poll"] is None            # the step that was broken
    assert out["sweep"] is not None       # the steps after it still ran
    assert out["journal"] is not None
    assert loop.status()["last_error"].startswith("poll:")


def test_starting_the_loop_twice_is_harmless(clean_db):
    async def go():
        first = await loop.start()
        second = await loop.start()
        await loop.stop()
        return first, second

    first, second = run(go())
    assert first["running"] is True
    assert second["note"] == "already running"


def test_stopping_the_loop_leaves_orders_alone(clean_db, paper):
    order_id = entry_row()
    tracker.update(order_id, status="open")

    async def go():
        await loop.start()
        await loop.stop()

    run(go())
    assert tracker.get(order_id)["status"] == "open"
    assert loop.running() is False


# ==========================================================================
# Paper mode has to model a stop as a stop
# ==========================================================================
def test_a_paper_stop_waits_for_its_trigger(clean_db, paper):
    """This one would have invalidated the entire forward test.

    Paper mode filled every order on arrival, including the protective
    stop -- so each position closed at the stop price the instant it
    opened. Two months of paper trading would have measured a guaranteed
    loss on every trade and called it the strategy.
    """
    from app.upstox import trading

    out = run(trading.place_order(
        instrument_key=INFY, side="SELL", quantity=10, order_type="SL-M",
        trigger_price=1470.0, product="I", source="t", intent="close"))
    assert out["status"] == "open"
    assert out["trigger_price"] == 1470.0


def test_a_paper_position_survives_its_stop_being_placed(clean_db, paper):
    out = run(executor.place_entry(approved(), stop_price=1470.0))
    assert tracker.our_positions().get(INFY) == 10

    from app.upstox import trading

    positions = run(trading.positions())
    held = {p["tradingsymbol"]: p["quantity"] for p in positions}
    assert held.get("INFY", 0) == 10 or held.get(INFY, 0) == 10


def test_a_paper_stop_fills_when_the_price_reaches_it(clean_db, paper, monkeypatch):
    from app.upstox import trading

    run(trading.place_order(
        instrument_key=INFY, side="BUY", quantity=10, order_type="MARKET",
        product="I", source="t", intent="open"))
    run(trading.place_order(
        instrument_key=INFY, side="SELL", quantity=10, order_type="SL-M",
        trigger_price=1470.0, product="I", source="t", intent="close"))

    assert run(trading.simulate_paper_fills()) == []     # 1500, not triggered

    async def lower(keys):
        return {k: {"ltp": 1465.0} for k in keys}

    monkeypatch.setattr("app.upstox.marketdata.ltp", lower)
    filled = run(trading.simulate_paper_fills())
    assert len(filled) == 1
    assert filled[0]["price"] == 1470.0             # fills at the trigger


def test_a_buy_stop_triggers_upwards(clean_db, paper, monkeypatch):
    """A sell stop sits below the market, a buy stop above. Getting the
    comparison backwards would fill every stop instantly again."""
    from app.upstox import trading

    run(trading.place_order(
        instrument_key=INFY, side="BUY", quantity=10, order_type="SL-M",
        trigger_price=1530.0, product="I", source="t", intent="close"))
    assert run(trading.simulate_paper_fills()) == []     # 1500 < 1530

    async def higher(keys):
        return {k: {"ltp": 1535.0} for k in keys}

    monkeypatch.setattr("app.upstox.marketdata.ltp", higher)
    assert len(run(trading.simulate_paper_fills())) == 1


def test_a_pending_paper_stop_reports_nothing_filled(clean_db, paper):
    """The order book is what the poll reads, so a pending stop showing a
    filled quantity would book a trade that never happened."""
    from app.upstox import trading

    run(trading.place_order(
        instrument_key=INFY, side="SELL", quantity=10, order_type="SL-M",
        trigger_price=1470.0, product="I", source="t", intent="close"))
    row = next(r for r in run(trading.order_book()) if r["status"] == "open")
    assert row["filled_quantity"] == 0
    assert row["average_price"] == 0
