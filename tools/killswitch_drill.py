"""Fire every kill switch on purpose and show what happens.

`docs/07-risk-limits.md` has this on the go-live checklist:

    [ ] Daily loss kill-switch test kiya hua hai (artificially trigger karke)
    [ ] Drawdown halt test kiya hua hai
    [ ] Manual kill switch test kiya hua hai

The unit tests cover the same ground, but a checklist item that says
"tested" deserves something a person can run and read. This is that: each
switch is tripped against a scratch database, and the output says what the
system would actually do.

It never touches the real database and never sends an order.

    python tools/killswitch_drill.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import db  # noqa: E402

# Point at a scratch file before anything can touch the real one.
_TMP = Path(tempfile.mkdtemp(prefix="killswitch-drill-")) / "drill.db"
db.DB_PATH = _TMP
db._local = type("L", (), {})()

from app import config  # noqa: E402
from app.risk import killswitch  # noqa: E402
from app.risk import state as risk_state  # noqa: E402
from app.risk.engine import Book, Proposal, check_limits, evaluate  # noqa: E402

CAPITAL = 1_000_000.0

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m")


def head(text: str) -> None:
    print(f"\n{'=' * 68}\n{text}\n{'=' * 68}")


def show_state(expect_entries: bool) -> bool:
    status = killswitch.status()
    allowed = status["entries_allowed"]
    colour = GREEN if allowed == expect_entries else RED
    verdict = "as expected" if allowed == expect_entries else "WRONG"

    print(f"  state            : {status['state'].upper()}")
    print(f"  new entries      : {'allowed' if allowed else 'BLOCKED'}")
    print(f"  closing orders   : {'allowed' if status['exits_allowed'] else 'BLOCKED'}")
    print(f"  needs a human    : {status['needs_manual_reset']}")
    for reason in status["reasons"]:
        print(f"  {DIM}reason           : {reason['trigger']} -- {reason['detail']}{RESET}")
    print(f"  {colour}-> {verdict}{RESET}")
    return allowed == expect_entries


def sample_proposal() -> Proposal:
    return Proposal(
        instrument_key="NSE_EQ|INE009A01021", symbol="INFY", side="BUY",
        entry_price=1500.0, stop_price=1470.0, sector="IT",
        bid=1499.5, ask=1500.5, day_volume=5_000_000, source="drill",
        expected_move_pct=1.5, probability=0.7,
    )


def check_order_is_refused() -> bool:
    """The engine must refuse an otherwise perfect trade while halted."""
    decision = evaluate(sample_proposal(), Book(equity=CAPITAL))
    ok = not decision.approved
    colour = GREEN if ok else RED
    print(f"  {colour}-> a clean BUY proposal is "
          f"{'refused' if ok else 'STILL APPROVED'}{RESET}")
    if not ok:
        print(f"  {RED}   quantity {decision.quantity} would have been sent{RESET}")
    return ok


def check_exit_still_works() -> bool:
    decision = evaluate(
        Proposal(instrument_key="NSE_EQ|INE009A01021", symbol="INFY",
                 side="SELL", entry_price=1500.0, intent="close",
                 source="drill"),
        Book(equity=CAPITAL))
    colour = GREEN if decision.approved else RED
    print(f"  {colour}-> closing a position is "
          f"{'still allowed' if decision.approved else 'BLOCKED -- trapped'}{RESET}")
    return decision.approved


def main() -> int:
    results: list[tuple[str, bool]] = []

    print(f"{DIM}scratch database: {_TMP}{RESET}")
    print(f"{DIM}capital assumed : Rs {CAPITAL:,.0f}{RESET}")
    print(f"{DIM}limits          : daily -{config.DAILY_LOSS_HALT_PCT}%, "
          f"drawdown -{config.MAX_DRAWDOWN_HALT_PCT}%{RESET}")

    # ---------------------------------------------------------------- 0
    head("0. Baseline -- nothing tripped")
    results.append(("baseline allows trading", show_state(expect_entries=True)))

    # ---------------------------------------------------------------- 1
    head(f"1. Daily loss limit (-{config.DAILY_LOSS_HALT_PCT}%)")
    risk_state.start_day(CAPITAL)
    loss = -CAPITAL * (config.DAILY_LOSS_HALT_PCT + 0.2) / 100
    risk_state.record_trade(loss, symbol="DRILL")
    print(f"  recorded a loss of Rs {abs(loss):,.0f} "
          f"({risk_state.daily_loss_pct():.2f}% of the day's starting equity)")
    tripped = check_limits(equity=CAPITAL + loss,
                           daily_loss_pct=risk_state.daily_loss_pct(),
                           drawdown_pct=0.0)
    print(f"  tripped          : {tripped}")
    results.append(("daily loss halts trading", show_state(expect_entries=False)))
    results.append(("halted engine refuses a buy", check_order_is_refused()))
    results.append(("halted engine allows an exit", check_exit_still_works()))

    print(f"\n  {DIM}a halt must not clear itself:{RESET}")
    killswitch.clear_auto("daily_loss")
    still_halted = killswitch.state() is killswitch.State.HALTED
    print(f"  {GREEN if still_halted else RED}-> "
          f"{'still halted' if still_halted else 'CLEARED ITSELF'}{RESET}")
    results.append(("daily loss cannot self-clear", still_halted))

    print(f"\n  {DIM}and must survive a restart:{RESET}")
    killswitch._reset_for_tests()              # as if the process had restarted
    survived = killswitch.state() is killswitch.State.HALTED
    print(f"  {GREEN if survived else RED}-> "
          f"{'still halted after restart' if survived else 'LOST ON RESTART'}{RESET}")
    results.append(("halt survives a restart", survived))

    killswitch.reset(by="drill")

    # ---------------------------------------------------------------- 2
    head(f"2. Drawdown halt (-{config.MAX_DRAWDOWN_HALT_PCT}%)")
    risk_state.record_equity(CAPITAL)
    low = CAPITAL * (1 - (config.MAX_DRAWDOWN_HALT_PCT + 1) / 100)
    print(f"  peak equity Rs {risk_state.peak_equity():,.0f}, "
          f"now Rs {low:,.0f} "
          f"({risk_state.drawdown_pct(low):.2f}% below peak)")
    tripped = check_limits(equity=low, daily_loss_pct=0.0,
                           drawdown_pct=risk_state.drawdown_pct(low))
    print(f"  tripped          : {tripped}")
    results.append(("drawdown halts trading", show_state(expect_entries=False)))
    killswitch.reset(by="drill")

    # ---------------------------------------------------------------- 3
    head("3. Manual STOP ALL")
    killswitch.stop_all(by="drill", detail="operator pressed stop")
    results.append(("manual stop blocks entries", show_state(expect_entries=False)))
    results.append(("manual stop leaves exits open", check_exit_still_works()))
    killswitch.reset(by="drill")

    # ---------------------------------------------------------------- 4
    head(f"4. Stale feed (pause, clears itself after "
         f"{config.STALE_FEED_PAUSE_SECONDS:.0f}s of silence)")
    check_limits(equity=CAPITAL, daily_loss_pct=0, drawdown_pct=0,
                 feed_stale_seconds=config.STALE_FEED_PAUSE_SECONDS + 30)
    results.append(("stale feed pauses entries", show_state(expect_entries=False)))
    print("\n  feed comes back:")
    check_limits(equity=CAPITAL, daily_loss_pct=0, drawdown_pct=0,
                 feed_stale_seconds=1)
    results.append(("stale feed clears itself", show_state(expect_entries=True)))

    # ---------------------------------------------------------------- 5
    head("5. A pause must never downgrade a halt")
    killswitch.trip("daily_loss", "drill")
    check_limits(equity=CAPITAL, daily_loss_pct=0, drawdown_pct=0,
                 feed_stale_seconds=0)        # would clear a pause
    held = killswitch.state() is killswitch.State.HALTED
    print(f"  {GREEN if held else RED}-> "
          f"{'halt held' if held else 'HALT WAS DOWNGRADED'}{RESET}")
    results.append(("pause cannot clear a halt", held))
    killswitch.reset(by="drill")

    # ---------------------------------------------------------------- 6
    head("6. Clearing requires a named human")
    try:
        killswitch.reset(by="")
        anonymous_refused = False
    except ValueError as exc:
        anonymous_refused = True
        print(f"  anonymous reset refused: {exc}")
    print(f"  {GREEN if anonymous_refused else RED}-> "
          f"{'a name is required' if anonymous_refused else 'ANONYMOUS RESET ALLOWED'}"
          f"{RESET}")
    results.append(("reset requires a name", anonymous_refused))

    # ---------------------------------------------------------------- summary
    head("Summary")
    passed = sum(1 for _, ok in results if ok)
    for name, ok in results:
        mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  [{mark}] {name}")

    colour = GREEN if passed == len(results) else RED
    print(f"\n{colour}{passed}/{len(results)} checks passed{RESET}")
    if passed != len(results):
        print(f"{RED}Do not go live. A kill switch did not do what it says.{RESET}")
        return 1
    print(f"{YELLOW}Note: this exercises the switches, not the broker. "
          f"Paper-trade before live.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
