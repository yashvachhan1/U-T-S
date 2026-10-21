"""Walk one trade end to end in paper mode and show every step.

The unit tests prove each piece. This shows the whole path in one output a
person can read: a decision becomes an order, the fill gets a stop, the
books agree, the stop fills, the P&L lands in the risk state, and the
square-off closes what is left.

It also forces the two failure states on purpose, because those are the
ones that matter:

    a filled position with no stop        -> halts
    our book disagreeing with the broker  -> halts

Scratch database, paper mode, no network. Nothing real is touched.

    python tools/execution_drill.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import db  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="execution-drill-")) / "drill.db"
db.DB_PATH = _TMP
db._local = type("L", (), {})()

from app import config  # noqa: E402

config.PAPER_TRADING = True
config.TRADING_CAPITAL = 1_000_000.0

from app.clock import IST  # noqa: E402
from app.execution import executor, reconcile, tracker  # noqa: E402
from app.risk import killswitch  # noqa: E402
from app.risk import state as risk_state  # noqa: E402
from app.risk.engine import Decision  # noqa: E402
from app.store import audit  # noqa: E402
from app.upstox import marketdata, trading  # noqa: E402

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m")

INFY = "NSE_EQ|INE009A01021"
ENTRY_PRICE = 1500.0
STOP_PRICE = 1470.0
QUANTITY = 10

# The broker's view, which the drill controls so both books can be made to
# agree and then deliberately made to disagree.
_broker_positions: list[dict] = []


async def _fake_ltp(keys):
    return {key: {"ltp": ENTRY_PRICE} for key in keys}


async def _fake_positions():
    return list(_broker_positions)


marketdata.ltp = _fake_ltp
trading.positions = _fake_positions


def head(text: str) -> None:
    print(f"\n{'=' * 70}\n{text}\n{'=' * 70}")


def check(label: str, ok: bool, detail: str = "") -> bool:
    colour = GREEN if ok else RED
    mark = "ok" if ok else "FAILED"
    print(f"  {colour}[{mark}]{RESET} {label}"
          + (f" {DIM}-- {detail}{RESET}" if detail else ""))
    return ok


def approved_decision() -> Decision:
    return Decision(
        approved=True, quantity=QUANTITY,
        reasons=["drill"],
        proposal={"instrument": INFY, "symbol": "INFY", "side": "BUY",
                  "entry": ENTRY_PRICE, "stop": STOP_PRICE, "intent": "open",
                  "source": "drill"})


async def main() -> int:
    results: list[tuple[str, bool]] = []

    print(f"{DIM}scratch database : {_TMP}{RESET}")
    print(f"{DIM}mode             : PAPER (no network, nothing real){RESET}")
    print(f"{DIM}capital          : Rs {config.TRADING_CAPITAL:,.0f}{RESET}")
    risk_state.start_day(config.TRADING_CAPITAL)

    # ------------------------------------------------------------------ 1
    head("1. An approved decision becomes an order")
    out = await executor.place_entry(approved_decision(), stop_price=STOP_PRICE,
                                     product="I", source="drill")
    entry = tracker.get(out["tracked_id"])
    print(f"  entry      : {entry['side']} {entry['quantity']} {entry['symbol']} "
          f"@ {entry['limit_price']}")
    print(f"  status     : {entry['status']}, filled {entry['filled_quantity']}")
    results.append(("entry filled", check(
        "entry order filled", entry["filled_quantity"] == QUANTITY)))

    # ------------------------------------------------------------------ 2
    head("2. The fill is protected")
    stops = tracker.children(entry["id"], role="stop")
    if stops:
        stop = stops[0]
        print(f"  stop       : {stop['side']} {stop['quantity']} "
              f"trigger {stop['trigger_price']} ({stop['order_type']})")
    results.append(("stop placed", check(
        "a stop exists for the filled entry", bool(stops))))
    results.append(("nothing unprotected", check(
        "no unprotected positions", not tracker.filled_entries_without_stops())))

    # ------------------------------------------------------------------ 3
    head("3. An unapproved decision is refused")
    rejected = Decision(approved=False, quantity=0,
                        proposal={"instrument": INFY, "symbol": "INFY",
                                  "side": "BUY"})
    try:
        await executor.place_entry(rejected, stop_price=STOP_PRICE)
        refused = False
    except executor.ExecutionError as exc:
        refused = True
        print(f"  refused    : {exc}")
    results.append(("unapproved refused", check(
        "the executor will not send an unapproved decision", refused)))

    # ------------------------------------------------------------------ 4
    head("4. The same intent twice does not place two orders")
    again = await executor.place_entry(approved_decision(),
                                       stop_price=STOP_PRICE, source="drill")
    print(f"  duplicate  : {again.get('duplicate')}")
    results.append(("idempotent", check(
        "a repeat of the same intent is recognised", again.get("duplicate") is True)))

    # ------------------------------------------------------------------ 5
    head("5. The books agree")
    global _broker_positions
    _broker_positions = [{"instrument_token": INFY, "quantity": QUANTITY}]
    trading.order_book = lambda: asyncio.sleep(0, result=[])

    report = await reconcile.run()
    print(f"  ours       : {report['ours']}")
    print(f"  broker     : {report['broker']}")
    results.append(("books agree", check(
        "reconciliation clean", report["ok"] is True)))
    results.append(("still running", check(
        "trading still allowed", killswitch.entries_allowed())))

    # ------------------------------------------------------------------ 6
    head("6. The stop fills -- P&L lands in the risk state")
    stop = tracker.children(entry["id"], role="stop")[0]
    tracker.update(stop["id"], filled_quantity=QUANTITY,
                   avg_fill_price=STOP_PRICE, status="complete")
    await executor._on_fill(tracker.get(stop["id"]), QUANTITY)

    pnl = risk_state._today_row()["realised_pnl"]
    print(f"  entry      : Rs {ENTRY_PRICE}  ->  stop Rs {STOP_PRICE}")
    print(f"  realised   : Rs {pnl:,.2f} {DIM}(after brokerage, taxes, "
          f"slippage){RESET}")
    print(f"  daily loss : {risk_state.daily_loss_pct():.3f}% of starting equity")
    expected_gross = (STOP_PRICE - ENTRY_PRICE) * QUANTITY
    results.append(("loss recorded", check(
        "a losing stop is recorded as a loss", pnl < 0,
        f"gross would be Rs {expected_gross:,.0f}, costs make it worse")))
    results.append(("streak counted", check(
        "it counts towards the losing streak",
        risk_state.consecutive_losses() == 1)))

    # ------------------------------------------------------------------ 7
    head("7. A position with no stop halts trading")
    orphan = tracker.create(tag="orphan", instrument_key=INFY, symbol="INFY",
                            side="BUY", quantity=5, role="entry",
                            trigger_price=0.0)
    tracker.update(orphan, filled_quantity=5, status="complete",
                   avg_fill_price=ENTRY_PRICE)
    print(f"  {DIM}forced: a filled entry with no stop price recorded{RESET}")
    await executor.ensure_stop(tracker.get(orphan))
    results.append(("unprotected halts", check(
        "an unprotected position halts trading",
        killswitch.state() is killswitch.State.HALTED,
        killswitch.reasons()[0]["detail"] if killswitch.reasons() else "")))
    results.append(("exits still open", check(
        "closing orders are still allowed", killswitch.exits_allowed())))
    killswitch.reset(by="drill")
    tracker.update(orphan, status="cancelled", filled_quantity=0)

    # ------------------------------------------------------------------ 8
    head("8. Books disagreeing halts trading")
    _broker_positions = [{"instrument_token": INFY, "quantity": 25}]
    print(f"  {DIM}forced: broker says 25, our fills say "
          f"{tracker.our_positions().get(INFY, 0)}{RESET}")
    report = await reconcile.run()
    for mismatch in report["mismatches"]:
        print(f"  mismatch   : {mismatch['instrument_key']} "
              f"ours {mismatch['ours']} vs broker {mismatch['broker']} "
              f"({mismatch['kind']})")
    results.append(("mismatch halts", check(
        "a disagreeing book halts trading",
        killswitch.state() is killswitch.State.HALTED)))
    killswitch.reset(by="drill")

    # ------------------------------------------------------------------ 9
    head("9. An unreadable broker is not a mismatch")

    async def broken():
        raise RuntimeError("502 from Upstox")

    trading.positions = broken
    report = await reconcile.run()
    print(f"  {DIM}{report.get('note', '')}{RESET}")
    results.append(("blip is not a halt", check(
        "a network failure does not halt trading",
        killswitch.state() is killswitch.State.RUNNING)))
    trading.positions = _fake_positions

    # ----------------------------------------------------------------- 10
    head("10. Square-off closes what is left")
    _broker_positions = []
    out = await executor.place_entry(
        Decision(approved=True, quantity=QUANTITY,
                 proposal={"instrument": INFY, "symbol": "INFY", "side": "BUY",
                           "entry": ENTRY_PRICE, "stop": STOP_PRICE,
                           "source": "drill-2"}),
        stop_price=STOP_PRICE, product="I", source="drill-2")
    before = tracker.our_positions().get(INFY, 0)

    late = datetime(2026, 10, 16, 15, 20, tzinfo=IST)
    squared = await executor.square_off_intraday(at=late)
    after = tracker.our_positions().get(INFY, 0)
    print(f"  before     : {before} units")
    print(f"  after      : {after} units ({squared['squared_off']} closed)")
    results.append(("square-off works", check(
        "intraday positions are closed at square-off", after == 0)))

    # ----------------------------------------------------------------- 11
    head("11. The audit log is complete and intact")
    verified = audit.verify()
    stats = audit.stats()
    print(f"  rows       : {verified['rows']}")
    print(f"  kinds      : {', '.join(sorted(stats['by_kind']))}")
    results.append(("audit intact", check(
        "the hash chain verifies", verified["intact"] is True)))
    results.append(("audit covers orders", check(
        "entries, stops and fills are all recorded",
        {"entry_sent", "stop_placed"} <= set(stats["by_kind"]))))

    # --------------------------------------------------------------- done
    head("Summary")
    passed = sum(1 for _, ok in results if ok)
    for name, ok in results:
        mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  [{mark}] {name}")

    colour = GREEN if passed == len(results) else RED
    print(f"\n{colour}{passed}/{len(results)} checks passed{RESET}")
    if passed != len(results):
        return 1
    print(f"{YELLOW}Paper mode only. This exercises our side of the "
          f"conversation, not Upstox's.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
