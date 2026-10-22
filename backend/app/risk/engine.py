"""The risk engine: the last word on whether a trade happens.

Everything upstream -- models, agents, strategies -- produces a `Proposal`,
which is a suggestion. This module turns it into a `Decision`, which is an
answer. Nothing can override that answer. There is no force flag, no
privileged caller, no configuration that bypasses a check, because every
such mechanism eventually gets used on the day it should not have been.

The shape of an evaluation:

    1. Is trading allowed at all?        kill switches
    2. Is this a sane thing to trade?    pre-trade filters
    3. Does the book have room?          portfolio limits
    4. How big, then?                    sizing, bound by the tightest cap
    5. Is it still worth it?             edge against the real cost

A rejection carries every reason, not the first one. "Rejected: market
closed" is useless when the trade was also oversized, in a blacked-out
name and past the exposure limit; fixing one and retrying four times is
how people learn to resent their own risk system.

Exits are evaluated separately and pass almost everything. A system that
can enter and not leave is worse than one that cannot trade.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import config
from ..clock import now_ist
from . import checks, killswitch, sizing
from .state import Position


@dataclass
class Proposal:
    """What something upstream would like to do. A suggestion, not an order."""

    instrument_key: str
    symbol: str
    side: str
    entry_price: float
    stop_price: float = 0.0
    target_price: float = 0.0
    sector: str = ""
    industry: str = ""
    source: str = "unknown"
    intent: str = "open"                       # "open" or "close"
    product: str = "I"                         # Intraday by default
    lot_size: int = 1

    # Context the checks read. Supplied by the caller so this module stays
    # free of I/O and remains testable.
    bid: float = 0.0
    ask: float = 0.0
    day_volume: float = 0.0
    tick_ts_ms: float = 0.0
    upper_circuit: float = 0.0
    lower_circuit: float = 0.0
    expected_move_pct: float = 0.0
    probability: float = 0.0

    # From the agent layer. Clamped before use; it can only reduce size.
    agent_multiplier: float = 1.0

    @property
    def is_exit(self) -> bool:
        return self.intent == "close"


@dataclass
class Decision:
    """The answer, and everything behind it."""

    approved: bool
    quantity: int = 0
    reasons: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    sizing: dict[str, Any] = field(default_factory=dict)
    multipliers: dict[str, float] = field(default_factory=dict)
    proposal: dict[str, Any] = field(default_factory=dict)
    at: str = field(default_factory=lambda: now_ist().isoformat(timespec="seconds"))

    def as_dict(self) -> dict[str, Any]:
        return {"approved": self.approved, "quantity": self.quantity,
                "reasons": self.reasons, "checks": self.checks,
                "sizing": self.sizing, "multipliers": self.multipliers,
                "proposal": self.proposal, "at": self.at}


@dataclass
class Book:
    """The state a portfolio limit is checked against."""

    equity: float = 0.0
    positions: list[Position] = field(default_factory=list)
    trades_today: int = 0
    consecutive_losses: int = 0
    weekly_loss_pct: float = 0.0
    blackout_symbols: list[str] = field(default_factory=list)

    # From the Chief Strategist's current stance. Both are one-way: a
    # blocked sector cannot be unblocked here, and the exposure cap only
    # ever tightens the configured one. Empty means "no stance", which is
    # not the same as "no restriction" only for the cap -- see below.
    blocked_sectors: list[str] = field(default_factory=list)
    allowed_sectors: list[str] = field(default_factory=list)
    stance_max_exposure_pct: float | None = None

    @property
    def exposure_pct(self) -> float:
        if self.equity <= 0:
            return 0.0
        return sum(p.value for p in self.positions) / self.equity * 100

    def sector_pct(self, sector: str) -> float:
        if self.equity <= 0 or not sector:
            return 0.0
        held = sum(p.value for p in self.positions if p.sector == sector)
        return held / self.equity * 100

    def industry_count(self, industry: str) -> int:
        if not industry:
            return 0
        return sum(1 for p in self.positions if p.industry == industry)

    def holds(self, instrument_key: str) -> bool:
        return any(p.instrument_key == instrument_key for p in self.positions)


# --------------------------------------------------------------------------
# Portfolio limits
# --------------------------------------------------------------------------
def portfolio_checks(proposal: Proposal, book: Book) -> list[checks.Check]:
    """Limits about the book as a whole rather than this one trade."""
    out: list[checks.Check] = []

    if len(book.positions) >= config.MAX_OPEN_POSITIONS:
        out.append(checks.fail(
            "max_positions",
            f"{len(book.positions)} open, limit is {config.MAX_OPEN_POSITIONS}"))
    else:
        out.append(checks.ok("max_positions", f"{len(book.positions)} open"))

    if book.exposure_pct >= config.MAX_TOTAL_EXPOSURE_PCT:
        out.append(checks.fail(
            "total_exposure",
            f"{book.exposure_pct:.1f}% invested, limit is "
            f"{config.MAX_TOTAL_EXPOSURE_PCT}%"))
    else:
        out.append(checks.ok("total_exposure", f"{book.exposure_pct:.1f}%"))

    sector_pct = book.sector_pct(proposal.sector)
    if proposal.sector and sector_pct >= config.MAX_SECTOR_EXPOSURE_PCT:
        out.append(checks.fail(
            "sector_exposure",
            f"{sector_pct:.1f}% already in {proposal.sector}, limit is "
            f"{config.MAX_SECTOR_EXPOSURE_PCT}%"))
    else:
        out.append(checks.ok("sector_exposure", f"{sector_pct:.1f}%"))

    # Three stocks in one industry is one bet held three times, and it will
    # be discovered as one bet on the day the industry moves.
    industry_held = book.industry_count(proposal.industry)
    if proposal.industry and industry_held >= config.MAX_CORRELATED_POSITIONS:
        out.append(checks.fail(
            "correlated_positions",
            f"{industry_held} positions already in {proposal.industry}, "
            f"limit is {config.MAX_CORRELATED_POSITIONS}"))
    else:
        out.append(checks.ok("correlated_positions", f"{industry_held} held"))

    if book.trades_today >= config.MAX_TRADES_PER_DAY:
        out.append(checks.fail(
            "trades_today",
            f"{book.trades_today} trades today, limit is "
            f"{config.MAX_TRADES_PER_DAY}"))
    else:
        out.append(checks.ok("trades_today", f"{book.trades_today} today"))

    # The stance's sector lists. Checked here rather than left to the
    # dashboard: an agent that blocks a sector and is then ignored is
    # worse than one that was never asked.
    sector = (proposal.sector or "").upper()
    if sector and sector in {s.upper() for s in book.blocked_sectors}:
        out.append(checks.fail(
            "stance_blocked_sector",
            f"Chief Strategist ne {sector} block kiya hai"))
    elif book.allowed_sectors and sector and sector not in {
            s.upper() for s in book.allowed_sectors}:
        out.append(checks.fail(
            "stance_allowed_sectors",
            f"stance sirf {', '.join(book.allowed_sectors)} allow kar raha "
            f"hai, {sector} nahi"))
    else:
        out.append(checks.ok("stance_sectors"))

    # The stance's exposure ceiling, applied as the tighter of the two.
    if book.stance_max_exposure_pct is not None:
        cap = min(config.MAX_TOTAL_EXPOSURE_PCT, book.stance_max_exposure_pct)
        if book.exposure_pct >= cap:
            out.append(checks.fail(
                "stance_exposure_cap",
                f"{book.exposure_pct:.1f}% invested, stance ka cap {cap:.0f}%"))
        else:
            out.append(checks.ok("stance_exposure_cap", f"cap {cap:.0f}%"))

    if book.holds(proposal.instrument_key):
        out.append(checks.fail(
            "already_held",
            f"already holding {proposal.symbol} -- adding to a position is a "
            f"separate decision, not a new entry"))
    else:
        out.append(checks.ok("already_held"))

    return out


def instrument_checks(proposal: Proposal, book: Book, at=None) -> list[checks.Check]:
    """Filters about this instrument, right now."""
    return [
        checks.market_hours(at),
        checks.opening_window(at),
        checks.square_off_window(at) if proposal.product == "I"
        else checks.ok("square_off_window", "not an intraday product"),
        checks.tick_fresh(proposal.tick_ts_ms, at),
        checks.spread_acceptable(proposal.bid, proposal.ask, proposal.entry_price),
        checks.liquid_enough(proposal.day_volume),
        checks.not_in_circuit(proposal.entry_price,
                              proposal.upper_circuit, proposal.lower_circuit),
        checks.not_in_blackout(proposal.symbol, book.blackout_symbols),
    ]


# --------------------------------------------------------------------------
# The decision
# --------------------------------------------------------------------------
def evaluate(proposal: Proposal, book: Book, at=None) -> Decision:
    """Approve or refuse, with every reason. Never raises."""
    summary = {"instrument": proposal.instrument_key, "symbol": proposal.symbol,
               "side": proposal.side, "intent": proposal.intent,
               "source": proposal.source, "entry": proposal.entry_price,
               "stop": proposal.stop_price}

    if proposal.is_exit:
        return _evaluate_exit(proposal, summary)

    switch = killswitch.status()
    if not switch["entries_allowed"]:
        reasons = [f"{r['trigger']}: {r['detail']}" for r in switch["reasons"]]
        return Decision(
            approved=False, quantity=0,
            reasons=[f"trading is {switch['state']}"] + reasons,
            checks=[checks.fail("kill_switch", switch["state"]).as_dict()],
            proposal=summary)

    all_checks = instrument_checks(proposal, book, at) + portfolio_checks(proposal, book)
    failed = checks.failures(all_checks)
    if failed:
        return Decision(
            approved=False, quantity=0,
            reasons=[f"{c.name}: {c.detail}" for c in failed],
            checks=[c.as_dict() for c in all_checks],
            proposal=summary)

    # ---- size it ------------------------------------------------------
    defensive, defensive_reasons = sizing.defensive_multiplier(
        consecutive_losses=book.consecutive_losses,
        weekly_loss_pct=book.weekly_loss_pct)
    agent_mult = max(0.0, min(1.0, proposal.agent_multiplier))
    combined = agent_mult * defensive

    # Room left under the tighter of the configured and stance caps.
    exposure_cap = config.MAX_TOTAL_EXPOSURE_PCT
    if book.stance_max_exposure_pct is not None:
        exposure_cap = min(exposure_cap, book.stance_max_exposure_pct)
    room = max(0.0, exposure_cap - book.exposure_pct)
    result = sizing.size(
        entry_price=proposal.entry_price,
        stop_price=proposal.stop_price,
        side=proposal.side,
        capital=book.equity or None,
        agent_multiplier=combined,
        available_exposure_pct=room,
        lot_size=proposal.lot_size,
    )

    multipliers = {"agents": agent_mult, "defensive": defensive,
                   "combined": combined}

    if not result.tradeable:
        return Decision(
            approved=False, quantity=0,
            reasons=[f"sizing: {result.detail}"] + defensive_reasons,
            checks=[c.as_dict() for c in all_checks],
            sizing=result.as_dict(), multipliers=multipliers, proposal=summary)

    # ---- is it still worth doing at that size? -------------------------
    edge = checks.has_edge(
        price=proposal.entry_price, quantity=result.quantity,
        expected_move_pct=proposal.expected_move_pct,
        probability=proposal.probability)
    all_checks.append(edge)

    if not edge.passed:
        return Decision(
            approved=False, quantity=0,
            reasons=[f"edge: {edge.detail}"],
            checks=[c.as_dict() for c in all_checks],
            sizing=result.as_dict(), multipliers=multipliers, proposal=summary)

    notes = [f"sized by {result.bound_by}: {result.detail}"] + defensive_reasons
    return Decision(
        approved=True, quantity=result.quantity, reasons=notes,
        checks=[c.as_dict() for c in all_checks],
        sizing=result.as_dict(), multipliers=multipliers, proposal=summary)


def _evaluate_exit(proposal: Proposal, summary: dict[str, Any]) -> Decision:
    """Exits clear almost everything.

    Not out of laziness. Every check that could block a close is a way of
    being trapped in a position: a halted kill switch, a wide spread, a
    stale tick, the close of the session. All of those are reasons the
    position is in trouble, which is when leaving matters most.

    The one thing still required is a quantity.
    """
    if proposal.lot_size <= 0:
        return Decision(approved=False, reasons=["exit: invalid lot size"],
                        proposal=summary)
    return Decision(
        approved=True, quantity=0,
        reasons=["exit -- entry filters do not apply to closing a position"],
        checks=[checks.ok("exits_allowed", "always").as_dict()],
        proposal=summary)


# --------------------------------------------------------------------------
# Automatic kill-switch evaluation
# --------------------------------------------------------------------------
def check_limits(*, equity: float, daily_loss_pct: float, drawdown_pct: float,
                 reject_rate_pct: float = 0.0,
                 feed_stale_seconds: float = 0.0) -> list[str]:
    """Fire any switch whose condition is met. Returns what was tripped.

    Called on a timer and after every fill. The self-clearing conditions
    are also cleared here when they pass, which is the only place that
    happens -- a halt is never cleared from this path.
    """
    tripped: list[str] = []

    if daily_loss_pct >= config.DAILY_LOSS_HALT_PCT:
        killswitch.trip("daily_loss",
                        f"down {daily_loss_pct:.2f}% today, limit is "
                        f"{config.DAILY_LOSS_HALT_PCT}%")
        tripped.append("daily_loss")

    if drawdown_pct >= config.MAX_DRAWDOWN_HALT_PCT:
        killswitch.trip("max_drawdown",
                        f"{drawdown_pct:.2f}% below peak equity, limit is "
                        f"{config.MAX_DRAWDOWN_HALT_PCT}%")
        tripped.append("max_drawdown")

    if reject_rate_pct >= config.ORDER_REJECT_RATE_PAUSE_PCT:
        killswitch.trip("order_rejects",
                        f"{reject_rate_pct:.0f}% of today's orders rejected")
        tripped.append("order_rejects")
    else:
        killswitch.clear_auto("order_rejects")

    if feed_stale_seconds >= config.STALE_FEED_PAUSE_SECONDS:
        killswitch.trip("stale_feed",
                        f"no market data for {feed_stale_seconds:.0f}s")
        tripped.append("stale_feed")
    else:
        killswitch.clear_auto("stale_feed")

    return tripped
