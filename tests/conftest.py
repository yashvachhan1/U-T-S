"""Shared test setup.

Every store caches "I have created my tables" in a module-level `_ready`
flag. That is the right thing in production, where the database path never
changes -- but a test that points `db.DB_PATH` at a temp file inherits a
flag set against the previous one, and the store then queries tables that
do not exist in a database that is empty.

This bit four different test files before it was worth fixing once. The
flags are reset before every test, so no individual fixture has to
remember the full list, and adding a store does not quietly break an
unrelated file three weeks later.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def _stores():
    """Every module that caches a schema-created flag."""
    from app.agents import scheduler
    from app.execution import tracker
    from app.forward import compare, journal
    from app.risk import killswitch
    from app.risk import state as risk_state
    from app.store import agent_outputs, audit, events, macro

    return [agent_outputs, audit, events, macro, risk_state, killswitch,
            tracker, journal, compare, scheduler]


@pytest.fixture(autouse=True)
def _reset_store_flags():
    """Clear every schema flag around each test.

    Autouse on purpose: the failure it prevents shows up in whichever test
    happens to run next, which is never the test that caused it.
    """
    for store in _stores():
        if hasattr(store, "_ready"):
            store._ready = False
        if hasattr(store, "_reset_for_tests"):
            store._reset_for_tests()
    yield
    for store in _stores():
        if hasattr(store, "_ready"):
            store._ready = False
