"""Where signals come from.

Everything downstream of a signal was built before this: the risk engine
decides, the executor sends, the journal records. Nothing was *producing*
signals. `decide()` could only be reached from two API routes a human has
to call, so a paper run left alone for two months would have recorded zero
trades and proved nothing.

This closes that. It walks a watchlist, runs the same strategy code the
backtester runs, and turns each fresh signal into a `Signal` for the
decision engine.

Three things it deliberately does not do.

**It does not place orders.** It produces proposals and hands them to the
risk engine, which is the only thing allowed to approve a trade. The old
bot runner calls `place_order` directly and therefore skips position
sizing, exposure limits, the edge test and the blackout list -- that is
the behaviour this path exists to replace.

**It is off until switched on.** Automatic trading does not turn itself on
as a side effect of starting the app, and `auto_execute` is separate from
scanning again: looking for signals and acting on them are different
decisions, and the gap between them is where a person gets to look.

**It acts once per candle.** A signal is a property of a closed candle, so
re-firing it every fifteen seconds would send the same trade repeatedly.
The still-forming candle is dropped for the same reason -- a signal that
appears mid-candle can vanish before the candle closes.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from .. import config, db
from ..clock import is_open, now_ist
from ..engine.indicators import atr as atr_series
from ..store import audit
from .. import strategies  # noqa: F401  -- registers the builtins
from ..strategies import base as strat
from .engine import Signal, decide_many

# ATR multiple for the protective stop when the strategy does not carry one.
STOP_ATR_MULTIPLE = 2.0

# Candles needed before a strategy's output means anything. Below this the
# indicators are still warming up and the signal is an artefact.
MIN_CANDLES = 40

# Instruments scanned per cycle. The watchlist is usually small, but this
# caps the damage if someone adds two hundred names.
MAX_INSTRUMENTS = 25

# A signal is only acted on if the candle that produced it is recent. An
# old candle means the scan fell behind, and acting on it is acting on a
# price that has moved.
MAX_CANDLE_AGE_SECONDS = 900


@dataclass
class ScanConfig:
    """What to scan, with what, and whether to act.

    `auto_execute` is false by default and stays a separate switch from
    `enabled`. Turning on the search for signals and turning on acting on
    them are different decisions.
    """

    enabled: bool = False
    auto_execute: bool = False
    strategy: str = "ema_crossover"
    params: dict[str, Any] = field(default_factory=dict)
    interval: str = "5m"
    product: str = "I"
    instruments: list[str] = field(default_factory=list)
    # The claim each signal makes about its own edge. Honest defaults: a
    # rule-based crossover is not a probability model, and pretending
    # otherwise would walk straight past the edge test.
    expected_move_pct: float = 1.0
    probability: float = 0.55

    def as_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "auto_execute": self.auto_execute,
                "strategy": self.strategy, "params": self.params,
                "interval": self.interval, "product": self.product,
                "instruments": self.instruments,
                "expected_move_pct": self.expected_move_pct,
                "probability": self.probability}


# Module-level so the loop and the API see the same thing. Not persisted:
# a scanner that survives a restart is a scanner that starts trading
# because the machine rebooted.
_config = ScanConfig()

# instrument_key -> timestamp of the candle we last acted on.
_last_candle: dict[str, int] = {}

_stats: dict[str, Any] = {"scans": 0, "signals": 0, "proposed": 0,
                          "approved": 0, "executed": 0, "last_run": None,
                          "last_error": None}


def configure(**kw: Any) -> dict[str, Any]:
    """Update the scan config. Unknown keys are ignored.

    An unknown strategy is rejected here rather than at scan time. The
    scan swallows its own errors so the loop keeps running, which means a
    bad strategy name would fail silently on every cycle forever -- the
    exact failure mode this system keeps trying to design out.
    """
    global _config
    if kw.get("strategy"):
        strat.get(kw["strategy"])          # raises KeyError if unknown

    current = _config.as_dict()
    for key, value in kw.items():
        if key in current and value is not None:
            current[key] = value
    _config = ScanConfig(**current)
    audit.record("scanner_config", outcome="updated", detail=current)
    return _config.as_dict()


def watchlist() -> list[str]:
    """Instruments to scan: the configured list, else the saved watchlist."""
    if _config.instruments:
        return _config.instruments[:MAX_INSTRUMENTS]
    rows = db.query("SELECT instrument_key FROM watchlist ORDER BY added_at")
    return [r["instrument_key"] for r in rows][:MAX_INSTRUMENTS]


# --------------------------------------------------------------------------
# Turning candles into a signal
# --------------------------------------------------------------------------
def signal_from_candles(candles: list[dict], strategy: str,
                        params: dict[str, Any] | None = None
                        ) -> tuple[str | None, dict[str, Any]]:
    """Run the strategy and read its last closed signal. Pure.

    Uses the same `strategies` code the backtester uses, which is what
    keeps live behaviour and backtested behaviour from drifting apart.
    """
    if len(candles) < MIN_CANDLES:
        return None, {"reason": f"only {len(candles)} candles, "
                                f"{MIN_CANDLES} needed"}

    rule = strat.get(strategy)
    signals = rule.run(candles, params or {})
    if not signals:
        return None, {"reason": "strategy returned nothing"}

    last = candles[-1]
    atr_values = atr_series(candles, 14)
    atr = atr_values[-1] if atr_values and atr_values[-1] else None

    return signals[-1], {
        "close": last.get("close"),
        "ts": last.get("ts"),
        "atr": atr,
        "candles": len(candles),
    }


def candle_is_fresh(ts: Any, at=None) -> bool:
    """Whether a candle is recent enough to act on."""
    if not ts:
        return False
    try:
        from datetime import datetime

        when = (datetime.fromisoformat(str(ts)) if not isinstance(ts, (int, float))
                else datetime.fromtimestamp(float(ts) / 1000, tz=(at or now_ist()).tzinfo))
    except (TypeError, ValueError):
        return False
    age = ((at or now_ist()) - when).total_seconds()
    return 0 <= age <= MAX_CANDLE_AGE_SECONDS


# --------------------------------------------------------------------------
# One pass
# --------------------------------------------------------------------------
async def scan() -> list[Signal]:
    """Fresh signals across the watchlist. Places nothing."""
    from ..upstox import marketdata

    out: list[Signal] = []
    for key in watchlist():
        try:
            candles = await marketdata.historical(key, _config.interval, days=30)
        except Exception as exc:                   # noqa: BLE001
            _stats["last_error"] = f"{key}: {str(exc)[:120]}"
            continue

        # Drop the still-forming candle: a signal that appears mid-candle
        # can vanish before it closes.
        closed = candles[:-1] if _config.interval != "1d" else candles
        verdict, detail = signal_from_candles(closed, _config.strategy,
                                              _config.params)
        if verdict != "BUY":
            # SELL here means "exit a long", which the stop and square-off
            # already handle. Opening shorts is a separate decision and is
            # not made here.
            continue

        ts = detail.get("ts")
        if _last_candle.get(key) == ts:
            continue                               # already acted on it
        if not candle_is_fresh(ts):
            continue

        _last_candle[key] = ts
        _stats["signals"] += 1

        close = float(detail.get("close") or 0)
        atr = detail.get("atr")
        stop = (close - STOP_ATR_MULTIPLE * float(atr)) if atr else 0.0

        out.append(Signal(
            instrument_key=key, side="BUY", source=f"scan:{_config.strategy}",
            product=_config.product,
            expected_move_pct=_config.expected_move_pct,
            probability=_config.probability,
            stop_price=stop, atr=float(atr) if atr else 0.0,
            note=f"{_config.strategy} on {_config.interval}, "
                 f"{detail.get('candles')} candles"))
    return out


async def run(*, execute: bool | None = None) -> dict[str, Any]:
    """Scan, decide, and optionally execute. Never raises.

    The decisions are made against one book that grows as approvals land,
    so five signals cannot each fill the last available slot.
    """
    _stats["scans"] += 1
    _stats["last_run"] = now_ist().isoformat(timespec="seconds")

    if not _config.enabled:
        return {"skipped": "scanner off"}
    if not is_open("equity"):
        return {"skipped": "market closed"}

    signals = await scan()
    if not signals:
        return {"signals": 0, "approved": 0, "executed": 0}

    _stats["proposed"] += len(signals)
    decisions = await decide_many(signals)
    approved = [d for d in decisions if d.approved]
    _stats["approved"] += len(approved)

    should_execute = _config.auto_execute if execute is None else execute
    executed = []
    if should_execute:
        from ..execution import executor

        for d in approved:
            stop = float((d.proposal or {}).get("stop") or 0)
            try:
                result = await executor.place_entry(
                    d, stop_price=stop, product=_config.product,
                    source=str((d.proposal or {}).get("source") or "scan"))
                executed.append(result)
                _stats["executed"] += 1
            except Exception as exc:               # noqa: BLE001
                _stats["last_error"] = str(exc)[:200]
                audit.record("scan_execute_failed",
                             subject=str((d.proposal or {}).get("symbol") or ""),
                             outcome="error", detail={"error": str(exc)[:300]})
            await asyncio.sleep(0)                 # let the rate limiter breathe

    audit.record("scan", outcome=f"{len(approved)}/{len(signals)} approved",
                 detail={"strategy": _config.strategy,
                         "executed": len(executed),
                         "auto_execute": should_execute})

    return {
        "signals": len(signals),
        "proposed": len(decisions),
        "approved": len(approved),
        "executed": len(executed),
        "rejected": [
            {"symbol": (d.proposal or {}).get("symbol"), "reasons": d.reasons}
            for d in decisions if not d.approved
        ][:10],
    }


def status() -> dict[str, Any]:
    return {
        "config": _config.as_dict(),
        "watchlist": watchlist(),
        "stats": dict(_stats),
        "market_open": is_open("equity"),
        "paper_trading": config.PAPER_TRADING,
    }


def _reset_for_tests() -> None:
    global _config
    _config = ScanConfig()
    _last_candle.clear()
    _stats.update({"scans": 0, "signals": 0, "proposed": 0, "approved": 0,
                   "executed": 0, "last_run": None, "last_error": None})
