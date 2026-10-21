"""One row per trading day, for the months the paper test runs.

The daily risk row already tracks what the kill switches read. This keeps
what a *forward test* reads, which is a different question: not "are we
inside the limits today" but "is this system behaving the way the backtest
said it would, over sixty sessions".

Three of the five Phase 7 checks live here -- drawdown, execution quality
and uptime -- and all three are only answerable if someone wrote them down
every day while nothing interesting was happening.

Uptime is the one people skip. A strategy that works and a process that
runs are different claims, and a system that crashes at 09:30 twice a week
has not been forward-tested regardless of what its P&L says.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import db
from ..clock import now_ist

SCHEMA = """
CREATE TABLE IF NOT EXISTS forward_sessions (
    trade_date       TEXT PRIMARY KEY,
    opened_at        TEXT NOT NULL,
    closed_at        TEXT,
    starting_equity  REAL NOT NULL DEFAULT 0,
    closing_equity   REAL NOT NULL DEFAULT 0,
    low_equity       REAL NOT NULL DEFAULT 0,
    realised_pnl     REAL NOT NULL DEFAULT 0,
    trades           INTEGER NOT NULL DEFAULT 0,
    wins             INTEGER NOT NULL DEFAULT 0,
    losses           INTEGER NOT NULL DEFAULT 0,
    orders_sent      INTEGER NOT NULL DEFAULT 0,
    orders_rejected  INTEGER NOT NULL DEFAULT 0,
    halts            INTEGER NOT NULL DEFAULT 0,
    halt_reasons     TEXT NOT NULL DEFAULT '',
    uptime_seconds   REAL NOT NULL DEFAULT 0,
    expected_seconds REAL NOT NULL DEFAULT 0,
    heartbeats       INTEGER NOT NULL DEFAULT 0,
    last_heartbeat   TEXT,
    note             TEXT NOT NULL DEFAULT ''
);
"""

# The equity session, 09:15 to 15:30, is what uptime is measured against.
# Being down overnight is not a gap; being down at 11:00 is.
SESSION_SECONDS = (15 * 60 + 30 - (9 * 60 + 15)) * 60

# A heartbeat further apart than this counts as a gap rather than a beat.
# The execution loop runs every 15s, so a minute of silence is already
# several missed cycles.
MAX_HEARTBEAT_GAP = 60.0

_ready = False


def ensure_schema() -> None:
    global _ready
    if not _ready:
        conn = db.conn()
        conn.executescript(SCHEMA)
        conn.commit()
        _ready = True


def today() -> str:
    return now_ist().date().isoformat()


def _row(day: str | None = None) -> dict[str, Any]:
    ensure_schema()
    day = day or today()
    rows = db.query("SELECT * FROM forward_sessions WHERE trade_date = ?", (day,))
    if rows:
        return rows[0]
    db.execute(
        """INSERT OR IGNORE INTO forward_sessions
           (trade_date, opened_at, expected_seconds) VALUES (?,?,?)""",
        (day, now_ist().isoformat(timespec="seconds"), SESSION_SECONDS))
    return db.query("SELECT * FROM forward_sessions WHERE trade_date = ?", (day,))[0]


def open_session(starting_equity: float, note: str = "") -> dict[str, Any]:
    """Start the day. Idempotent -- re-running does not reset the baseline."""
    row = _row()
    if not row["starting_equity"]:
        db.execute(
            """UPDATE forward_sessions
               SET starting_equity = ?, closing_equity = ?, low_equity = ?, note = ?
               WHERE trade_date = ?""",
            (float(starting_equity), float(starting_equity),
             float(starting_equity), note, row["trade_date"]))
    return _row()


def heartbeat(equity: float | None = None) -> dict[str, Any]:
    """Record that the system was alive, and how much it was worth.

    Uptime is counted from the gaps between beats rather than from a
    running timer: a timer keeps counting through a crash, and the thing
    being measured is precisely whether it crashed.
    """
    row = _row()
    now = now_ist()
    gap = 0.0
    if row["last_heartbeat"]:
        try:
            from datetime import datetime
            gap = (now - datetime.fromisoformat(row["last_heartbeat"])).total_seconds()
        except (TypeError, ValueError):
            gap = 0.0

    counted = gap if 0 < gap <= MAX_HEARTBEAT_GAP else 0.0

    low = row["low_equity"]
    closing = row["closing_equity"]
    if equity is not None:
        equity = float(equity)
        closing = equity
        low = min(low, equity) if low else equity

    db.execute(
        """UPDATE forward_sessions
           SET uptime_seconds = uptime_seconds + ?, heartbeats = heartbeats + 1,
               last_heartbeat = ?, closing_equity = ?, low_equity = ?
           WHERE trade_date = ?""",
        (counted, now.isoformat(timespec="seconds"), closing, low,
         row["trade_date"]))
    return _row()


def record_halt(reason: str) -> dict[str, Any]:
    """A halt during a forward test is a finding, not an incident to hide."""
    row = _row()
    reasons = [r for r in (row["halt_reasons"] or "").split("|") if r]
    if reason not in reasons:
        reasons.append(reason)
    db.execute(
        """UPDATE forward_sessions SET halts = halts + 1, halt_reasons = ?
           WHERE trade_date = ?""",
        ("|".join(reasons[:20]), row["trade_date"]))
    return _row()


def close_session(*, realised_pnl: float, trades: int, wins: int, losses: int,
                  orders_sent: int, orders_rejected: int,
                  closing_equity: float | None = None) -> dict[str, Any]:
    """Write the day's totals. Called at the close, or by the report if missed."""
    row = _row()
    closing = float(closing_equity) if closing_equity is not None else row["closing_equity"]
    db.execute(
        """UPDATE forward_sessions
           SET closed_at = ?, realised_pnl = ?, trades = ?, wins = ?, losses = ?,
               orders_sent = ?, orders_rejected = ?, closing_equity = ?
           WHERE trade_date = ?""",
        (now_ist().isoformat(timespec="seconds"), float(realised_pnl),
         int(trades), int(wins), int(losses), int(orders_sent),
         int(orders_rejected), closing, row["trade_date"]))
    return _row()


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------
def sessions(limit: int = 90) -> list[dict[str, Any]]:
    ensure_schema()
    rows = db.query(
        "SELECT * FROM forward_sessions ORDER BY trade_date DESC LIMIT ?", (limit,))
    return list(reversed(rows))


