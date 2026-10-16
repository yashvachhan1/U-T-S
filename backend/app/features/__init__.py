"""Feature engineering.

Three rules hold everywhere in this package:

  * every update is **O(1)** -- an O(n) recompute per tick stalls the feed
  * streaming output **matches** engine/indicators.py, so the backtest and the
    live bot cannot silently diverge
  * an unwarmed feature is **None**, never zero -- zero is a value, and a
    wrong one
"""
from .engine import FeatureEngine, FeatureState, StaleUpdate, engine  # noqa: F401
from .registry import FeatureSpec, Horizon, registry  # noqa: F401

__all__ = ["engine", "FeatureEngine", "FeatureState", "StaleUpdate",
           "registry", "FeatureSpec", "Horizon"]
