"""External data collectors.

Each one turns a single source into Points. Registration happens here so the
runner has them all without the app importing each module by hand.
"""
from .base import Collector, CollectorRunner, runner  # noqa: F401
from . import crypto, fred  # noqa: F401

runner.register(crypto.collector)
runner.register(fred.collector)

__all__ = ["runner", "crypto", "fred", "Collector", "CollectorRunner"]
