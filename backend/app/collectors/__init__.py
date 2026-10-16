"""External data collectors.

Each one turns a single source into Points. Registration happens here so the
runner has them all without the app importing each module by hand.
"""
from .base import Collector, CollectorRunner, runner  # noqa: F401
from . import crypto, fred, global_markets, news  # noqa: F401

runner.register(crypto.collector)
runner.register(fred.collector)
runner.register(global_markets.collector)
runner.register(news.collector)

__all__ = ["runner", "crypto", "fred", "global_markets", "news",
           "Collector", "CollectorRunner"]
