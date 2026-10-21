"""Agents running themselves.

Every agent's doc states a frequency and nothing acted on any of them --
the only way one ran was somebody pressing Run, which made the whole layer
decorative during an unattended paper run. These tests are about the
scheduler doing what the docs always said.

The recurring concern is restraint: a loop that ticks every fifteen
seconds must not fire fifteen agents every tick, must not re-run
everything after a restart, and must not run anything at all when it would
only write empty rows.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents import llm, scheduler  # noqa: E402
from app.agents.base import registry  # noqa: E402
from app.agents.schemas import StoredOutput  # noqa: E402
from app.clock import IST  # noqa: E402
from app.store import agent_outputs  # noqa: E402


def run(coro):
    return asyncio.run(coro)


# Mid-session Friday, and a Sunday for the weekly window.
OPEN_TIME = datetime(2026, 10, 16, 11, 0, tzinfo=IST)
AFTER_CLOSE = datetime(2026, 10, 16, 16, 30, tzinfo=IST)
NIGHT = datetime(2026, 10, 16, 23, 0, tzinfo=IST)
SUNDAY = datetime(2026, 10, 18, 11, 0, tzinfo=IST)

QUIET = json.dumps({"view": "neutral", "confidence": 0.3, "horizon": "intraday",
                    "sectors_positive": [], "sectors_negative": [],
                    "affected_symbols": [], "risk_flag": False,
                    "reasoning": "quiet"})


@pytest.fixture
def clean(tmp_path, monkeypatch):
    from app import db
    from app.execution import tracker
    from app.forward import journal
    from app.risk import killswitch
    from app.risk import state as risk_state
    from app.store import audit, events, macro

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "_local", type("L", (), {})())
    db.conn()
    # macro belongs here too: the commodity, crypto and Asia agents all
    # read it, and a stale schema flag makes them fail -- which used to
    # look like "never ran" and re-fire them every cycle.
    stores = (agent_outputs, tracker, risk_state, killswitch, audit, events,
              journal, macro)
    for store in stores:
        store._ready = False
    scheduler._reset_for_tests()
    yield
    for store in stores:
        store._ready = False
    scheduler._reset_for_tests()


def record(name: str, when) -> None:
    agent_outputs.save(StoredOutput(
        agent=name, ts=when, payload={"view": "neutral", "confidence": 0.1}))


# ==========================================================================
# Windows
# ==========================================================================
def test_market_hours_agents_sleep_outside_the_session():
    assert scheduler.window_open("market_hours", OPEN_TIME) is True
    assert scheduler.window_open("market_hours", NIGHT) is False


def test_always_agents_never_sleep():
    """Crypto, US futures and Asia all move when we are shut."""
    assert scheduler.window_open("always", NIGHT) is True
    assert scheduler.window_open("always", SUNDAY) is True


def test_the_daily_review_waits_for_the_day_to_end():
    assert scheduler.window_open("after_close", OPEN_TIME) is False
    assert scheduler.window_open("after_close", AFTER_CLOSE) is True


def test_the_daily_review_does_not_run_at_the_weekend():
    sunday_evening = SUNDAY.replace(hour=17)
    assert scheduler.window_open("after_close", sunday_evening) is False


def test_the_weekly_audit_runs_on_a_sunday():
    assert scheduler.window_open("weekly", SUNDAY) is True
    assert scheduler.window_open("weekly", OPEN_TIME) is False


def test_an_unknown_window_is_never_open():
    """Safer than guessing: an agent with a typo stays quiet instead of
    running every cycle forever."""
    assert scheduler.window_open("whenever") is False


# ==========================================================================
# Being due
# ==========================================================================
def test_an_agent_that_never_ran_is_due(clean):
    assert scheduler.overdue_by("crypto_risk", NIGHT) == float("inf")
    assert "crypto_risk" in scheduler.due(NIGHT)


def test_an_agent_that_just_ran_is_not_due(clean):
    record("crypto_risk", NIGHT - timedelta(seconds=60))
    assert scheduler.overdue_by("crypto_risk", NIGHT) < 0
    assert "crypto_risk" not in scheduler.due(NIGHT)


def test_an_agent_past_its_interval_is_due(clean):
    interval = registry.get("crypto_risk").config.interval_seconds
    record("crypto_risk", NIGHT - timedelta(seconds=interval + 60))
    assert "crypto_risk" in scheduler.due(NIGHT)


def test_last_run_comes_from_the_store_so_a_restart_changes_nothing(clean):
    """Keeping it in memory would make every restart a thundering herd."""
    record("crypto_risk", NIGHT - timedelta(seconds=60))
    scheduler._reset_for_tests()          # as if the process had restarted
    assert "crypto_risk" not in scheduler.due(NIGHT)


def test_a_failed_run_still_counts_as_a_run(clean):
    """An agent failing every cycle should back off like any other, not
    retry in a tight loop."""
    agent_outputs.save(StoredOutput(
        agent="crypto_risk", ts=NIGHT - timedelta(seconds=60),
        payload={"view": "neutral"}, error="boom"))
    assert "crypto_risk" not in scheduler.due(NIGHT)


def test_the_most_overdue_agent_goes_first(clean):
    """Whichever has slipped furthest behind its own schedule has the
    stalest view."""
    record("crypto_risk", NIGHT - timedelta(hours=10))
    record("commodity", NIGHT - timedelta(hours=2))
    order = scheduler.due(NIGHT)
    assert order.index("crypto_risk") < order.index("commodity")


def test_a_shut_window_keeps_an_agent_out_of_the_queue(clean):
    """Overdue is not the same as runnable."""
    assert scheduler.overdue_by("sector_rotation", NIGHT) == float("inf")
    assert "sector_rotation" not in scheduler.due(NIGHT)


# ==========================================================================
# Synthesis waits for the analysts
# ==========================================================================
def test_synthesis_is_held_back_while_analysts_are_stale(clean):
    """It reads their stored output, so running together would hand it
    the previous cycle's views."""
    assert scheduler.analysts_current(OPEN_TIME) is False


