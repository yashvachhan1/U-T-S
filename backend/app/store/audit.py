"""Append-only audit log: what was decided, when, and why.

Every decision the risk engine makes lands here, approved or not. The
rejections matter more than the approvals -- "why did it not take that
trade" is the question asked after every good move missed, and without a
record the honest answer is a shrug.

Rows are **chained by hash**. Each row's hash covers its own contents and
the previous row's hash, so removing or editing a row breaks the chain from
that point on and `verify()` says where. This is not protection against a
determined attacker with write access, who could recompute the chain; it is
protection against the ordinary cases -- a bug deleting rows, a manual
sqlite edit to tidy up an embarrassing day, a partially restored backup --
where the alternative is a log that looks complete and is not.

Nothing here updates or deletes. The only operations are append, read and
verify.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .. import db
from ..clock import now_ist

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    subject   TEXT NOT NULL DEFAULT '',
    outcome   TEXT NOT NULL DEFAULT '',
    detail    TEXT NOT NULL DEFAULT '{}',
    prev_hash TEXT NOT NULL DEFAULT '',
    hash      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_kind ON audit_log(kind);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
"""

GENESIS = "0" * 64

_ready = False


def ensure_schema() -> None:
    global _ready
    if not _ready:
        conn = db.conn()
        conn.executescript(SCHEMA)
        conn.commit()
        _ready = True


def row_hash(*, ts: str, kind: str, subject: str, outcome: str,
             detail: str, prev_hash: str) -> str:
    """Hash of this row's contents plus the previous row's hash."""
    basis = "|".join([prev_hash, ts, kind, subject, outcome, detail])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def last_hash() -> str:
    ensure_schema()
    rows = db.query("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1")
    return rows[0]["hash"] if rows else GENESIS


def record(kind: str, *, subject: str = "", outcome: str = "",
           detail: Any = None) -> dict[str, Any]:
    """Append one entry. Returns the stored row.

    `detail` is serialised with a fallback, because an audit write must
    never be the thing that raises: losing the record of a decision is
    worse than losing some of its detail.
    """
    ensure_schema()
    ts = now_ist().isoformat(timespec="seconds")
    try:
        payload = json.dumps(detail if detail is not None else {}, default=str)
    except (TypeError, ValueError):
        payload = json.dumps({"unserialisable": str(detail)[:2000]})

    prev = last_hash()
    digest = row_hash(ts=ts, kind=kind, subject=subject, outcome=outcome,
                      detail=payload, prev_hash=prev)
    row_id = db.execute(
        """INSERT INTO audit_log (ts, kind, subject, outcome, detail, prev_hash, hash)
           VALUES (?,?,?,?,?,?,?)""",
        (ts, kind, subject, outcome, payload, prev, digest),
    )
    return {"id": row_id, "ts": ts, "kind": kind, "subject": subject,
            "outcome": outcome, "hash": digest}


def record_decision(decision: Any) -> dict[str, Any]:
    """Log a risk decision. Rejections are logged as carefully as approvals."""
    data = decision.as_dict() if hasattr(decision, "as_dict") else dict(decision)
    proposal = data.get("proposal") or {}
    return record(
        "decision",
        subject=str(proposal.get("symbol") or proposal.get("instrument") or ""),
        outcome="approved" if data.get("approved") else "rejected",
        detail=data,
    )


def recent(limit: int = 100, kind: str | None = None) -> list[dict[str, Any]]:
    ensure_schema()
    if kind:
        rows = db.query(
            "SELECT * FROM audit_log WHERE kind = ? ORDER BY id DESC LIMIT ?",
            (kind, limit))
    else:
        rows = db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
    for row in rows:
        try:
            row["detail"] = json.loads(row["detail"])
        except (TypeError, ValueError):
            pass
    return rows


def verify() -> dict[str, Any]:
    """Walk the chain and report the first row that does not fit.

    A broken chain does not say what happened, only that the log is no
    longer a complete record of it. That is still worth knowing before
    relying on it to explain a bad day.
    """
    ensure_schema()
    rows = db.query("SELECT * FROM audit_log ORDER BY id")
    prev = GENESIS
    for row in rows:
        expected = row_hash(ts=row["ts"], kind=row["kind"],
                            subject=row["subject"], outcome=row["outcome"],
                            detail=row["detail"], prev_hash=prev)
        if row["prev_hash"] != prev:
            return {"intact": False, "rows": len(rows), "broken_at": row["id"],
                    "problem": "previous-hash does not match the row before it "
                               "-- a row was removed or reordered"}
        if row["hash"] != expected:
            return {"intact": False, "rows": len(rows), "broken_at": row["id"],
                    "problem": "row contents do not match its hash -- edited"}
        prev = row["hash"]
    return {"intact": True, "rows": len(rows), "head": prev}


def stats() -> dict[str, Any]:
    ensure_schema()
    by_kind = db.query(
        "SELECT kind, COUNT(*) AS n FROM audit_log GROUP BY kind ORDER BY n DESC")
    by_outcome = db.query(
        "SELECT outcome, COUNT(*) AS n FROM audit_log "
        "WHERE kind = 'decision' GROUP BY outcome")
    return {
        "total": sum(r["n"] for r in by_kind),
        "by_kind": {r["kind"]: r["n"] for r in by_kind},
        "decisions": {r["outcome"]: r["n"] for r in by_outcome},
    }
