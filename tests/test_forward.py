"""Phase 7: the measuring, and its refusal to flatter.

Everything here exists to answer two questions honestly -- does the paper
run match the backtest, and is each agent worth keeping. Both have an easy
wrong answer that looks like a right one, so most of these tests are about
the refusals:

  * no edge figure below a minimum sample, rather than one with a caveat
  * an agent measured against the base rate, not against zero
  * views replayed from what was stored, never recomputed today
  * uptime counted from heartbeat gaps, because a timer keeps running
    through the crash it is supposed to detect
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.clock import now_ist  # noqa: E402
from app.forward import attribution, compare, journal, report  # noqa: E402


@pytest.fixture
def clean_db(tmp_path, monkeypatch):
    from app import db
    from app.execution import tracker
    from app.risk import killswitch
    from app.store import agent_outputs, audit

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "_local", type("L", (), {})())
    stores = (journal, compare, tracker, agent_outputs, audit, killswitch)
    for store in stores:
        store._ready = False
    yield
    for store in stores:
        store._ready = False


# ==========================================================================
# The daily journal
# ==========================================================================
def test_opening_a_session_twice_does_not_reset_the_baseline(clean_db):
    """Re-running at midday would wipe the day's recorded move."""
    journal.open_session(1_000_000)
    journal.close_session(realised_pnl=-20_000, trades=3, wins=1, losses=2,
                          orders_sent=6, orders_rejected=0,
                          closing_equity=980_000)
    journal.open_session(980_000)
    row = journal.session(journal.today())
    assert row["starting_equity"] == 1_000_000
    assert row["realised_pnl"] == -20_000


def _set_last_beat(seconds_ago: float) -> None:
    """Pretend the previous heartbeat landed this long ago.

    The gap is written straight into the row rather than by faking the
    clock: `today()` and `_row()` both read the clock too, so a patched
    clock would be consumed by bookkeeping before the measurement.
    """
    from app import db

    when = (now_ist() - timedelta(seconds=seconds_ago)).isoformat(timespec="seconds")
    db.execute("UPDATE forward_sessions SET last_heartbeat = ? WHERE trade_date = ?",
               (when, journal.today()))


def test_uptime_is_counted_from_gaps_not_from_a_timer(clean_db):
    """A timer keeps counting through a crash. Whether it crashed is the
    thing being measured, so the measure has to come from the beats."""
    journal.open_session(1_000_000)
    journal.heartbeat(equity=1_000_000)

    _set_last_beat(15)
    journal.heartbeat(equity=1_000_000)
    _set_last_beat(15)
    journal.heartbeat(equity=1_000_000)

    # Two 15-second gaps. The band is wide because each write costs a
    # little real time and the stored timestamp is truncated to the second
    # -- but it still separates the two implementations cleanly: summing
    # wall-clock instead of gaps would give well under a second here, since
    # the whole test runs in milliseconds.
    row = journal.session(journal.today())
    assert 25 < row["uptime_seconds"] < 40
    assert row["heartbeats"] == 3


def test_a_long_gap_between_beats_is_not_counted_as_uptime(clean_db):
    """The process was down for that hour; it does not get credit for it."""
    journal.open_session(1_000_000)
    journal.heartbeat()

    _set_last_beat(3600)
    journal.heartbeat()

    row = journal.session(journal.today())
    assert row["uptime_seconds"] == 0
    assert row["heartbeats"] == 2      # the beats happened; the hour did not


def test_a_gap_just_inside_the_limit_still_counts(clean_db):
    journal.open_session(1_000_000)
    journal.heartbeat()
    _set_last_beat(journal.MAX_HEARTBEAT_GAP - 5)
    journal.heartbeat()
    assert journal.session(journal.today())["uptime_seconds"] > 0


def test_the_intraday_low_is_kept_not_just_the_close(clean_db):
    """A day that fell 4% and closed flat still spent the afternoon down,
    and a drawdown limit would have fired."""
    journal.open_session(1_000_000)
    journal.heartbeat(equity=960_000)
    journal.heartbeat(equity=1_000_000)
    row = journal.session(journal.today())
    assert row["low_equity"] == 960_000
    assert row["closing_equity"] == 1_000_000


def test_drawdown_uses_the_intraday_low(clean_db):
    rows = [
        {"trade_date": "2026-01-01", "starting_equity": 1_000_000,
         "closing_equity": 1_000_000, "low_equity": 1_000_000,
         "realised_pnl": 0, "trades": 0, "wins": 0, "losses": 0,
         "orders_sent": 0, "orders_rejected": 0, "halts": 0,
         "uptime_seconds": 0, "expected_seconds": 1},
        {"trade_date": "2026-01-02", "starting_equity": 1_000_000,
         "closing_equity": 995_000, "low_equity": 900_000,
         "realised_pnl": -5_000, "trades": 1, "wins": 0, "losses": 1,
         "orders_sent": 2, "orders_rejected": 0, "halts": 0,
         "uptime_seconds": 0, "expected_seconds": 1},
    ]
    curve = journal.equity_curve(rows)
    assert curve.max_drawdown_pct == pytest.approx(10.0, abs=0.01)


