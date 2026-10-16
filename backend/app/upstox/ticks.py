"""Decode Upstox feed frames into one flat tick shape.

The wire format is a protobuf union: a feed is LTPC, or a full feed (which is
itself either a market or an index feed), or a first-level-with-greeks. Letting
that shape leak into features and strategies would mean every consumer handling
four cases. So everything collapses here into a single Tick.

The generated module comes from the official MarketDataFeed.proto, compiled
into proto/MarketDataFeedV3_pb2.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The generated module imports itself by bare name, so its directory has to be
# importable on its own.
import sys

_PROTO_DIR = Path(__file__).parent / "proto"
if str(_PROTO_DIR) not in sys.path:
    sys.path.insert(0, str(_PROTO_DIR))

try:
    import MarketDataFeedV3_pb2 as pb          # type: ignore
    PROTO_AVAILABLE = True
    PROTO_ERROR: str | None = None
except Exception as exc:                        # noqa: BLE001
    pb = None                                   # type: ignore
    PROTO_AVAILABLE = False
    PROTO_ERROR = str(exc)


@dataclass
class DepthLevel:
    bid_qty: int = 0
    bid_price: float = 0.0
    ask_qty: int = 0
    ask_price: float = 0.0


@dataclass
class Tick:
    """One instrument's state at one instant, flattened.

    Only `instrument_key`, `ts` and `ltp` are always present. Depth and greeks
    depend on the subscription mode, so consumers check before using them.
    """

    instrument_key: str
    ts: int                                     # exchange ms, or feed ts
    ltp: float = 0.0
    ltq: int = 0
    close_price: float = 0.0                    # previous close
    volume: int = 0                             # cumulative day volume (vtt)
    atp: float = 0.0                            # average traded price
    oi: float = 0.0
    iv: float = 0.0
    total_buy_qty: float = 0.0
    total_sell_qty: float = 0.0
    depth: list[DepthLevel] = field(default_factory=list)
    greeks: dict[str, float] = field(default_factory=dict)
    mode: str = ""
    is_index: bool = False

    # ---- derived, cheap enough to compute on every tick ----------------
    @property
    def best_bid(self) -> float:
        return self.depth[0].bid_price if self.depth else 0.0

    @property
    def best_ask(self) -> float:
        return self.depth[0].ask_price if self.depth else 0.0

    @property
    def spread(self) -> float:
        if not self.depth or not self.best_bid or not self.best_ask:
            return 0.0
        return self.best_ask - self.best_bid

    @property
    def mid(self) -> float:
        if not self.best_bid or not self.best_ask:
            return self.ltp
        return (self.best_bid + self.best_ask) / 2

    @property
    def book_imbalance(self) -> float:
        """(bid - ask) / (bid + ask) over the visible book, in [-1, 1].

        Positive means more size resting on the bid. This is the cheap cousin
        of order-flow imbalance and needs no history.
        """
        bid = sum(d.bid_qty for d in self.depth)
        ask = sum(d.ask_qty for d in self.depth)
        total = bid + ask
        return (bid - ask) / total if total else 0.0

    @property
    def change(self) -> float:
        return self.ltp - self.close_price if self.close_price else 0.0

    @property
    def change_pct(self) -> float:
        return (self.change / self.close_price * 100) if self.close_price else 0.0

    def to_row(self) -> dict[str, Any]:
        """Flat row for Parquet. Depth is kept as the top level only."""
        d0 = self.depth[0] if self.depth else DepthLevel()
        return {
            "instrument_key": self.instrument_key,
            "ts": self.ts,
            "ltp": self.ltp,
            "ltq": self.ltq,
            "close_price": self.close_price,
            "volume": self.volume,
            "atp": self.atp,
            "oi": self.oi,
            "iv": self.iv,
            "bid_qty": d0.bid_qty,
            "bid_price": d0.bid_price,
            "ask_qty": d0.ask_qty,
            "ask_price": d0.ask_price,
            "total_buy_qty": self.total_buy_qty,
            "total_sell_qty": self.total_sell_qty,
            "book_imbalance": round(self.book_imbalance, 6),
            "mode": self.mode,
        }


@dataclass
class FeedFrame:
    """One decoded message: ticks plus whatever market status came with it."""

    type: str = ""
    current_ts: int = 0
    ticks: list[Tick] = field(default_factory=list)
    market_status: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Decoding
# --------------------------------------------------------------------------
def _ltpc_into(tick: Tick, ltpc: Any) -> None:
    tick.ltp = ltpc.ltp
    tick.ltq = ltpc.ltq
    tick.close_price = ltpc.cp
    if ltpc.ltt:
        tick.ts = ltpc.ltt


def _quote_to_level(q: Any) -> DepthLevel:
    return DepthLevel(bid_qty=q.bidQ, bid_price=q.bidP, ask_qty=q.askQ, ask_price=q.askP)


def _greeks_to_dict(g: Any) -> dict[str, float]:
    return {"delta": g.delta, "theta": g.theta, "gamma": g.gamma,
            "vega": g.vega, "rho": g.rho}


def _decode_feed(key: str, feed: Any, fallback_ts: int) -> Tick:
    tick = Tick(instrument_key=key, ts=fallback_ts)
    which = feed.WhichOneof("FeedUnion")

    if which == "ltpc":
        tick.mode = "ltpc"
        _ltpc_into(tick, feed.ltpc)

    elif which == "firstLevelWithGreeks":
        f = feed.firstLevelWithGreeks
        tick.mode = "option_greeks"
        _ltpc_into(tick, f.ltpc)
        tick.volume, tick.oi, tick.iv = f.vtt, f.oi, f.iv
        tick.depth = [_quote_to_level(f.firstDepth)]
        tick.greeks = _greeks_to_dict(f.optionGreeks)

    elif which == "fullFeed":
        inner = feed.fullFeed.WhichOneof("FullFeedUnion")
        if inner == "marketFF":
            m = feed.fullFeed.marketFF
            tick.mode = "full"
            _ltpc_into(tick, m.ltpc)
            tick.volume, tick.atp = m.vtt, m.atp
            tick.oi, tick.iv = m.oi, m.iv
            tick.total_buy_qty, tick.total_sell_qty = m.tbq, m.tsq
            tick.depth = [_quote_to_level(q) for q in m.marketLevel.bidAskQuote]
            tick.greeks = _greeks_to_dict(m.optionGreeks)
        elif inner == "indexFF":
            idx = feed.fullFeed.indexFF
            tick.mode = "full"
            tick.is_index = True
            _ltpc_into(tick, idx.ltpc)

    return tick


def decode(raw: bytes) -> FeedFrame:
    """Parse one websocket frame. Never raises on bad input -- returns empty."""
    if not PROTO_AVAILABLE:
        raise RuntimeError(
            "Protobuf module nahi mila. Chalayein:\n"
            "  python -m grpc_tools.protoc --proto_path=backend/app/upstox/proto "
            "--python_out=backend/app/upstox/proto "
            "backend/app/upstox/proto/MarketDataFeedV3.proto\n"
            f"Error: {PROTO_ERROR}"
        )

    response = pb.FeedResponse()
    try:
        response.ParseFromString(raw)
    except Exception:                           # noqa: BLE001
        return FeedFrame()

    frame = FeedFrame(
        type=pb.Type.Name(response.type) if response.type is not None else "",
        current_ts=response.currentTs,
    )

    for key, feed in response.feeds.items():
        frame.ticks.append(_decode_feed(key, feed, response.currentTs))

    if response.HasField("marketInfo"):
        frame.market_status = {
            seg: pb.MarketStatus.Name(st)
            for seg, st in response.marketInfo.segmentStatus.items()
        }

    return frame


__all__ = [
    "Tick", "DepthLevel", "FeedFrame", "decode",
    "PROTO_AVAILABLE", "PROTO_ERROR",
]
