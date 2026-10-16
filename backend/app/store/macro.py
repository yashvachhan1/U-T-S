"""Macro and alternative time series.

One table for every external series -- crude, USDINR, BTC, India EPU, power
demand -- keyed by (series_id, ts).

The column that matters most is `fetched_at`. Macro data gets **revised**: the
first GDP or CPI print is replaced weeks later by a better one. A backtest must
see the number that was actually knowable on the day, not today's corrected
value. Storing when we learned something is the only way to answer that later.

Lives in SQLite for now; Phase 2's store migration moves it to PostgreSQL
without anything above this layer noticing.
"""
from __future__ import annotations

from typing import Any, Iterable

from .. import db
from ..clock import now_ist

SCHEMA = """
CREATE TABLE IF NOT EXISTS macro_series (
    series_id   TEXT NOT NULL,
    ts          TEXT NOT NULL,          -- the observation's own date/time
    value       REAL NOT NULL,
    source      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,          -- when WE learned it (point-in-time)
    meta        TEXT,                   -- JSON, source-specific extras
    PRIMARY KEY (series_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_macro_series_ts ON macro_series(series_id, ts DESC);

CREATE TABLE IF NOT EXISTS collector_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    collector   TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,          -- ok | error
    points      INTEGER DEFAULT 0,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_collector_runs ON collector_runs(collector, started_at DESC);
"""

_ready = False


def ensure_schema() -> None:
    global _ready
    if not _ready:
        conn = db.conn()
        conn.executescript(SCHEMA)
        conn.commit()
        _ready = True


class Point:
    """One observation. Deliberately tiny -- collectors make thousands."""

    __slots__ = ("series_id", "ts", "value", "meta")

    def __init__(self, series_id: str, ts: str, value: float, meta: dict | None = None):
        self.series_id = series_id
        self.ts = ts
        self.value = float(value)
        self.meta = meta

    def __repr__(self) -> str:
        return f"Point({self.series_id}, {self.ts}, {self.value})"


def save(points: Iterable[Point], source: str) -> int:
    """Upsert observations. Returns how many rows were written.

    `fetched_at` is only set on first insert: re-fetching an unchanged value
    should not make it look newer than it is. A changed value does update, and
    then the new fetch time is the truth.
    """
    ensure_schema()
    fetched = now_ist().isoformat(timespec="seconds")
    rows = [
        (p.series_id, p.ts, p.value, source, fetched,
         db.dumps(p.meta) if p.meta else None)
        for p in points
    ]
    if not rows:
        return 0

    conn = db.conn()
    conn.executemany(
        """INSERT INTO macro_series (series_id, ts, value, source, fetched_at, meta)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(series_id, ts) DO UPDATE SET
               value      = excluded.value,
               meta       = excluded.meta,
               fetched_at = CASE WHEN macro_series.value != excluded.value
                                 THEN excluded.fetched_at
                                 ELSE macro_series.fetched_at END""",
        rows,
    )
    conn.commit()
    return len(rows)


def series(series_id: str, limit: int = 500) -> list[dict[str, Any]]:
    ensure_schema()
    return db.query(
        "SELECT ts, value, fetched_at FROM macro_series "
        "WHERE series_id = ? ORDER BY ts DESC LIMIT ?",
        (series_id, limit),
    )


def latest(series_id: str) -> dict[str, Any] | None:
    rows = series(series_id, limit=1)
    return rows[0] if rows else None


def latest_many(series_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    return {sid: row for sid in series_ids if (row := latest(sid))}


def as_of(series_id: str, when: str) -> dict[str, Any] | None:
    """The value we would have seen on `when` -- revisions excluded.

    This is what a backtest must call. Filtering on `fetched_at` keeps data we
    only learned later out of a decision we are replaying.
    """
    ensure_schema()
    rows = db.query(
        "SELECT ts, value, fetched_at FROM macro_series "
        "WHERE series_id = ? AND ts <= ? AND fetched_at <= ? "
        "ORDER BY ts DESC LIMIT 1",
        (series_id, when, when),
    )
    return rows[0] if rows else None


def known_series() -> list[dict[str, Any]]:
    ensure_schema()
    return db.query(
        "SELECT series_id, source, COUNT(*) AS points, MAX(ts) AS latest_ts, "
        "MAX(fetched_at) AS last_fetch FROM macro_series GROUP BY series_id, source "
        "ORDER BY series_id"
    )


# ---------------------------------------------------------- run tracking
def start_run(collector: str) -> int:
    ensure_schema()
    return db.execute(
        "INSERT INTO collector_runs (collector, started_at, status) VALUES (?,?,?)",
        (collector, now_ist().isoformat(timespec="seconds"), "running"),
    )


def finish_run(run_id: int, status: str, points: int = 0, error: str | None = None) -> None:
    db.execute(
        "UPDATE collector_runs SET finished_at = ?, status = ?, points = ?, error = ? "
        "WHERE id = ?",
        (now_ist().isoformat(timespec="seconds"), status, points, error, run_id),
    )


def recent_runs(collector: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    ensure_schema()
    if collector:
        return db.query(
            "SELECT * FROM collector_runs WHERE collector = ? "
            "ORDER BY id DESC LIMIT ?", (collector, limit))
    return db.query("SELECT * FROM collector_runs ORDER BY id DESC LIMIT ?", (limit,))