def test_a_halt_is_recorded_as_a_finding(clean_db):
    journal.open_session(1_000_000)
    journal.record_halt("daily_loss")
    journal.record_halt("daily_loss")      # same cause, same finding
    journal.record_halt("stale_feed")
    row = journal.session(journal.today())
    assert row["halts"] == 3
    assert set(row["halt_reasons"].split("|")) == {"daily_loss", "stale_feed"}


def test_the_summary_reports_what_phase_7_asks_about(clean_db):
    journal.open_session(1_000_000)
    journal.heartbeat(equity=1_010_000)
    journal.close_session(realised_pnl=10_000, trades=4, wins=3, losses=1,
                          orders_sent=10, orders_rejected=1,
                          closing_equity=1_010_000)
    out = journal.summary()
    assert out["sessions"] == 1
    assert out["win_rate_pct"] == 75.0
    assert out["reject_rate_pct"] == 10.0
    assert out["trades"] == 4


def test_a_flat_day_is_counted(clean_db):
    """Days the system did nothing are part of the record."""
    journal.open_session(1_000_000)
    journal.close_session(realised_pnl=0, trades=0, wins=0, losses=0,
                          orders_sent=0, orders_rejected=0)
    assert journal.summary()["flat_days"] == 1


# ==========================================================================
# Agent attribution
# ==========================================================================
def seed_trades(n: int, *, win_every: int = 2, base_move: float = 1.0):
    """n closed trades, alternating wins, via the execution tracker."""
    from app.execution import tracker

    out = []
    for i in range(n):
        up = (i % win_every) == 0
        entry = tracker.create(tag=f"e{i}", instrument_key=f"NSE_EQ|X{i}",
                               symbol=f"S{i}", side="BUY", quantity=10,
                               role="entry")
        tracker.update(entry, filled_quantity=10, avg_fill_price=100.0,
                       status="complete")
        exit_price = 100.0 + (base_move if up else -base_move)
        ex = tracker.create(tag=f"x{i}", instrument_key=f"NSE_EQ|X{i}",
                            symbol=f"S{i}", side="SELL", quantity=10,
                            role="stop", parent_id=entry)
        tracker.update(ex, filled_quantity=10, avg_fill_price=exit_price,
                       status="complete")
        out.append((entry, ex, up))
    return out


def seed_view(agent: str, when, *, view: str, confidence: float = 0.7,
              risk_flag: bool = False):
    from app.agents.schemas import StoredOutput
    from app.store import agent_outputs

    agent_outputs.save(StoredOutput(
        agent=agent, ts=when,
        payload={"view": view, "confidence": confidence,
                 "risk_flag": risk_flag, "reasoning": "seeded"}))


def test_closed_trades_are_read_with_their_entry_time(clean_db):
    """The question is what an agent believed when the position opened."""
    seed_trades(3)
    trades = attribution.closed_trades()
    assert len(trades) == 3
    assert all(t["entry_at"] for t in trades)
    assert all(t["return_pct"] != 0 for t in trades)


def test_a_thin_sample_produces_no_edge_number(clean_db):
    """A caveat beside a number gets dropped the moment someone quotes it."""
    seed_trades(5)
    out = attribution.report()
    assert out["ready"] is False
    assert out["trades"] == 5
    assert "chahiye" in out["note"]


def test_an_agent_is_measured_against_the_base_rate_not_zero(clean_db):
    """An agent whose calls average +0.4% looks useful until every trade
    in the sample averaged +0.4%."""
    trades = [{"return_pct": 1.0, "side": "BUY", "won": True} for _ in range(30)]
    paired = [(t, {"view": "bullish", "confidence": 0.8}) for t in trades]
    score = attribution.score_agent("always_bullish", paired, trades)
    assert score["verdict"] == "no_measurable_effect"
    assert score["edge_vs_base_pct"] == pytest.approx(0.0, abs=0.001)


def test_an_agent_that_picks_the_winners_adds_value(clean_db):
    trades = ([{"return_pct": 2.0, "side": "BUY", "won": True} for _ in range(15)]
              + [{"return_pct": -2.0, "side": "BUY", "won": False} for _ in range(15)])
    paired = ([(t, {"view": "bullish", "confidence": 0.8}) for t in trades[:15]]
              + [(t, {"view": "bearish", "confidence": 0.8}) for t in trades[15:]])
    score = attribution.score_agent("good", paired, trades)
    assert score["verdict"] == "adds_value"
    assert score["edge_vs_base_pct"] > 0


