"""Turning a signal into a decision.

A `Signal` is whatever an upstream component believes: a strategy rule, an
ML probability, eventually both. This module gathers the state around it --
live quote, open positions, equity, the agents' current caution, which
names are in a news blackout -- builds a `Proposal`, and hands it to the
risk engine.

Three things are deliberate.

**The agent layer only ever reduces.** The synthesis multiplier arrives
here as a number in [0, 1] and is passed through to sizing, where it is
clamped again. There is no arithmetic anywhere in this path that could
turn a confident agent into a larger position.

**The stop is required, and is computed from volatility when not given.**
A signal without a stop is an opinion, not a trade, and the engine refuses
to size one. ATR-based is the default because a fixed percentage is too
tight on a volatile name and too loose on a calm one.

**Every decision is audited, including the refusals.** Especially those:
"why did it not take that trade" is the question that gets asked, and the
answer has to exist before it is asked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import config
from ..agents import synthesis
from ..clock import now_ist
from ..collectors import constituents
from ..engine import feed_service
from ..risk import killswitch, sizing
from ..risk.engine import Book, Decision, Proposal, evaluate
from ..risk.state import Position
from ..store import audit, events
from ..upstox import instruments

# Map an NSE industry label onto the shorter sector vocabulary the risk
# engine's limits are written in. Only where the two genuinely correspond;
# an unmapped industry keeps its own name rather than being forced into a
# bucket it does not belong in.
INDUSTRY_TO_SECTOR = {
    "INFORMATION_TECHNOLOGY": "IT",
    "FINANCIAL_SERVICES": "FINANCIAL",
    "HEALTHCARE": "PHARMA",
    "AUTOMOBILE_AND_AUTO_COMPONENTS": "AUTO",
    "FAST_MOVING_CONSUMER_GOODS": "FMCG",
    "METALS_&_MINING": "METAL",
    "OIL_GAS_&_CONSUMABLE_FUELS": "OIL_GAS",
    "POWER": "POWER",
    "REALTY": "REALTY",
    "TELECOMMUNICATION": "TELECOM",
    "CAPITAL_GOODS": "CAPITAL_GOODS",
    "CONSUMER_DURABLES": "CONSUMER_DURABLES",
    "CHEMICALS": "CHEMICALS",
    "CONSTRUCTION": "INFRA",
    "CONSTRUCTION_MATERIALS": "CEMENT",
    "TEXTILES": "TEXTILES",
    "MEDIA_ENTERTAINMENT_&_PUBLICATION": "MEDIA",
}

# Default stop distance when the signal does not carry one.
DEFAULT_ATR_MULTIPLE = 2.0


@dataclass
class Signal:
    """What something upstream believes about one instrument."""

    instrument_key: str
    side: str = "BUY"
    source: str = "unknown"
    intent: str = "open"
    product: str = "I"

    # The edge claim. Both are needed: a 2% expected move at 51% confidence
    # is a different trade from the same move at 80%.
    expected_move_pct: float = 0.0
    probability: float = 0.0

    # Optional. A stop is computed from ATR when not supplied.
    stop_price: float = 0.0
    target_price: float = 0.0
    atr: float = 0.0

    note: str = ""


def sector_for(symbol: str, industry: str = "") -> tuple[str, str]:
    """(sector, industry) for a symbol, from NSE's own industry column."""
    industry = industry or constituents.industry_of(symbol)
    return INDUSTRY_TO_SECTOR.get(industry, industry), industry


def symbol_for(instrument_key: str) -> str:
    row = instruments.by_key(instrument_key)
    return (row or {}).get("symbol", "") or instrument_key.split("|")[-1]


# --------------------------------------------------------------------------
# Gathering the state
# --------------------------------------------------------------------------
async def build_book(*, equity: float | None = None) -> Book:
    """The current book, as the risk engine needs to see it.

    Positions come from the broker rather than from our own records. Those
    two can disagree -- a fill we missed, a manual trade in the app -- and
    when they do, the broker is right and our copy is a story about what we
    thought we did.
    """
    from ..upstox import trading

    positions: list[Position] = []
    try:
        raw = await trading.positions()
    except Exception as exc:                      # noqa: BLE001
        # No position list means no way to check concentration limits.
        # Recorded, and the caller sees an empty book -- which the limits
        # treat as "room for one trade", so this must not be silent.
        audit.record("book_error", outcome="positions unavailable",
                     detail={"error": str(exc)[:300]})
        raw = []

    for row in raw:
        qty = int(row.get("quantity") or 0)
        if not qty:
            continue
        symbol = row.get("tradingsymbol") or row.get("trading_symbol") or ""
        sector, industry = sector_for(symbol)
        positions.append(Position(
            instrument_key=row.get("instrument_token") or row.get("instrument_key") or "",
            symbol=symbol,
            quantity=qty,
            average_price=float(row.get("average_price") or row.get("avg_price") or 0),
            sector=sector,
            industry=industry,
        ))

    from ..risk import state as risk_state

    if equity is None:
        equity = config.TRADING_CAPITAL
    snap = risk_state.snapshot(positions, equity)

    return Book(
        equity=float(equity),
        positions=positions,
        trades_today=snap["trades_today"],
        consecutive_losses=snap["consecutive_losses"],
        weekly_loss_pct=snap["weekly_loss_pct"],
        blackout_symbols=events.symbols_with_news(config.EVENT_BLACKOUT_MINUTES),
    )