def session(day: str) -> dict[str, Any] | None:
    ensure_schema()
    rows = db.query("SELECT * FROM forward_sessions WHERE trade_date = ?", (day,))
    return rows[0] if rows else None


@dataclass
class Curve:
    """The equity path and what it implies. Computed, never stored.

    Stored summary statistics go stale the moment a day is corrected;
    recomputing from the sessions means the number always matches the rows
    a reader can see.
    """

    equity: list[float]
    peak: float
    max_drawdown_pct: float
    total_return_pct: float


def equity_curve(rows: list[dict[str, Any]] | None = None) -> Curve:
    rows = sessions() if rows is None else rows
    points = [r["closing_equity"] for r in rows if r["closing_equity"]]
    if not points:
        return Curve([], 0.0, 0.0, 0.0)

    start = next((r["starting_equity"] for r in rows if r["starting_equity"]), points[0])
    peak = points[0]
    worst = 0.0
    for r in rows:
        # The intraday low matters: a day that fell 4% and closed flat still
        # spent the afternoon 4% down, and a drawdown limit would have fired.
        for value in (r["low_equity"], r["closing_equity"]):
            if not value:
                continue
            peak = max(peak, value)
            if peak > 0:
                worst = max(worst, (peak - value) / peak * 100)

    total = ((points[-1] - start) / start * 100) if start else 0.0
    return Curve(points, peak, round(worst, 2), round(total, 2))


def summary(rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Everything Phase 7 asks about the paper run, in one object."""
    rows = sessions() if rows is None else rows
    if not rows:
        return {"sessions": 0, "ready": False,
                "note": "koi session record nahi hua abhi tak"}

    curve = equity_curve(rows)
    trades = sum(r["trades"] for r in rows)
    wins = sum(r["wins"] for r in rows)
    losses = sum(r["losses"] for r in rows)
    sent = sum(r["orders_sent"] for r in rows)
    rejected = sum(r["orders_rejected"] for r in rows)
    uptime = sum(r["uptime_seconds"] for r in rows)
    expected = sum(r["expected_seconds"] for r in rows) or 1
    halt_days = [r["trade_date"] for r in rows if r["halts"]]

    return {
        "sessions": len(rows),
        "first_day": rows[0]["trade_date"],
        "last_day": rows[-1]["trade_date"],
        "starting_equity": rows[0]["starting_equity"],
        "closing_equity": rows[-1]["closing_equity"],
        "total_return_pct": curve.total_return_pct,
        "max_drawdown_pct": curve.max_drawdown_pct,
        "realised_pnl": round(sum(r["realised_pnl"] for r in rows), 2),
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(wins / trades * 100, 1) if trades else None,
        "orders_sent": sent,
        "orders_rejected": rejected,
        "reject_rate_pct": round(rejected / sent * 100, 2) if sent else 0.0,
        "uptime_pct": round(min(100.0, uptime / expected * 100), 1),
        "days_with_halts": len(halt_days),
        "halt_days": halt_days[-10:],
        "flat_days": sum(1 for r in rows if not r["trades"]),
    }


def _reset_for_tests() -> None:
    global _ready
    _ready = False
