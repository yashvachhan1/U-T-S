"""Paper results against what the backtest promised.

This is the check Phase 7 exists for. A backtest is a claim about the
future made by fitting the past; the paper run is the first evidence about
whether that claim transfers. When they disagree badly, the strategy does
not work -- and the backtest is the one that is wrong, because it is the
one that had the answers in advance.

The gaps are named rather than scored into a single number, because they
mean different things:

  **Returns much lower.** The usual cause is cost and slippage the
  backtest modelled optimistically, or signals that cannot be acted on as
  fast as the backtest assumed.

  **Drawdown much deeper.** The dangerous one. A backtest with a 6%
  drawdown and a paper run at 14% means position sizing is wrong, and
  sizing errors scale with capital.

  **Win rate similar but returns worse.** Entries are fine and exits are
  not: the winners are being cut and the losers held, or the stop is too
  tight for the instrument.

  **Far fewer trades.** The filters are rejecting things the backtest took.
  Often correct -- the backtest did not have a blackout list or an edge
  test -- but it means the two are not measuring the same strategy.

A baseline has to be registered deliberately. There is no attempt to guess
which backtest run corresponds to the paper test: a comparison against a
baseline nobody chose is worse than no comparison.
"""
from __future__ import annotations

import json
from typing import Any

from .. import db
from ..clock import now_ist

SCHEMA = """
CREATE TABLE IF NOT EXISTS forward_baseline (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    registered_at TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT '',
    payload     TEXT NOT NULL
);
"""

# How far apart the two can be before it is called a gap rather than noise.
# Relative, because 2 points of drawdown means something different at 4%
# than at 25%.
RETURN_GAP_PCT = 40.0        # paper return this much below backtest
DRAWDOWN_GAP_PCT = 50.0      # paper drawdown this much deeper
WIN_RATE_GAP_POINTS = 10.0   # percentage points
TRADE_COUNT_GAP_PCT = 50.0

_ready = False


def ensure_schema() -> None:
    global _ready
    if not _ready:
        conn = db.conn()
        conn.executescript(SCHEMA)
        conn.commit()
        _ready = True


def register(result: dict[str, Any], label: str = "") -> dict[str, Any]:
    """Pin a backtest result as the thing the paper run is measured against.

    Only one baseline is kept. Keeping several invites picking the
    flattering one after the fact, which is the whole failure this
    comparison exists to catch.
    """
    ensure_schema()
    payload = {
        "total_return_pct": _num(result.get("total_return_pct")
                                 or result.get("return_pct")),
        "max_drawdown_pct": _num(result.get("max_drawdown_pct")
                                 or result.get("max_dd_pct")),
        "win_rate_pct": _num(result.get("win_rate_pct") or result.get("win_rate")),
        "trades": int(_num(result.get("trades") or result.get("total_trades")) or 0),
        "sessions": int(_num(result.get("sessions")) or 0),
    }
    db.execute(
        """INSERT INTO forward_baseline (id, registered_at, label, payload)
           VALUES (1, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             registered_at = excluded.registered_at,
             label = excluded.label, payload = excluded.payload""",
        (now_ist().isoformat(timespec="seconds"), label, json.dumps(payload)))
    return baseline()


def baseline() -> dict[str, Any] | None:
    ensure_schema()
    rows = db.query("SELECT * FROM forward_baseline WHERE id = 1")
    if not rows:
        return None
    try:
        payload = json.loads(rows[0]["payload"])
    except (TypeError, ValueError):
        return None
    return {"registered_at": rows[0]["registered_at"],
            "label": rows[0]["label"], **payload}


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _relative_gap(paper: float | None, back: float | None) -> float | None:
    """How far paper sits below backtest, as a percentage of backtest."""
    if paper is None or back is None or back == 0:
        return None
    return round((back - paper) / abs(back) * 100, 1)


