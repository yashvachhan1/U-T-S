"""Running the agents on their own schedules.

Every agent's doc states a frequency -- "har 15 min", "roz 16:00", "hafte
me ek baar" -- and until this existed, nothing acted on any of them. The
only way an agent ran was somebody pressing Run on the dashboard, which
meant the whole layer was decorative during an unattended paper run.

Four things shape how this works.

**Last-run comes from the store, not from memory.** `agent_outputs` has a
row per run, so a restart does not re-fire everything, and an agent that
has never run is simply one with no row. Keeping it in memory would make
every restart a thundering herd.

**Few per cycle.** The loop ticks every fifteen seconds; firing every due
agent at once would burn a free Groq tier in minutes and get the rest
rate-limited into silence. A handful per cycle, slowest-overdue first, is
enough to keep everything inside its interval.

**Synthesis runs after the analysts.** It reads their stored output, so a
cycle that runs both would hand it yesterday's views. It is held back
until the analysts in its window are current.

**Without an LLM key, nothing is scheduled.** The agents degrade to
neutral on their own, so running them anyway would write fifteen empty
rows every quarter hour and bury the real ones.
"""
from __future__ import annotations

import asyncio
from typing import Any

from ..clock import is_open, now_ist
from ..store import agent_outputs
from . import llm
from .base import registry
from .schemas import REVIEW_AGENT_NAMES, SYNTHESIS_AGENT_NAMES

# How many agents may run in one cycle. The loop ticks every 15s, so this
# is a rate cap rather than a throughput limit.
MAX_PER_CYCLE = 3

# Pause between calls inside one cycle, so a burst does not look like one.
STAGGER_SECONDS = 0.5

# The daily review runs after this, once.
AFTER_CLOSE_HOUR = 16

# Agents whose window has no data outside it are simply not due then; this
# is the session their "market_hours" refers to.
SEGMENT = "equity"

_stats: dict[str, Any] = {"cycles": 0, "runs": 0, "skipped_no_key": 0,
                          "last_run": None, "last_agents": []}


# --------------------------------------------------------------------------
# Is an agent due?
# --------------------------------------------------------------------------
def last_run_at(name: str) -> Any:
    """When this agent last produced anything, successful or not.

    Failures are stored too, which is deliberate: an agent failing every
    cycle should back off like any other, not retry in a tight loop.
    """
    row = agent_outputs.last_attempt(name)
    if not row:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(row["ts"]))
    except (TypeError, ValueError):
        return None


def window_open(window: str, at=None) -> bool:
    """Whether this kind of agent has anything to look at right now."""
    at = at or now_ist()
    if window == "always":
        return True
    if window == "market_hours":
        return is_open(SEGMENT, at)
    if window == "after_close":
        # The day's account, written once the day has one.
        return at.hour >= AFTER_CLOSE_HOUR and at.weekday() < 5
    if window == "weekly":
        # Sunday, when nothing is moving and a slow read costs nothing.
        return at.weekday() == 6
    return False


def overdue_by(name: str, at=None) -> float:
    """Seconds past this agent's interval. Negative means not due yet.

    An agent that has never run is maximally overdue, so a fresh install
    fills in rather than waiting out a full interval first.
    """
    agent = registry.get(name)
    last = last_run_at(name)
    if last is None:
        return float("inf")
    return ((at or now_ist()) - last).total_seconds() - agent.config.interval_seconds


def due(at=None, *, include_synthesis: bool = True) -> list[str]:
    """Agents that should run now, most overdue first."""
    at = at or now_ist()
    out = []
    for name in registry.names():
        if not include_synthesis and name in SYNTHESIS_AGENT_NAMES:
            continue
        agent = registry.get(name)
        if not window_open(agent.config.window, at):
            continue
        if overdue_by(name, at) >= 0:
            out.append(name)

    # Most overdue first: an agent that has slipped furthest behind its
    # own schedule is the one whose view is most stale.
    out.sort(key=lambda n: overdue_by(n, at), reverse=True)
    return out


def analysts_current(at=None) -> bool:
    """Whether the analysts in the current window have fresh views.

    Synthesis reads their stored output, so running it alongside them
    would hand it the previous cycle's opinions.
    """
    at = at or now_ist()
    names = [n for n in registry.names()
             if n not in SYNTHESIS_AGENT_NAMES and n not in REVIEW_AGENT_NAMES
             and window_open(registry.get(n).config.window, at)]
    if not names:
        return False
    return all(overdue_by(n, at) < 0 for n in names)


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------
async def run_due(at=None, *, limit: int = MAX_PER_CYCLE) -> dict[str, Any]:
    """Run the most overdue agents. Never raises.

    Returns what ran, so the loop can report it without having to ask
    again.
    """
    _stats["cycles"] += 1
    at = at or now_ist()

    if not llm.client.status().get("configured"):
        _stats["skipped_no_key"] += 1
        return {"ran": [], "skipped": "no LLM key -- agents would all return "
                                      "neutral, so nothing is scheduled"}

    # Analysts first. Synthesis joins only once they are current, so it is
    # reading this cycle's views rather than the last one's.
    candidates = due(at, include_synthesis=False)
    if analysts_current(at):
        candidates += [n for n in due(at) if n in SYNTHESIS_AGENT_NAMES]

    ran: list[str] = []
    for name in candidates[:limit]:
        try:
            await registry.run_one(name)
            ran.append(name)
            _stats["runs"] += 1
        except Exception:                          # noqa: BLE001
            # run_one already swallows agent failures and stores a neutral
            # row; anything reaching here is the registry itself, and one
            # bad name must not stop the rest.
            continue
        await asyncio.sleep(STAGGER_SECONDS)

    _stats["last_run"] = at.isoformat(timespec="seconds")
    _stats["last_agents"] = ran
    return {"ran": ran, "due": len(candidates), "limit": limit}


def status(at=None) -> dict[str, Any]:
    at = at or now_ist()
    rows = []
    for name in registry.names():
        agent = registry.get(name)
        last = last_run_at(name)
        rows.append({
            "agent": name,
            "interval_seconds": agent.config.interval_seconds,
            "window": agent.config.window,
            "window_open": window_open(agent.config.window, at),
            "last_run": last.isoformat(timespec="seconds") if last else None,
            "overdue_seconds": (None if last is None
                                else round(overdue_by(name, at))),
            "due_now": name in due(at),
        })
    rows.sort(key=lambda r: r["agent"])
    return {
        "configured": llm.client.status().get("configured", False),
        "max_per_cycle": MAX_PER_CYCLE,
        "due_now": due(at),
        "analysts_current": analysts_current(at),
        "agents": rows,
        **_stats,
    }


def _reset_for_tests() -> None:
    _stats.update({"cycles": 0, "runs": 0, "skipped_no_key": 0,
                   "last_run": None, "last_agents": []})
