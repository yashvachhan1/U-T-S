"""Execution: sending orders, watching them, and keeping the books in step.

The division of labour across the three modules here:

    tracker.py    what we intended, and what became of it
    executor.py   sending, watching fills, and protecting every one of them
    reconcile.py  our book against the broker's -- they drift, and a drift
                  means every risk limit is being computed from fiction

Two invariants this package exists to hold:

  * **No filled position without a stop.** The risk engine refuses to size
    a trade without one; this makes sure the stop actually reaches the
    broker, and halts if it cannot.
  * **Our positions equal the broker's positions.** When they do not, the
    system stops opening new ones rather than reasoning from a book that
    does not exist.
"""
from . import executor, loop, reconcile, tracker  # noqa: F401

__all__ = ["executor", "loop", "reconcile", "tracker"]
