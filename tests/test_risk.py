"""The risk layer, and specifically the ways it must refuse to be helpful.

The roadmap gate for Phase 6 is that kill switches are tested before
anything else proceeds, so a large part of this file triggers them
deliberately: the daily loss limit, the drawdown halt, the manual stop.

SEBI's FY26 figures sit behind all of it -- 87.7% of individual F&O traders
lost money. Those losses were not mostly bad strategy. They were size, one
more trade to make it back, and the position that was never cut. Each test
below corresponds to one of those.

The guarantee being tested throughout: nothing upstream can loosen a limit.
Not a strategy, not an agent, not the config file.
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
from app.risk import checks, killswitch, sizing  # noqa: E402
from app.risk.engine import (  # noqa: E402
    Book,
    Proposal,
    check_limits,
    evaluate,
    portfolio_checks,
)
from app.risk.state import Position  # noqa: E402
from app.store import audit  # noqa: E402


def run(coro):
    return asyncio.run(coro)


OPEN_TIME = datetime(2026, 10, 16, 11, 0, tzinfo=IST)


@pytest.fixture
def clean_db(tmp_path, monkeypatch):
    from app import db
    from app.risk import state as risk_state
    from app.store import events as events_store

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "_local", type("L", (), {})())
    stores = (risk_state, killswitch, audit, events_store)
    for store in stores:
        store._ready = False
    yield
    for store in stores:
        store._ready = False


def good_proposal(**kw) -> Proposal:
    """A proposal that passes everything, so a test can break one thing."""
    base = dict(
        instrument_key="NSE_EQ|INE009A01021", symbol="INFY", side="BUY",
        entry_price=1500.0, stop_price=1470.0,
        sector="IT", industry="INFORMATION_TECHNOLOGY", source="test",
        bid=1499.5, ask=1500.5, day_volume=5_000_000,
        tick_ts_ms=OPEN_TIME.timestamp() * 1000,
        expected_move_pct=1.5, probability=0.65,
    )
    base.update(kw)
    return Proposal(**base)


def good_book(**kw) -> Book:
    base = dict(equity=1_000_000.0)
    base.update(kw)
    return Book(**base)


# ==========================================================================
# Kill switches -- the Phase 6 gate
# ==========================================================================
def test_the_manual_stop_blocks_new_entries(clean_db):
    killswitch.stop_all(by="tester", detail="drill")
    assert killswitch.state() is killswitch.State.HALTED
    assert killswitch.entries_allowed() is False


def test_the_manual_stop_does_not_block_exits(clean_db):
    """A halt takes away the ability to get further in, never to get out."""
    killswitch.stop_all(by="tester")
    assert killswitch.exits_allowed() is True

    decision = evaluate(good_proposal(intent="close"), good_book(), at=OPEN_TIME)
    assert decision.approved is True


def test_a_halt_survives_a_restart(clean_db):
    """Restarting the app must not be a way to clear a halt.

    It is written to disk precisely because the moment a halt matters is
    the moment someone will be tempted to restart and try again.
    """
    killswitch.trip("daily_loss", "down 3.2%")
    killswitch._reset_for_tests()          # as if the process had restarted
    assert killswitch.state() is killswitch.State.HALTED
    assert killswitch.status()["needs_manual_reset"] is True


def test_the_daily_loss_limit_halts_trading(clean_db):
    tripped = check_limits(equity=970_000, daily_loss_pct=3.1, drawdown_pct=3.0)
    assert "daily_loss" in tripped
    assert killswitch.entries_allowed() is False


def test_the_daily_loss_limit_does_not_fire_early(clean_db):
    tripped = check_limits(equity=995_000, daily_loss_pct=0.5, drawdown_pct=0.5)
    assert tripped == []
    assert killswitch.entries_allowed() is True


def test_the_drawdown_limit_halts_trading(clean_db):
    tripped = check_limits(equity=890_000, daily_loss_pct=0.2, drawdown_pct=11.0)
    assert "max_drawdown" in tripped
    assert killswitch.status()["needs_manual_reset"] is True


def test_a_halt_cannot_clear_itself(clean_db):
    """The automatic path clears pauses only. Forgiving its own halt is the
    whole failure this switch exists to prevent."""
    killswitch.trip("daily_loss", "down 3.2%")
    killswitch.clear_auto("daily_loss")
    assert killswitch.state() is killswitch.State.HALTED


def test_a_stale_feed_pauses_and_then_clears_itself(clean_db):
    """A pause is for conditions that are observable right now."""
    check_limits(equity=1_000_000, daily_loss_pct=0, drawdown_pct=0,
                 feed_stale_seconds=60)
    assert killswitch.state() is killswitch.State.PAUSED
    assert killswitch.entries_allowed() is False

    check_limits(equity=1_000_000, daily_loss_pct=0, drawdown_pct=0,
                 feed_stale_seconds=1)
    assert killswitch.state() is killswitch.State.RUNNING


def test_a_pause_cannot_downgrade_a_halt(clean_db):
    """Clearing the stale feed must not quietly resume a halted system."""
    killswitch.trip("daily_loss", "down 3.2%")
    check_limits(equity=1_000_000, daily_loss_pct=0, drawdown_pct=0,
                 feed_stale_seconds=60)
    assert killswitch.state() is killswitch.State.HALTED

    check_limits(equity=1_000_000, daily_loss_pct=0, drawdown_pct=0,
                 feed_stale_seconds=0)
    assert killswitch.state() is killswitch.State.HALTED


def test_too_many_rejected_orders_pauses_trading(clean_db):
    check_limits(equity=1_000_000, daily_loss_pct=0, drawdown_pct=0,
                 reject_rate_pct=25.0)
    assert killswitch.state() is killswitch.State.PAUSED


def test_clearing_a_halt_requires_a_name(clean_db):
    """"Who cleared it" is the first question asked after a halt was right."""
    killswitch.trip("daily_loss", "down 3.2%")
    with pytest.raises(ValueError):
        killswitch.reset(by="")
    assert killswitch.state() is killswitch.State.HALTED

    status = killswitch.reset(by="tester")
    assert status["state"] == "running"
    assert status["last_cleared_by"] == "tester"


def test_clearing_one_trigger_leaves_the_others(clean_db):
    killswitch.trip("daily_loss", "down 3.2%")
    killswitch.trip("max_drawdown", "11% below peak")
    killswitch.reset(by="tester", trigger="daily_loss")
    assert killswitch.state() is killswitch.State.HALTED
    assert [r["trigger"] for r in killswitch.reasons()] == ["max_drawdown"]


def test_the_same_trigger_firing_repeatedly_is_one_reason(clean_db):
    """A limit breached on every tick for an hour is one reason, not 3000."""
    for i in range(50):
        killswitch.trip("daily_loss", f"down {3 + i / 100:.2f}%")
    assert len(killswitch.reasons()) == 1


def test_an_unreadable_state_halts_rather_than_assuming_running(clean_db):
    from app import db

    killswitch.ensure_schema()
    db.execute(
        """INSERT INTO risk_state (id, state, reasons, changed_at)
           VALUES (1, 'nonsense', '[]', ?)""", (db.now(),))
    assert killswitch.state() is killswitch.State.HALTED


def test_a_halted_engine_rejects_before_checking_anything_else(clean_db):
    killswitch.stop_all(by="tester", detail="drill")
    decision = evaluate(good_proposal(), good_book(), at=OPEN_TIME)
    assert decision.approved is False
    assert any("halted" in r for r in decision.reasons)


# ==========================================================================
# Config cannot loosen a limit
# ==========================================================================
def test_config_above_a_hard_ceiling_is_a_startup_problem(monkeypatch):
    """A typo in .env must stop the app, not widen the limit."""
    monkeypatch.setattr(config, "MAX_RISK_PER_TRADE_PCT", 5.0)
    problems = config.validate()
    assert any("MAX_RISK_PER_TRADE_PCT" in p for p in problems)


def test_a_disabled_limit_is_a_startup_problem(monkeypatch):
    """Zero is not a tight limit, it is an absent one."""
    monkeypatch.setattr(config, "MAX_OPEN_POSITIONS", 0)
    assert any("MAX_OPEN_POSITIONS" in p for p in config.validate())


def test_a_daily_halt_above_the_drawdown_halt_is_a_problem(monkeypatch):
    """Otherwise the daily switch can never fire before the account one."""
    monkeypatch.setattr(config, "DAILY_LOSS_HALT_PCT", 4.9)
    monkeypatch.setattr(config, "MAX_DRAWDOWN_HALT_PCT", 4.0)
    assert any("never fire" in p for p in config.validate())


def test_one_position_cannot_exceed_the_whole_book(monkeypatch):
    monkeypatch.setattr(config, "MAX_POSITION_PCT", 20.0)
    monkeypatch.setattr(config, "MAX_TOTAL_EXPOSURE_PCT", 15.0)
    assert any("cannot be larger than the whole book" in p
               for p in config.validate())


def test_the_shipped_config_is_within_every_ceiling():
    assert config.validate() == []


# ==========================================================================
# Sizing
# ==========================================================================
def test_quantity_comes_from_the_stop_distance():
    """100 shares of a volatile stock and 100 of a calm one are not the
    same trade, and sizing by quantity treats them as if they were.

    Priced so that the risk calculation is what binds -- on a cheap stock
    the 10% position cap dominates and the stop distance stops mattering,
    which is correct behaviour and a different test.
    """
    tight = sizing.size(entry_price=100, stop_price=80, side="BUY",
                        capital=1_000_000)
    wide = sizing.size(entry_price=100, stop_price=60, side="BUY",
                       capital=1_000_000)
    assert tight.bound_by == wide.bound_by == "risk"
    assert tight.quantity > wide.quantity
    assert tight.risk_rupees <= 10_000          # 1% of capital
    assert wide.risk_rupees <= 10_000


def test_no_stop_means_no_size():
    """Without a stop there is no risk per share, so there is nothing to
    size against. Inventing a default would be inventing a risk appetite."""
    result = sizing.size(entry_price=1000, stop_price=0, side="BUY",
                         capital=1_000_000)
    assert result.quantity == 0
    assert result.bound_by == "no_stop"


def test_a_stop_on_the_wrong_side_is_refused():
    """A BUY stop above the entry is a target wearing a stop's name."""
    result = sizing.size(entry_price=1000, stop_price=1010, side="BUY",
                         capital=1_000_000)
    assert result.quantity == 0
    assert result.bound_by == "stop_wrong_side"


