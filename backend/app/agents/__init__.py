"""LLM agent layer.

Three rules hold everywhere here, and they are what make an LLM safe to have
in a trading loop at all:

  * **An agent never places an order.** It returns structured fields; a
    deterministic engine decides.
  * **An agent can reduce risk, never increase it.** size_multiplier() is
    capped at 1.0, so a successful prompt injection costs an opportunity
    rather than money.
  * **Every output is stored.** Backtests replay stored rows instead of
    calling a model, which keeps them deterministic and point-in-time correct.

A fourth rule applies to the analysts specifically: **a model is asked for a
judgement, never for a number we already hold.** Prices, index moves and
breadth counts are computed in Python and put into the prompt. A model that
miscounts advancers does so silently, and a hallucinated number that looks
plausible is worse than no number at all.
"""
from .base import Agent, AgentConfig, AgentRegistry, registry  # noqa: F401
from .schemas import AgentOutput, View, combined_multiplier, neutral  # noqa: F401
from . import llm  # noqa: F401
from . import analysts  # noqa: F401

# Registration order is the order they appear in the dashboard: the fast
# India-specific reads first, then the foreign channels, then the slow ones.
for _agent in (
    analysts.india_news.agent,
    analysts.index_composition.nifty,
    analysts.index_composition.banknifty,
    analysts.industry.agent,
    analysts.sector_rotation.agent,
    analysts.asia_markets.agent,
    analysts.us_markets.agent,
    analysts.currency.agent,
    analysts.commodity.agent,
    analysts.crypto_risk.agent,
    analysts.earnings.agent,
    analysts.global_macro.agent,
    analysts.power_policy.agent,
):
    registry.register(_agent)

del _agent

__all__ = ["registry", "Agent", "AgentConfig", "AgentOutput", "View",
           "neutral", "combined_multiplier", "llm", "analysts"]
