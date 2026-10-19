"""What the account looks like right now, as the risk engine sees it.

Every limit in `07-risk-limits.md` is a comparison against something in
here: today's loss, the drawdown from peak, how many positions are open,
how much of the book is in one sector.

The numbers are kept rather than recomputed from the broker on every check,
for two reasons. A limit check runs per order and must not wait on a network
call; and the peak equity a drawdown is measured from only exists if someone
has been recording it. Upstox reports positions, not the high-water mark.

The daily row is keyed by IST trading date, not by the machine's date. A
machine in the wrong timezone would otherwise reset the day's loss counter
in the middle of a session -- which is precisely when the counter matters.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from .. import config, db
from ..clock import now_ist

SCHEMA = """
CREATE TABLE IF NOT EXISTS risk_daily (
    trade_date      TEXT PRIMARY KEY,
    starting_equity REAL NOT NULL,
    realised_pnl    REAL NOT NULL DEFAULT 0,
    trades          INTEGER NOT NULL DEFAULT 0,
    wins            INTEGER NOT NULL DEFAULT 0,
    losses          INTEGER NOT NULL DEFAULT 0,
    orders_sent     INTEGER NOT NULL DEFAULT 0,
    orders_rejected INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_equity (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    peak_equity REAL NOT NULL,
    updated_at  TEXT NOT NULL
);

-- One row per closed trade. Kept separately from the daily totals because
-- a losing streak is a sequence, and a daily sum cannot tell you whether
-- three losses and two wins came in the order that matters.
CREATE TABLE IF NOT EXISTS risk_trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    closed_at   TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    symbol      TEXT NOT NULL DEFAULT '',
    pnl         REAL NOT NULL,
    source      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_risk_trades_date ON risk_trades(trade_date);
"""

_ready = False


def ensure_schema() -> None:
    global _ready
    if not _ready:
        conn = db.conn()
        conn.executescript(SCHEMA)
        conn.commit()
        _ready = True


def trade_date(at=None) -> str:
    """Today in IST. The exchange's day, not the machine's."""
    return (at or now_ist()).date().isoformat()


# --------------------------------------------------------------------------
# Daily row
# --------------------------------------------------------------------------
def _today_row(starting_equity: float | None = None) -> dict[str, Any]:
    ensure_schema()
    day = trade_date()
    rows = db.query("SELECT * FROM risk_daily WHERE trade_date = ?", (day,))
    if rows:
        return rows[0]

    # First activity of the day. The starting equity is the baseline every
    # daily-loss check measures against, so it is fixed now and never moved.
    equity = (starting_equity if starting_equity is not None
              else config.TRADING_CAPITAL)
    db.execute(
        """INSERT OR IGNORE INTO risk_daily
           (trade_date, starting_equity, updated_at) VALUES (?,?,?)""",
        (day, float(equity), db.now()),
    )
    return db.query("SELECT * FROM risk_daily WHERE trade_date = ?", (day,))[0]


def start_day(starting_equity: float) -> dict[str, Any]:
    """Record the equity the day began with. Idempotent.

    Deliberately does not overwrite an existing row: re-running this
    mid-session would reset the baseline and wipe out the day's recorded
    loss, which is the one number the kill switch depends on.
    """
    return _today_row(starting_equity)


def record_trade(pnl: float, *, symbol: str = "", source: str = "") -> dict[str, Any]:
    """Record one closed trade: its P&L, and its place in the sequence.

    Both tables are written because they answer different questions. The
    daily row answers "how much have we lost today", which is what the kill
    switch reads; the trade row answers "how many in a row", which a daily
    sum cannot.
    """
    row = _today_row()
    pnl = float(pnl)
    db.execute(
        """INSERT INTO risk_trades (closed_at, trade_date, symbol, pnl, source)
           VALUES (?,?,?,?,?)""",
        (db.now(), row["trade_date"], symbol, pnl, source),
    )
    db.execute(
        """UPDATE risk_daily SET realised_pnl = realised_pnl + ?,
             trades = trades + 1, wins = wins + ?, losses = losses + ?,
             updated_at = ?
           WHERE trade_date = ?""",
        (pnl, 1 if pnl > 0 else 0, 1 if pnl < 0 else 0,
         db.now(), row["trade_date"]),
    )
    return _today_row()


def record_order(*, rejected: bool = False) -> None:
    """Count an order and whether the broker refused it."""
    row = _today_row()
    db.execute(
        """UPDATE risk_daily SET orders_sent = orders_sent + 1,
             orders_rejected = orders_rejected + ?, updated_at = ?
           WHERE trade_date = ?""",
        (1 if rejected else 0, db.now(), row["trade_date"]),
    )


def reject_rate() -> float:
    """Percentage of today's orders the broker refused.

    Below a handful of orders this is noise -- one rejection out of two is
    100% and means nothing -- so it reports zero until there are enough to
    say anything.
    """
    row = _today_row()
    if row["orders_sent"] < 5:
        return 0.0
    return row["orders_rejected"] / row["orders_sent"] * 100


# --------------------------------------------------------------------------
# Equity and drawdown
# --------------------------------------------------------------------------
def peak_equity() -> float:
    ensure_schema()
    rows = db.query("SELECT peak_equity FROM risk_equity WHERE id = 1")
    return rows[0]["peak_equity"] if rows else float(config.TRADING_CAPITAL)


def record_equity(equity: float) -> float:
    """Update the high-water mark. It only ever goes up.

    Drawdown is measured from the peak, so letting the peak fall with the
    account would make the drawdown halt unreachable -- the measure would
    chase the losses down and always read near zero.
    """
    ensure_schema()
    equity = float(equity)
    current = peak_equity()
    if equity <= current:
        return current
    db.execute(
        """INSERT INTO risk_equity (id, peak_equity, updated_at) VALUES (1, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             peak_equity = excluded.peak_equity, updated_at = excluded.updated_at""",
        (equity, db.now()),
    )
    return equity


def drawdown_pct(equity: float) -> float:
    """How far below the peak we are, as a positive percentage."""
    peak = peak_equity()
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - float(equity)) / peak * 100)


def daily_loss_pct() -> float:
    """Today's realised loss as a positive percentage. Zero when up."""
    row = _today_row()
    base = row["starting_equity"] or config.TRADING_CAPITAL
    if base <= 0:
        return 0.0
    return max(0.0, -row["realised_pnl"] / base * 100)


def weekly_loss_pct(days: int = 7) -> float:
    """Realised loss over the trailing week, as a positive percentage."""
    ensure_schema()
    cutoff = (now_ist() - timedelta(days=days)).date().isoformat()
    rows = db.query(
        "SELECT realised_pnl, starting_equity FROM risk_daily "
        "WHERE trade_date >= ? ORDER BY trade_date", (cutoff,))
    if not rows:
        return 0.0
    total = sum(r["realised_pnl"] for r in rows)
    base = rows[0]["starting_equity"] or config.TRADING_CAPITAL
    if base <= 0:
        return 0.0
    return max(0.0, -total / base * 100)


def consecutive_losses() -> int:
    """Losing trades since the last winning one.

    Counted across days rather than reset each morning: five losses in a
    row is the same signal whether they took one session or three.
    """
    ensure_schema()
    rows = db.query(
        "SELECT pnl FROM risk_trades ORDER BY id DESC LIMIT 50")
    streak = 0
    for row in rows:
        if row["pnl"] < 0:
            streak += 1
        elif row["pnl"] > 0:
            break
    return streak


# --------------------------------------------------------------------------
# Positions
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Position:
    """One open position, as the risk engine needs to see it."""

    instrument_key: str
    symbol: str
    quantity: int
    average_price: float
    sector: str = ""
    industry: str = ""

    @property
    def value(self) -> float:
        return abs(self.quantity) * self.average_price


def exposure(positions: list[Position], equity: float) -> dict[str, Any]:
    """Total and per-sector exposure, as percentages of equity."""
    if equity <= 0:
        return {"available": False}

    total = sum(p.value for p in positions)
    by_sector: dict[str, float] = {}
    by_industry: dict[str, int] = {}
    for p in positions:
        if p.sector:
            by_sector[p.sector] = by_sector.get(p.sector, 0.0) + p.value
        if p.industry:
            by_industry[p.industry] = by_industry.get(p.industry, 0) + 1

    return {
        "available": True,
        "open_positions": len(positions),
        "total_value": round(total, 2),
        "total_pct": round(total / equity * 100, 2),
        "by_sector_pct": {s: round(v / equity * 100, 2)
                          for s, v in by_sector.items()},
        "positions_by_industry": by_industry,
    }


def snapshot(positions: list[Position] | None = None,
             equity: float | None = None) -> dict[str, Any]:
    """Everything the limit checks read, in one call."""
    row = _today_row()
    equity = float(equity if equity is not None else config.TRADING_CAPITAL)
    positions = positions or []
    return {
        "trade_date": row["trade_date"],
        "equity": equity,
        "starting_equity": row["starting_equity"],
        "peak_equity": peak_equity(),
        "realised_pnl_today": row["realised_pnl"],
        "daily_loss_pct": round(daily_loss_pct(), 3),
        "weekly_loss_pct": round(weekly_loss_pct(), 3),
        "drawdown_pct": round(drawdown_pct(equity), 3),
        "trades_today": row["trades"],
        "wins": row["wins"],
        "losses": row["losses"],
        "consecutive_losses": consecutive_losses(),
        "orders_sent": row["orders_sent"],
        "orders_rejected": row["orders_rejected"],
        "reject_rate_pct": round(reject_rate(), 2),
        "exposure": exposure(positions, equity),
    }
