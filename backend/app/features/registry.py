"""Feature catalogue and versioning.

Two problems this solves.

**Reproducibility.** Six months from now, "why did the model trade that?"
needs an answer. Every feature carries a version, and a model records the
feature-set hash it was trained against. Change a feature's maths and its
version must change, or a model silently starts seeing different numbers from
the ones it learned on.

**Discipline about count.** Each feature needs roughly a thousand independent
observations to be worth including. Two years of daily bars is about five
hundred, so a daily strategy can afford five to ten features -- not fifty.
The registry knows which horizon a feature belongs to and can refuse a set
that is too large for the data behind it.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable


class Horizon(str, Enum):
    TICK = "tick"          # order book, every update
    INTRADAY = "intraday"  # 1-15 minute bars
    DAILY = "daily"        # swing and positional
    SLOW = "slow"          # macro context, changes weekly or monthly


# Observations available per horizon over a two-year sample, and the resulting
# budget at ~1000 observations per feature.
HORIZON_BUDGET = {
    Horizon.TICK: 40,
    Horizon.INTRADAY: 25,
    Horizon.DAILY: 10,
    Horizon.SLOW: 5,
}


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    version: str
    horizon: Horizon
    description: str
    why: str = ""
    depends_on: tuple[str, ...] = ()
    # Only set where there is published work behind the feature; blank means
    # it is here on reasoning alone, which is useful to know when pruning.
    evidence: str = ""
    warmup: int = 0

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "horizon": self.horizon.value,
            "description": self.description,
            "why": self.why,
            "depends_on": list(self.depends_on),
            "evidence": self.evidence,
            "warmup": self.warmup,
        }


class FeatureRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, FeatureSpec] = {}

    def register(self, spec: FeatureSpec) -> FeatureSpec:
        existing = self._specs.get(spec.name)
        if existing and existing.version != spec.version:
            # Two versions of one name in the same process means something
            # imported twice with different maths.
            raise ValueError(
                f"{spec.name} already registered at v{existing.version}, "
                f"now v{spec.version}. Rename it or bump deliberately."
            )
        self._specs[spec.name] = spec
        return spec

    def get(self, name: str) -> FeatureSpec:
        if name not in self._specs:
            raise KeyError(f"Unknown feature: {name}")
        return self._specs[name]

    def all(self) -> list[FeatureSpec]:
        return sorted(self._specs.values(), key=lambda s: (s.horizon.value, s.name))

    def by_horizon(self, horizon: Horizon) -> list[FeatureSpec]:
        return [s for s in self.all() if s.horizon is horizon]

    def names(self) -> list[str]:
        return sorted(self._specs)

    # ---------------------------------------------------------- integrity
    def set_hash(self, names: Iterable[str]) -> str:
        """Stable id for a feature set, including versions.

        A model stores this. If it stops matching, the model is being fed
        something other than what it was trained on.
        """
        keys = sorted(self.get(n).key for n in names)
        return hashlib.sha256("|".join(keys).encode()).hexdigest()[:16]

    def check_budget(self, names: Iterable[str]) -> list[str]:
        """Warn when a horizon is carrying more features than its data supports."""
        counts: dict[Horizon, int] = {}
        for name in names:
            spec = self.get(name)
            counts[spec.horizon] = counts.get(spec.horizon, 0) + 1

        problems = []
        for horizon, count in counts.items():
            budget = HORIZON_BUDGET[horizon]
            if count > budget:
                problems.append(
                    f"{horizon.value}: {count} features but only ~{budget} "
                    f"supportable at this horizon -- overfitting risk"
                )
        return problems

    def max_warmup(self, names: Iterable[str]) -> int:
        """Longest warmup in the set -- nothing is valid before this."""
        return max((self.get(n).warmup for n in names), default=0)


registry = FeatureRegistry()


def feature(**kwargs) -> FeatureSpec:
    return registry.register(FeatureSpec(**kwargs))


# --------------------------------------------------------------------------
# The catalogue. Keeping specs beside the code that computes them has a habit
# of drifting, so they live here together.
# --------------------------------------------------------------------------

# ---- tick / order book ----
feature(name="ofi", version="1.0", horizon=Horizon.TICK, warmup=50,
        description="Order flow imbalance over a rolling window of book updates",
        why="Net queue pressure. Explains short-horizon moves better than volume",
        evidence="Cont, Kukanov & Stoikov -- strongest microstructure result")
feature(name="ofi_z", version="1.0", horizon=Horizon.TICK, warmup=200,
        depends_on=("ofi",),
        description="OFI z-scored over 200 updates",
        why="Raw OFI is in shares, so it is not comparable across instruments")
feature(name="book_imbalance", version="1.0", horizon=Horizon.TICK,
        description="(bid - ask) / (bid + ask) over visible depth",
        why="Cheap cousin of OFI -- no history, available on every tick",
        evidence="widely replicated")
feature(name="spread_bps", version="1.0", horizon=Horizon.TICK,
        description="Bid-ask spread in basis points",
        why="Direct cost of being wrong, and a liquidity warning when it widens")
feature(name="spread_z", version="1.0", horizon=Horizon.TICK, warmup=100,
        depends_on=("spread_bps",),
        description="Spread versus its own recent norm",
        why="Wide-for-this-stock matters; wide-in-absolute-terms does not")
feature(name="weighted_mid", version="1.0", horizon=Horizon.TICK,
        description="Mid price weighted by resting size",
        why="10 bid against 1000 offered is not really the middle")
feature(name="trade_flow", version="1.0", horizon=Horizon.TICK, warmup=50,
        description="Net aggressor flow, Lee-Ready signed",
        why="Who is crossing the spread, buyers or sellers")

# ---- intraday ----
feature(name="vwap_dev_pct", version="1.0", horizon=Horizon.INTRADAY,
        description="Price distance from session VWAP, percent",
        why="Institutions benchmark to VWAP, so it acts as a magnet")
feature(name="opening_range_pos", version="1.0", horizon=Horizon.INTRADAY,
        description="Where price sits inside the first 15 minutes' range",
        why="The opening range is the day's reference level")
feature(name="opening_range_break", version="1.0", horizon=Horizon.INTRADAY,
        depends_on=("opening_range_pos",),
        description="Broken above (1), below (-1) or inside (0)",
        why="Breakout on volume is one of the few durable intraday patterns")
feature(name="ret_5m", version="1.0", horizon=Horizon.INTRADAY, warmup=5,
        description="Five-minute return, percent")
feature(name="rsi_14", version="1.0", horizon=Horizon.INTRADAY, warmup=15,
        description="Wilder RSI over 14 periods")
feature(name="atr_14", version="1.0", horizon=Horizon.INTRADAY, warmup=14,
        description="Average true range over 14 periods",
        why="Volatility-based position sizing depends on this")
feature(name="volume_ratio", version="1.0", horizon=Horizon.INTRADAY, warmup=20,
        description="Volume against its own 20-period average",
        why="A move without volume is noise")

# ---- daily / swing ----
feature(name="momentum_12_1", version="1.0", horizon=Horizon.DAILY, warmup=252,
        description="Twelve-month return excluding the most recent month",
        why="Skipping the last month avoids short-term reversal",
        evidence="strongest cross-sectional factor in the literature")
feature(name="rel_strength", version="1.0", horizon=Horizon.DAILY, warmup=20,
        description="Return relative to the stock's own sector index",
        why="Separates stock-specific strength from sector drift")
feature(name="vol_20d", version="1.0", horizon=Horizon.DAILY, warmup=20,
        description="Twenty-day realised volatility",
        evidence="low-volatility anomaly")
feature(name="dist_from_high", version="1.0", horizon=Horizon.DAILY, warmup=252,
        description="Distance from the 52-week high, percent")

# ---- slow / macro context ----
feature(name="vix_level", version="1.0", horizon=Horizon.SLOW,
        description="India VIX",
        why="Single best regime filter we have")
feature(name="vix_z", version="1.0", horizon=Horizon.SLOW, warmup=60,
        depends_on=("vix_level",),
        description="VIX against its own 60-day norm")
feature(name="crude_rupee_stress", version="1.0", horizon=Horizon.SLOW,
        description="Combined crude and USDINR pressure",
        why="India imports ~85% of its oil and pays in dollars, so the two "
            "compound rather than acting separately")
feature(name="breadth_ratio", version="1.0", horizon=Horizon.SLOW,
        description="Share of index constituents advancing",
        why="A rally carried by five names is fragile")
feature(name="asia_bias", version="1.0", horizon=Horizon.SLOW,
        description="Breadth across Asian markets during our session")
