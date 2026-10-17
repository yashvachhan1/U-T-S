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
"""
from .base import Agent, AgentConfig, AgentRegistry, registry  # noqa: F401
from .schemas import AgentOutput, View, combined_multiplier, neutral  # noqa: F401
from . import llm  # noqa: F401
from .analysts import india_news  # noqa: F401

registry.register(india_news.agent)

__all__ = ["registry", "Agent", "AgentConfig", "AgentOutput", "View",
           "neutral", "combined_multiplier", "llm"]
