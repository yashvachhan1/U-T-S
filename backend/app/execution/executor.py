"""Sending orders, watching them, and making sure every fill has a stop.

The decision engine says yes; this sends it. Three things are harder than
they look.

**A position without a stop is the worst state in the system.** The risk
engine refuses to size a trade without one, but the stop still has to reach
the broker, and the gap between "entry filled" and "stop accepted" is a
real window. It opens for ordinary reasons: an API timeout, a margin
rejection, a restart. So the stop is placed as soon as the fill is seen,
the attempt is retried on every poll, and an entry that stays unprotected
halts the system. It is not closed automatically -- see below.

**A fill is a transition, not a state.** Polling means seeing "complete" on
every cycle. Acting on the state would book the same trade repeatedly, so
everything here reacts to a change reported by the tracker.

**A limit order that never fills is a position you think you have.** Orders
left working past a timeout are cancelled rather than left to fill into a
market that has moved on.

On not auto-closing: when a stop cannot be placed the honest options are to
flatten immediately or to stop and shout. Flattening is itself a panic-exit
at whatever price exists right now, which is the thing this system
otherwise refuses to do. So it halts, keeps retrying the stop, and makes
the state loud. The position has a planned exit price the operator can act
on; what it must not do is quietly continue while new positions open
alongside it.
"""
from __future__ import annotations

from typing import Any

from .. import config
from ..clock import now_ist
from ..risk import killswitch
from ..risk import state as risk_state
from ..store import audit
from ..upstox import client, orders as order_builder, trading
from . import tracker

# A working limit order past this gets cancelled: the price it was sent for
# no longer exists.
STALE_ORDER_SECONDS = config.STALE_ORDER_SECONDS

# How many times to retry placing a protective stop before giving up for
# this cycle. The sweep tries again next time round.
STOP_RETRIES = 2


class ExecutionError(Exception):
    pass


def _symbol(instrument_key: str) -> str:
    from ..upstox import instruments

    row = instruments.by_key(instrument_key)
    return (row or {}).get("symbol", "") or instrument_key.split("|")[-1]


# --------------------------------------------------------------------------
# Placing
# --------------------------------------------------------------------------
async def place_entry(decision: Any, *, stop_price: float,
                      product: str = "I", source: str = "decision") -> dict[str, Any]:
    """Send an approved decision as an entry order.

    Refuses an unapproved decision rather than trusting the caller. The
    risk engine's answer is the authority, and a function that will execute
    whatever it is handed turns that answer into a suggestion.
    """
    if not getattr(decision, "approved", False):
        raise ExecutionError("decision approved nahi hai -- order nahi jayega")
    if decision.quantity <= 0:
        raise ExecutionError("quantity zero hai")
    if config.REQUIRE_STOP_LOSS and stop_price <= 0:
        raise ExecutionError(
            "stop ke bina entry nahi -- har position par stop zaroori hai")

    proposal = decision.proposal or {}
    instrument_key = str(proposal.get("instrument") or "")
    side = str(proposal.get("side") or "BUY").upper()
    symbol = str(proposal.get("symbol") or _symbol(instrument_key))

    tag = order_builder.idempotency_key(
        instrument_key=instrument_key, side=side,
        quantity=decision.quantity, source=source)

    # An order with this intent already exists. Returning it beats sending
    # a second one -- this is the retry-after-timeout path.
    existing = tracker.by_tag(tag)
    if existing and existing["status"] not in ("rejected",):
        return {"duplicate": True, "order": existing}

    # Recorded before sending, so a timeout still leaves evidence of intent.
    order_id = tracker.create(
        tag=tag, instrument_key=instrument_key, symbol=symbol, side=side,
        quantity=decision.quantity, role="entry",
        limit_price=float(proposal.get("entry") or 0),
        trigger_price=stop_price, product=product, source=source)

    try:
        result = await trading.place_order(
            instrument_key=instrument_key, side=side,
            quantity=decision.quantity, order_type="MARKET", product=product,
            source=source, intent="open")
    except Exception as exc:                       # noqa: BLE001
        tracker.update(order_id, status="rejected", last_error=str(exc)[:300])
        audit.record("entry_failed", subject=symbol, outcome="rejected",
                     detail={"error": str(exc)[:300], "tracked_id": order_id})
        raise

    broker_id = result.get("order_id")
    tracker.update(order_id, broker_order_id=broker_id,
                   limit_price=result.get("price") or 0,
                   status="complete" if result.get("status") == "complete" else "open")

    audit.record("entry_sent", subject=symbol,
                 outcome=str(broker_id or "sent"),
                 detail={"quantity": decision.quantity, "side": side,
                         "stop": stop_price, "tracked_id": order_id,
                         "source": source})

    # Paper fills are instant, so the stop has to follow immediately rather
    # than waiting for a poll that would find nothing left to watch.
    if result.get("status") == "complete":
        tracker.update(order_id, filled_quantity=decision.quantity,
                       avg_fill_price=result.get("price") or 0)
        await ensure_stop(tracker.get(order_id))

    return {"duplicate": False, "tracked_id": order_id, "broker": result}


