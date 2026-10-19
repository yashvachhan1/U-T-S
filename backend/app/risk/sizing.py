"""How big the position is.

One rule above all the others: **size from risk, never from a fixed
quantity.** A hundred shares of a stock that moves 4% a day and a hundred
shares of one that moves 0.5% are not the same trade, and treating them as
the same is how an account dies of a position that looked ordinary.

So quantity falls out of the stop distance:

    risk_rupees  = capital x max_risk_per_trade_pct
    quantity     = risk_rupees / (entry - stop)

and is then cut down by every cap that applies. The order matters only in
that the smallest wins; `size()` returns which one bound it, because "why
is this position so small" is otherwise unanswerable after the fact.

The agent multiplier enters here and can only reduce. There is no path in
this module by which any input makes a position larger than the stop
distance and the caps allow.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .. import config


@dataclass
class SizeResult:
    quantity: int
    risk_rupees: float
    position_value: float
    bound_by: str
    detail: str = ""
    caps: dict[str, int] = field(default_factory=dict)

    @property
    def tradeable(self) -> bool:
        return self.quantity > 0

    def as_dict(self) -> dict[str, Any]:
        return {"quantity": self.quantity,
                "risk_rupees": round(self.risk_rupees, 2),
                "position_value": round(self.position_value, 2),
                "bound_by": self.bound_by, "detail": self.detail,
                "caps": self.caps}


def stop_from_atr(entry: float, atr: float, side: str,
                  multiplier: float = 2.0) -> float:
    """A stop placed at a distance the instrument actually moves.

    A fixed percentage stop is too tight on a volatile name -- it gets hit
    by noise -- and too loose on a calm one, where it risks far more than
    intended for the same nominal distance.
    """
    distance = abs(atr) * multiplier
    return entry - distance if side.upper() == "BUY" else entry + distance


def size(*, entry_price: float, stop_price: float, side: str,
         capital: float | None = None,
         agent_multiplier: float = 1.0,
         available_exposure_pct: float | None = None,
         lot_size: int = 1,
         max_risk_pct: float | None = None) -> SizeResult:
    """Quantity for one trade, bound by whichever cap is tightest.

    `agent_multiplier` comes from the agent layer and is clamped to at most
    1.0 before use. The clamp is here as well as in the schema because this
    is the last place the number is read, and a guarantee that matters is
    worth asserting at the point of use.
    """
    capital = config.TRADING_CAPITAL if capital is None else float(capital)
    max_risk_pct = (config.MAX_RISK_PER_TRADE_PCT if max_risk_pct is None
                    else max_risk_pct)

    if capital <= 0:
        return SizeResult(0, 0.0, 0.0, "no_capital",
                          "TRADING_CAPITAL is not set -- refusing to guess")
    if entry_price <= 0:
        return SizeResult(0, 0.0, 0.0, "no_price", "no entry price")

    stop_distance = abs(entry_price - stop_price)
    if stop_price <= 0 or stop_distance <= 0:
        # Without a stop there is no risk per share, so there is nothing to
        # size against. Refusing is the only honest answer; picking a
        # default stop here would be inventing the trader's risk appetite.
        return SizeResult(0, 0.0, 0.0, "no_stop",
                          "every position needs a stop -- no stop, no size")

    # The stop must be on the losing side of the entry, or it is a target.
    wrong_side = ((side.upper() == "BUY" and stop_price >= entry_price)
                  or (side.upper() == "SELL" and stop_price <= entry_price))
    if wrong_side:
        return SizeResult(0, 0.0, 0.0, "stop_wrong_side",
                          f"{side} stop at {stop_price} is not below/above "
                          f"entry {entry_price}")

    risk_rupees = capital * max_risk_pct / 100
    by_risk = int(risk_rupees / stop_distance)

    # Concentration cap: no position larger than this share of capital.
    by_position_cap = int(capital * config.MAX_POSITION_PCT / 100 / entry_price)

    # What is left under the total exposure limit, when the caller knows.
    if available_exposure_pct is None:
        by_exposure = by_position_cap
    else:
        by_exposure = int(capital * max(0.0, available_exposure_pct)
                          / 100 / entry_price)

    # Fat-finger cap, shared with the order builder.
    by_order_value = int(config.MAX_ORDER_VALUE / entry_price)

    caps = {
        "risk": by_risk,
        "position_cap": by_position_cap,
        "exposure_left": by_exposure,
        "order_value": by_order_value,
    }

    quantity = min(caps.values())
    bound_by = min(caps, key=lambda k: caps[k])

    # The agent layer applies last and only downward.
    multiplier = max(0.0, min(1.0, float(agent_multiplier)))
    if multiplier < 1.0:
        reduced = int(quantity * multiplier)
        if reduced < quantity:
            bound_by = "agents"
            quantity = reduced

    # Round down to a whole lot. Rounding up would breach the cap that was
    # just applied.
    if lot_size > 1:
        lots = quantity // lot_size
        if lots == 0:
            return SizeResult(
                0, risk_rupees, 0.0, "below_one_lot",
                f"risk budget allows {quantity} units, one lot is {lot_size}",
                caps)
        quantity = lots * lot_size

    if quantity <= 0:
        return SizeResult(0, risk_rupees, 0.0, bound_by,
                          "every cap rounds down to zero quantity", caps)

    return SizeResult(
        quantity=quantity,
        risk_rupees=quantity * stop_distance,
        position_value=quantity * entry_price,
        bound_by=bound_by,
        detail=(f"{quantity} units, risking Rs {quantity * stop_distance:,.0f} "
                f"at a stop {stop_distance:.2f} away"),
        caps=caps,
    )


def defensive_multiplier(*, consecutive_losses: int = 0,
                         weekly_loss_pct: float = 0.0) -> tuple[float, list[str]]:
    """Size reduction from the account's recent record, with the reasons.

    These are the orange lights from `07-risk-limits.md`: not a halt, but a
    signal that whatever is being traded is not working at the moment.
    Multiplicative, because two of them are worse than one.
    """
    multiplier = 1.0
    reasons: list[str] = []

    if consecutive_losses >= config.CONSECUTIVE_LOSSES_HALF_SIZE:
        multiplier *= 0.5
        reasons.append(f"{consecutive_losses} losses in a row -- half size")
    if weekly_loss_pct >= config.WEEKLY_LOSS_HALF_SIZE_PCT:
        multiplier *= 0.5
        reasons.append(f"down {weekly_loss_pct:.1f}% on the week -- half size")

    return multiplier, reasons


def btst_cap(quantity: int, entry_price: float, capital: float | None = None,
             gap_pct: float = 5.0) -> tuple[int, str]:
    """Shrink an overnight position until a gap through the stop is survivable.

    An overnight stop is not a stop. The market can open below it and the
    first available price is the one you get, so the position has to be
    sized as though the gap has already happened.

    Worth being honest about how much this does: under the default 10%
    position cap, a 5% gap costs 0.5% of capital, which is already inside
    the 1% per-trade budget. So on the shipped config this rarely binds and
    the real BTST protection is the position cap. It starts to matter when
    the position cap is raised or a wider gap is assumed -- which is the
    case it exists for.
    """
    capital = config.TRADING_CAPITAL if capital is None else float(capital)
    if capital <= 0 or entry_price <= 0 or quantity <= 0:
        return 0, "no capital or price for a BTST size"

    risk_rupees = capital * config.MAX_RISK_PER_TRADE_PCT / 100
    gap_loss_per_share = entry_price * gap_pct / 100
    allowed = int(risk_rupees / gap_loss_per_share)

    if allowed >= quantity:
        return quantity, f"survives a {gap_pct}% gap at this size"
    return allowed, (f"cut from {quantity} to {allowed} so a {gap_pct}% "
                     f"overnight gap stays within the per-trade risk")
