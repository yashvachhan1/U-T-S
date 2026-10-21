"""Tiny SQLite layer -- no ORM, just what we need."""
import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from .config import DB_PATH

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    instrument_key TEXT NOT NULL,
    symbol        TEXT,
    side          TEXT NOT NULL,          -- BUY / SELL
    quantity      INTEGER NOT NULL,
    price         REAL NOT NULL,
    order_type    TEXT NOT NULL,
    product       TEXT NOT NULL,
    status        TEXT NOT NULL,          -- open / complete / rejected / cancelled
    source        TEXT NOT NULL,          -- manual / bot:<name>
    note          TEXT,
    -- A stop order waits for its trigger instead of filling on arrival.
    -- Without this, paper mode filled the protective stop the moment it was
    -- placed, which closed every position at the stop price the instant it
    -- opened -- a guaranteed loss on every paper trade.
    trigger_price REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bot_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    stopped_at  TEXT,
    strategy    TEXT NOT NULL,
    params      TEXT NOT NULL,
    instrument_key TEXT NOT NULL,
    symbol      TEXT,
    mode        TEXT NOT NULL,            -- paper / live
    status      TEXT NOT NULL             -- running / stopped / error
);

CREATE TABLE IF NOT EXISTS bot_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   INTEGER NOT NULL,
    ts       TEXT NOT NULL,
    level    TEXT NOT NULL,
    message  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
    instrument_key TEXT PRIMARY KEY,
    symbol         TEXT,
    name           TEXT,
    exchange       TEXT,
    added_at       TEXT NOT NULL
);
"""


def _migrate(c: sqlite3.Connection) -> None:
    """Add columns that older databases will not have.

    SQLite has no "add column if missing", and an existing install must
    not have to be deleted to pick up a fix.
    """
    cols = {r[1] for r in c.execute("PRAGMA table_info(paper_orders)")}
    if cols and "trigger_price" not in cols:
        c.execute("ALTER TABLE paper_orders ADD COLUMN "
                  "trigger_price REAL NOT NULL DEFAULT 0")
        c.commit()


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.executescript(SCHEMA)
        c.commit()
        _migrate(c)
        _local.conn = c
    return c


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    cur = conn().execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def execute(sql: str, params: tuple = ()) -> int:
    c = conn()
    cur = c.execute(sql, params)
    c.commit()
    return cur.lastrowid


def log_bot(run_id: int, level: str, message: str) -> None:
    execute(
        "INSERT INTO bot_log (run_id, ts, level, message) VALUES (?,?,?,?)",
        (run_id, now(), level, message),
    )


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)