async def ensure_stop(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """Place the protective stop for a filled entry, if it has none.

    Safe to call repeatedly -- it checks first. That is what makes the
    retry-every-poll behaviour cheap enough to simply always do.
    """
    if not entry or entry["filled_quantity"] <= 0:
        return None
    if tracker.has_live_stop(entry["id"]):
        return None

    stop_price = float(entry["trigger_price"] or 0)
    if stop_price <= 0:
        # Nothing to place. This should be impossible -- the risk engine
        # will not size a trade without a stop -- so it is a bug, not a
        # market condition, and it is recorded as one.
        killswitch.trip("position_mismatch",
                        f"{entry['symbol']} filled with no stop price recorded")
        audit.record("stop_missing", subject=entry["symbol"], outcome="halted",
                     detail={"entry": entry["id"]})
        return None

    exit_side = "SELL" if entry["side"] == "BUY" else "BUY"
    quantity = entry["filled_quantity"]
    tag = order_builder.idempotency_key(
        instrument_key=entry["instrument_key"], side=exit_side,
        quantity=quantity, source=f"stop:{entry['id']}")

    stop_id = tracker.create(
        tag=tag, instrument_key=entry["instrument_key"], symbol=entry["symbol"],
        side=exit_side, quantity=quantity, role="stop", parent_id=entry["id"],
        trigger_price=stop_price, order_type="SL-M",
        product=entry["product"], source=f"stop:{entry['id']}")

    last_error = ""
    for _ in range(STOP_RETRIES):
        try:
            result = await trading.place_order(
                instrument_key=entry["instrument_key"], side=exit_side,
                quantity=quantity, order_type="SL-M",
                trigger_price=stop_price, product=entry["product"],
                source=f"stop:{entry['id']}", intent="close")
        except Exception as exc:                   # noqa: BLE001
            last_error = str(exc)[:300]
            continue

        tracker.update(stop_id, broker_order_id=result.get("order_id"),
                       status="open" if result.get("status") != "complete"
                       else "complete")
        audit.record("stop_placed", subject=entry["symbol"],
                     outcome=str(result.get("order_id") or "placed"),
                     detail={"entry": entry["id"], "trigger": stop_price,
                             "quantity": quantity})
        return tracker.get(stop_id)

    # Could not protect the position. Halt so nothing new opens alongside
    # it; the next poll will try again.
    tracker.update(stop_id, status="rejected", last_error=last_error)
    killswitch.trip(
        "position_mismatch",
        f"{entry['symbol']}: {quantity} units filled with no stop placed "
        f"({last_error or 'unknown error'})")
    audit.record("stop_failed", subject=entry["symbol"], outcome="halted",
                 detail={"entry": entry["id"], "error": last_error})
    return None


async def close_position(instrument_key: str, quantity: int, side: str, *,
                         role: str = "exit", product: str = "I",
                         source: str = "exit") -> dict[str, Any]:
    """Close out a quantity. Never blocked by the kill switch."""
    exit_side = "SELL" if side.upper() == "BUY" else "BUY"
    symbol = _symbol(instrument_key)
    tag = order_builder.idempotency_key(
        instrument_key=instrument_key, side=exit_side, quantity=quantity,
        source=source)

    order_id = tracker.create(
        tag=tag, instrument_key=instrument_key, symbol=symbol, side=exit_side,
        quantity=quantity, role=role, order_type="MARKET", product=product,
        source=source)

    result = await trading.place_order(
        instrument_key=instrument_key, side=exit_side, quantity=quantity,
        order_type="MARKET", product=product, source=source, intent="close")

    tracker.update(order_id, broker_order_id=result.get("order_id"),
                   status="complete" if result.get("status") == "complete" else "open",
                   filled_quantity=quantity if result.get("status") == "complete" else 0,
                   avg_fill_price=result.get("price") or 0)
    audit.record("exit_sent", subject=symbol,
                 outcome=str(result.get("order_id") or "sent"),
                 detail={"quantity": quantity, "role": role, "source": source})
    return {"tracked_id": order_id, "broker": result}


# --------------------------------------------------------------------------
# Watching
# --------------------------------------------------------------------------
async def poll() -> dict[str, Any]:
    """Update every live order from the broker's book, and react to changes.

    Reacting to *changes* rather than states is what stops a fill being
    booked on every cycle.
    """
    live = tracker.live_orders()
    if not live:
        return {"polled": 0, "filled": 0, "stops_placed": 0, "cancelled": 0}

    try:
        book = await trading.order_book()
    except Exception as exc:                       # noqa: BLE001
        audit.record("poll_failed", outcome="order book unavailable",
                     detail={"error": str(exc)[:300]})
        return {"polled": 0, "error": str(exc)[:200]}

    by_id = {str(row.get("order_id")): row for row in book}
    by_tag = {str(row.get("tag")): row for row in book if row.get("tag")}

    filled = stops = 0
    for order in live:
        row = (by_id.get(str(order["broker_order_id"]))
               or by_tag.get(order["tag"]))
        if not row:
            continue

        change = tracker.apply_broker_row(order["id"], row)
        if change.get("newly_filled"):
            filled += 1
            await _on_fill(tracker.get(order["id"]), change["newly_filled"])
        if change.get("became_terminal") and change.get("to") == "rejected":
            risk_state.record_order(rejected=True)

    # Retry any protection that is still missing, whether or not anything
    # changed this cycle.
    for entry in tracker.filled_entries_without_stops():
        if await ensure_stop(entry):
            stops += 1

    cancelled = await cancel_stale_orders()
    return {"polled": len(live), "filled": filled, "stops_placed": stops,
            "cancelled": cancelled}


async def _on_fill(order: dict[str, Any] | None, quantity: int) -> None:
    """React to a quantity that has just filled."""
    if not order:
        return

    audit.record("fill", subject=order["symbol"],
                 outcome=f"{order['side']} {quantity}",
                 detail={"tracked_id": order["id"], "role": order["role"],
                         "price": order["avg_fill_price"]})

    if order["role"] == "entry":
        await ensure_stop(order)
        return

    # A stop or exit filling closes something, so the result is realised.
    pnl = realised_pnl(order)
    if pnl is not None:
        risk_state.record_trade(pnl, symbol=order["symbol"],
                                source=order["role"])
        audit.record("trade_closed", subject=order["symbol"],
                     outcome=f"{pnl:+.2f}",
                     detail={"tracked_id": order["id"], "role": order["role"]})

    # A filled stop leaves no reason for its siblings to stay working.
    if order["parent_id"]:
        await cancel_siblings(order["parent_id"], keep=order["id"])


def realised_pnl(exit_order: dict[str, Any]) -> float | None:
    """P&L of a closing fill against the entry it belongs to.

    Returns None when the entry cannot be identified -- better no number
    than a wrong one, since this feeds the daily-loss kill switch.
    """
    entry_id = exit_order.get("parent_id")
    if not entry_id:
        return None
    entry = tracker.get(entry_id)
    if not entry or not entry["avg_fill_price"] or not exit_order["avg_fill_price"]:
        return None

    quantity = min(exit_order["filled_quantity"], entry["filled_quantity"])
    if quantity <= 0:
        return None

    move = exit_order["avg_fill_price"] - entry["avg_fill_price"]
    direction = 1 if entry["side"] == "BUY" else -1
    gross = move * quantity * direction

    from ..risk.checks import round_trip_cost

    return gross - round_trip_cost(entry["avg_fill_price"], quantity)


async def cancel_siblings(parent_id: int, keep: int | None = None) -> int:
    """Cancel the other protective orders attached to one entry."""
    cancelled = 0
    for child in tracker.children(parent_id):
        if child["id"] == keep or child["status"] not in tracker.LIVE:
            continue
        if child["broker_order_id"]:
            try:
                await trading.cancel_order(child["broker_order_id"])
            except Exception:                      # noqa: BLE001
                pass                               # already gone is fine
        tracker.update(child["id"], status="cancelled")
        cancelled += 1
    return cancelled


async def cancel_stale_orders(max_age: float | None = None) -> int:
    """Cancel working orders that are too old to still mean anything.

    Protective stops are exempt. A stop is supposed to sit there all day;
    cancelling it for being old would remove the protection precisely
    because nothing went wrong yet.
    """
    max_age = STALE_ORDER_SECONDS if max_age is None else max_age
    now = now_ist()
    cancelled = 0

    for order in tracker.live_orders():
        if order["role"] in ("stop", "target"):
            continue
        try:
            from datetime import datetime
            age = (now - datetime.fromisoformat(order["created_at"])).total_seconds()
        except (TypeError, ValueError):
            continue
        if age < max_age:
            continue

        if order["broker_order_id"]:
            try:
                await trading.cancel_order(order["broker_order_id"])
            except Exception as exc:               # noqa: BLE001
                tracker.update(order["id"], last_error=str(exc)[:200])
                continue
        tracker.update(order["id"], status="cancelled",
                       last_error=f"stale after {age:.0f}s")
        audit.record("order_cancelled", subject=order["symbol"],
                     outcome="stale", detail={"tracked_id": order["id"],
                                              "age_seconds": round(age)})
        cancelled += 1
    return cancelled


# --------------------------------------------------------------------------
# Square-off
# --------------------------------------------------------------------------
def past_square_off(at=None) -> bool:
    at = at or now_ist()
    return (at.hour * 60 + at.minute) >= (config.SQUARE_OFF_HOUR * 60
                                          + config.SQUARE_OFF_MINUTE)


async def square_off_intraday(at=None) -> dict[str, Any]:
    """Close intraday positions at the square-off time.

    This is the one automatic close in the system, and it is automatic
    because the alternative is worse: the broker squares off intraday
    positions itself, at its own time and price, and being surprised by
    that is strictly worse than choosing the moment.
    """
    if not past_square_off(at):
        return {"squared_off": 0, "reason": "not yet square-off time"}

    closed = []
    for instrument_key, quantity in tracker.our_positions().items():
        if quantity == 0:
            continue
        entry = _entry_for(instrument_key)
        if entry and entry["product"] != "I":
            continue                                # delivery stays overnight

        side = "BUY" if quantity > 0 else "SELL"
        try:
            await close_position(instrument_key, abs(quantity), side,
                                 role="square_off", product="I",
                                 source="square_off")
            closed.append(instrument_key)
        except Exception as exc:                   # noqa: BLE001
            audit.record("square_off_failed", subject=instrument_key,
                         outcome="error", detail={"error": str(exc)[:300]})

    if closed:
        audit.record("square_off", outcome=f"{len(closed)} closed",
                     detail={"instruments": closed})
    return {"squared_off": len(closed), "instruments": closed}


def _entry_for(instrument_key: str) -> dict[str, Any] | None:
    from .. import db

    rows = db.query(
        """SELECT * FROM exec_orders WHERE instrument_key = ? AND role = 'entry'
           ORDER BY id DESC LIMIT 1""", (instrument_key,))
    return rows[0] if rows else None


def status() -> dict[str, Any]:
    unprotected = tracker.filled_entries_without_stops()
    return {
        "orders": tracker.stats(),
        "live": tracker.live_orders(),
        "unprotected_positions": [
            {"symbol": e["symbol"], "quantity": e["filled_quantity"],
             "instrument_key": e["instrument_key"]}
            for e in unprotected
        ],
        "our_positions": tracker.our_positions(),
        "past_square_off": past_square_off(),
    }
