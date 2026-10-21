"""Our own record of every order we sent, and what became of it.

The broker has an order book, so why keep another one? Because the broker's
book answers "what orders exist" and this one answers "what did we intend".
Those differ in the cases that matter: an order we sent and never saw a
response for, a stop that belongs to a specific entry, a fill that arrived
while the process was restarting.

Reconciliation is the comparison of these two books. It only works if both
exist.

Every row carries the idempotency tag the order was sent with, so an order
can always be found again by intent rather than by an id we may not have
received.
"""
from __future__ import annotations

from typing import Any

from .. import db
from ..clock import now_ist

SCHEMA = """
CREATE TABLE IF NOT EXISTS exec_orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    tag             TEXT NOT NULL,
    broker_order_id TEXT,
    parent_id       INTEGER,
    role            TEXT NOT NULL DEFAULT 'entry',
    instrument_key  TEXT NOT NULL,
    symbol          TEXT NOT NULL DEFAULT '',
    side            TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    filled_quantity INTEGER NOT NULL DEFAULT 0,
    avg_fill_price  REAL NOT NULL DEFAULT 0,
    limit_price     REAL NOT NULL DEFAULT 0,
    trigger_price   REAL NOT NULL DEFAULT 0,
    order_type      TEXT NOT NULL DEFAULT 'LIMIT',
    product         TEXT NOT NULL DEFAULT 'I',
    status          TEXT NOT NULL DEFAULT 'pending',
    source          TEXT NOT NULL DEFAULT '',
    last_error      TEXT
);
CREATE INDEX IF NOT EXISTS idx_exec_status ON exec_orders(status);
CREATE INDEX IF NOT EXISTS idx_exec_tag ON exec_orders(tag);
CREATE INDEX IF NOT EXISTS idx_exec_instrument ON exec_orders(instrument_key);
"""

# Roles an order can play. `stop` is the protective order attached to an
# entry; losing track of which is which is how a position ends up with two
# stops or none.
ROLES = {"entry", "stop", "target", "exit", "square_off"}

# Nothing more will happen to an order in one of these states.
TERMINAL = {"complete", "rejected", "cancelled"}
LIVE = {"pending", "open", "partial"}

# Broker vocabulary differs per exchange and per endpoint. Anything we do
# not recognise stays "open" rather than being guessed into a terminal
# state -- treating an unknown status as complete would book a fill that
# never happened.
BROKER_STATUS = {
    "complete": "complete",
    "completed": "complete",
    "filled": "complete",
    "rejected": "rejected",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "cancelled after market order": "cancelled",
    "open": "open",
    "open pending": "open",
    "trigger pending": "open",
    "validation pending": "pending",
    "put order req received": "pending",
    "modify validation pending": "open",
    "after market order req received": "pending",
    "partially filled": "partial",
    "partial": "partial",
}

_ready = False


def ensure_schema() -> None:
    global _ready
    if not _ready:
        conn = db.conn()
        conn.executescript(SCHEMA)
        conn.commit()
        _ready = True