def agent_multiplier() -> tuple[float, dict[str, Any]]:
    """The agent layer's caution, as one number in [0, 1].

    Taken from the stored synthesis pair rather than by running agents: a
    decision must not wait on an LLM, and a backtest replaying this moment
    has to see the view that existed then.
    """
    try:
        pair = synthesis.current()
    except Exception as exc:                      # noqa: BLE001
        # No view is not permission to trade at full size.
        return 0.5, {"error": str(exc)[:200], "size_multiplier": 0.5}
    return max(0.0, min(1.0, float(pair.get("size_multiplier", 0.5)))), pair


def propose(signal: Signal, *, agent_mult: float = 1.0) -> Proposal:
    """Build a proposal from a signal and the live quote.

    Pure apart from the feed read, so the whole shape of what the risk
    engine will see can be inspected before anything is sent.
    """
    tick = feed_service.latest(signal.instrument_key)
    symbol = symbol_for(signal.instrument_key)
    sector, industry = sector_for(symbol)
    row = instruments.by_key(signal.instrument_key) or {}

    entry = tick.ltp if tick else 0.0
    stop = signal.stop_price
    if not stop and signal.atr and entry:
        stop = sizing.stop_from_atr(entry, signal.atr, signal.side,
                                    DEFAULT_ATR_MULTIPLE)

    return Proposal(
        instrument_key=signal.instrument_key,
        symbol=symbol,
        side=signal.side.upper(),
        entry_price=entry,
        stop_price=stop,
        target_price=signal.target_price,
        sector=sector,
        industry=industry,
        source=signal.source,
        intent=signal.intent,
        product=signal.product,
        lot_size=int(row.get("lot_size") or 1),
        bid=tick.best_bid if tick else 0.0,
        ask=tick.best_ask if tick else 0.0,
        day_volume=tick.volume if tick else 0.0,
        tick_ts_ms=tick.ts if tick else 0.0,
        expected_move_pct=signal.expected_move_pct,
        probability=signal.probability,
        agent_multiplier=agent_mult,
    )


# --------------------------------------------------------------------------
# The decision
# --------------------------------------------------------------------------
async def decide(signal: Signal, *, book: Book | None = None,
                 equity: float | None = None, audit_it: bool = True) -> Decision:
    """Signal to decision, recorded either way."""
    mult, pair = agent_multiplier()
    proposal = propose(signal, agent_mult=mult)
    book = book if book is not None else await build_book(equity=equity)

    decision = evaluate(proposal, book)

    if audit_it:
        audit.record_decision(decision)
        if decision.approved:
            audit.record("intent", subject=proposal.symbol,
                         outcome=f"{proposal.side} {decision.quantity}",
                         detail={"source": signal.source,
                                 "note": signal.note,
                                 "stance": pair.get("stance", {}).get("stance"),
                                 "agent_multiplier": mult})
    return decision


async def decide_many(signals: list[Signal], *,
                      equity: float | None = None) -> list[Decision]:
    """Evaluate several signals against one snapshot of the book.

    The book is built once and then **updated as approvals accumulate**.
    Evaluating five signals against the same empty book would approve five
    positions when the limit is one -- each one individually fine, the set
    of them a breach. This is the limit that is easiest to pass by
    accident.
    """
    book = await build_book(equity=equity)
    out: list[Decision] = []

    for signal in signals:
        decision = await decide(signal, book=book)
        out.append(decision)
        if decision.approved:
            proposal = decision.proposal
            book.positions.append(Position(
                instrument_key=str(proposal.get("instrument", "")),
                symbol=str(proposal.get("symbol", "")),
                quantity=decision.quantity,
                average_price=float(proposal.get("entry") or 0),
                sector=sector_for(str(proposal.get("symbol", "")))[0],
                industry=sector_for(str(proposal.get("symbol", "")))[1],
            ))
            book.trades_today += 1
    return out


# --------------------------------------------------------------------------
# The periodic safety sweep
# --------------------------------------------------------------------------
async def sweep(*, equity: float | None = None) -> dict[str, Any]:
    """Re-check the automatic kill switches against current state.

    Run on a timer and after every fill. Separate from `decide()` because a
    limit must be able to fire on a day when no signal appears -- a losing
    position drifting past the drawdown limit should halt the system
    whether or not anything wanted to trade.
    """
    from ..risk import state as risk_state
    from ..risk.engine import check_limits

    equity = config.TRADING_CAPITAL if equity is None else float(equity)
    risk_state.record_equity(equity)

    last_frame = feed_service.status().get("last_frame_at")
    stale_seconds = 0.0
    if last_frame:
        try:
            from datetime import datetime
            stale_seconds = (now_ist() - datetime.fromisoformat(last_frame)
                             ).total_seconds()
        except (TypeError, ValueError):
            stale_seconds = 0.0

    snap = risk_state.snapshot(equity=equity)
    tripped = check_limits(
        equity=equity,
        daily_loss_pct=snap["daily_loss_pct"],
        drawdown_pct=snap["drawdown_pct"],
        reject_rate_pct=snap["reject_rate_pct"],
        feed_stale_seconds=stale_seconds,
    )

    if tripped:
        audit.record("kill_switch", outcome=",".join(tripped),
                     detail={"state": snap, "feed_stale_seconds": stale_seconds})

    return {"tripped": tripped, "state": killswitch.status(),
            "account": snap, "feed_stale_seconds": round(stale_seconds, 1)}