def test_synthesis_joins_once_the_analysts_are_current(clean):
    for name in registry.names():
        agent = registry.get(name)
        if scheduler.window_open(agent.config.window, OPEN_TIME):
            record(name, OPEN_TIME - timedelta(seconds=10))
    assert scheduler.analysts_current(OPEN_TIME) is True


def test_run_due_leaves_synthesis_out_while_analysts_are_stale(clean):
    llm.use_client(llm.StubClient([QUIET] * 20))
    out = run(scheduler.run_due(OPEN_TIME))
    assert "chief_strategist" not in out["ran"]
    assert "red_team" not in out["ran"]


# ==========================================================================
# Restraint
# ==========================================================================
def test_only_a_few_agents_run_per_cycle(clean):
    """The loop ticks every fifteen seconds; firing everything due would
    burn a free tier in minutes."""
    llm.use_client(llm.StubClient([QUIET] * 40))
    out = run(scheduler.run_due(NIGHT))
    assert len(out["ran"]) <= scheduler.MAX_PER_CYCLE
    assert out["due"] > len(out["ran"])


def test_successive_cycles_work_through_the_queue(clean):
    llm.use_client(llm.StubClient([QUIET] * 40))
    first = run(scheduler.run_due(NIGHT))["ran"]
    second = run(scheduler.run_due(NIGHT))["ran"]
    assert first and second
    assert not set(first) & set(second)


def test_nothing_is_scheduled_without_an_llm_key(clean):
    """They would all return neutral, and fifteen empty rows a quarter
    hour would bury the real ones."""
    llm.use_client(llm.GroqClient(pool=llm.KeyPool([])))
    out = run(scheduler.run_due(NIGHT))
    assert out["ran"] == []
    assert "no LLM key" in out["skipped"]


def test_a_broken_agent_does_not_stop_the_others(clean, monkeypatch):
    llm.use_client(llm.StubClient([QUIET] * 40))

    async def explode(name):
        if name == "crypto_risk":
            raise RuntimeError("registry is on fire")
        return None

    monkeypatch.setattr(registry, "run_one", explode)
    out = run(scheduler.run_due(NIGHT))
    assert "crypto_risk" not in out["ran"]
    assert len(out["ran"]) >= 1


# ==========================================================================
# Status
# ==========================================================================
def test_status_reports_every_agent(clean):
    out = scheduler.status(NIGHT)
    assert len(out["agents"]) == len(registry.names())
    for row in out["agents"]:
        assert row["interval_seconds"] > 0
        assert row["window"]


def test_status_says_when_the_window_is_shut(clean):
    rows = {r["agent"]: r for r in scheduler.status(NIGHT)["agents"]}
    assert rows["sector_rotation"]["window_open"] is False
    assert rows["crypto_risk"]["window_open"] is True


def test_every_registered_agent_has_a_schedule():
    """A new agent without one would silently never run."""
    for name in registry.names():
        cfg = registry.get(name).config
        assert cfg.interval_seconds > 0, name
        assert cfg.window in ("market_hours", "always", "after_close", "weekly"), name
