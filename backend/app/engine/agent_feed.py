"""Subscribes the feed to what the agents actually need.

Until now the agents declared their instruments and nobody acted on it, so
every one of them read "not subscribed" on every line, returned a neutral
view, and reported itself healthy. That is the quietest way for a trading
system to be wrong, and this module closes it.

Three things make it more than a loop over `subscribe()`.

**Modes are tiered.** Thirty-odd headline instruments -- indices, factor
ETFs, crude, the rupee -- go in `full`, which carries depth and volume. Five
hundred index constituents go in `ltpc`, which carries the last price and
the previous close. That is exactly what a breadth count needs, and asking
for depth on all of them would spend most of the feed budget on fields
nothing reads.

**It degrades by priority rather than failing.** The feed caps a mixed-mode
subscription at 1500 keys. If the universe ever outgrows that, the headline
instruments and the index constituents are subscribed first and the tail of
the Nifty 500 is dropped, with the count reported. A partial feed is worth
having; an exception during startup is not.

**It is safe to call before the socket is up.** The feed records
subscriptions locally and replays them on connect, so this runs at startup
without waiting for authentication.
"""
from __future__ import annotations

from typing import Any

from ..agents import registry
from ..collectors import constituents
from ..upstox import feed as feed_mod
from . import feed_service

# Smaller is better if we ever have to cut: these are the instruments the
# most agents depend on, and the ones with no substitute.
PRIORITY = (
    "full",        # indices, factor ETFs, futures, ETF proxies -- ~31 keys
    "ltpc",        # index constituents -- ~500 keys
)

# Leave room for instruments subscribed outside the agent layer: whatever
# the user has open on a chart, and anything an order path needs to price.
RESERVED_KEYS = 200


def budget() -> int:
    """How many keys the agent layer may claim."""
    return max(0, feed_mod.COMBINED_KEY_LIMIT - RESERVED_KEYS)


def plan() -> dict[str, list[str]]:
    """mode -> keys to subscribe, already trimmed to the budget.

    Constituent order is preserved from the CSV, so a trim drops the tail of
    the Nifty 500 rather than a random slice -- and the Nifty 50 and Bank
    Nifty members, which the index agents need in full, come from their own
    smaller lists and are never in the part that gets cut.
    """
    modes = registry.feed_modes()
    ordered = _priority_order()

    out: dict[str, list[str]] = {mode: [] for mode in PRIORITY}
    remaining = budget()

    for key in ordered:
        mode = modes.get(key)
        if mode is None or remaining <= 0:
            continue
        out.setdefault(mode, []).append(key)
        remaining -= 1

    return {mode: keys for mode, keys in out.items() if keys}


def _priority_order() -> list[str]:
    """Every wanted key, most important first.

    Headline instruments lead because thirty of them serve eleven agents.
    Then the index constituents, smallest index first: Bank Nifty's fifteen
    names buy a whole agent's output, while the tail of the Nifty 500 buys a
    slightly better industry median.
    """
    modes = registry.feed_modes()
    seen: set[str] = set()
    ordered: list[str] = []

    def take(keys) -> None:
        for key in keys:
            if key and key in modes and key not in seen:
                seen.add(key)
                ordered.append(key)

    take(k for k, mode in modes.items() if mode == "full")
    for index in ("banknifty", "nifty50", "nifty500"):
        take(constituents.instrument_keys(index).values())
    take(modes)                                   # anything not covered above
    return ordered


async def apply() -> dict[str, Any]:
    """Subscribe the plan. One mode failing does not stop the others."""
    results: dict[str, Any] = {}
    wanted = plan()

    for mode in PRIORITY:
        keys = wanted.get(mode)
        if not keys:
            continue
        try:
            added = await feed_service.subscribe(keys, mode=mode)
            results[mode] = {"requested": len(keys), "added": len(added)}
        except Exception as exc:                  # noqa: BLE001
            # A limit breach or a dead socket must not take down startup.
            # The agents degrade to neutral, which is their designed
            # behaviour, and the status endpoint shows why.
            results[mode] = {"requested": len(keys), "added": 0,
                             "error": str(exc)[:200]}

    results["dropped_over_budget"] = max(
        0, len(registry.feed_modes()) - sum(len(k) for k in wanted.values()))
    return results


async def refresh_universe(max_age_days: float = 7.0) -> dict[str, Any]:
    """Re-download constituent lists if they are missing or old, then subscribe.

    Constituents change at index review, so weekly is generous. A failed
    download is not fatal: the cached list from last week is a correct list
    for almost every purpose, and the age is reported either way.
    """
    out: dict[str, Any] = {"downloaded": {}}
    stale = [index for index in constituents.CORE_INDEXES
             if (age := constituents.age_days(index)) is None or age > max_age_days]
    if stale:
        out["downloaded"] = await constituents.refresh_all(stale)
    out["lists"] = constituents.status()
    out["subscribed"] = await apply()
    return out


def status() -> dict[str, Any]:
    """What the agents need against what the feed is delivering.

    `receiving` counts instruments that have actually ticked, not ones we
    asked for. Those differ for a long time after the open, and the gap is
    the thing worth seeing.
    """
    wanted = registry.required_instruments()
    modes = registry.feed_modes()
    subscribed = feed_mod.feed.subscription.all_keys
    live = {row["instrument_key"] for row in feed_service.snapshot()}

    by_agent: dict[str, dict[str, int]] = {}
    for key, agents in wanted.items():
        for name in agents:
            row = by_agent.setdefault(
                name, {"needs": 0, "subscribed": 0, "receiving": 0})
            row["needs"] += 1
            row["subscribed"] += key in subscribed
            row["receiving"] += key in live

    missing = sorted(set(wanted) - subscribed)
    return {
        "needed": len(wanted),
        "subscribed": len(set(wanted) & subscribed),
        "receiving": len(set(wanted) & live),
        "by_mode": {mode: sum(1 for m in modes.values() if m == mode)
                    for mode in PRIORITY},
        "budget": budget(),
        "not_subscribed": missing[:50],
        "not_subscribed_total": len(missing),
        "by_agent": by_agent,
        "constituent_lists": constituents.status(),
    }
