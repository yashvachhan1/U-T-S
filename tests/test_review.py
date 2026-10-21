"""The review pair, and the boundary that keeps them advisory.

These two look backwards. The thing that must hold is that looking
backwards never becomes acting: a review agent's `view` is a judgement
about past performance, not about the market, so letting it into position
sizing would mean yesterday's results shrinking today's trades through a
channel nobody designed.

The auditor has a second boundary: it can read config and cannot write it.
Tested as "there is no path", not as "it is told not to".
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config  # noqa: E402
from app.agents import llm  # noqa: E402
from app.agents.review import concentration  # noqa: E402
from app.agents.schemas import (  # noqa: E402
    NON_ANALYST_AGENTS,
    AuditOutput,
    ReviewOutput,
)
from app.risk.state import Position  # noqa: E402


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
    db.conn()
    stores = (tracker, risk_state, killswitch, audit, events, agent_outputs,
              journal)
    for store in stores:
        store._ready = False
    monkeypatch.setattr(config, "TRADING_CAPITAL", 1_000_000.0)
    yield
    for store in stores:
        store._ready = False


def positions(spec: list[tuple[str, str, float]]) -> list[Position]:
    return [Position(instrument_key=f"NSE_EQ|{sym}", symbol=sym, quantity=1,
                     average_price=value, industry=ind, sector=ind)
            for sym, ind, value in spec]


# ==========================================================================
# The boundary: review never sizes a trade
# ==========================================================================
def test_a_review_cannot_change_position_size():
    """Its view is about the past. The market does not care."""
    out = ReviewOutput(view="bearish", confidence=1.0, risk_flag=True)
    assert out.size_multiplier() == 1.0


def test_an_audit_cannot_change_position_size():
    """It warns; it does not trade. An advisory agent with a channel into
    live sizing would be a second risk engine nobody tests."""
    out = AuditOutput(view="bearish", confidence=1.0, risk_flag=True,
                      warnings=["everything is on fire"])
    assert out.size_multiplier() == 1.0


def test_review_agents_are_outside_the_analyst_multiplier():
    assert "post_trade_review" in NON_ANALYST_AGENTS
    assert "risk_auditor" in NON_ANALYST_AGENTS


def test_the_analyst_multiplier_ignores_them(clean):
    """A bearish review of yesterday must not shrink today's trade."""
    from app.agents.schemas import combined_multiplier

    views = [ReviewOutput(view="bearish", confidence=1.0, risk_flag=True),
             AuditOutput(view="bearish", confidence=1.0, risk_flag=True)]
    assert combined_multiplier(views) == 1.0


def test_the_auditor_has_no_field_that_writes_config():
    """The strongest form of "must not" is "cannot"."""
    fields = set(AuditOutput.model_fields)
    for name in fields:
        assert "set_" not in name and "config_" not in name or name == "config_drift"
    # config_drift is a boolean observation, not a setting.
    assert AuditOutput.model_fields["config_drift"].annotation is bool


# ==========================================================================
# Schema behaviour
# ==========================================================================
def test_an_agent_cannot_be_both_right_and_wrong_about_one_day():
    out = ReviewOutput(agents_correct=["us_markets", "commodity"],
                       agents_wrong=["commodity"])
    assert out.agents_correct == ["us_markets"]
    assert out.agents_wrong == []


def test_invented_agent_names_are_dropped():
    """A mangled name that still looks like one is worse than an absence,
    because something downstream will try to look it up."""
    out = ReviewOutput(agents_correct=["us_markets", "DROP TABLE agents;"])
    assert out.agents_correct == ["us_markets"]


def test_warnings_force_the_risk_flag():
    """Listing three problems and then reporting everything fine is a
    self-contradiction, and the problems carry the information."""
    out = AuditOutput(warnings=["limits at ceiling"], risk_flag=False)
    assert out.risk_flag is True


def test_a_clean_audit_keeps_its_flag_down():
    out = AuditOutput(warnings=[], risk_flag=False)
    assert out.risk_flag is False


def test_hidden_correlation_takes_tickers_only():
    out = AuditOutput(hidden_correlation=["TCS", "INFY", "not a ticker!!"])
    assert out.hidden_correlation == ["TCS", "INFY"]


def test_a_single_warning_string_is_accepted():
    out = AuditOutput(warnings="just the one")
    assert out.warnings == ["just the one"]


def test_the_lesson_is_capped():
    out = ReviewOutput(lesson="x" * 9999)
    assert len(out.lesson) == 500


# ==========================================================================
# Concentration: the arithmetic the model is not asked to do
# ==========================================================================
def test_five_positions_in_five_industries_are_five_bets():
    assert concentration.effective_positions([1, 1, 1, 1, 1]) == 5.0


def test_five_positions_in_one_industry_are_one_bet():
    """They will be discovered as one bet on the day the industry moves."""
    out = concentration.analyse(positions([
        ("TCS", "IT", 100.0), ("INFY", "IT", 100.0), ("WIPRO", "IT", 100.0),
        ("HCLTECH", "IT", 100.0), ("TECHM", "IT", 100.0),
    ]))
    assert out["positions"] == 5
    assert out["effective_positions"] == 1.0
    assert out["concentrated"] is True
    assert out["diversification_is_illusory"] is True


def test_a_genuinely_spread_book_is_not_flagged():
    out = concentration.analyse(positions([
        ("TCS", "IT", 100.0), ("HDFCBANK", "BANK", 100.0),
        ("TATASTEEL", "METAL", 100.0), ("SUNPHARMA", "PHARMA", 100.0),
    ]))
    assert out["effective_positions"] == 4.0
    assert out["concentrated"] is False
    assert out["illusion_pct"] == 0.0


