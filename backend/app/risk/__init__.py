"""Risk: the layer with the final say.

The rule that organises everything here, from `docs/07-risk-limits.md`:

    agents, models and strategies  ->  suggest
    the risk engine                ->  decides

Nothing can override it. Not an LLM, not a strategy, not the config file --
a config outside the hard ceilings stops the app from starting rather than
loosening a limit.
"""
from . import checks, killswitch, sizing, state  # noqa: F401
from .engine import Decision, Proposal, evaluate  # noqa: F401

__all__ = ["checks", "killswitch", "sizing", "state",
           "Decision", "Proposal", "evaluate"]
