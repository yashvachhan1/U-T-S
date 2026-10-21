"""Print the paper forward-test report.

Run it weekly during Phase 7. It reads the real database and changes
nothing.

    python tools/forward_report.py

With --demo it instead builds a scratch database containing a plausible
sixty-session run and reports on that, so the output can be read -- and
the thresholds argued with -- before two months have passed:

    python tools/forward_report.py --demo

The demo plants problems the report is supposed to catch: a run that
underperforms its backtest while keeping a similar win rate (which points
at the exits, not the entries), one agent whose opinions sit on the wrong
side of the outcome, and one that never commits to a view at all.
"""
from __future__ import annotations

import random
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

DEMO = "--demo" in sys.argv

if DEMO:
    from app import db

    db.DB_PATH = Path(tempfile.mkdtemp(prefix="forward-demo-")) / "demo.db"
    db._local = type("L", (), {})()

from app.clock import now_ist  # noqa: E402
from app.forward import compare, journal, report  # noqa: E402

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m")


def build_demo() -> None:
    """A sixty-session run with problems planted for the report to find."""
    from app import db
    from app.agents.schemas import StoredOutput
    from app.execution import tracker
    from app.store import agent_outputs

    # Create every schema up front: the demo writes raw SQL for speed, so
    # it does not go through the helpers that would create them lazily.
    journal.ensure_schema()
    tracker.ensure_schema()
    agent_outputs.ensure_schema()

    random.seed(7)
    capital = 1_000_000.0
    equity = capital
    start = now_ist() - timedelta(days=90)

    compare.register(
        {"total_return_pct": 18.0, "max_drawdown_pct": 6.0,
         "win_rate_pct": 56.0, "trades": 120, "sessions": 60},
        label="demo backtest v1")

    trade_no = 0
    for day in range(60):
        when = start + timedelta(days=day)
        if when.weekday() >= 5:
            continue

        date = when.date().isoformat()
        db.execute(
            """INSERT OR IGNORE INTO forward_sessions
               (trade_date, opened_at, expected_seconds, starting_equity)
               VALUES (?,?,?,?)""",
            (date, when.isoformat(timespec="seconds"),
             journal.SESSION_SECONDS, equity))

        trades_today = random.choice([0, 1, 2, 2, 3])
        pnl = 0.0
        wins = losses = 0

        for _ in range(trades_today):
            trade_no += 1
            # A slight positive edge, with occasional bad days -- and a
            # fatter left tail than the backtest assumed, which is what
            # produces the drawdown the report should catch.
            won = random.random() < 0.54
            move = random.uniform(0.4, 1.6) if won else -random.uniform(0.5, 2.6)
            pnl += capital * move / 100 / 12
            wins += won
            losses += not won

            entry_at = when.replace(hour=10, minute=random.randint(0, 50))
            exit_at = entry_at + timedelta(minutes=random.randint(20, 120))
            key = f"NSE_EQ|DEMO{trade_no % 17}"

            entry = tracker.create(tag=f"e{trade_no}", instrument_key=key,
                                   symbol=f"DEMO{trade_no % 17}", side="BUY",
                                   quantity=10, role="entry")
            db.execute("UPDATE exec_orders SET created_at = ? WHERE id = ?",
                       (entry_at.isoformat(timespec="seconds"), entry))
            tracker.update(entry, filled_quantity=10, avg_fill_price=100.0,
                           status="complete")

            ex = tracker.create(tag=f"x{trade_no}", instrument_key=key,
                                symbol=f"DEMO{trade_no % 17}", side="SELL",
                                quantity=10, role="stop", parent_id=entry)
            db.execute("UPDATE exec_orders SET created_at = ? WHERE id = ?",
                       (exit_at.isoformat(timespec="seconds"), ex))
            tracker.update(ex, filled_quantity=10,
                           avg_fill_price=100.0 * (1 + move / 100),
                           status="complete")

            view_at = entry_at - timedelta(minutes=5)
            # One agent that sees it coming, one that reads it backwards,
            # one that never commits.
            agent_outputs.save(StoredOutput(
                agent="asia_markets", ts=view_at,
                payload={"view": "bullish" if won else "bearish",
                         "confidence": 0.7, "risk_flag": not won,
                         "reasoning": "demo"}))
            agent_outputs.save(StoredOutput(
                agent="crypto_risk", ts=view_at,
                payload={"view": "bearish" if won else "bullish",
                         "confidence": 0.7, "risk_flag": False,
                         "reasoning": "demo"}))
            agent_outputs.save(StoredOutput(
                agent="global_macro", ts=view_at,
                payload={"view": "neutral", "confidence": 0.5,
                         "risk_flag": False, "reasoning": "demo"}))

        equity += pnl
        low = equity - abs(pnl) * random.uniform(0.2, 1.4)
        uptime = journal.SESSION_SECONDS * (0.99 if random.random() > 0.08 else 0.72)
        sent = trades_today * 2 + random.randint(0, 2)

        db.execute(
            """UPDATE forward_sessions
               SET closed_at = ?, realised_pnl = ?, trades = ?, wins = ?,
                   losses = ?, orders_sent = ?, orders_rejected = ?,
                   closing_equity = ?, low_equity = ?, uptime_seconds = ?,
                   heartbeats = ?
               WHERE trade_date = ?""",
            (when.isoformat(timespec="seconds"), pnl, trades_today, wins,
             losses, sent, 1 if random.random() < 0.05 else 0,
             equity, low, uptime, int(uptime / 15), date))


def colourise(line: str) -> str:
    if "[FAIL]" in line:
        return RED + line + RESET
    if "[ok  ]" in line:
        return GREEN + line + RESET
    if "[CRITICAL]" in line:
        return RED + line + RESET
    if "[SERIOUS]" in line or "[WARNING]" in line:
        return YELLOW + line + RESET
    if "costs_money" in line:
        return RED + line + RESET
    if "adds_value" in line:
        return GREEN + line + RESET
    if line.startswith("==="):
        return DIM + line + RESET
    return line


def main() -> int:
    if DEMO:
        print(f"{YELLOW}DEMO: sixty invented sessions in a scratch database. "
              f"Nothing here is real.{RESET}")
        print(f"{DIM}Problems are planted on purpose: a run that trails its "
              f"backtest, an agent reading the market backwards, and one "
              f"that never commits.{RESET}")
        build_demo()

    data = report.collect()
    for line in report.text(data).splitlines():
        print(colourise(line))

    checklist = data["checklist"]
    print()
    if checklist["machine_checks_clear"]:
        print(f"{GREEN}Machine-checkable items: all clear "
              f"({checklist['passed']}/{checklist['total']}).{RESET}")
    else:
        print(f"{RED}Blocking: {', '.join(checklist['blocking_failures'])}{RESET}")
    print(f"{DIM}Ye report kabhi 'live jao' nahi kehti. Wo faisla aapka hai.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