def test_uneven_weights_lower_the_effective_count():
    """One position carrying most of the book is most of the risk."""
    out = concentration.effective_positions([900, 25, 25, 25, 25])
    assert 1.0 < out < 1.5


def test_names_sharing_an_industry_are_listed():
    """The concrete list is what a human can act on."""
    out = concentration.analyse(positions([
        ("TCS", "IT", 100.0), ("INFY", "IT", 100.0),
        ("HDFCBANK", "BANK", 100.0),
    ]))
    assert sorted(out["shared_industries"]["IT"]) == ["INFY", "TCS"]
    assert "BANK" not in out["shared_industries"]


def test_an_empty_book_is_not_concentrated():
    assert concentration.analyse([])["available"] is False
    assert concentration.effective_positions([]) == 0.0


def test_a_single_position_is_not_called_illusory():
    """One position is honestly one bet, not a diversification failure."""
    out = concentration.analyse(positions([("TCS", "IT", 100.0)]))
    assert out["effective_positions"] == 1.0
    assert out["diversification_is_illusory"] is False
    assert out["concentrated"] is False


# ==========================================================================
# Gathering
# ==========================================================================
def test_the_review_reads_the_day_without_a_model(clean):
    from app.agents.review.post_trade import PostTradeReviewAgent
    from app.risk import state as risk_state

    risk_state.start_day(1_000_000)
    risk_state.record_trade(-5_000, symbol="INFY", source="stop")
    risk_state.record_trade(8_000, symbol="TCS", source="exit")

    text = run(PostTradeReviewAgent().gather())
    assert "THE DAY" in text
    assert "INFY" in text and "TCS" in text


def test_the_review_says_so_on_a_quiet_day(clean):
    """A report that invents activity on a flat day stops being read."""
    from app.agents.review.post_trade import PostTradeReviewAgent

    text = run(PostTradeReviewAgent().gather())
    assert "koi trade close nahi hua" in text
    assert "saaf din" in text


def test_the_review_excludes_itself_from_the_agent_views(clean):
    """Reading its own previous verdict back in would compound it daily."""
    from app.agents.review.post_trade import PostTradeReviewAgent
    from app.agents.schemas import StoredOutput
    from app.clock import now_ist
    from app.store import agent_outputs

    agent_outputs.save(StoredOutput(
        agent="post_trade_review", ts=now_ist(),
        payload={"view": "bearish", "confidence": 0.9, "reasoning": "yesterday"}))
    text = run(PostTradeReviewAgent().gather())
    assert "post_trade_review" not in text


def test_the_auditor_shows_limits_against_their_ceilings(clean):
    """A limit at its ceiling is one that was raised until it stopped
    binding, and the ratio is what makes that visible."""
    from app.agents.review.risk_auditor import RiskAuditorAgent

    rows = RiskAuditorAgent().limits_against_ceilings()
    assert "max risk per trade %" in rows
    assert "of max" in rows["max risk per trade %"]


def test_the_auditor_flags_a_limit_at_its_ceiling(clean, monkeypatch):
    from app.agents.review.risk_auditor import RiskAuditorAgent

    monkeypatch.setattr(config, "MAX_RISK_PER_TRADE_PCT", 2.0)   # the ceiling
    rows = RiskAuditorAgent().limits_against_ceilings()
    assert "at the ceiling" in rows["max risk per trade %"]


def test_the_auditor_gathers_without_a_book(clean):
    from app.agents.review.risk_auditor import RiskAuditorAgent

    text = run(RiskAuditorAgent().gather())
    assert "CONFIGURED LIMITS" in text
    assert "koi position nahi" in text


def test_the_auditor_explains_the_effective_count(clean):
    """The model is given the number and told how to read it, rather than
    being asked to estimate it."""
    from app.agents.review.risk_auditor import RiskAuditorAgent

    text = run(RiskAuditorAgent().gather())
    assert "Inverse Herfindahl" in text


# ==========================================================================
# End to end
# ==========================================================================
BASE = {"view": "neutral", "confidence": 0.4, "horizon": "intraday",
        "sectors_positive": [], "sectors_negative": [], "affected_symbols": [],
        "risk_flag": False, "reasoning": "quiet day"}


def test_the_review_runs_end_to_end(clean):
    from app import agents

    payload = {**BASE, "trades_today": 2, "agents_correct": ["us_markets"],
               "agents_wrong": [], "lesson": "nothing notable"}
    llm.use_client(llm.StubClient([json.dumps(payload)]))
    out = run(agents.registry.run_one("post_trade_review"))
    assert out.lesson == "nothing notable"
    assert out.size_multiplier() == 1.0


def test_the_audit_runs_end_to_end(clean):
    from app import agents

    payload = {**BASE, "config_drift": False, "hidden_correlation": [],
               "warnings": []}
    llm.use_client(llm.StubClient([json.dumps(payload)]))
    out = run(agents.registry.run_one("risk_auditor"))
    assert out.config_drift is False
    assert out.size_multiplier() == 1.0


def test_a_failed_review_costs_nothing(clean):
    """Not in the critical path: it runs tomorrow."""
    from app import agents

    llm.use_client(llm.StubClient([]))
    out = run(agents.registry.run_one("post_trade_review"))
    assert out.size_multiplier() == 1.0


def test_all_seventeen_agents_are_registered():
    from app import agents

    names = agents.registry.names()
    assert len(names) == 17
    assert "post_trade_review" in names
    assert "risk_auditor" in names