def normalise_status(raw: Any, filled: int = 0, quantity: int = 0) -> str:
    """Broker status to ours, erring towards "still live".

    A status we do not recognise leaves the order live so that the next
    poll looks at it again. The alternative -- assuming it finished -- stops
    us watching an order that may still fill.
    """
    text = str(raw or "").strip().lower()
    status = BROKER_STATUS.get(text)
    if status is None:
        return "partial" if 0 < filled < quantity else "open"
    if status == "complete" and quantity and filled < quantity:
        # Some brokers report "complete" on the last partial fill event.
        return "partial"
    return status


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------
def create(*, tag: str, instrument_key: str, side: str, quantity: int,
           symbol: str = "", role: str = "entry", parent_id: int | None = None,
           limit_price: float = 0.0, trigger_price: float = 0.0,
           order_type: str = "LIMIT", product: str = "I",
           source: str = "", broker_order_id: str | None = None,
           status: str = "pending") -> int:
    """Record an order we are about to send, before sending it.

    Written first on purpose. If the send times out we still have a row
    saying what we tried, which is what lets a retry find it by tag instead
    of placing a second one.
    """
    ensure_schema()
    if role not in ROLES:
        raise ValueError(f"Unknown order role: {role}")
    now = now_ist().isoformat(timespec="seconds")
    return db.execute(
        """INSERT INTO exec_orders
           (created_at, updated_at, tag, broker_order_id, parent_id, role,
            instrument_key, symbol, side, quantity, limit_price, trigger_price,
            order_type, product, status, source)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (now, now, tag, broker_order_id, parent_id, role, instrument_key,
         symbol, side.upper(), int(quantity), float(limit_price),
         float(trigger_price), order_type.upper(), product.upper(),
         status, source),
    )


def update(order_id: int, **fields: Any) -> None:
    """Patch a tracked order. Unknown columns are ignored, not an error."""
    ensure_schema()
    allowed = {"broker_order_id", "filled_quantity", "avg_fill_price",
               "status", "last_error", "limit_price", "trigger_price",
               "quantity"}
    sets, params = [], []
    for key, value in fields.items():
        if key in allowed:
            sets.append(f"{key} = ?")
            params.append(value)
    if not sets:
        return
    sets.append("updated_at = ?")
    params.append(now_ist().isoformat(timespec="seconds"))
    params.append(order_id)
    db.execute(f"UPDATE exec_orders SET {', '.join(sets)} WHERE id = ?",
               tuple(params))


def apply_broker_row(order_id: int, row: dict[str, Any]) -> dict[str, Any]:
    """Fold one broker order-book row into our record.

    Returns what changed, so a caller can react to a *transition* rather
    than to a state. "Is complete" fires on every poll; "just became
    complete" fires once, which is what a fill handler needs.
    """
    before = get(order_id)
    if not before:
        return {}

    quantity = int(row.get("quantity") or before["quantity"] or 0)
    filled = int(row.get("filled_quantity") or 0)
    status = normalise_status(row.get("status"), filled, quantity)
    avg = float(row.get("average_price") or 0) or before["avg_fill_price"]

    update(order_id,
           broker_order_id=row.get("order_id") or before["broker_order_id"],
           filled_quantity=filled or before["filled_quantity"],
           avg_fill_price=avg,
           status=status,
           last_error=row.get("status_message") or None)

    newly_filled = (filled or before["filled_quantity"]) - before["filled_quantity"]
    return {
        "status_changed": status != before["status"],
        "from": before["status"],
        "to": status,
        "newly_filled": max(0, newly_filled),
        "became_terminal": status in TERMINAL and before["status"] not in TERMINAL,
    }


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------
def get(order_id: int) -> dict[str, Any] | None:
    ensure_schema()
    rows = db.query("SELECT * FROM exec_orders WHERE id = ?", (order_id,))
    return rows[0] if rows else None


def by_tag(tag: str) -> dict[str, Any] | None:
    ensure_schema()
    rows = db.query(
        "SELECT * FROM exec_orders WHERE tag = ? ORDER BY id DESC LIMIT 1", (tag,))
    return rows[0] if rows else None


def by_broker_id(broker_order_id: str) -> dict[str, Any] | None:
    ensure_schema()
    rows = db.query(
        "SELECT * FROM exec_orders WHERE broker_order_id = ? LIMIT 1",
        (str(broker_order_id),))
    return rows[0] if rows else None


def live_orders(role: str | None = None) -> list[dict[str, Any]]:
    """Orders that may still do something."""
    ensure_schema()
    marks = ",".join("?" * len(LIVE))
    params: list[Any] = sorted(LIVE)
    clause = f"status IN ({marks})"
    if role:
        clause += " AND role = ?"
        params.append(role)
    return db.query(f"SELECT * FROM exec_orders WHERE {clause} ORDER BY id",
                    tuple(params))


def children(order_id: int, role: str | None = None) -> list[dict[str, Any]]:
    ensure_schema()
    if role:
        return db.query(
            "SELECT * FROM exec_orders WHERE parent_id = ? AND role = ? ORDER BY id",
            (order_id, role))
    return db.query("SELECT * FROM exec_orders WHERE parent_id = ? ORDER BY id",
                    (order_id,))


def has_live_stop(order_id: int) -> bool:
    """Whether this entry currently has a working protective order."""
    return any(c["status"] in LIVE or c["status"] == "complete"
               for c in children(order_id, role="stop"))


def filled_entries_without_stops() -> list[dict[str, Any]]:
    """Filled entries with no live protective order.

    This is the list that must always be empty. A filled position with no
    stop is the single most expensive state this system can be in, and it
    arises from ordinary things -- a stop rejected for margin, an API
    timeout between the fill and the stop going out.
    """
    ensure_schema()
    rows = db.query(
        """SELECT * FROM exec_orders
           WHERE role = 'entry' AND filled_quantity > 0
             AND status IN ('complete', 'partial')
           ORDER BY id""")
    return [r for r in rows if not has_live_stop(r["id"])]


def our_positions() -> dict[str, int]:
    """Net quantity per instrument, from our own fills.

    This is what reconciliation compares against the broker's view. It is
    deliberately computed from fills rather than kept as a running total --
    a running total drifts, and the drift is invisible until it matters.
    """
    ensure_schema()
    rows = db.query(
        "SELECT instrument_key, side, filled_quantity FROM exec_orders "
        "WHERE filled_quantity > 0")
    out: dict[str, int] = {}
    for row in rows:
        signed = row["filled_quantity"] * (1 if row["side"] == "BUY" else -1)
        out[row["instrument_key"]] = out.get(row["instrument_key"], 0) + signed
    return {k: v for k, v in out.items() if v != 0}


def recent(limit: int = 100) -> list[dict[str, Any]]:
    ensure_schema()
    return db.query("SELECT * FROM exec_orders ORDER BY id DESC LIMIT ?", (limit,))


def stats() -> dict[str, Any]:
    ensure_schema()
    by_status = db.query(
        "SELECT status, COUNT(*) AS n FROM exec_orders GROUP BY status")
    by_role = db.query(
        "SELECT role, COUNT(*) AS n FROM exec_orders GROUP BY role")
    return {
        "total": sum(r["n"] for r in by_status),
        "by_status": {r["status"]: r["n"] for r in by_status},
        "by_role": {r["role"]: r["n"] for r in by_role},
        "live": len(live_orders()),
        "unprotected": len(filled_entries_without_stops()),
    }


def _reset_for_tests() -> None:
    global _ready
    _ready = False
