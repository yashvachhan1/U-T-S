"""How many bets are actually on the book.

Three IT stocks look like three positions and behave like one. That is the
failure the Risk Auditor exists to catch, and it is arithmetic rather than
judgement, so it is done here and handed to the model as a fact.

The measure is the inverse Herfindahl index of exposure by industry:

    effective_positions = 1 / sum(weight_i ** 2)

Five equal positions in five industries gives 5.0. Five equal positions in
one industry gives 1.0 -- which is the honest count, because they will all
move together on the day it matters. Anything between says how much of the
diversification is real.

Industry comes from NSE's own constituent column, so it is the exchange's
grouping rather than one we invented and have to defend.
"""
from __future__ import annotations

from typing import Any

# Below this, the book is one bet wearing several hats.
CONCENTRATED_BELOW = 2.0

# How far effective count may fall short of the nominal one before it is
# worth saying out loud. 0.4 means "at least 40% of the diversification is
# illusory".
ILLUSION_THRESHOLD = 0.4


def effective_positions(weights: list[float]) -> float:
    """Inverse Herfindahl over exposure weights. Pure.

    Weights need not sum to 1 -- they are normalised here, because the
    caller has rupee values and normalising at the call site is one more
    place to get it wrong.
    """
    values = [abs(float(w)) for w in weights if w]
    total = sum(values)
    if not values or total <= 0:
        return 0.0
    shares = [v / total for v in values]
    return round(1 / sum(s * s for s in shares), 2)


def by_group(positions: list[Any], attr: str = "industry") -> dict[str, float]:
    """Exposure value per industry (or sector)."""
    out: dict[str, float] = {}
    for p in positions:
        key = getattr(p, attr, "") or "UNCLASSIFIED"
        out[key] = out.get(key, 0.0) + abs(getattr(p, "value", 0.0))
    return out


def analyse(positions: list[Any]) -> dict[str, Any]:
    """What the book really looks like, as numbers the model is told.

    Reports both counts. The gap between them is the finding: "5 positions"
    and "1.4 effective positions" is a sentence that explains a drawdown
    nobody expected.
    """
    if not positions:
        return {"available": False, "positions": 0}

    groups = by_group(positions)
    effective = effective_positions(list(groups.values()))
    nominal = len(positions)
    total = sum(abs(getattr(p, "value", 0.0)) for p in positions)

    # Names sharing an industry: the concrete list a human can act on.
    clusters = {}
    for p in positions:
        key = getattr(p, "industry", "") or "UNCLASSIFIED"
        clusters.setdefault(key, []).append(getattr(p, "symbol", ""))
    shared = {k: v for k, v in clusters.items() if len(v) > 1}

    illusion = (1 - effective / nominal) if nominal else 0.0

    return {
        "available": True,
        "positions": nominal,
        "effective_positions": effective,
        "industries": len(groups),
        "total_value": round(total, 2),
        "by_industry_pct": {k: round(v / total * 100, 1)
                            for k, v in groups.items()} if total else {},
        "shared_industries": shared,
        "concentrated": effective < CONCENTRATED_BELOW and nominal > 1,
        # How much of the apparent diversification is not there.
        "illusion_pct": round(illusion * 100, 1),
        "diversification_is_illusory": illusion >= ILLUSION_THRESHOLD and nominal > 1,
    }
