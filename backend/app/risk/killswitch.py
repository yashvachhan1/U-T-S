"""Kill switches: the part that stops trading and will not be argued with.

Three states, and the difference between them is who is allowed to clear
them:

  RUNNING  — normal.
  PAUSED   — no new entries; clears by itself when the cause goes away.
             Stale feed, a run of rejected orders: conditions that pass.
  HALTED   — no new entries; **only a human clears it**, and only after the
             reason is gone. Daily loss, drawdown, a position mismatch, an
             expired session.

Two decisions here are deliberate and worth stating.

**A halt never closes positions.** It stops new ones. Automatic panic-exits
happen at the worst price of the day, in the thinnest book, usually right
before the reversal -- that is how a bad day becomes a terrible one.
Deciding to be out is a human decision; this only takes away the ability to
get further in.

**A halt survives a restart.** It is written to disk, so restarting the app
is not a way to clear it. Anything else makes the switch advisory, and the
moment it matters is exactly the moment someone will be tempted to restart.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .. import config, db
from ..clock import now_ist

SCHEMA = """
CREATE TABLE IF NOT EXISTS risk_state (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    state       TEXT    NOT NULL,
    reasons     TEXT    NOT NULL DEFAULT '[]',
    changed_at  TEXT    NOT NULL,
    cleared_by  TEXT
);
"""

_ready = False


def ensure_schema() -> None:
    global _ready
    if not _ready:
        conn = db.conn()
        conn.executescript(SCHEMA)
        conn.commit()
        _ready = True


class State(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    HALTED = "halted"


# Every trigger, and whether it clears on its own. A trigger that can be
# cleared automatically must be one whose cause is observable right now --
# a stale feed is, a 3% loss is not.
AUTO_CLEARING = {"stale_feed", "order_rejects"}

HALT_TRIGGERS = {
    "daily_loss": "daily loss limit hit",
    "max_drawdown": "account drawdown limit hit",
    "position_mismatch": "our positions do not match the broker's",
    "session_expired": "Upstox session expired",
    "manual": "stopped by hand",
    "config_unsafe": "risk config outside its hard ceilings",
}

PAUSE_TRIGGERS = {
    "stale_feed": "no market data recently",
    "order_rejects": "too many orders being rejected",
}


@dataclass
class Reason:
    trigger: str
    detail: str
    at: str = field(default_factory=lambda: now_ist().isoformat(timespec="seconds"))

    def as_dict(self) -> dict[str, Any]:
        return {"trigger": self.trigger, "detail": self.detail, "at": self.at}


def _load() -> tuple[State, list[Reason], str | None]:
    ensure_schema()
    rows = db.query("SELECT state, reasons, cleared_by FROM risk_state WHERE id = 1")
    if not rows:
        return State.RUNNING, [], None
    try:
        raw = json.loads(rows[0]["reasons"] or "[]")
    except (TypeError, ValueError):
        raw = []
    reasons = [Reason(**r) for r in raw if isinstance(r, dict)]
    try:
        state = State(rows[0]["state"])
    except ValueError:
        # An unreadable state is not a reason to assume everything is fine.
        return State.HALTED, [Reason("config_unsafe", "unreadable risk state")], None
    return state, reasons, rows[0]["cleared_by"]


def _save(state: State, reasons: list[Reason], cleared_by: str | None = None) -> None:
    ensure_schema()
    db.execute(
        """INSERT INTO risk_state (id, state, reasons, changed_at, cleared_by)
           VALUES (1, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             state = excluded.state, reasons = excluded.reasons,
             changed_at = excluded.changed_at, cleared_by = excluded.cleared_by""",
        (state.value, json.dumps([r.as_dict() for r in reasons]),
         now_ist().isoformat(timespec="seconds"), cleared_by),
    )


def state() -> State:
    return _load()[0]


def reasons() -> list[dict[str, Any]]:
    return [r.as_dict() for r in _load()[1]]


def entries_allowed() -> bool:
    """New positions. False in both PAUSED and HALTED."""
    return _load()[0] is State.RUNNING


def exits_allowed() -> bool:
    """Always true.

    There is no state in which closing a position should be blocked. A
    system that can enter but not leave is more dangerous than one that
    cannot trade at all, and every blocked-exit scenario ends with a
    position nobody can get out of.
    """
    return True


def trip(trigger: str, detail: str = "") -> State:
    """Fire a switch. Returns the resulting state.

    Repeated firing of the same trigger updates the detail rather than
    stacking duplicates -- a loss limit breached on every tick for an hour
    should read as one reason, not three thousand.
    """
    current, existing, _ = _load()

    if trigger in HALT_TRIGGERS:
        new_state = State.HALTED
        text = detail or HALT_TRIGGERS[trigger]
    elif trigger in PAUSE_TRIGGERS:
        # A pause must never downgrade a halt.
        new_state = State.HALTED if current is State.HALTED else State.PAUSED
        text = detail or PAUSE_TRIGGERS[trigger]
    else:
        new_state = State.HALTED
        text = detail or f"unknown trigger: {trigger}"

    kept = [r for r in existing if r.trigger != trigger]
    kept.append(Reason(trigger, text))
    _save(new_state, kept)
    print(f"[risk] {new_state.value.upper()}: {trigger} -- {text}")
    return new_state


def clear_auto(trigger: str) -> State:
    """Drop a self-clearing trigger whose cause has gone.

    Only the auto-clearing ones. A caller asking to clear `daily_loss` this
    way is refused: that is exactly the automatic self-forgiveness the
    switch exists to prevent.
    """
    if trigger not in AUTO_CLEARING:
        return _load()[0]

    current, existing, cleared_by = _load()
    kept = [r for r in existing if r.trigger != trigger]
    if len(kept) == len(existing):
        return current

    if any(r.trigger in HALT_TRIGGERS for r in kept):
        new_state = State.HALTED
    elif kept:
        new_state = State.PAUSED
    else:
        new_state = State.RUNNING
    _save(new_state, kept, cleared_by)
    return new_state


def reset(by: str, trigger: str | None = None) -> dict[str, Any]:
    """Clear a halt by hand. `by` is recorded and is not optional.

    With no trigger, everything is cleared. The caller is named in the row
    because "who restarted it, and when" is the first question asked after
    a halt turns out to have been right.
    """
    if not str(by or "").strip():
        raise ValueError("reset ke liye naam zaroori hai -- kaun clear kar raha hai")

    _, existing, _ = _load()
    if trigger is None:
        kept: list[Reason] = []
    else:
        kept = [r for r in existing if r.trigger != trigger]

    if kept:
        new_state = (State.HALTED if any(r.trigger in HALT_TRIGGERS for r in kept)
                     else State.PAUSED)
    else:
        new_state = State.RUNNING
    _save(new_state, kept, by)
    print(f"[risk] reset by {by}: now {new_state.value}"
          + (f" (cleared {trigger})" if trigger else ""))
    return status()


def stop_all(by: str = "user", detail: str = "") -> dict[str, Any]:
    """The manual kill switch: one call, no new orders.

    Open positions are untouched. That is the documented behaviour and not
    an omission -- see the module docstring.
    """
    trip("manual", detail or f"STOP ALL by {by}")
    return status()


def status() -> dict[str, Any]:
    current, rs, cleared_by = _load()
    return {
        "state": current.value,
        "entries_allowed": current is State.RUNNING,
        "exits_allowed": True,
        "needs_manual_reset": any(r.trigger in HALT_TRIGGERS for r in rs),
        "reasons": [r.as_dict() for r in rs],
        "last_cleared_by": cleared_by,
        "limits": {
            "daily_loss_halt_pct": config.DAILY_LOSS_HALT_PCT,
            "max_drawdown_halt_pct": config.MAX_DRAWDOWN_HALT_PCT,
            "stale_feed_pause_seconds": config.STALE_FEED_PAUSE_SECONDS,
            "order_reject_rate_pause_pct": config.ORDER_REJECT_RATE_PAUSE_PCT,
        },
    }


def _reset_for_tests() -> None:
    global _ready
    _ready = False
