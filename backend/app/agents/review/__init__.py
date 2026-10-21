"""Review layer — looking backwards, changing nothing.

    post_trade.py    what happened today, written for a person
    risk_auditor.py  is the risk setup still the one we meant?

Neither takes part in a trading decision. Both return a size multiplier of
exactly 1.0, always, because their `view` is a judgement about the past
rather than about the market -- letting that reach position sizing would
mean yesterday's results quietly shrinking today's trades.

The auditor can read config and cannot write it. Not by convention: there
is no field in its output through which a setting could be changed.
"""
from . import concentration, post_trade, risk_auditor  # noqa: F401

agents = (post_trade.agent, risk_auditor.agent)

__all__ = ["concentration", "post_trade", "risk_auditor", "agents"]