def test_unset_capital_refuses_rather_than_guessing():
    result = sizing.size(entry_price=1000, stop_price=990, side="BUY",
                         capital=0)
    assert result.quantity == 0
    assert result.bound_by == "no_capital"


def test_the_tightest_cap_wins_and_says_so():
    result = sizing.size(entry_price=1000, stop_price=999, side="BUY",
                         capital=1_000_000)
    # Risk would allow 10,000 units; the 10% position cap allows 100.
    assert result.quantity == 100
    assert result.bound_by == "position_cap"


def test_the_agent_multiplier_can_only_shrink_a_position():
    """The guarantee the whole agent layer rests on, asserted where the
    number is finally used."""
    full = sizing.size(entry_price=1000, stop_price=990, side="BUY",
                       capital=1_000_000)
    half = sizing.size(entry_price=1000, stop_price=990, side="BUY",
                       capital=1_000_000, agent_multiplier=0.5)
    huge = sizing.size(entry_price=1000, stop_price=990, side="BUY",
                       capital=1_000_000, agent_multiplier=50.0)
    assert half.quantity < full.quantity
    assert huge.quantity == full.quantity


def test_quantity_rounds_down_to_a_whole_lot():
    """Rounding up would breach the cap that was just applied."""
    result = sizing.size(entry_price=1000, stop_price=990, side="BUY",
                         capital=1_000_000, lot_size=30)
    assert result.quantity % 30 == 0
    assert result.quantity <= 100