def compare(paper: dict[str, Any], base: dict[str, Any] | None = None
            ) -> dict[str, Any]:
    """Name the gaps. Pure, so the thresholds are testable."""
    base = base if base is not None else baseline()
    if not base:
        return {"ready": False,
                "note": "koi backtest baseline register nahi hai -- "
                        "POST /api/forward/baseline se set karein"}
    if not paper.get("sessions"):
        return {"ready": False, "note": "abhi koi paper session record nahi hua"}

    findings: list[dict[str, Any]] = []

    return_gap = _relative_gap(paper.get("total_return_pct"),
                               base.get("total_return_pct"))
    if return_gap is not None and return_gap > RETURN_GAP_PCT:
        findings.append({
            "check": "returns",
            "severity": "serious",
            "detail": f"paper return backtest se {return_gap:.0f}% neeche hai",
            "likely_cause": "cost aur slippage backtest me kam aanke gaye, ya "
                            "signal utni jaldi act nahi ho pa raha",
        })

    # Drawdown compares in the other direction: deeper is worse.
    paper_dd = paper.get("max_drawdown_pct")
    base_dd = base.get("max_drawdown_pct")
    if paper_dd and base_dd:
        deeper = (paper_dd - base_dd) / abs(base_dd) * 100
        if deeper > DRAWDOWN_GAP_PCT:
            findings.append({
                "check": "drawdown",
                "severity": "critical",
                "detail": f"paper drawdown {paper_dd:.1f}% vs backtest "
                          f"{base_dd:.1f}% ({deeper:.0f}% zyada gehra)",
                "likely_cause": "position sizing galat hai -- aur sizing ki "
                                "galti capital ke saath badhti hai",
            })

    paper_win = paper.get("win_rate_pct")
    base_win = base.get("win_rate_pct")
    if paper_win is not None and base_win is not None:
        drop = base_win - paper_win
        if drop > WIN_RATE_GAP_POINTS:
            findings.append({
                "check": "win_rate",
                "severity": "serious",
                "detail": f"win rate {paper_win:.0f}% vs backtest {base_win:.0f}%",
                "likely_cause": "entry filters live me alag behave kar rahe hain",
            })
        elif abs(drop) <= WIN_RATE_GAP_POINTS and return_gap and return_gap > RETURN_GAP_PCT:
            findings.append({
                "check": "exits",
                "severity": "serious",
                "detail": "win rate theek hai par return nahi -- entry sahi, "
                          "exit galat",
                "likely_cause": "jeetne wale jaldi kat rahe hain ya stop "
                                "instrument ke liye bahut tight hai",
            })

    trade_gap = _relative_gap(paper.get("trades"), base.get("trades"))
    if trade_gap is not None and abs(trade_gap) > TRADE_COUNT_GAP_PCT:
        findings.append({
            "check": "trade_count",
            "severity": "warning",
            "detail": f"paper me {paper.get('trades')} trades vs backtest "
                      f"{base.get('trades')}",
            "likely_cause": "risk filters wo trades reject kar rahe hain jo "
                            "backtest ne liye the -- aksar sahi, par tab dono "
                            "ek hi strategy nahi naap rahe",
        })

    worst = ("critical" if any(f["severity"] == "critical" for f in findings)
             else "serious" if any(f["severity"] == "serious" for f in findings)
             else "warning" if findings else "none")

    return {
        "ready": True,
        "baseline": base,
        "paper": {k: paper.get(k) for k in
                  ("sessions", "total_return_pct", "max_drawdown_pct",
                   "win_rate_pct", "trades")},
        "findings": findings,
        "worst_severity": worst,
        "verdict": _verdict(worst, findings),
    }


def _verdict(worst: str, findings: list[dict[str, Any]]) -> str:
    if worst == "none":
        return ("Paper backtest ke kareeb hai. Isse strategy sahi sabit nahi "
                "hoti -- sirf ye ki dono ek hi cheez naap rahe hain.")
    if worst == "critical":
        return ("Rukein. Drawdown backtest se kaafi gehra hai, aur sizing ki "
                "galti capital ke saath badhti hai. Live jaane se pehle ye "
                "samajhna zaroori hai.")
    if worst == "serious":
        return (f"{len(findings)} gap mila. Live jaane se pehle har ek ka "
                f"jawab chahiye -- 'shayad theek ho jayega' jawab nahi hai.")
    return "Chhote fark hain, par dhyan rakhne layak."