def test_an_agent_that_backs_the_losers_is_named(clean_db):
    """Phase 7's instruction is to remove these, so they have to be found."""
    trades = ([{"return_pct": 2.0, "side": "BUY", "won": True} for _ in range(15)]
              + [{"return_pct": -2.0, "side": "BUY", "won": False} for _ in range(15)])
    paired = ([(t, {"view": "bearish", "confidence": 0.8}) for t in trades[:15]]
              + [(t, {"view": "bullish", "confidence": 0.8}) for t in trades[15:]])
    score = attribution.score_agent("bad", paired, trades)
    assert score["verdict"] == "costs_money"
    assert score["edge_vs_base_pct"] < 0


def test_a_silent_agent_is_a_coverage_finding_not_an_accuracy_one(clean_db):
    trades = [{"return_pct": 1.0, "side": "BUY", "won": True} for _ in range(30)]
    score = attribution.score_agent("quiet", [], trades)
    assert score["verdict"] == "silent"
    assert score["opinions"] == 0
    assert "coverage" in score["note"]


def test_a_neutral_view_is_an_abstention_not_an_opinion(clean_db):
    """Folding neutrals into either side would make an agent that never
    commits look like one that is often right."""
    trades = [{"return_pct": 1.0, "side": "BUY", "won": True} for _ in range(30)]
    paired = [(t, {"view": "neutral", "confidence": 0.8}) for t in trades]
    score = attribution.score_agent("fence_sitter", paired, trades)
    assert score["abstained"] == 30
    assert score["aligned"] == 0
    assert score["verdict"] == "not_enough_evidence"


def test_a_short_is_aligned_with_a_bearish_view(clean_db):
    trades = [{"return_pct": 2.0, "side": "SELL", "won": True} for _ in range(25)]
    paired = [(t, {"view": "bearish", "confidence": 0.8}) for t in trades]
    score = attribution.score_agent("shorter", paired, trades)
    assert score["aligned"] == 25
    assert score["opposed"] == 0


def test_a_risk_flag_is_judged_on_whether_it_preceded_trouble(clean_db):
    """If flagged trades do better than average, the flag is costing
    opportunities and buying nothing."""
    good = [{"return_pct": 2.0, "side": "BUY", "won": True} for _ in range(20)]
    bad = [{"return_pct": -3.0, "side": "BUY", "won": False} for _ in range(10)]
    trades = good + bad
    paired = ([(t, {"view": "bullish", "confidence": 0.8}) for t in good]
              + [(t, {"view": "bullish", "confidence": 0.8, "risk_flag": True})
                 for t in bad])
    score = attribution.score_agent("flagger", paired, trades)
    assert score["risk_flagged_trades"] == 10
    assert score["risk_flag_useful"] is True


def test_low_confidence_views_are_not_counted(clean_db):
    """A view that weak was never going to move a decision."""
    from app.execution import tracker

    seeded = seed_trades(2)
    entry_at = tracker.get(seeded[0][0])["created_at"]
    from datetime import datetime

    seed_view("asia_markets", datetime.fromisoformat(entry_at),
              view="bullish", confidence=0.05)
    trades = attribution.closed_trades()
    paired = attribution.views_at_entry(trades, ["asia_markets"])
    assert paired["asia_markets"] == []


def test_views_are_replayed_not_recomputed(clean_db):
    """A view stored after the entry must not count towards that entry."""
    from datetime import datetime

    from app.execution import tracker

    seeded = seed_trades(1)
    entry_at = datetime.fromisoformat(tracker.get(seeded[0][0])["created_at"])

    # Formed an hour after we entered: it cannot have informed the trade.
    seed_view("asia_markets", entry_at + timedelta(hours=1), view="bullish")
    trades = attribution.closed_trades()
    assert attribution.views_at_entry(trades, ["asia_markets"])["asia_markets"] == []

    # Formed just before: that is the view that was acting.
    seed_view("asia_markets", entry_at - timedelta(minutes=5), view="bullish")
    paired = attribution.views_at_entry(trades, ["asia_markets"])
    assert len(paired["asia_markets"]) == 1


# ==========================================================================
# Paper versus backtest
# ==========================================================================
BACKTEST = {"total_return_pct": 20.0, "max_drawdown_pct": 6.0,
            "win_rate_pct": 55.0, "trades": 100}


def paper(**kw):
    base = {"sessions": 45, "total_return_pct": 18.0, "max_drawdown_pct": 6.5,
            "win_rate_pct": 53.0, "trades": 95}
    base.update(kw)
    return base


def test_no_baseline_means_no_comparison(clean_db):
    """A comparison against a baseline nobody chose is worse than none."""
    out = compare.compare(paper())
    assert out["ready"] is False
    assert "baseline" in out["note"]


