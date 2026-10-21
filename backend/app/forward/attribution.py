"""Does each agent actually earn its place?

Phase 7's instruction is blunt: "jo add nahi karta, nikal do". Acting on it
needs a number per agent, and getting that number honestly is harder than it
looks.

**The view is replayed, not re-derived.** For each closed trade we ask
`agent_outputs.as_of(agent, entry_time)` -- what that agent had actually
said by the moment we entered. Recomputing a view today would use data that
did not exist then and would flatter every agent equally.

**The comparison is against the base rate, not against zero.** An agent
whose bullish calls average +0.4% looks useful until you notice every trade
in the sample averaged +0.4%. What is measured here is the difference
between the trades it had an opinion on and the rest.

**Small samples are refused, not reported with caveats.** A caveat next to a
number gets dropped the moment someone quotes it. Below `MIN_TRADES` the
verdict is "not enough evidence" and no edge figure is produced at all --
the same discipline the Power & Policy agent applies to its own base rates.

What this cannot tell you: whether an agent that never fires is useless or
merely waiting for its day. An agent with no opinions is reported as silent,
which is a finding about coverage rather than about accuracy.
"""
from __future__ import annotations

from statistics import mean
from typing import Any

from .. import db
from ..store import agent_outputs

# Below this many trades with an opinion, no edge is reported. Twenty is
# already thin; it is a floor, not a target.
MIN_TRADES = 20

# How far before the entry an agent's view may have been formed and still
# count as the view that was acting at that moment.
MAX_VIEW_AGE_SECONDS = 3600

# A view this weak was never going to move a decision, so counting it would
# dilute the measurement with opinions nobody acted on.
MIN_CONFIDENCE = 0.3


def closed_trades(limit: int = 1000) -> list[dict[str, Any]]:
    """Closed trades with the entry time each one was opened at.

    The entry time is what the replay is anchored to. It comes from the
    tracked entry order rather than the exit, because the question is what
    an agent believed when the position was *opened*.
    """
    from ..execution import tracker

    tracker.ensure_schema()
    rows = db.query(
        """SELECT x.id          AS exit_id,
                  x.symbol      AS symbol,
                  x.role        AS exit_role,
                  x.updated_at  AS closed_at,
                  e.id          AS entry_id,
                  e.created_at  AS entry_at,
                  e.side        AS side,
                  e.avg_fill_price AS entry_price,
                  x.avg_fill_price AS exit_price,
                  e.filled_quantity AS quantity
           FROM exec_orders x
           JOIN exec_orders e ON e.id = x.parent_id
           WHERE x.filled_quantity > 0 AND e.filled_quantity > 0
             AND x.role IN ('stop', 'target', 'exit', 'square_off')
           ORDER BY x.id DESC LIMIT ?""", (limit,))

    out = []
    for row in rows:
        if not row["entry_price"] or not row["exit_price"]:
            continue
        direction = 1 if row["side"] == "BUY" else -1
        move_pct = ((row["exit_price"] - row["entry_price"])
                    / row["entry_price"] * 100 * direction)
        out.append({**row, "return_pct": round(move_pct, 4),
                    "won": move_pct > 0})
    return out


def views_at_entry(trades: list[dict[str, Any]], agents: list[str]
                   ) -> dict[str, list[tuple[dict, dict]]]:
    """agent -> [(trade, the view it held when that trade opened)].

    Trades where the agent had no usable view are simply absent, which is
    what makes the "silent" verdict possible.
    """
    out: dict[str, list[tuple[dict, dict]]] = {a: [] for a in agents}
    for trade in trades:
        for agent in agents:
            row = agent_outputs.as_of(agent, trade["entry_at"],
                                      MAX_VIEW_AGE_SECONDS)
            if not row:
                continue
            payload = row.get("payload") or {}
            if float(payload.get("confidence") or 0) < MIN_CONFIDENCE:
                continue
            out[agent].append((trade, payload))
    return out


