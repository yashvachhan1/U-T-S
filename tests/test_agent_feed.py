"""Getting live data to the agents, and what happens when it will not fit.

The failure this guards against is the quiet one. An agent with no
subscription reads "not subscribed" on every line, returns a neutral view,
and reports itself healthy indefinitely; nothing in the system raises, and
the dashboard looks fine. So the tests here are mostly about whether the
right instruments are actually requested, and whether a shortage degrades in
the right order instead of throwing.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents import registry  # noqa: E402
from app.collectors import constituents  # noqa: E402
from app.engine import agent_feed, feed_service  # noqa: E402
from app.upstox import feed as feed_mod  # noqa: E402


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def clean_feed(monkeypatch):
    """A feed whose subscriptions are recorded but never sent."""
    sent: list[tuple[str, list[str]]] = []

    async def fake_subscribe(keys, mode="full"):
        sent.append((mode, list(keys)))
        return feed_mod.feed.subscription.add(feed_mod.Mode(mode), keys)

    monkeypatch.setattr(feed_mod.feed, "subscription", feed_mod.Subscription())
    monkeypatch.setattr(feed_service, "subscribe", fake_subscribe)
    monkeypatch.setattr(feed_service, "_latest", {})
    return sent


# --------------------------------------------------------------------------
# Tiering
# --------------------------------------------------------------------------
def test_constituents_go_in_the_cheap_mode():
    """Breadth needs a price and a previous close, and nothing else.

    Full depth on five hundred stocks would spend most of the feed budget
    on bid/ask levels that no agent reads.
    """
    modes = registry.feed_modes()
    infy = constituents.instrument_keys("nifty500").get("INFY")
    assert infy, "Nifty 500 list is not downloaded"
    assert modes[infy] == "ltpc"


def test_headline_instruments_go_in_full():
    """Indices and factor ETFs are few, and volume is part of their signal."""
    from app.agents.analysts import _shared as sh

    modes = registry.feed_modes()
    assert modes[sh.index_key("Nifty 50")] == "full"
    assert modes[sh.index_key("India VIX")] == "full"


def test_the_richer_mode_wins_when_two_agents_disagree():
    """One key, one mode -- the feed cannot hold both, so this resolves here.

    Nifty 50 is a constituent-list entry for the index agent (ltpc) and a
    benchmark for sector rotation (full). Dropping to ltpc would silently
    cost sector rotation its volume.
    """
    from app.agents.analysts import _shared as sh

    key = sh.index_key("Nifty 50")
    wanted = registry.required_instruments()[key]
    assert "sector_rotation" in wanted
    assert registry.feed_modes()[key] == "full"


def test_the_plan_covers_everything_the_agents_asked_for():
    planned = agent_feed.plan()
    flat = {k for keys in planned.values() for k in keys}
    assert flat == set(registry.feed_modes())


def test_the_plan_fits_the_feed_limit():
    """A mixed-mode subscription is capped across all modes, not per mode."""
    planned = agent_feed.plan()
    total = sum(len(keys) for keys in planned.values())
    assert total <= agent_feed.budget() <= feed_mod.COMBINED_KEY_LIMIT


def test_headroom_is_left_for_instruments_outside_the_agent_layer():
    """Whatever the user has on a chart still has to fit."""
    assert agent_feed.budget() < feed_mod.COMBINED_KEY_LIMIT


# --------------------------------------------------------------------------
# Degradation
# --------------------------------------------------------------------------
def test_a_tight_budget_keeps_the_headline_instruments(monkeypatch):
    """Thirty keys serve eleven agents; the Nifty 500 tail serves one median."""
    monkeypatch.setattr(agent_feed, "budget", lambda: 40)
    planned = agent_feed.plan()
    assert len(planned.get("full", [])) == 31
    total = sum(len(keys) for keys in planned.values())
    assert total == 40


def test_a_tight_budget_keeps_the_smaller_index_before_the_larger(monkeypatch):
    """Bank Nifty's fifteen names buy a whole agent's output."""
    monkeypatch.setattr(agent_feed, "budget", lambda: 60)
    planned = agent_feed.plan()
    kept = {k for keys in planned.values() for k in keys}
    banknifty = set(constituents.instrument_keys("banknifty").values())
    assert banknifty <= kept


def test_dropped_instruments_are_counted_not_hidden(monkeypatch, clean_feed):
    monkeypatch.setattr(agent_feed, "budget", lambda: 40)
    out = run(agent_feed.apply())
    assert out["dropped_over_budget"] == len(registry.feed_modes()) - 40


def test_a_failing_subscribe_does_not_take_down_the_other_mode(
        monkeypatch, clean_feed):
    """Startup must survive a dead socket; the agents degrade to neutral."""
    async def half_broken(keys, mode="full"):
        if mode == "ltpc":
            raise feed_mod.SubscriptionLimitExceeded("too many keys")
        return set(keys)

    monkeypatch.setattr(feed_service, "subscribe", half_broken)
    out = run(agent_feed.apply())
    assert out["full"]["added"] == 31
    assert out["ltpc"]["added"] == 0
    assert "too many keys" in out["ltpc"]["error"]


def test_apply_never_raises_even_with_no_socket(clean_feed):
    out = run(agent_feed.apply())
    assert out["full"]["added"] == 31
    assert out["ltpc"]["added"] == 501


# --------------------------------------------------------------------------
# What actually got through
# --------------------------------------------------------------------------
def test_status_separates_subscribed_from_receiving(clean_feed):
    """Asking for an instrument is not the same as having a price for it.

    The two differ for a long stretch after the open, and that gap is the
    thing worth looking at.
    """
    run(agent_feed.apply())
    st = agent_feed.status()
    assert st["subscribed"] == 532
    assert st["receiving"] == 0            # nothing has ticked
    assert st["not_subscribed_total"] == 0


def test_status_shows_the_gap_per_agent(clean_feed):
    st = agent_feed.status()
    assert st["by_agent"]["industry"]["needs"] == 501
    assert st["by_agent"]["industry"]["subscribed"] == 0
    assert st["by_agent"]["asia_markets"]["needs"] == 2


def test_receiving_counts_instruments_that_actually_ticked(clean_feed, monkeypatch):
    run(agent_feed.apply())

    from app.agents.analysts import _shared as sh

    key = sh.index_key("Nifty 50")
    monkeypatch.setattr(
        feed_service, "snapshot", lambda keys=None: [{"instrument_key": key}])
    assert agent_feed.status()["receiving"] == 1


def test_agents_with_no_live_instruments_are_absent_from_the_breakdown(clean_feed):
    """A text-only agent is not failing just because it has no subscription."""
    st = agent_feed.status()
    assert "india_news" not in st["by_agent"]
    assert "chief_strategist" not in st["by_agent"]


# --------------------------------------------------------------------------
# Constituent refresh
# --------------------------------------------------------------------------
def test_a_fresh_list_is_not_downloaded_again(clean_feed, monkeypatch):
    """Constituents change at index review, so weekly is already generous."""
    calls = []

    async def should_not_run(indexes):
        calls.append(indexes)
        return {}

    monkeypatch.setattr(constituents, "refresh_all", should_not_run)
    monkeypatch.setattr(constituents, "age_days", lambda index: 1.0)
    out = run(agent_feed.refresh_universe())
    assert calls == []
    assert out["downloaded"] == {}


def test_a_stale_list_is_downloaded(clean_feed, monkeypatch):
    asked = []

    async def fake_refresh(indexes):
        asked.extend(indexes)
        return {i: 1 for i in indexes}

    monkeypatch.setattr(constituents, "refresh_all", fake_refresh)
    monkeypatch.setattr(constituents, "age_days", lambda index: 99.0)
    run(agent_feed.refresh_universe())
    assert set(asked) == set(constituents.CORE_INDEXES)


def test_a_missing_list_is_downloaded(clean_feed, monkeypatch):
    asked = []

    async def fake_refresh(indexes):
        asked.extend(indexes)
        return {}

    monkeypatch.setattr(constituents, "refresh_all", fake_refresh)
    monkeypatch.setattr(constituents, "age_days", lambda index: None)
    run(agent_feed.refresh_universe())
    assert set(asked) == set(constituents.CORE_INDEXES)


def test_a_failed_download_still_subscribes_the_cached_list(clean_feed, monkeypatch):
    """Last week's constituent list is a correct list for almost every purpose."""
    async def fails(indexes):
        return {i: "failed: HTTP 403" for i in indexes}

    monkeypatch.setattr(constituents, "refresh_all", fails)
    monkeypatch.setattr(constituents, "age_days", lambda index: 99.0)
    out = run(agent_feed.refresh_universe())
    assert out["subscribed"]["ltpc"]["added"] == 501


