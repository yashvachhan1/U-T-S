"""Order construction and the guards every order passes through.

Separate from trading.py because this is the part that decides *what* gets
sent, and it has to be testable without a broker connection.

Upstox stopped accepting MARKET orders over the API on 1 April 2026, so a
"market" order here becomes a marketable LIMIT: priced through the touch so it
fills like a market order, but with a ceiling on how bad the fill can be.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any

from .. import config
from ..clock import now_ist
from . import client, instruments

VALID_SIDES = {"BUY", "SELL"}
VALID_PRODUCTS = {"I", "D", "MTF"}              # Intraday / Delivery / MTF
VALID_ORDER_TYPES = {"MARKET", "LIMIT", "SL", "SL-M"}
VALID_VALIDITY = {"DAY", "IOC"}

# What each requested type actually becomes on the wire.
WIRE_ORDER_TYPE = {
    "MARKET": "LIMIT",      # marketable limit -- MARKET is no longer accepted
    "LIMIT": "LIMIT",
    "SL-M": "SL",           # stop-loss market -> stop-loss limit
    "SL": "SL",
}

DEFAULT_TICK = 0.05


class OrderRejected(Exception):
    """A guard refused the order before it reached the broker."""


# --------------------------------------------------------------------------
# Price helpers
# --------------------------------------------------------------------------
def tick_size_for(instrument_key: str) -> float:
    row = instruments.by_key(instrument_key)
    tick = float((row or {}).get("tick_size") or 0) or DEFAULT_TICK
    # The instrument master reports some ticks in paise (e.g. 5 for 0.05).
    return tick / 100 if tick >= 1 else tick


def round_to_tick(price: float, tick: float, side: str) -> float:
    """Round so the order stays marketable.

    A BUY rounds up and a SELL rounds down: rounding the other way can leave
    the order sitting just outside the touch, which defeats the point of a
    marketable limit.
    """
    if tick <= 0:
        return round(price, 2)
    steps = price / tick
    steps = math.ceil(steps) if side == "BUY" else math.floor(steps)
    return round(steps * tick, 2)


def marketable_limit_price(ltp: float, side: str, band_pct: float | None = None) -> float:
    """Price a 'market' order through the touch by `band_pct`."""
    band = config.MARKET_ORDER_BAND_PCT if band_pct is None else band_pct
    factor = 1 + band / 100 if side == "BUY" else 1 - band / 100
    return ltp * factor


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------
def idempotency_key(
    *, instrument_key: str, side: str, quantity: int, source: str, bucket_seconds: int = 60
) -> str:
    """Stable id for 'the same order, sent again'.

    A retry after a timeout must not create a second position, so the tag is
    derived from the order's own fields plus a coarse time bucket. The same
    intent inside the bucket produces the same tag; a genuinely new order a
    minute later produces a different one.

    Upstox caps the tag at 20 characters.
    """
    bucket = int(now_ist().timestamp() // bucket_seconds)
    raw = f"{instrument_key}|{side}|{quantity}|{source}|{bucket}"
    digest = hashlib.sha1(raw.encode()).hexdigest()[:12]
    return f"{config.ALGO_NAME[:7]}-{digest}"[:20]


# --------------------------------------------------------------------------
# The order itself
# --------------------------------------------------------------------------
@dataclass
class OrderRequest:
    instrument_key: str
    side: str
    quantity: int
    order_type: str = "MARKET"
    product: str = "D"
    price: float = 0.0
    trigger_price: float = 0.0
    validity: str = "DAY"
    source: str = "manual"

    def normalised(self) -> OrderRequest:
        return OrderRequest(
            instrument_key=self.instrument_key,
            side=self.side.upper(),
            quantity=int(self.quantity),
            order_type=self.order_type.upper(),
            product=self.product.upper(),
            price=float(self.price or 0),
            trigger_price=float(self.trigger_price or 0),
            validity=self.validity.upper(),
            source=self.source,
        )


@dataclass
class BuiltOrder:
    """What actually goes to the broker, plus why it looks that way."""

    payload: dict[str, Any]
    limit_price: float
    reference_price: float
    tag: str
    converted_from: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def order_value(self) -> float:
        return self.limit_price * self.payload["quantity"]


def validate(req: OrderRequest) -> None:
    """Shape checks -- cheap, and they run before anything touches the network."""
    if req.side not in VALID_SIDES:
        raise OrderRejected(f"Side BUY ya SELL hona chahiye, mila: {req.side}")
    if req.product not in VALID_PRODUCTS:
        raise OrderRejected(f"Product I, D ya MTF hona chahiye, mila: {req.product}")
    if req.order_type not in VALID_ORDER_TYPES:
        raise OrderRejected(f"Order type galat hai: {req.order_type}")
    if req.validity not in VALID_VALIDITY:
        raise OrderRejected(f"Validity DAY ya IOC honi chahiye, mili: {req.validity}")
    if req.quantity <= 0:
        raise OrderRejected("Quantity 0 se zyada honi chahiye.")
    if req.order_type == "LIMIT" and not req.price:
        raise OrderRejected("LIMIT order ke liye price dena zaroori hai.")
    if req.order_type in ("SL", "SL-M") and not req.trigger_price:
        raise OrderRejected(f"{req.order_type} order ke liye trigger price zaroori hai.")


def build(req: OrderRequest, ltp: float) -> BuiltOrder:
    """Turn a request into the exact payload Upstox will receive.

    Pure function: no network, no clock beyond the idempotency bucket. That is
    what makes the whole guard chain testable.
    """
    req = req.normalised()
    validate(req)

    notes: list[str] = []
    converted: str | None = None
    tick = tick_size_for(req.instrument_key)

    # ---- work out the limit price -------------------------------------
    if req.order_type in ("MARKET", "SL-M"):
        if ltp <= 0:
            raise OrderRejected(
                "Market order ke liye live price nahi mila. "
                "LIMIT order use karein ya market khulne ka intezaar karein."
            )
        base = req.trigger_price if req.order_type == "SL-M" and req.trigger_price else ltp
        raw = marketable_limit_price(base, req.side)
        limit_price = round_to_tick(raw, tick, req.side)
        converted = req.order_type
        notes.append(
            f"{req.order_type} -> LIMIT @ {limit_price} "
            f"({config.MARKET_ORDER_BAND_PCT}% band, ref {base})"
        )
    else:
        limit_price = round_to_tick(req.price, tick, req.side)
        if abs(limit_price - req.price) > 1e-9:
            notes.append(f"Price {req.price} -> {limit_price} (tick {tick})")

    # ---- sanity against the live price --------------------------------
    if ltp > 0:
        deviation = abs(limit_price - ltp) / ltp * 100
        if deviation > config.MAX_PRICE_DEVIATION_PCT:
            raise OrderRejected(
                f"Limit price {limit_price} LTP {ltp} se {deviation:.1f}% door hai "
                f"(max {config.MAX_PRICE_DEVIATION_PCT}%). Stale data ya galat input."
            )
    elif not config.PAPER_TRADING:
        # No reference price means the deviation guard cannot run. In paper mode
        # that is fine; with real money it means the data path is broken, and
        # placing orders blind is exactly how a bad price gets through.
        raise OrderRejected(
            "Live price nahi mil raha, isliye price verify nahi ho sakti. "
            "Live mode me bina reference price ke order nahi jayega."
        )

    # ---- fat-finger guard ---------------------------------------------
    value = limit_price * req.quantity
    if value > config.MAX_ORDER_VALUE:
        raise OrderRejected(
            f"Order value Rs {value:,.0f} limit Rs {config.MAX_ORDER_VALUE:,.0f} se zyada hai."
        )

    tag = idempotency_key(
        instrument_key=req.instrument_key,
        side=req.side,
        quantity=req.quantity,
        source=req.source,
    )

    payload = {
        "quantity": req.quantity,
        "product": req.product,
        "validity": req.validity,
        "price": limit_price,
        "tag": tag,
        "instrument_token": req.instrument_key,
        "order_type": WIRE_ORDER_TYPE[req.order_type],
        "transaction_type": req.side,
        "disclosed_quantity": 0,
        "trigger_price": round_to_tick(req.trigger_price, tick, req.side)
        if req.trigger_price else 0.0,
        "is_amo": False,
        # Market Price Protection is on by default now; being explicit means the
        # payload says what we intend rather than relying on a broker default.
        "slice": False,
    }

    return BuiltOrder(
        payload=payload,
        limit_price=limit_price,
        reference_price=ltp,
        tag=tag,
        converted_from=converted,
        notes=notes,
    )


def algo_headers() -> dict[str, str]:
    """Exchange-mandated algo identification, sent with every order."""
    return {"X-Algo-Name": config.ALGO_NAME}


def describe(built: BuiltOrder) -> str:
    p = built.payload
    sym = (instruments.by_key(p["instrument_token"]) or {}).get(
        "symbol", p["instrument_token"]
    )
    return (
        f"{p['transaction_type']} {p['quantity']} x {sym} "
        f"@ {built.limit_price} ({p['order_type']}, {p['product']})"
    )


__all__ = [
    "OrderRequest", "BuiltOrder", "OrderRejected", "build", "validate",
    "idempotency_key", "round_to_tick", "marketable_limit_price",
    "tick_size_for", "algo_headers", "describe",
]