def test_a_risk_budget_below_one_lot_is_no_trade():
    result = sizing.size(entry_price=1000, stop_price=990, side="BUY",
                         capital=1_000_000, lot_size=5000)
    assert result.quantity == 0
    assert result.bound_by == "below_one_lot"


def test_a_losing_streak_halves_the_size():
    mult, reasons = sizing.defensive_multiplier(consecutive_losses=5)
    assert mult == 0.5
    assert "losses in a row" in reasons[0]


def test_a_bad_week_and_a_losing_streak_compound():
    mult, reasons = sizing.defensive_multiplier(consecutive_losses=6,
                                                weekly_loss_pct=7.0)
    assert mult == 0.25
    assert len(reasons) == 2


def test_an_overnight_position_is_sized_for_a_gap():
    """An overnight stop is not a stop. The market can open through it.

    1000 units at Rs 1000 is Rs 10 lakh; a 5% gap costs Rs 50,000, which is
    5% of capital against a 1% per-trade budget. The cap cuts it to 200.
    """
    overnight, note = sizing.btst_cap(1000, 1000, capital=1_000_000)
    assert overnight == 200
    assert "gap" in note


def test_the_overnight_cap_leaves_an_already_small_position_alone():
    kept, note = sizing.btst_cap(50, 1000, capital=1_000_000)
    assert kept == 50
    assert "survives" in note