def test_a_close_match_reports_no_findings(clean_db):
    compare.register(BACKTEST, label="v1")
    out = compare.compare(paper())
    assert out["ready"] is True
    assert out["findings"] == []
    assert "sahi sabit nahi hoti" in out["verdict"]


def test_a_much_deeper_drawdown_is_critical(clean_db):
    """Sizing errors scale with capital, so this one stops the process."""
    compare.register(BACKTEST)
    out = compare.compare(paper(max_drawdown_pct=15.0))
    assert out["worst_severity"] == "critical"
    assert any(f["check"] == "drawdown" for f in out["findings"])
    assert "Rukein" in out["verdict"]


def test_much_lower_returns_are_flagged(clean_db):
    compare.register(BACKTEST)
    out = compare.compare(paper(total_return_pct=5.0))
    assert any(f["check"] == "returns" for f in out["findings"])


def test_a_good_win_rate_with_bad_returns_points_at_the_exits(clean_db):
    """Entries fine, exits not -- a different fix from a bad entry filter."""
    compare.register(BACKTEST)
    out = compare.compare(paper(total_return_pct=5.0, win_rate_pct=54.0))
    assert any(f["check"] == "exits" for f in out["findings"])


def test_far_fewer_trades_is_a_warning_not_a_failure(clean_db):
    """Often correct -- the backtest had no blackout list or edge test --
    but then the two are not measuring the same strategy."""
    compare.register(BACKTEST)
    out = compare.compare(paper(trades=20))
    finding = next(f for f in out["findings"] if f["check"] == "trade_count")
    assert finding["severity"] == "warning"


def test_registering_a_second_baseline_replaces_the_first(clean_db):
    """Keeping several invites picking the flattering one afterwards."""
    compare.register(BACKTEST, label="first")
    compare.register({**BACKTEST, "total_return_pct": 99.0}, label="second")
    assert compare.baseline()["label"] == "second"
    assert compare.baseline()["total_return_pct"] == 99.0


# ==========================================================================
# The checklist
# ==========================================================================
def test_the_checklist_blocks_on_a_short_paper_run(clean_db):
    journal.open_session(1_000_000)
    journal.close_session(realised_pnl=0, trades=0, wins=0, losses=0,
                          orders_sent=0, orders_rejected=0)
    out = report.checklist()
    assert "paper_run_long_enough" in out["blocking_failures"]
    assert out["machine_checks_clear"] is False


def test_the_checklist_notices_a_position_that_had_no_stop(clean_db):
    from app.store import audit

    audit.record("stop_failed", subject="INFY", outcome="halted")
    out = report.checklist()
    item = next(i for i in out["items"] if i["check"] == "every_position_had_a_stop")
    assert item["passed"] is False


def test_the_checklist_notices_a_reconciliation_mismatch(clean_db):
    from app.store import audit

    audit.record("reconcile_mismatch", outcome="1 mismatched")
    out = report.checklist()
    item = next(i for i in out["items"] if i["check"] == "books_reconciled")
    assert item["passed"] is False


def test_the_checklist_notices_a_broken_audit_chain(clean_db):
    """If the log is not trustworthy, nothing checked against it is."""
    from app import db
    from app.store import audit

    for i in range(3):
        audit.record("decision", subject=f"S{i}")
    db.execute("DELETE FROM audit_log WHERE id = 2")
    out = report.checklist()
    item = next(i for i in out["items"] if i["check"] == "audit_log_intact")
    assert item["passed"] is False


def test_the_kill_switch_drill_check_is_not_blocking(clean_db):
    """The drill runs against a scratch database, so a clean audit log is
    not evidence the switches were never tested."""
    out = report.checklist()
    item = next(i for i in out["items"] if i["check"] == "kill_switches_exercised")
    assert item["blocking"] is False


def test_the_report_never_says_ready_to_go_live(clean_db):
    """A system that grades its own homework is not evidence."""
    out = report.checklist()
    assert "still_your_call" in out
    assert len(out["still_your_call"]) >= 3
    assert not any("ready" in str(k).lower() and "live" in str(k).lower()
                   for k in out)


def test_the_text_report_renders_without_data(clean_db):
    """It has to be readable on day one, not only after two months."""
    text = report.text()
    assert "PAPER FORWARD TEST" in text
    assert "koi session record nahi" in text


def test_the_text_report_renders_with_data(clean_db):
    compare.register(BACKTEST, label="v1")
    journal.open_session(1_000_000)
    journal.heartbeat(equity=1_020_000)
    journal.close_session(realised_pnl=20_000, trades=5, wins=3, losses=2,
                          orders_sent=11, orders_rejected=0,
                          closing_equity=1_020_000)
    text = report.text()
    assert "sessions" in text
    assert "VERSUS BACKTEST" in text
    assert "GO-LIVE CHECKLIST" in text