def score_agent(name: str, paired: list[tuple[dict, dict]],
                all_trades: list[dict[str, Any]]) -> dict[str, Any]:
    """One agent's record against the base rate of every trade.

    "Aligned" means the agent's view pointed the same way as the position:
    bullish on a long, bearish on a short. A neutral view is not alignment
    and not opposition -- it is an abstention, and it is counted separately
    rather than folded into either side.
    """
    base_returns = [t["return_pct"] for t in all_trades]
    base_rate = round(mean(base_returns), 4) if base_returns else 0.0

    if not paired:
        return {
            "agent": name, "verdict": "silent", "opinions": 0,
            "base_rate_pct": base_rate,
            "note": "is agent ne kisi trade par raay nahi di -- coverage ka "
                    "sawaal hai, accuracy ka nahi",
        }

    aligned, opposed, abstained = [], [], []
    flagged = []
    for trade, view in paired:
        stance = str(view.get("view") or "neutral")
        long_side = trade["side"] == "BUY"
        if stance == "neutral":
            abstained.append(trade)
        elif (stance == "bullish") == long_side:
            aligned.append(trade)
        else:
            opposed.append(trade)
        if view.get("risk_flag"):
            flagged.append(trade)

    result: dict[str, Any] = {
        "agent": name,
        "opinions": len(paired),
        "aligned": len(aligned),
        "opposed": len(opposed),
        "abstained": len(abstained),
        "base_rate_pct": base_rate,
        "coverage_pct": round(len(paired) / len(all_trades) * 100, 1)
        if all_trades else 0.0,
    }

    directional = aligned + opposed
    if len(directional) < MIN_TRADES:
        result["verdict"] = "not_enough_evidence"
        result["note"] = (f"sirf {len(directional)} directional raay -- "
                          f"{MIN_TRADES} chahiye. Koi edge number nahi diya "
                          f"ja raha, kyunki itne me jo nikalta wo shor hai.")
        return result

    aligned_mean = round(mean([t["return_pct"] for t in aligned]), 4) if aligned else None
    opposed_mean = round(mean([t["return_pct"] for t in opposed]), 4) if opposed else None

    # The edge: how much better the trades it backed did than the average
    # trade. Against the base rate, not against zero.
    edge = round(aligned_mean - base_rate, 4) if aligned_mean is not None else None

    result.update({
        "aligned_return_pct": aligned_mean,
        "opposed_return_pct": opposed_mean,
        "edge_vs_base_pct": edge,
        "aligned_win_rate_pct": round(
            sum(1 for t in aligned if t["won"]) / len(aligned) * 100, 1)
        if aligned else None,
    })

    if flagged:
        flagged_mean = round(mean([t["return_pct"] for t in flagged]), 4)
        result["risk_flagged_trades"] = len(flagged)
        result["risk_flagged_return_pct"] = flagged_mean
        # A risk flag is meant to precede a worse-than-usual outcome. If
        # flagged trades do better than average, the flag is costing
        # opportunities and buying nothing.
        result["risk_flag_useful"] = flagged_mean < base_rate

    if edge is None:
        result["verdict"] = "no_aligned_trades"
    elif edge > 0.1:
        result["verdict"] = "adds_value"
    elif edge < -0.1:
        result["verdict"] = "costs_money"
        result["note"] = ("jin trades ko is agent ne support kiya wo average "
                          "se bure nikle -- hatane par vichar karein")
    else:
        result["verdict"] = "no_measurable_effect"
        result["note"] = ("edge base rate ke barabar hai -- ye agent decision "
                          "par asar nahi daal raha")
    return result


def report(limit: int = 1000) -> dict[str, Any]:
    """Every agent's record over the forward test so far."""
    from ..agents import registry

    trades = closed_trades(limit)
    agents = registry.names()

    if len(trades) < MIN_TRADES:
        return {
            "ready": False,
            "trades": len(trades),
            "needed": MIN_TRADES,
            "note": (f"{len(trades)} closed trades hain, {MIN_TRADES} chahiye. "
                     f"Tab tak kisi agent ke baare me kuch nahi kaha ja sakta."),
            "agents": [],
        }

    paired = views_at_entry(trades, agents)
    scores = [score_agent(name, paired[name], trades) for name in agents]

    order = {"costs_money": 0, "adds_value": 1, "no_measurable_effect": 2,
             "not_enough_evidence": 3, "no_aligned_trades": 4, "silent": 5}
    scores.sort(key=lambda s: (order.get(s["verdict"], 9),
                               -(s.get("edge_vs_base_pct") or 0)))

    return {
        "ready": True,
        "trades": len(trades),
        "base_rate_pct": round(mean([t["return_pct"] for t in trades]), 4),
        "win_rate_pct": round(
            sum(1 for t in trades if t["won"]) / len(trades) * 100, 1),
        "agents": scores,
        "summary": {
            "adds_value": [s["agent"] for s in scores if s["verdict"] == "adds_value"],
            "costs_money": [s["agent"] for s in scores if s["verdict"] == "costs_money"],
            "no_effect": [s["agent"] for s in scores
                          if s["verdict"] == "no_measurable_effect"],
            "undecided": [s["agent"] for s in scores
                          if s["verdict"] in ("not_enough_evidence",
                                              "no_aligned_trades", "silent")],
        },
    }