def test_the_overnight_cap_rarely_binds_under_the_default_position_cap():
    """Worth knowing rather than assuming it is doing work.

    With MAX_POSITION_PCT at 10 and a 5% gap assumption, the worst
    overnight outcome is 0.5% of capital -- already inside the 1% per-trade
    budget. So this cap only bites if the position cap is raised or the gap
    assumption widened, and the protection for BTST comes from the position
    cap rather than from here.
    """
    capped = int(1_000_000 * config.MAX_POSITION_PCT / 100 / 1000)
    kept, _ = sizing.btst_cap(capped, 1000, capital=1_000_000)
    assert kept == capped


def test_an_atr_stop_adapts_to_the_instrument():
    calm = sizing.stop_from_atr(1000, atr=2, side="BUY")
    volatile = sizing.stop_from_atr(1000, atr=40, side="BUY")
    assert calm > volatile                      # the calm stop sits closer
    assert sizing.stop_from_atr(1000, atr=10, side="SELL") > 1000


# ==========================================================================
# Pre-trade filters
# ==========================================================================
def test_a_stale_tick_blocks_a_trade():
    old = (OPEN_TIME - timedelta(seconds=30)).timestamp() * 1000
    assert checks.tick_fresh(old, OPEN_TIME).passed is False
    fresh = (OPEN_TIME - timedelta(seconds=1)).timestamp() * 1000
    assert checks.tick_fresh(fresh, OPEN_TIME).passed is True


def test_no_tick_at_all_blocks_a_trade():
    assert checks.tick_fresh(0, OPEN_TIME).passed is False


def test_a_tick_from_the_future_is_treated_as_a_clock_problem():
    """Not silently accepted: the age check means nothing until it is
    understood, and sizing reads the same price."""
    future = (OPEN_TIME + timedelta(seconds=60)).timestamp() * 1000
    assert checks.tick_fresh(future, OPEN_TIME).passed is False


def test_a_wide_spread_blocks_a_trade():
    assert checks.spread_acceptable(bid=99, ask=101, ltp=100).passed is False
    assert checks.spread_acceptable(bid=99.9, ask=100.1, ltp=100).passed is True


def test_a_crossed_book_blocks_a_trade():
    assert checks.spread_acceptable(bid=101, ask=99, ltp=100).passed is False


def test_a_one_sided_quote_blocks_a_trade():
    assert checks.spread_acceptable(bid=0, ask=100, ltp=100).passed is False


def test_an_illiquid_name_blocks_a_trade():
    assert checks.liquid_enough(500).passed is False
    assert checks.liquid_enough(5_000_000).passed is True


def test_a_circuit_blocks_a_trade():
    assert checks.not_in_circuit(110, upper=110).passed is False
    assert checks.not_in_circuit(90, lower=90).passed is False
    assert checks.not_in_circuit(100, upper=110, lower=90).passed is True


def test_a_fresh_filing_blocks_a_new_position():
    assert checks.not_in_blackout("DLF", ["DLF", "LT"]).passed is False
    assert checks.not_in_blackout("INFY", ["DLF"]).passed is True


def test_the_opening_minutes_block_new_entries():
    nine_twenty = datetime(2026, 10, 16, 9, 20, tzinfo=IST)
    assert checks.opening_window(nine_twenty, minutes=15).passed is False
    assert checks.opening_window(OPEN_TIME, minutes=15).passed is True


def test_the_square_off_window_blocks_new_intraday_entries():
    late = datetime(2026, 10, 16, 15, 20, tzinfo=IST)
    assert checks.square_off_window(late).passed is False
    assert checks.square_off_window(OPEN_TIME).passed is True


def test_a_closed_market_blocks_a_trade():
    night = datetime(2026, 10, 16, 23, 0, tzinfo=IST)
    assert checks.market_hours(night).passed is False
    assert checks.market_hours(OPEN_TIME).passed is True