# --------------------------------------------------------------------------
# The assumption the cheap tier rests on
# --------------------------------------------------------------------------
@pytest.mark.skipif(not __import__("app.upstox.ticks", fromlist=["x"]).PROTO_AVAILABLE,
                    reason="protobuf module not built")
def test_breadth_works_from_ltpc_alone(monkeypatch):
    """Five hundred stocks are subscribed cheaply only if this holds.

    An ltpc frame carries the last price and the previous close, which is
    exactly what an advance/decline count needs. If it did not, the whole
    constituent tier would be subscribed to a feed that cannot answer the
    question it was subscribed for.
    """
    import MarketDataFeedV3_pb2 as pb  # noqa: N806

    from app.upstox import ticks as tk

    members = constituents.load("nifty50")[:3]
    resp = pb.FeedResponse()
    resp.type = pb.Type.live_feed
    resp.currentTs = 1_760_000_000_000
    for i, member in enumerate(members):
        resp.feeds[member.instrument_key].ltpc.ltp = 100.0 + i      # +0, +1, +2
        resp.feeds[member.instrument_key].ltpc.cp = 101.0

    decoded = {t.instrument_key: t for t in tk.decode(resp.SerializeToString()).ticks}
    assert all(t.mode == "ltpc" for t in decoded.values())

    changes = constituents.live_changes("nifty50", lambda k: decoded.get(k))
    out = constituents.breadth(changes)
    assert out["covered"] == 3
    assert out["declining"] == 1          # the one below its close
    assert out["advancing"] == 1          # the one above it
    assert out["unchanged"] == 1
