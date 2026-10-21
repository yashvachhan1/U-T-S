"""The background loop: poll, reconcile, sweep, square off.

One task, a fixed order, and each step isolated from the others. The order
is not arbitrary:

    1. poll       -- find fills first, so everything after sees them
    2. stops      -- (inside poll) protect anything newly filled
    3. square-off -- close intraday positions once the time passes
    4. reconcile  -- compare the books, now that fills are recorded
    5. sweep      -- re-check the kill switches against the new numbers

Reconciling before polling would compare the broker's fresh fills against
our stale record and report a mismatch that is really just latency. That
single ordering mistake would make the halt fire on an ordinary day, and a
safety mechanism that cries wolf gets switched off.

Each step is wrapped: one failing must not stop the others, because the
later steps are the ones that halt trading when something is wrong.
"""
from __future__ import annotations

import asyncio
from typing import Any

from ..clock import is_open, now_ist
from ..store import audit
from . import executor, reconcile

# How often the loop runs while the market is open.
INTERVAL_SECONDS = 15.0
# Out of hours there is nothing to fill, but reconciliation still matters.
IDLE_INTERVAL_SECONDS = 120.0

# Reconciling on every cycle is wasteful; this is every N cycles.
RECONCILE_EVERY = 4

_task: asyncio.Task | None = None
_stop = asyncio.Event()
_stats: dict[str, Any] = {"cycles": 0, "errors": 0, "last_run": None,
                          "last_error": None, "last_result": {}}


async def _step(name: str, coro) -> Any:
    """Run one step, swallowing its failure so the rest still run."""
    try:
        return await coro
    except Exception as exc:                       # noqa: BLE001
        _stats["errors"] += 1
        _stats["last_error"] = f"{name}: {str(exc)[:200]}"
        audit.record("loop_step_failed", subject=name, outcome="error",
                     detail={"error": str(exc)[:300]})
        return None


async def cycle(*, reconcile_now: bool = True) -> dict[str, Any]:
    """One pass. Never raises."""
    from .. import decision

    result: dict[str, Any] = {"at": now_ist().isoformat(timespec="seconds")}

    result["poll"] = await _step("poll", executor.poll())

    if executor.past_square_off():
        result["square_off"] = await _step(
            "square_off", executor.square_off_intraday())

    if reconcile_now:
        result["reconcile"] = await _step("reconcile", reconcile.run())

    result["sweep"] = await _step("sweep", decision.sweep())

    # The forward-test journal is written from here rather than on a timer
    # of its own: uptime measured by a separate timer keeps counting
    # through a crash, and whether it crashed is the thing being measured.
    result["journal"] = await _step("journal", _record_session(result))

    _stats["cycles"] += 1
    _stats["last_run"] = result["at"]
    _stats["last_result"] = result
    return result


async def _record_session(cycle_result: dict[str, Any]) -> dict[str, Any]:
    """Keep the forward-test journal current. Never raises."""
    from .. import config
    from ..forward import journal
    from ..risk import state as risk_state

    equity = config.TRADING_CAPITAL
    journal.open_session(equity)

    snap = risk_state.snapshot(equity=equity)
    journal.heartbeat(equity=equity + snap["realised_pnl_today"])

    for trigger in (cycle_result.get("sweep") or {}).get("tripped", []):
        journal.record_halt(trigger)

    return journal.close_session(
        realised_pnl=snap["realised_pnl_today"],
        trades=snap["trades_today"], wins=snap["wins"], losses=snap["losses"],
        orders_sent=snap["orders_sent"], orders_rejected=snap["orders_rejected"])


async def _run() -> None:
    cycles = 0
    while not _stop.is_set():
        cycles += 1
        await cycle(reconcile_now=cycles % RECONCILE_EVERY == 1)

        delay = INTERVAL_SECONDS if is_open("equity") else IDLE_INTERVAL_SECONDS
        try:
            await asyncio.wait_for(_stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            continue


async def start() -> dict[str, Any]:
    """Start the loop. Idempotent."""
    global _task
    if _task and not _task.done():
        return {"running": True, "note": "already running"}
    _stop.clear()
    _task = asyncio.create_task(_run())
    audit.record("execution_loop", outcome="started")
    return {"running": True}


async def stop() -> dict[str, Any]:
    """Stop the loop. Does not touch orders or positions."""
    global _task
    _stop.set()
    if _task:
        try:
            await asyncio.wait_for(_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _task.cancel()
    _task = None
    audit.record("execution_loop", outcome="stopped")
    return {"running": False}


def running() -> bool:
    return bool(_task and not _task.done())


def status() -> dict[str, Any]:
    return {
        "running": running(),
        "interval_seconds": INTERVAL_SECONDS,
        "reconcile_every": RECONCILE_EVERY,
        **_stats,
    }


def _reset_for_tests() -> None:
    global _task
    _task = None
    _stop.clear()
    _stats.update({"cycles": 0, "errors": 0, "last_run": None,
                   "last_error": None, "last_result": {}})
