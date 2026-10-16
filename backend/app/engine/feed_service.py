"""Wires the market feed to the rest of the app.

Three things happen for every frame, in this order:
  1. decode protobuf -> Tick
  2. update the in-memory latest-price cache  (microseconds, used by everything)
  3. hand the row to the Parquet writer       (buffered, flushed in background)

Step 2 is what the order path and the bots read. Step 3 is what the backtester
will read months from now. Neither one may block the socket, so nothing here
awaits on disk or network.
"""
from __future__ import annotations

import asyncio
from typing import Any

from ..clock import now_ist
from ..features.engine import engine as feature_engine
from ..store import ticks as tick_store
from ..upstox import feed as feed_mod
from ..upstox import ticks as tick_codec

# instrument_key -> latest Tick. Plain dict: reads are the hot path and this
# is single-writer (the feed task), so a lock would only add latency.
_latest: dict[str, tick_codec.Tick] = {}

_frames = 0
_ticks_seen = 0
_decode_errors = 0
_last_frame_at: str | None = None
_market_status: dict[str, str] = {}


def _handle_frame(raw: bytes) -> None:
    """Called from the feed loop for every websocket message."""
    global _frames, _ticks_seen, _decode_errors, _last_frame_at

    try:
        frame = tick_codec.decode(raw)
    except Exception:                        # noqa: BLE001 -- never kill the socket
        _decode_errors += 1
        return

    _frames += 1
    _last_frame_at = now_ist().isoformat(timespec="seconds")

    if frame.market_status:
        _market_status.update(frame.market_status)

    if not frame.ticks:
        return

    rows = []
    for tick in frame.ticks:
        _latest[tick.instrument_key] = tick
        rows.append(tick.to_row())
        # O(1) per feature, so this stays inside the socket loop. Stale ticks
        # are rejected inside the engine rather than silently folded in.
        feature_engine.on_tick(tick)
    _ticks_seen += len(rows)
    tick_store.writer.add_many(rows)


# --------------------------------------------------------------- reads
def latest(instrument_key: str) -> tick_codec.Tick | None:
    return _latest.get(instrument_key)


def latest_price(instrument_key: str) -> float:
    """LTP from the live feed, or 0.0 if we have not seen this instrument."""
    tick = _latest.get(instrument_key)
    return tick.ltp if tick else 0.0


def snapshot(keys: list[str] | None = None) -> list[dict[str, Any]]:
    wanted = keys if keys is not None else list(_latest)
    out = []
    for key in wanted:
        tick = _latest.get(key)
        if not tick:
            continue
        out.append({
            "instrument_key": key,
            "ltp": tick.ltp,
            "change": round(tick.change, 2),
            "change_pct": round(tick.change_pct, 2),
            "volume": tick.volume,
            "best_bid": tick.best_bid,
            "best_ask": tick.best_ask,
            "spread": round(tick.spread, 2),
            "book_imbalance": round(tick.book_imbalance, 4),
            "mode": tick.mode,
            "ts": tick.ts,
        })
    return out


def status() -> dict[str, Any]:
    return {
        "feed": feed_mod.feed.status(),
        "writer": tick_store.writer.stats.as_dict(),
        "frames": _frames,
        "ticks_seen": _ticks_seen,
        "decode_errors": _decode_errors,
        "instruments_cached": len(_latest),
        "last_frame_at": _last_frame_at,
        "market_status": _market_status,
        "features": feature_engine.status(),
        "proto_available": tick_codec.PROTO_AVAILABLE,
        "proto_error": tick_codec.PROTO_ERROR,
    }


# ----------------------------------------------------------- lifecycle
async def start() -> None:
    feed_mod.feed.on_message = _handle_frame
    await tick_store.writer.start()
    await feed_mod.feed.start()


async def stop() -> None:
    await feed_mod.feed.stop()
    await tick_store.writer.stop()


async def subscribe(keys: list[str], mode: str = "full") -> set[str]:
    return await feed_mod.feed.subscribe(keys, feed_mod.Mode(mode))


async def unsubscribe(keys: list[str]) -> set[str]:
    return await feed_mod.feed.unsubscribe(keys)


async def flush_now() -> int:
    """Force a Parquet write -- used by tests and the shutdown path."""
    return await tick_store.writer.flush()


def _reset_for_tests() -> None:
    global _frames, _ticks_seen, _decode_errors, _last_frame_at
    _latest.clear()
    _market_status.clear()
    _frames = _ticks_seen = _decode_errors = 0
    _last_frame_at = None
