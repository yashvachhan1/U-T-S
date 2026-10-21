"""Comparing what we think we hold against what the broker says we hold.

These two drift. A fill arrives while the process is restarting; someone
trades the same account from the Upstox app; an order we believed was
rejected actually went through. None of these announce themselves, and all
of them mean the risk engine is reasoning about a book that does not exist.

So a mismatch is a **halt**, not a warning. Every limit downstream --
position count, sector exposure, total exposure -- is computed from the
position list, and a wrong position list makes every one of those numbers
wrong in the same direction: it under-reports what we hold, which means it
over-permits what we can add.

The broker is always right. Our record is a story about what we think we
did; theirs is the account.
"""
from __future__ import annotations

from typing import Any

from ..risk import killswitch
from ..store import audit
from ..upstox import trading
from . import tracker

# Quantity differences smaller than this are not treated as a mismatch.
# Zero, deliberately: there is no such thing as a rounding error in a share
# count, so any difference at all is a real disagreement.
QUANTITY_TOLERANCE = 0


def broker_positions(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Broker rows to instrument -> net quantity. Pure, so it is testable."""
    out: dict[str, int] = {}
    for row in rows:
        key = (row.get("instrument_token") or row.get("instrument_key") or "")
        quantity = int(row.get("quantity") or 0)
        if key and quantity:
            out[key] = out.get(key, 0) + quantity
    return out


def compare(ours: dict[str, int], theirs: dict[str, int]) -> dict[str, Any]:
    """Differences between the two books, from both directions.

    Both directions matter and they mean different things. Something we
    hold and they do not is usually a fill we recorded that never
    happened. Something they hold and we do not is a position nobody is
    watching -- no stop, not counted in any limit.
    """
    mismatches = []
    for key in sorted(set(ours) | set(theirs)):
        mine = ours.get(key, 0)
        yours = theirs.get(key, 0)
        if abs(mine - yours) > QUANTITY_TOLERANCE:
            mismatches.append({
                "instrument_key": key,
                "ours": mine,
                "broker": yours,
                "difference": yours - mine,
                "kind": ("unknown_to_us" if key not in ours
                         else "missing_at_broker" if key not in theirs
                         else "quantity_differs"),
            })

    return {
        "matched": not mismatches,
        "ours": ours,
        "broker": theirs,
        "mismatches": mismatches,
        "instruments_checked": len(set(ours) | set(theirs)),
    }


def compare_orders(live: list[dict[str, Any]],
                   book: list[dict[str, Any]]) -> dict[str, Any]:
    """Orders we think are working against what the broker is working.

    An order we believe is live and the broker has never heard of is the
    dangerous direction: we are waiting for a fill that will never come,
    and a stop in that state is protection that does not exist.
    """
    broker_ids = {str(r.get("order_id")) for r in book if r.get("order_id")}
    broker_tags = {str(r.get("tag")) for r in book if r.get("tag")}

    orphans = [
        {"tracked_id": o["id"], "symbol": o["symbol"], "role": o["role"],
         "status": o["status"], "broker_order_id": o["broker_order_id"]}
        for o in live
        if o["broker_order_id"] and str(o["broker_order_id"]) not in broker_ids
        and o["tag"] not in broker_tags
    ]
    return {"live_tracked": len(live), "broker_orders": len(book),
            "unknown_to_broker": orphans}


async def run(*, halt_on_mismatch: bool = True) -> dict[str, Any]:
    """One reconciliation pass. Never raises.

    A failure to *read* the broker is not treated as a mismatch -- an
    unreadable account is not a disagreeing account, and halting on a
    transient network error would stop trading for the wrong reason. It is
    recorded, and the next pass tries again.
    """
    try:
        broker_rows = await trading.positions()
    except Exception as exc:                       # noqa: BLE001
        audit.record("reconcile_failed", outcome="positions unavailable",
                     detail={"error": str(exc)[:300]})
        return {"ok": False, "error": str(exc)[:200],
                "note": "could not read the broker -- not treated as a mismatch"}

    ours = tracker.our_positions()
    theirs = broker_positions(broker_rows)
    result = compare(ours, theirs)

    try:
        book = await trading.order_book()
    except Exception:                              # noqa: BLE001
        book = []
    result["orders"] = compare_orders(tracker.live_orders(), book)

    unprotected = tracker.filled_entries_without_stops()
    result["unprotected_positions"] = [
        {"symbol": e["symbol"], "quantity": e["filled_quantity"]}
        for e in unprotected]

    if result["matched"] and not unprotected:
        result["ok"] = True
        return result

    result["ok"] = False
    detail = {"mismatches": result["mismatches"],
              "unprotected": result["unprotected_positions"]}
    audit.record("reconcile_mismatch",
                 outcome=f"{len(result['mismatches'])} mismatched",
                 detail=detail)

    if halt_on_mismatch:
        if result["mismatches"]:
            first = result["mismatches"][0]
            killswitch.trip(
                "position_mismatch",
                f"{len(result['mismatches'])} position(s) disagree with the "
                f"broker, e.g. {first['instrument_key']}: ours {first['ours']}, "
                f"broker {first['broker']}")
        elif unprotected:
            killswitch.trip(
                "position_mismatch",
                f"{len(unprotected)} filled position(s) with no stop in place")

    return result


def status() -> dict[str, Any]:
    """The last thing we know, without calling the broker."""
    return {
        "our_positions": tracker.our_positions(),
        "live_orders": len(tracker.live_orders()),
        "unprotected": len(tracker.filled_entries_without_stops()),
        "switches": killswitch.status(),
    }