# --------------------------------------------------------------------------
# Edge
# --------------------------------------------------------------------------
def test_a_move_smaller_than_the_cost_is_not_a_trade():
    """The filter that removes the most signals, and should.

    A 0.1% expected move is roughly the round trip, so taking it is a way
    of paying brokerage to express an opinion.
    """
    thin = checks.has_edge(price=1000, quantity=100,
                           expected_move_pct=0.1, probability=0.6)
    assert thin.passed is False
    assert "no edge" in thin.detail


def test_a_real_move_clears_the_cost():
    good = checks.has_edge(price=1000, quantity=100,
                           expected_move_pct=2.0, probability=0.7)
    assert good.passed is True


def test_probability_scales_the_expected_gain():
    """A 2% move at 51% confidence is not a 2% move."""
    confident = checks.has_edge(price=1000, quantity=100,
                               expected_move_pct=1.0, probability=0.9)
    doubtful = checks.has_edge(price=1000, quantity=100,
                               expected_move_pct=1.0, probability=0.05)
    assert confident.passed is True
    assert doubtful.passed is False


def test_the_cost_model_matches_the_backtester():
    """A trade the backtest called profitable must not be one live trading
    quietly loses on."""
    from app.engine.backtester import Costs

    costs = Costs()
    assert config.BROKERAGE_PER_ORDER == costs.brokerage_per_order
    assert config.SLIPPAGE_PCT == costs.slippage_pct
    assert config.TAXES_PCT == costs.taxes_pct


# ==========================================================================
# Portfolio limits
# ==========================================================================
def positions(n: int, sector: str = "IT", industry: str = "INFORMATION_TECHNOLOGY",
              value: float = 50_000.0) -> list[Position]:
    return [Position(instrument_key=f"NSE_EQ|X{i}", symbol=f"S{i}", quantity=1,
                     average_price=value, sector=sector, industry=industry)
            for i in range(n)]


def test_the_open_position_limit_blocks_a_new_entry(clean_db):
    book = good_book(positions=positions(config.MAX_OPEN_POSITIONS))
    failed = [c.name for c in portfolio_checks(good_proposal(), book) if not c.passed]
    assert "max_positions" in failed


def test_the_sector_limit_blocks_a_new_entry(clean_db):
    """A sector crash should cost one allocation, not the account."""
    book = good_book(positions=positions(3, value=120_000))   # 36% in IT
    failed = [c.name for c in portfolio_checks(good_proposal(), book) if not c.passed]
    assert "sector_exposure" in failed


def test_three_stocks_in_one_industry_is_one_bet(clean_db):
    book = good_book(positions=positions(2, value=10_000))
    failed = [c.name for c in portfolio_checks(good_proposal(), book) if not c.passed]
    assert "correlated_positions" in failed


def test_the_total_exposure_limit_blocks_a_new_entry(clean_db):
    book = good_book(positions=positions(4, sector="AUTO", industry="AUTO",
                                         value=210_000))      # 84%
    failed = [c.name for c in portfolio_checks(good_proposal(), book) if not c.passed]
    assert "total_exposure" in failed


def test_the_daily_trade_limit_blocks_a_new_entry(clean_db):
    book = good_book(trades_today=config.MAX_TRADES_PER_DAY)
    failed = [c.name for c in portfolio_checks(good_proposal(), book) if not c.passed]
    assert "trades_today" in failed


def test_adding_to_an_existing_position_is_not_a_new_entry(clean_db):
    held = [Position(instrument_key="NSE_EQ|INE009A01021", symbol="INFY",
                     quantity=10, average_price=1500, sector="IT")]
    book = good_book(positions=held)
    failed = [c.name for c in portfolio_checks(good_proposal(), book) if not c.passed]
    assert "already_held" in failed


# ==========================================================================
# The whole decision
# ==========================================================================
def test_a_clean_proposal_is_approved_and_sized(clean_db):
    decision = evaluate(good_proposal(), good_book(), at=OPEN_TIME)
    assert decision.approved is True
    assert decision.quantity > 0
    assert decision.sizing["bound_by"]


