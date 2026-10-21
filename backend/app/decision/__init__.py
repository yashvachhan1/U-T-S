"""Decision layer: everything upstream becomes one proposal, then an answer.

The split from `risk/` is the point of this package. Here is where the I/O
lives -- positions from the broker, prices from the feed, views from the
agent store -- and where the pieces are assembled into a `Proposal`. The
risk engine is then handed that proposal and decides, with no network
access and no way to be hurried.

So this module can be wrong about the world. It cannot be wrong about the
limits.
"""
from . import scanner  # noqa: F401
from .engine import (  # noqa: F401
    Signal,
    agent_multiplier,
    build_book,
    decide,
    decide_many,
    propose,
    sector_for,
    sweep,
)

__all__ = ["Signal", "agent_multiplier", "build_book", "decide", "decide_many",
           "propose", "scanner", "sector_for", "sweep"]
