"""Analyst agents — messy text and numbers into structured views.

Twelve of them, each watching one channel. They do not call each other: an
agent that needs another's opinion reads it from the store (`_shared.peer_view`),
which keeps the dependency one-way and lets a backtest replay the exact view
that existed at the time.
"""
from . import (  # noqa: F401
    asia_markets,
    commodity,
    crypto_risk,
    currency,
    earnings,
    global_macro,
    india_news,
    index_composition,
    industry,
    power_policy,
    sector_rotation,
    us_markets,
)

__all__ = [
    "asia_markets", "commodity", "crypto_risk", "currency", "earnings",
    "global_macro", "india_news", "index_composition", "industry",
    "power_policy", "sector_rotation", "us_markets",
]