def test_a_rejection_carries_every_reason_not_the_first(clean_db):
    """Fixing one reason and retrying four times is how people learn to
    resent their own risk system."""
    bad = good_proposal(bid=90, ask=110, day_volume=10, tick_ts_ms=0)
    decision = evaluate(bad, good_book(), at=OPEN_TIME)
    assert decision.approved is False
    assert len(decision.reasons) >= 3
    names = {r.split(":")[0] for r in decision.reasons}
    assert {"spread", "liquidity", "tick_fresh"} <= names


def test_cautious_agents_shrink_an_approved_trade(clean_db):
    full = evaluate(good_proposal(), good_book(), at=OPEN_TIME)
    shy = evaluate(good_proposal(agent_multiplier=0.25), good_book(), at=OPEN_TIME)
    assert shy.quantity < full.quantity
    assert shy.multipliers["agents"] == 0.25


def test_confident_agents_cannot_enlarge_a_trade(clean_db):
    """Asserted at the engine level as well as in the schema and in sizing.

    Three places, one guarantee -- because this is the property that makes
    a prompt injection cost an opportunity instead of money.
    """
    full = evaluate(good_proposal(), good_book(), at=OPEN_TIME)
    greedy = evaluate(good_proposal(agent_multiplier=10.0), good_book(),
                      at=OPEN_TIME)
    assert greedy.quantity == full.quantity


def test_an_exit_is_approved_when_everything_else_would_block_it(clean_db):
    """Every check that could block a close is a way of being trapped."""
    killswitch.stop_all(by="tester")
    trapped = good_proposal(intent="close", bid=0, ask=0, day_volume=0,
                            tick_ts_ms=0)
    night = datetime(2026, 10, 16, 23, 0, tzinfo=IST)
    assert evaluate(trapped, good_book(), at=night).approved is True


def test_the_edge_test_runs_on_the_size_actually_approved(clean_db):
    """Edge depends on quantity -- brokerage is per order, not per share."""
    decision = evaluate(good_proposal(expected_move_pct=0.05), good_book(),
                        at=OPEN_TIME)
    assert decision.approved is False
    assert any(r.startswith("edge") for r in decision.reasons)


def test_a_losing_streak_shrinks_the_next_trade(clean_db):
    calm = evaluate(good_proposal(), good_book(), at=OPEN_TIME)
    bruised = evaluate(good_proposal(),
                       good_book(consecutive_losses=6, weekly_loss_pct=7.0),
                       at=OPEN_TIME)
    assert bruised.quantity < calm.quantity
    assert bruised.multipliers["defensive"] == 0.25


# ==========================================================================
# Account state
# ==========================================================================
def test_the_daily_loss_is_measured_from_the_starting_equity(clean_db):
    from app.risk import state as risk_state

    risk_state.start_day(1_000_000)
    risk_state.record_trade(-20_000, symbol="INFY")
    assert risk_state.daily_loss_pct() == pytest.approx(2.0)


def test_restarting_the_day_does_not_wipe_the_loss(clean_db):
    """Re-running start_day mid-session would reset the baseline, and the
    kill switch reads exactly that number."""
    from app.risk import state as risk_state

    risk_state.start_day(1_000_000)
    risk_state.record_trade(-30_000)
    risk_state.start_day(970_000)
    assert risk_state.daily_loss_pct() == pytest.approx(3.0)


def test_a_profitable_day_reports_no_loss(clean_db):
    from app.risk import state as risk_state

    risk_state.start_day(1_000_000)
    risk_state.record_trade(50_000)
    assert risk_state.daily_loss_pct() == 0.0


def test_the_peak_equity_only_goes_up(clean_db):
    """Drawdown is measured from the peak. A peak that falls with the
    account would chase the losses down and read near zero forever."""
    from app.risk import state as risk_state

    risk_state.record_equity(1_000_000)
    risk_state.record_equity(1_200_000)
    risk_state.record_equity(800_000)
    assert risk_state.peak_equity() == 1_200_000
    assert risk_state.drawdown_pct(800_000) == pytest.approx(33.33, abs=0.01)


def test_consecutive_losses_stop_at_the_last_win(clean_db):
    from app.risk import state as risk_state

    for pnl in (-100, 500, -100, -100, -100):
        risk_state.record_trade(pnl)
    assert risk_state.consecutive_losses() == 3


