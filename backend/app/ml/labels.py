"""Turning price history into something a model can learn.

Two decisions here shape everything downstream.

**Labels are cost-aware.** "Did price go up?" is the wrong question, because
most small up-moves are not tradeable once brokerage, STT and slippage are
paid. The label asks instead: did it move far enough to be worth taking? That
turns a near-50/50 coin flip into a genuinely imbalanced problem, which is
harder to fit but is the problem we actually have.

**Every label records when it resolves.** A label on bar i built from the next
N bars is not knowable until bar i+N. Validation needs that to purge training
rows whose outcome overlaps the test window -- without it, the model is
scored on information it was trained with, and the result looks excellent and
means nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Direction(IntEnum):
    DOWN = -1
    FLAT = 0
    UP = 1


@dataclass
class LabelConfig:
    horizon: int = 5
    # Round-trip cost in percent. Backtester defaults: brokerage ~0.04% on a
    # typical order, taxes 0.12%, slippage 0.03% per leg.
    cost_pct: float = 0.20
    # How far past cost a move must go before it counts. 1.0 means "just
    # covers cost"; 1.5 demands a real margin.
    threshold_multiple: float = 1.5
    # Cap on how long a triple-barrier label may stay open.
    max_holding: int | None = None

    @property
    def barrier_pct(self) -> float:
        return self.cost_pct * self.threshold_multiple


@dataclass
class Label:
    index: int
    direction: Direction
    forward_return_pct: float
    resolves_at: int          # bar index where the outcome becomes known
    bars_held: int

    @property
    def is_tradeable(self) -> bool:
        return self.direction is not Direction.FLAT


def forward_return(closes: list[float], i: int, horizon: int) -> float | None:
    """Percent return from bar i to bar i+horizon. None if it runs off the end."""
    j = i + horizon
    if j >= len(closes) or not closes[i]:
        return None
    return (closes[j] - closes[i]) / closes[i] * 100


def fixed_horizon_labels(closes: list[float], cfg: LabelConfig) -> list[Label]:
    """Label each bar by where price sits `horizon` bars later.

    Simple and transparent. Its weakness is that it ignores the path: a trade
    that drops 5% before recovering gets the same label as one that rises
    smoothly, even though the first would have been stopped out. Use the
    triple-barrier version when a stop is part of the strategy.
    """
    out: list[Label] = []
    barrier = cfg.barrier_pct

    for i in range(len(closes)):
        ret = forward_return(closes, i, cfg.horizon)
        if ret is None:
            break
        if ret > barrier:
            direction = Direction.UP
        elif ret < -barrier:
            direction = Direction.DOWN
        else:
            direction = Direction.FLAT
        out.append(Label(
            index=i,
            direction=direction,
            forward_return_pct=ret,
            resolves_at=i + cfg.horizon,
            bars_held=cfg.horizon,
        ))
    return out


def triple_barrier_labels(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    cfg: LabelConfig,
) -> list[Label]:
    """Label by whichever barrier price touches first.

    Three barriers: a profit target above, a stop below, and a time limit. The
    label is whichever is hit first, which is exactly how a real position with
    a stop resolves -- so the model learns the outcome the strategy would
    actually have experienced.

    When both barriers fall inside the same bar we assume the **stop** hit
    first. The intrabar path is unknown, and assuming the good outcome is how
    a backtest flatters itself.
    """
    out: list[Label] = []
    n = len(closes)
    barrier = cfg.barrier_pct
    max_hold = cfg.max_holding or cfg.horizon

    for i in range(n):
        entry = closes[i]
        if not entry:
            continue
        upper = entry * (1 + barrier / 100)
        lower = entry * (1 - barrier / 100)

        last = min(i + max_hold, n - 1)
        if last <= i:
            break

        direction = Direction.FLAT
        resolved_at = last
        for j in range(i + 1, last + 1):
            hit_low = lows[j] <= lower
            hit_high = highs[j] >= upper
            if hit_low:                      # stop wins a tie, deliberately
                direction, resolved_at = Direction.DOWN, j
                break
            if hit_high:
                direction, resolved_at = Direction.UP, j
                break

        out.append(Label(
            index=i,
            direction=direction,
            forward_return_pct=(closes[resolved_at] - entry) / entry * 100,
            resolves_at=resolved_at,
            bars_held=resolved_at - i,
        ))
    return out


def label_distribution(labels: list[Label]) -> dict[str, float]:
    """Class balance. A model that cannot beat the majority class has learnt nothing."""
    if not labels:
        return {}
    n = len(labels)
    counts = {d: 0 for d in Direction}
    for lab in labels:
        counts[lab.direction] += 1
    majority = max(counts.values()) / n
    return {
        "total": n,
        "up": counts[Direction.UP] / n,
        "down": counts[Direction.DOWN] / n,
        "flat": counts[Direction.FLAT] / n,
        "tradeable": sum(1 for lab in labels if lab.is_tradeable) / n,
        # Any honest accuracy claim has to clear this.
        "majority_baseline": majority,
    }


def sample_weights(labels: list[Label]) -> list[float]:
    """Weight each row by how little its outcome window overlaps others.

    Overlapping labels are not independent observations: bars i and i+1 share
    most of the same future. Counting them as two full samples inflates the
    effective sample size and the confidence that follows from it.
    """
    if not labels:
        return []
    n = len(labels)
    end = max(lab.resolves_at for lab in labels) + 1

    # How many label windows cover each bar.
    overlap = [0] * end
    for lab in labels:
        for t in range(lab.index, min(lab.resolves_at + 1, end)):
            overlap[t] += 1

    weights = []
    for lab in labels:
        span = range(lab.index, min(lab.resolves_at + 1, end))
        uniqueness = sum(1.0 / overlap[t] for t in span if overlap[t]) / max(len(span), 1)
        weights.append(uniqueness)

    total = sum(weights)
    return [w * n / total for w in weights] if total else [1.0] * n
