"""Persisted agent outputs — the thing that makes backtests deterministic.

An LLM gives a different answer on a different day, which would make any
backtest involving agents unreproducible. The fix is not to make the model
deterministic; it is to stop calling it. Every output is written here with a
timestamp, and a backtest replays these rows.

That gives three things at once: the backtest is reproducible, it is
point-in-time correct because a row is only visible after its own timestamp,
and each agent can be scored separately on what it actually said.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from .. import db
from ..clock import now_ist

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_outputs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    agent          TEXT NOT NULL,
    ts             TEXT NOT NULL,
    payload        TEXT NOT NULL,
    model          TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    latency_ms     INTEGER DEFAULT 0,
    tokens_in      INTEGER DEFAULT 0,
    tokens_out     INTEGER DEFAULT 0,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_outputs ON agent_outputs(agent, ts DESC);
CREATE INDEX IF NOT EXISTS idx_agent_outputs_ts ON agent_outputs(ts DESC);
"""

_ready = False


def ensure_schema() -> None:
    global _ready
    if not _ready:
        conn = db.conn()
        conn.executescript(SCHEMA)
        conn.commit()
        _ready = True


def save(output: Any) -> int:
    """Persist one result. Failures are stored too -- an agent that keeps
    erroring is itself a finding."""
    ensure_schema()
    return db.execute(
        """INSERT INTO agent_outputs
           (agent, ts, payload, model, prompt_version,
            latency_ms, tokens_in, tokens_out, error)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            output.agent,
            output.ts.isoformat(timespec="seconds"),
            json.dumps(output.payload),
            output.model,
            output.prompt_version,
            output.latency_ms,
            output.tokens_in,
            output.tokens_out,
            output.error,
        ),
    )


def _row(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(raw)
    try:
        out["payload"] = json.loads(raw["payload"])
    except (json.JSONDecodeError, TypeError):
        out["payload"] = {}
    return out


def latest(agent: str, max_age_seconds: int | None = None) -> dict[str, Any] | None:
    """Most recent successful output, optionally only if still fresh."""
    ensure_schema()
    rows = db.query(
        "SELECT * FROM agent_outputs WHERE agent = ? AND error IS NULL "
        "ORDER BY ts DESC LIMIT 1",
        (agent,),
    )
    if not rows:
        return None

    row = _row(rows[0])
    if max_age_seconds is not None:
        cutoff = (now_ist() - timedelta(seconds=max_age_seconds)).isoformat(
            timespec="seconds")
        if row["ts"] < cutoff:
            return None
    return row


def as_of(agent: str, when: str, max_age_seconds: int = 3600) -> dict[str, Any] | None:
    """What this agent had said by `when`.

    The backtest's entry point. Filtering on ts <= when is what stops a replay
    seeing an opinion formed after the decision it is reconstructing.
    """
    ensure_schema()
    rows = db.query(
        "SELECT * FROM agent_outputs WHERE agent = ? AND error IS NULL "
        "AND ts <= ? ORDER BY ts DESC LIMIT 1",
        (agent, when),
    )
    if not rows:
        return None

    row = _row(rows[0])
    # A view from well before the decision is stale, not merely old.
    from datetime import datetime

    try:
        age = (datetime.fromisoformat(when) - datetime.fromisoformat(row["ts"]))
        if age.total_seconds() > max_age_seconds:
            return None
    except ValueError:
        pass
    return row


def all_as_of(agents: list[str], when: str,
              max_age_seconds: int = 3600) -> dict[str, dict[str, Any]]:
    return {a: row for a in agents if (row := as_of(a, when, max_age_seconds))}


def history(agent: str, limit: int = 100) -> list[dict[str, Any]]:
    ensure_schema()
    return [_row(r) for r in db.query(
        "SELECT * FROM agent_outputs WHERE agent = ? ORDER BY ts DESC LIMIT ?",
        (agent, limit),
    )]


def stats(agent: str | None = None) -> dict[str, Any]:
    """Cost and reliability per agent."""
    ensure_schema()
    where, params = ("WHERE agent = ?", (agent,)) if agent else ("", ())
    rows = db.query(
        f"""SELECT agent,
                   COUNT(*)                                  AS total,
                   SUM(CASE WHEN error IS NULL THEN 1 ELSE 0 END) AS ok,
                   AVG(latency_ms)                           AS avg_latency,
                   SUM(tokens_in + tokens_out)               AS tokens,
                   MAX(ts)                                   AS last_seen
            FROM agent_outputs {where} GROUP BY agent ORDER BY agent""",
        params,
    )
    for r in rows:
        r["error_rate"] = round(1 - (r["ok"] or 0) / r["total"], 4) if r["total"] else 0
        r["avg_latency"] = round(r["avg_latency"] or 0)
    return {"by_agent": rows, "total_rows": sum(r["total"] for r in rows)}


def purge_before(cutoff_iso: str) -> int:
    """Drop old rows. Kept out of normal operation -- these are small and
    form the record a backtest depends on."""
    ensure_schema()
    conn = db.conn()
    before = conn.execute("SELECT COUNT(*) FROM agent_outputs").fetchone()[0]
    conn.execute("DELETE FROM agent_outputs WHERE ts < ?", (cutoff_iso,))
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM agent_outputs").fetchone()[0]
    return before - after