def test_the_reject_rate_ignores_a_tiny_sample(clean_db):
    """One rejection out of two is 100% and means nothing."""
    from app.risk import state as risk_state

    risk_state.record_order(rejected=True)
    risk_state.record_order()
    assert risk_state.reject_rate() == 0.0

    for _ in range(8):
        risk_state.record_order()
    assert 0 < risk_state.reject_rate() < 20


def test_exposure_is_reported_per_sector(clean_db):
    from app.risk import state as risk_state

    held = positions(2, value=100_000) + positions(1, sector="AUTO",
                                                   industry="AUTO", value=50_000)
    out = risk_state.exposure(held, 1_000_000)
    assert out["total_pct"] == pytest.approx(25.0)
    assert out["by_sector_pct"]["IT"] == pytest.approx(20.0)


# ==========================================================================
# Audit log
# ==========================================================================
def test_every_decision_is_logged_including_the_refusals(clean_db):
    """"Why did it not take that trade" is the question that gets asked."""
    audit.record_decision(evaluate(good_proposal(), good_book(), at=OPEN_TIME))
    audit.record_decision(evaluate(good_proposal(day_volume=1), good_book(),
                                   at=OPEN_TIME))
    outcomes = [r["outcome"] for r in audit.recent(kind="decision")]
    assert sorted(outcomes) == ["approved", "rejected"]


def test_the_audit_chain_detects_a_deleted_row(clean_db):
    """Not protection against an attacker -- protection against a bug, a
    manual tidy-up, or a half-restored backup."""
    from app import db

    for i in range(5):
        audit.record("decision", subject=f"S{i}", outcome="rejected")
    assert audit.verify()["intact"] is True

    db.execute("DELETE FROM audit_log WHERE id = 3")
    broken = audit.verify()
    assert broken["intact"] is False
    assert broken["broken_at"] == 4


def test_the_audit_chain_detects_an_edited_row(clean_db):
    from app import db

    for i in range(3):
        audit.record("decision", subject=f"S{i}", outcome="rejected")
    db.execute("UPDATE audit_log SET outcome = 'approved' WHERE id = 2")
    broken = audit.verify()
    assert broken["intact"] is False
    assert broken["broken_at"] == 2


def test_an_unserialisable_detail_does_not_lose_the_record(clean_db):
    """Losing some detail beats losing the record of the decision."""
    audit.record("decision", subject="X", detail={"obj": object()})
    assert audit.verify()["intact"] is True
    assert audit.recent()[0]["subject"] == "X"


# ==========================================================================
# A dead Upstox session
# ==========================================================================
def test_a_401_halts_trading(clean_db, monkeypatch):
    """Upstox invalidates the token daily at 03:30, and immediately if the
    registered IP changes. So this fires on an ordinary morning, not only
    when something has gone wrong -- and without it every call after the
    first just fails, orders among them, one quiet failure at a time.
    """
    from app.upstox import client

    client._halt_on_dead_session({"errors": [{"message": "Invalid token"}]})
    assert killswitch.state() is killswitch.State.HALTED
    assert any(r["trigger"] == "session_expired" for r in killswitch.reasons())


def test_a_401_needs_a_human_to_clear(clean_db):
    """Clearing it means logging in again, which is a person's job."""
    from app.upstox import client

    client._halt_on_dead_session({})
    assert killswitch.status()["needs_manual_reset"] is True
    assert killswitch.entries_allowed() is False


def test_a_401_does_not_block_exits(clean_db):
    """Being unable to leave is worse than any condition that caused it --
    and a closing order placed against a dead session fails loudly rather
    than silently."""
    from app.upstox import client

    client._halt_on_dead_session({})
    assert killswitch.exits_allowed() is True


def test_a_401_does_not_overwrite_an_existing_halt(clean_db):
    """The first reason is the useful one. A token dying because trading
    already stopped should not hide why it stopped."""
    from app.upstox import client

    killswitch.trip("daily_loss", "down 3.2%")
    client._halt_on_dead_session({})
    triggers = [r["trigger"] for r in killswitch.reasons()]
    assert triggers == ["daily_loss"]


def test_recording_the_halt_never_raises(clean_db, monkeypatch):
    """It runs inside the HTTP error path: a failure here must not replace
    the 401 the caller is waiting for with something unrelated."""
    from app.upstox import client

    def explode(*a, **kw):
        raise RuntimeError("db gone")

    monkeypatch.setattr(killswitch, "trip", explode)
    client._halt_on_dead_session({})      # must not raise
